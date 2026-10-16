"""E2E 评分：逐点 + 整篇 + 数值程序核验（PRD §6.1/§6.2）。

- 逐点：LLM-as-judge → GradeResult（verdict ∈ met/partial/missing/contradicted/unknown）。
- 整篇：LLM-as-judge → 5 维 1–5 分。
- 数值：能可靠抽取时程序核验并标 measured，否则标 model_judged，不谎称程序已确认。
- 汇总：required 加权命中率、遗漏、矛盾、unknown 保留在分母（不静默剔除），
  optional 独立展示；本版不把质量/耗时/token 拼成单一总分。
"""
from __future__ import annotations

import re

from eval.contracts import GradeResult
from eval.financial_e2e.judge import WHOLE_DIMENSIONS
from eval.financial_e2e.schema import CaseRecord

GRADER_VERSION = "e2e-v1"
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 常见单位换算到 usd_mn（答案可能用亿/百万表达）
_UNIT_TO_USD_MN = {"usd_mn": 1.0, "usd_million": 1.0, "百万美元": 1.0, "百万": 1.0,
                   "usd_bn": 1000.0, "十亿美元": 1000.0, "亿美元": 100.0, "亿": 100.0}


def extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _match_number(expected: float, numbers: list[float], rel: float,
                  abs_tol: float | None) -> float | None:
    for n in numbers:
        tol = abs_tol if abs_tol is not None else abs(expected) * rel
        if abs(n - expected) <= max(tol, 1e-9):
            return n
    return None


def verify_numeric(case: CaseRecord, answer_text: str) -> list[dict]:
    """程序核验数值断言。找到匹配数字 → measured；否则 model_judged 交由逐点判定。"""
    numbers = extract_numbers(answer_text)
    results: list[dict] = []
    for a in case.numeric_assertions:
        found = _match_number(a.value, numbers, a.tolerance, a.tolerance_abs)
        if found is not None:
            results.append({"metric": a.metric, "period": a.period, "expected": a.value,
                            "unit": a.unit, "found": found, "ok": True,
                            "method": "measured", "category": a.category})
        else:
            # 允许单位换算后的匹配（如答案写成亿美元）
            factor = _UNIT_TO_USD_MN.get(a.unit, 1.0)
            alt = None
            for n in numbers:
                for scale in (factor, factor * 100, factor / 100):
                    if scale and abs(n * scale - a.value) <= abs(a.value) * a.tolerance:
                        alt = n
                        break
                if alt is not None:
                    break
            if alt is not None:
                results.append({"metric": a.metric, "period": a.period, "expected": a.value,
                                "unit": a.unit, "found": alt, "ok": True,
                                "method": "measured_unit_converted", "category": a.category})
            else:
                results.append({"metric": a.metric, "period": a.period, "expected": a.value,
                                "unit": a.unit, "found": None, "ok": None,
                                "method": "model_judged", "category": a.category})
    return results


def grade_point(case: CaseRecord, point, answer_text: str, judge) -> GradeResult:
    facts = _facts_for(case, point)
    out = judge.score_point(case.question, point.model_dump(), facts, answer_text)
    verdict = out.get("verdict", "unknown")
    return GradeResult(
        grader_name="point", grader_version=GRADER_VERSION, case_id=case.case_id,
        run_id="", object_id=point.point_id, verdict=verdict,
        dimension_scores={"weight": point.weight,
                          "required": 1.0 if point.required == "required" else 0.0},
        evidence_refs=[out.get("evidence", "")] if out.get("evidence") else [],
        unknown_reason=None if verdict != "unknown" else out.get("reason", ""),
        model=out.get("model"), prompt_version=out.get("prompt_version"),
        usage=out.get("usage") or {},
    )


def _missing_point(case: CaseRecord, point, run_id: str) -> GradeResult:
    return GradeResult(
        grader_name="point", grader_version=GRADER_VERSION, case_id=case.case_id,
        run_id=run_id, object_id=point.point_id, verdict="missing",
        dimension_scores={"weight": point.weight,
                          "required": 1.0 if point.required == "required" else 0.0},
        evidence_refs=[], unknown_reason=None,
    )


def _facts_for(case: CaseRecord, point) -> str:
    """把该点相关的数值断言整理成"可用事实"，供 judge 核验；不暴露无关 gold。"""
    relevant = [a for a in case.numeric_assertions if a.metric in " ".join(point.evidence_source)
                or not point.evidence_source]
    lines = [f"- {a.metric} {a.period}: {a.value} {a.unit}"
             + (f"（基期 {a.baseline_period}）" if a.baseline_period else "")
             for a in (relevant or case.numeric_assertions)[:12]]
    return "\n".join(lines)


def grade_whole(case: CaseRecord, answer_text: str, judge) -> GradeResult:
    out = judge.score_whole(case.question, answer_text)
    dims = out.get("dimensions") or {}
    clean = {d: float(dims[d]) for d in WHOLE_DIMENSIONS if d in dims}
    return GradeResult(
        grader_name="whole", grader_version=GRADER_VERSION, case_id=case.case_id,
        run_id="", object_id="whole", verdict="met" if clean else "unknown",
        dimension_scores=clean, evidence_refs=[out.get("reason", "")] if out.get("reason") else [],
        model=out.get("model"), prompt_version=out.get("prompt_version"),
        usage=out.get("usage") or {},
    )


def grade_case(case: CaseRecord, answer_text: str, judge, run_id: str = "") -> dict:
    """完整评一题：逐点 + 整篇 + 数值。

    未交付（answer_text 为空）时所有点直接记 missing，不调用 judge——
    refuse/异常/轮数耗尽计入未完成分母（PRD §6.1），避免把"没答案"浪费成 judge 调用。
    """
    if not (answer_text or "").strip():
        points = [_missing_point(case, p, run_id) for p in case.scoring_points]
        whole = GradeResult(grader_name="whole", grader_version=GRADER_VERSION,
                            case_id=case.case_id, run_id=run_id, object_id="whole",
                            verdict="missing", dimension_scores={}, evidence_refs=[],
                            unknown_reason=None)
        numeric = verify_numeric(case, "")
        return {"case_id": case.case_id, "run_id": run_id, "points": points,
                "whole": whole, "numeric": numeric,
                "aggregate": aggregate_case(case, points, whole, numeric, delivered=False)}
    points = [grade_point(case, p, answer_text, judge) for p in case.scoring_points]
    for g in points:
        g.run_id = run_id
    whole = grade_whole(case, answer_text, judge)
    whole.run_id = run_id
    numeric = verify_numeric(case, answer_text)
    return {"case_id": case.case_id, "run_id": run_id, "points": points,
            "whole": whole, "numeric": numeric,
            "aggregate": aggregate_case(case, points, whole, numeric,
                                        delivered=bool(answer_text))}


def _point_met(g: GradeResult) -> float:
    return {"met": 1.0, "partial": 0.5, "missing": 0.0,
            "contradicted": 0.0, "unknown": 0.0}[g.verdict]


def aggregate_case(case: CaseRecord, points: list[GradeResult], whole: GradeResult,
                   numeric: list[dict], delivered: bool) -> dict:
    by_id = {p.point_id: g for g, p in zip(points, case.scoring_points)}
    req_pairs = [(case_point, by_id[case_point.point_id])
                 for case_point in case.required_points() if case_point.point_id in by_id]
    opt_pairs = [(case_point, by_id[case_point.point_id])
                 for case_point in case.optional_points() if case_point.point_id in by_id]

    def _weighted(pairs) -> float:
        denom = sum(p.weight for p, _ in pairs)
        if denom <= 0:
            return 0.0
        return sum(p.weight * _point_met(g) for p, g in pairs) / denom

    def _range(pairs) -> tuple[float, float]:
        # unknown 保守=0，乐观=1（PRD §6.2：未知点报告保守/乐观区间）
        denom = sum(p.weight for p, _ in pairs) or 1.0

        def score(unknown_value: float) -> float:
            return sum(p.weight * (unknown_value if g.verdict == "unknown" else _point_met(g))
                       for p, g in pairs) / denom
        return score(0.0), score(1.0)

    conserv, optimist = _range(req_pairs)
    verdicts = [g.verdict for _, g in req_pairs]
    return {
        "delivered": delivered,
        "required_total": len(req_pairs),
        "required_met": verdicts.count("met"),
        "required_partial": verdicts.count("partial"),
        "required_missing": verdicts.count("missing"),
        "required_contradicted": verdicts.count("contradicted"),
        "required_unknown": verdicts.count("unknown"),
        "required_hit_rate": round(_weighted(req_pairs), 4),
        "required_hit_range": [round(conserv, 4), round(optimist, 4)],
        "optional_total": len(opt_pairs),
        "optional_hit_rate": round(_weighted(opt_pairs), 4),
        "whole_dimensions": whole.dimension_scores,
        "numeric_measured": sum(1 for n in numeric if n["method"].startswith("measured")),
        "numeric_model_judged": sum(1 for n in numeric if n["method"] == "model_judged"),
        "missing_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "missing"],
        "contradicted_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "contradicted"],
        "unknown_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "unknown"],
    }

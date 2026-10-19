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

GRADER_VERSION = "e2e-v2"
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 常见单位换算到 usd_mn（答案可能用亿/百万表达）
_UNIT_TO_USD_MN = {"usd_mn": 1.0, "usd_million": 1.0, "百万美元": 1.0, "百萬美元": 1.0,
                   "百万": 1.0, "usd_bn": 1000.0, "十亿美元": 1000.0, "亿美元": 100.0,
                   "億美元": 100.0, "亿": 100.0, "億": 100.0}
# 单位应出现的正向词（宽松）；以及明显冲突词（保守黑名单）
_UNIT_TOKENS = {
    "usd_mn": ("美元", "usd", "million", "百萬", "百万"),
    "pct": ("%", "％", "百分"),
    "pct_point": ("百分点", "個百分點", "pp"),
    "days": ("天",),
    "ratio": ("倍", "x"),
}
_UNIT_CONFLICT = {
    "usd_mn": ("人民币", "港元", "日元", "%"),
    "pct": ("天", "百万美元", "百萬美元"),
    "pct_point": ("天",),
    "days": ("%", "美元"),
}
_WINDOW = 48  # 数字前后检索上下文的字符数

# 季度/累计的多种写法（中英文），用于年度与累计断言的期间隔离
_LATIN_Q = ("Q1", "Q2", "Q3", "Q4", "q1", "q2", "q3", "q4")
_CH_Q1Q4 = ("第一季", "第一季度", "第二季", "第二季度", "第三季", "第三季度",
            "第四季", "第四季度", "一季度", "二季度", "三季度", "四季度",
            "1季度", "2季度", "3季度", "4季度", "首季")
_Q3_Q4 = ("Q3", "q3", "Q4", "q4", "第三季", "第三季度", "三季度", "3季度",
          "第四季", "第四季度", "四季度", "4季度")
_Q4 = ("Q4", "q4", "第四季", "第四季度", "四季度", "4季度")
_CUMULATIVE = ("9M", "9m", "9个月", "9個月", "九个月", "九個月", "前三季", "前三季度",
               "H1", "h1", "上半年", "半年度", "半财年", "半財年")


def extract_numbers(text: str) -> list[float]:
    return [v for _, v in _number_spans(text)]


def _number_spans(text: str) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            out.append((m.start(), float(m.group(0).replace(",", ""))))
        except ValueError:
            continue
    return out


def _period_requirement(period: str) -> tuple[list[list[str]], list[str]]:
    """把断言的 period 归一成 (必须出现的期间 token 组, 禁止出现的期间写法)。

    每个 token 组是"任一命中"；所有组都必须命中。必须区分单季与累计：
    FY2026Q1 的断言不能只凭窗口里的 "FY2026" 就当作命中，否则 FY2026Q1 的数字
    会被算成 FY2026 9M 的命中（复审问题 1）。
    """
    p = period.replace(" ", "").replace("　", "")
    m = re.fullmatch(r"FY(\d{4})(Q[1-4])?", p)
    if m:
        fy4, q = m.group(1), m.group(2)
        short = fy4[2:]
        if q:
            return [[f"FY{fy4}{q}", f"FY{short}{q}", f"{fy4}{q}", f"{short}{q}"]], []
        # 年度断言：窗口内出现季度（含中文"第一季/第一季度"）或累计标记时不算命中，
        # 避免 FY2026Q1 / FY2026第一季度 的数字被当成 FY2026 年度值（复审阻断项）。
        return [[f"FY{fy4}", f"FY{short}"]], \
            list(_LATIN_Q) + list(_CH_Q1Q4) + list(_CUMULATIVE)
    groups: list[list[str]] = []
    forbidden: list[str] = []
    if ("9M" in p or "9个月" in period or "9個月" in period or "九个月" in period
            or "九個月" in period or "前三季" in period):
        groups.append(["9M", "9m", "九个月", "九個月", "9个月", "9個月",
                       "前三季", "前三季度", "九個"])
        forbidden += list(_Q4)
    if "H1" in p.upper() or "上半年" in period or "半财年" in period or "半財年" in period:
        groups.append(["H1", "h1", "上半年", "半年度", "半年"])
        forbidden += list(_Q3_Q4)
    fys = re.findall(r"FY(\d{4})", period)
    if fys:
        groups.append([t for f in fys for t in (f"FY{f}", f"FY{f[2:]}")])
    return groups or [[period]], forbidden


def _candidate_hits(a, spans, text: str) -> list[tuple[int, float, str]]:
    """返回 (位置, 命中值, 命中方式)。含单位换算匹配。"""
    hits: list[tuple[int, float, str]] = []
    factor = _UNIT_TO_USD_MN.get(a.unit, 1.0)
    for pos, n in spans:
        tol = a.tolerance_abs if a.tolerance_abs is not None else abs(a.value) * a.tolerance
        if abs(n - a.value) <= max(tol, 1e-9):
            hits.append((pos, n, "direct"))
        else:
            for scale, tag in ((factor, "unit_converted"), (factor * 100, "unit_converted_100"),
                               (factor / 100, "unit_converted_0.01")):
                if scale and abs(n * scale - a.value) <= max(abs(a.value) * a.tolerance, 1e-9):
                    hits.append((pos, n, tag))
                    break
    return hits


def verify_numeric(case: CaseRecord, answer_text: str) -> list[dict]:
    """程序核验数值断言——必须先确认"该数字出现在对应对期间附近"才算 measured。

    只按数值近似命中会把碰巧出现的数字误报为"程序已核实"（复审问题 2）；
    因此要求数字窗口内出现该断言的期间写法，且单位不出现明显冲突词。
    期间不符或无法确认时标 model_judged，不谎称程序已确认。
    """
    text = answer_text or ""
    spans = _number_spans(text)
    results: list[dict] = []
    for a in case.numeric_assertions:
        required, forbidden = _period_requirement(a.period)
        unit_tokens = _UNIT_TOKENS.get(a.unit, ())
        hits = _candidate_hits(a, spans, text)
        base = {"metric": a.metric, "period": a.period, "expected": a.value,
                "unit": a.unit, "category": a.category}
        measured = None
        conflict = None
        for pos, n, tag in hits:
            window = text[max(0, pos - _WINDOW):pos + _WINDOW]
            if any(w in window for w in _UNIT_CONFLICT.get(a.unit, ())):
                bad = [w for w in _UNIT_CONFLICT.get(a.unit, ()) if w in window]
                conflict = (n, tag, bad)
                continue
            if required and not all(any(t in window for t in grp) for grp in required):
                continue                       # 期间写法不符（如单季 vs 累计）→ 不算命中
            if forbidden and any(f in window for f in forbidden):
                continue                       # 出现超出该口径的期间 → 不算命中
            if unit_tokens and not any(u in window for u in unit_tokens):
                continue                       # 正单位词未出现 → 不能确认单位
            measured = (n, tag)
            break
        if measured is not None:
            n, tag = measured
            results.append({**base, "found": n, "ok": True, "method": f"measured:{tag}",
                            "reason": "数字与期间/单位上下文一致"})
        elif conflict is not None:
            n, tag, bad = conflict
            results.append({**base, "found": n, "ok": False,
                            "method": "measured_unit_mismatch",
                            "reason": f"命中数字但单位疑似冲突: {bad}"})
        else:
            reason = ("命中相近数字但期间口径或单位不符/缺失" if hits
                      else "未抽取到对应期间的确切数字")
            results.append({**base, "found": None, "ok": None, "method": "model_judged",
                            "reason": reason})
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
        from eval.financial_e2e.forecast import forecast_metrics
        return {"case_id": case.case_id, "run_id": run_id, "points": points,
                "whole": whole, "numeric": numeric,
                "forecast": forecast_metrics(case, ""),
                "aggregate": aggregate_case(case, points, whole, numeric, delivered=False)}
    points = [grade_point(case, p, answer_text, judge) for p in case.scoring_points]
    for g in points:
        g.run_id = run_id
    whole = grade_whole(case, answer_text, judge)
    whole.run_id = run_id
    numeric = verify_numeric(case, answer_text)
    from eval.financial_e2e.forecast import forecast_metrics
    forecast = forecast_metrics(case, answer_text)
    return {"case_id": case.case_id, "run_id": run_id, "points": points,
            "whole": whole, "numeric": numeric, "forecast": forecast,
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
        "numeric_measured": sum(1 for n in numeric if n.get("ok") is True
                                and str(n["method"]).startswith("measured")),
        "numeric_conflict": sum(1 for n in numeric if n.get("ok") is False),
        "numeric_model_judged": sum(1 for n in numeric if n["method"] == "model_judged"),
        "missing_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "missing"],
        "contradicted_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "contradicted"],
        "unknown_point_ids": [p.point_id for p, g in req_pairs if g.verdict == "unknown"],
    }

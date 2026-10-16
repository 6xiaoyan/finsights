"""E2E 报告：逐题报告与汇总报告（PRD §9.1）。

- 逐题报告按同一 case/run 关联：最终状态与答案、逐点、整篇、数值、用量、错误。
- 汇总按题型、真实/合成、开发/留出、审核状态分组，显示样本数、完成数、失败数、评分错误数。
- blocked 题保留在报告中，不无声消失，也不与正常答错混成一个比例。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from eval.financial_e2e.schema import CaseRecord


def case_report_md(case: CaseRecord, result: dict, run_id: str, final_status: str,
                   final_answer: str | None) -> str:
    agg = result["aggregate"]
    lines = [f"# 逐题报告：{case.case_id}（run={run_id}）", "",
             f"- 题型：{case.question_type}｜真实/合成：{case.real_synthetic}"
             f"｜split：{case.split}｜审核状态：{case.review_status}",
             f"- 事件族：{case.event_family or '—'}｜split_group：{case.split_group or '—'}",
             f"- 最终交付状态：{final_status}｜是否交付答案：{agg['delivered']}",
             "",
             "## 题目", "", case.question, "",
             f"**范围**：{case.scope}",
             (f"**as_of**：{case.as_of}" if case.as_of else "**as_of**：未提供（当前库）"), "",
             "## 必答点结果", "",
             f"- 命中率（加权）：{agg['required_hit_rate']:.2f}"
             f"（保守/乐观区间 {agg['required_hit_range'][0]:.2f}–{agg['required_hit_range'][1]:.2f}）",
             f"- met={agg['required_met']} partial={agg['required_partial']}"
             f" missing={agg['required_missing']} contradicted={agg['required_contradicted']}"
             f" unknown={agg['required_unknown']} / 共 {agg['required_total']}",
             "",
             "| point_id | 判定 | 权重 | 依据/理由 |",
             "|---|---|---|---|"]
    by_id = {}
    for g in result["points"]:
        by_id[g.object_id] = g
    for p in case.required_points():
        g = by_id.get(p.point_id)
        verdict = g.verdict if g else "unknown"
        ev = "；".join(filter(None, [g.unknown_reason if g else "", *((g.evidence_refs if g else []) or [])]))
        lines.append(f"| {p.point_id} | {verdict} | {p.weight:g} | {_esc(ev)[:160]} |")
    if case.optional_points():
        lines += ["", "## 可选点（独立展示，不计入必答分母）", "",
                  "| point_id | 判定 | 权重 | 依据/理由 |", "|---|---|---|---|"]
        for p in case.optional_points():
            g = by_id.get(p.point_id)
            lines.append(f"| {p.point_id} | {g.verdict if g else 'unknown'} | {p.weight:g} | "
                         f"{_esc('；'.join((g.evidence_refs if g else []) or []))[:160]} |")
    lines += ["", "## 整篇评分（1–5）", ""]
    dims = agg["whole_dimensions"]
    if dims:
        lines += ["| 维度 | 分数 |", "|---|---|"]
        for d, v in dims.items():
            lines.append(f"| {d} | {v:g} |")
    else:
        lines.append("（整篇 judge 未返回有效维度）")
    lines += ["", "## 数值程序核验", "",
              f"- measured：{agg['numeric_measured']}｜model_judged（未可靠抽取）：{agg['numeric_model_judged']}",
              "", "| 指标 | 期间 | 期望 | 抽取 | 方式 |", "|---|---|---|---|---|"]
    for n in result["numeric"]:
        got = "—" if n["found"] is None else f"{n['found']:g}"
        lines.append(f"| {n['metric']} | {n['period']} | {n['expected']:g} {n['unit']}"
                     f" | {got} | {n['method']} |")
    lines += ["", "## 最终答案", "", (final_answer or "（未交付：refuse/clarify/error）"), ""]
    return "\n".join(lines) + "\n"


def _esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def summary_report_md(results: list[dict], dataset_name: str, judge_name: str,
                      judge_model: str | None, run_ids: list[str]) -> str:
    """results: 每项 {case, result, run_id, final_status}。按题型/标记分组。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in results:
        c: CaseRecord = r["case"]
        key = (c.question_type, c.real_synthetic, c.split, c.review_status)
        groups[key].append(r)

    lines = [f"# E2E 汇总报告：{dataset_name}", "",
             f"- 运行：{', '.join(run_ids) or '—'}",
             f"- judge：{judge_name}（model={judge_model or '—'}）",
             f"- 题数：{len(results)}",
             "- 说明：本版不把质量/耗时/token 拼成单一总分，不设统一通过线。", "",
             "## 分组统计", "",
             "| 题型 | 真实/合成 | split | 审核 | 题数 | 交付数 | 必答命中(均值) | 保守–乐观 | 整篇均值 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(groups, key=lambda k: (k[0], k[1], k[2], k[3])):
        rows = groups[key]
        n = len(rows)
        delivered = sum(1 for x in rows if x["result"]["aggregate"]["delivered"])
        hits = [x["result"]["aggregate"]["required_hit_rate"] for x in rows]
        lo = [x["result"]["aggregate"]["required_hit_range"][0] for x in rows]
        hi = [x["result"]["aggregate"]["required_hit_range"][1] for x in rows]
        whole = [sum(x["result"]["aggregate"]["whole_dimensions"].values()) /
                 max(1, len(x["result"]["aggregate"]["whole_dimensions"])) for x in rows
                 if x["result"]["aggregate"]["whole_dimensions"]]
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {n} | {delivered}"
                     f" | {_mean(hits):.2f} | {_mean(lo):.2f}–{_mean(hi):.2f}"
                     f" | {_mean(whole):.2f} |")

    blocked = [r for r in results if r["case"].blocked]
    refused = [r for r in results if r["final_status"] != "answered"]
    lines += ["", "## 未交付与阻塞", "",
              f"- 未交付答案（refuse/clarify/error）：{len(refused)} 题"
              f"（计入应答题分母，不因节点 completed 改记成功）",
              f"- blocked 题：{len(blocked)} 题（保留在数据集中，不与正常答错混算）", ""]
    for r in refused:
        lines.append(f"- 未交付：{r['case'].case_id}｜status={r['final_status']}")
    for r in blocked:
        lines.append(f"- blocked：{r['case'].case_id}｜{r['case'].blocked_reason or '原因未记录'}")

    lines += ["", "## 逐题明细", "",
              "| case_id | 题型 | 交付 | 必答命中 | missing | contradicted | unknown |",
              "|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x["case"].case_id):
        a = r["result"]["aggregate"]
        lines.append(f"| {r['case'].case_id} | {r['case'].question_type}"
                     f" | {'是' if a['delivered'] else '否'} | {a['required_hit_rate']:.2f}"
                     f" | {a['required_missing']} | {a['required_contradicted']}"
                     f" | {a['required_unknown']} |")
    return "\n".join(lines) + "\n"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def write_case_report(outdir: Path, case: CaseRecord, result: dict, run_id: str,
                      final_status: str, final_answer: str | None) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{case.case_id}.md"
    path.write_text(case_report_md(case, result, run_id, final_status, final_answer),
                    encoding="utf-8")
    return path

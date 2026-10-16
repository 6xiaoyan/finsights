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


def _agg(item: dict) -> dict | None:
    return (item.get("result") or {}).get("aggregate")


def summary_report_md(results: list[dict], dataset_name: str, judge_name: str,
                      judge_model: str | None, run_ids: list[str]) -> str:
    """results: 每项 {case, result|None, run_id, final_status}。按题型/标记分组。

    遍历数据集全部题：缺结果的题（未运行/被阻塞）也计入分母，不无声消失（复审问题 3）。
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in results:
        c: CaseRecord = r["case"]
        groups[(c.question_type, c.real_synthetic, c.split, c.review_status)].append(r)

    total = len(results)
    scored = [r for r in results if _agg(r) is not None
              and r["final_status"] not in ("blocked", "run_missing", "not_run")]
    blocked = [r for r in results if r["final_status"] == "blocked" or r["case"].blocked]
    not_run = [r for r in results if r["final_status"] in ("not_run", "run_missing")]
    refused = [r for r in scored if r["final_status"] != "answered"]

    lines = [f"# E2E 汇总报告：{dataset_name}", "",
             f"- 运行：{', '.join(run_ids) or '—'}",
             f"- judge：{judge_name}（model={judge_model or '—'}）",
             f"- 题数：{total}",
             "- 说明：本版不把质量/耗时/token 拼成单一总分，不设统一通过线。", "",
             "## 分母完整性", "",
             "| 口径 | 数量 | 说明 |", "|---|---|---|",
             f"| 数据集题数 | {total} | 汇总范围内全部题 |",
             f"| 已评分 | {len(scored)} | 有运行包并完成评分 |",
             f"| 其中未交付 | {len(refused)} | refuse/clarify/error，计入未完成分母 |",
             f"| blocked | {len(blocked)} | 保留在数据集，不与正常答错混算 |",
             f"| 未运行/缺运行包 | {len(not_run)} | 计入分母并单列，不静默丢弃 |", "",
             "## 分组统计", "",
             "| 题型 | 真实/合成 | split | 审核 | 题数 | 已评分 | 交付数 | 必答命中(均值) | 保守–乐观 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key in sorted(groups, key=lambda k: (k[0], k[1], k[2], k[3])):
        rows = groups[key]
        n = len(rows)
        aggs = [_agg(x) for x in rows]
        scored_rows = [a for a in aggs if a is not None]
        delivered = sum(1 for a in scored_rows if a["delivered"])
        hits = [a["required_hit_rate"] for a in scored_rows]
        lo = [a["required_hit_range"][0] for a in scored_rows]
        hi = [a["required_hit_range"][1] for a in scored_rows]
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {n} | {len(scored_rows)}"
                     f" | {delivered} | {_mean(hits):.2f} | {_mean(lo):.2f}–{_mean(hi):.2f} |")

    if refused:
        lines += ["", "## 已评分但未交付", ""]
        for r in refused:
            lines.append(f"- {r['case'].case_id}｜status={r['final_status']}"
                         f"（计入未完成分母，不因节点 completed 改记成功）")
    if blocked:
        lines += ["", "## blocked（不计入通过率，单列）", ""]
        for r in blocked:
            lines.append(f"- {r['case'].case_id}｜{r['case'].blocked_reason or '原因未记录'}")
    if not_run:
        lines += ["", "## 未运行/缺运行包（计入分母）", ""]
        for r in not_run:
            lines.append(f"- {r['case'].case_id}｜{r['final_status']}")

    # 预测误差（仅评测侧，事后）
    fc = [(r, (_agg(r) or {}) ) for r in results
          if r["case"].question_type == "forecast" and _agg(r) is not None]
    if fc:
        lines += ["", "## 预测题事后误差（仅评测侧）", "",
                  "| case_id | 真实值 | 预测点 | 抽出方式 | 绝对误差 | 相对误差 | 区间覆盖 | 区间宽度 |",
                  "|---|---|---|---|---|---|---|---|"]
        for r, _ in fc:
            fm = (r["result"] or {}).get("forecast") or {}
            if not fm:
                lines.append(f"| {r['case'].case_id} | — | — | 无评测侧真实实绩 | — | — | — | — |")
                continue
            pt = "—" if fm.get("point") is None else f"{fm['point']:g}"
            ae = "—" if fm.get("abs_err") is None else f"{fm['abs_err']:,.2f}"
            pe = "—" if fm.get("pct_err") is None else f"{fm['pct_err']:.1%}"
            cov = "—" if fm.get("covered") is None else ("是" if fm["covered"] else "否")
            wid = "—" if fm.get("width") is None else f"{fm['width']:,.2f}"
            lines.append(f"| {r['case'].case_id} | {fm['actual']:g} | {pt} | "
                         f"{fm.get('point_method')} | {ae} | {pe} | {cov} | {wid} |")
        lines += ["", "- 备注：预测论证质量由逐点得分点判定，与预测结果误差分开呈现（PRD §5.3）。"]

    lines += ["", "## 逐题明细", "",
              "| case_id | 题型 | split | 状态 | 交付 | 必答命中 | missing | contradicted | unknown |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x["case"].case_id):
        a = _agg(r)
        if a is None:
            lines.append(f"| {r['case'].case_id} | {r['case'].question_type}"
                         f" | {r['case'].split} | {r['final_status']}"
                         f" | 否 | — | — | — | — |")
        else:
            lines.append(f"| {r['case'].case_id} | {r['case'].question_type}"
                         f" | {r['case'].split} | {r['final_status']}"
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

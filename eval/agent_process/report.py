"""逐题报告与汇总报告（PRD §9.1）。

逐题报告按同一 case/run 关联：最终状态与答案、宏观（A 的 E2E，若有）、中观角色、
微观查询、运行消耗、错误与证据链接。

硬性要求：
- 直接区分 measured / model_judged / human_reviewed / unknown 的依据来源。
- 不把质量、耗时和 token 拼成单一总分。
- blocked / 未交付的题不静默消失。
- 与 A 的 E2E 结果按 (case_id, run_id) 对齐；没有就写「未提供」，不编造。
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.agent_process.adapt import FIELD_COMPAT
from eval.agent_process.schema import ProcessPackage

REPORT_VERSION = "process-report-v1"

_STATUS_LABEL = {"answered": "answered（已交付）", "clarify": "clarify（澄清）",
                 "refuse": "refuse（未交付）"}


def _esc(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def compat_table_md() -> str:
    """渲染字段兼容表。

    特别标注 `query_trace_id` 与 `plan_digest` 的**同名不同义**：executor 里的
    `query_trace_id` 是内容寻址（sha256(sql+plan)），我们记录的是调用级 id（q1、q2…），
    两者混用会导致关联错位。
    """
    rows = ["| 区域 | 来源 | 规范化字段 | 状态 | 说明 |",
            "|---|---|---|---|---|"]
    for e in FIELD_COMPAT:
        rows.append(f"| {_esc(e['area'])} | `{_esc(e['source'])}` | "
                    f"`{_esc(e['normalized'])}` | {e['status']} | {_esc(e.get('note', ''))} |")
    legend = [
        "",
        "状态含义：`available` 今天的运行包里已有；`derived` 由包内数据推导；",
        "`new_instrumentation` 本次补的埋点才产生（历史包为 unknown）；",
        "`unknown_legacy` 历史包必然缺失，只能记 unknown。",
        "",
        "**同名字段辨析**：`query_records[].query_trace_id` 是**调用级** id（`q1`、`q2`…，",
        "运行内唯一、每次调用必有）；`query_records[].plan_digest` 才是 executor 的",
        "**内容寻址** id（`sha256(sql + plan)[:12]`，非 ok 路径为 null）。",
        "两者同名不同义，不能互换：同计划复用 rid 时，多次调用共享同一个 plan_digest，",
        "但各有自己的 query_trace_id。",
        "",
    ]
    return "\n".join(rows + legend)


def join_e2e(record: dict, e2e_results: list[dict] | None) -> dict | None:
    """按 (case_id, run_id) 对齐 A 的 E2E 结果；对不上就返回 None，不猜。"""
    for r in (e2e_results or []):
        if (r.get("case_id") == record.get("case_id")
                and r.get("run_id") == record.get("run_id")):
            return r
    return None


def build_record(pkg: ProcessPackage, *, metrics: dict, roles: dict, queries: dict,
                 e2e_results: list[dict] | None = None) -> dict:
    """B 的记录：与 A 的 results.jsonl 是兄弟关系，按 (case_id, run_id) join。"""
    rec = {
        "report_version": REPORT_VERSION,
        "case_id": pkg.case_id,
        "run_id": pkg.run_id,
        "final_status": pkg.final_status,
        "delivered": pkg.final_answer is not None,
        "final_answer": pkg.final_answer,
        "process": {
            "metrics": metrics,
            "roles": roles,
            "queries": queries,
            "plans": {"revisions": len(pkg.plans),
                      "nodes": [n.node_id for n in (pkg.plans[-1].nodes if pkg.plans else [])]},
            "reviews": {"total": len(pkg.reviews),
                        "verdicts": roles.get("reviewer", {}).get("counts", {})
                        .get("verdicts", {})},
            "unknown_fields": list(pkg.unknown_fields),
            "diagnostics": dict(pkg.diagnostics),
        },
        # 问题登记：发现的缺陷留痕，供后续优化任务处理（本任务不修 Agent 行为）
        "known_defects": _known_defects(pkg),
    }
    rec["e2e"] = join_e2e(rec, e2e_results)
    return rec


def _known_defects(pkg: ProcessPackage) -> list[dict]:
    """只登记有证据的缺陷；没有就不写。"""
    out: list[dict] = []
    results = json.loads((Path(pkg.run_dir) / "results.json").read_text(encoding="utf-8")) \
        if (Path(pkg.run_dir) / "results.json").is_file() else {}
    rows = results.get("results") or []
    hardcoded = [r for r in rows if r.get("created_step") == 0]
    if len(hardcoded) > 1:
        out.append({
            "id": "created_step-hardcoded",
            "evidence": f"{len(hardcoded)}/{len(rows)} 条结果的 created_step 都是 0",
            "detail": "executor 落库时 step 恒为 0，导致 results.json 的 initial_packet "
                      "无法区分真正的初始包与查询结果；本评测不使用该字段判定",
            "action": "登记待后续优化任务处理，本任务不改 Agent 行为",
        })
    return out


def case_report_md(pkg: ProcessPackage, *, metrics: dict, roles: dict, queries: dict,
                   e2e: dict | None = None) -> str:
    status = _STATUS_LABEL.get(pkg.final_status, pkg.final_status)
    multi = (pkg.run_meta.get("multi_agent") or {})
    plan = pkg.plans[-1] if pkg.plans else None
    cand = pkg.answer_candidate or None

    out: list[str] = [
        f"# 过程评测报告：{pkg.case_id or pkg.run_id}（run={pkg.run_id}）",
        "",
        f"- report_version：{REPORT_VERSION}",
        f"- 运行目录：`{pkg.run_dir}`",
        "- 本报告只评估过程与角色职责，不改变被测 Agent；质量、耗时、token 分列展示，",
        "  不拼成单一总分。",
        "",
        "## 最终状态与答案",
        "",
        f"- 最终状态：{status}",
        f"- 核验通过：{pkg.verified}",
        f"- 终止原因：{multi.get('stop_reason') or '（无）'}",
        f"- final_answer 校验被打回次数：{multi.get('final_answer_rejects')}",
        f"- 最终答案：{'（未提交，见下）' if pkg.final_answer is None else '见 answer.md'}",
        "",
    ]

    if plan:
        nodes = "；".join(f"{n.node_id}={n.execution_status}"
                         f"（审查 {n.review_status}）" for n in plan.nodes)
        out += [
            f"- 计划 revision：{pkg.latest_revision}，节点状态：{nodes}",
            f"- 内部审查结论：semantic_review_status="
            f"{multi.get('semantic_review_status')}，"
            f"task_coverage_status={multi.get('task_coverage_status')}",
            "",
            "> **节点 completed、内部审查 accepted，都不代表 E2E 成功。** "
            "本次运行最终未提交答案，E2E 应记为未完成。",
            "",
        ]
    else:
        out += ["- 该运行包没有分析计划对象（可能为 single_agent 路径）", ""]

    out += ["### 宏观 E2E（来自 Agent A 的评分，若有）", ""]
    if e2e:
        agg = ((e2e.get("result") or {}).get("aggregate") or {})
        out += [f"- 必答点加权命中率：{agg.get('required_hit_rate')}"
                f"（{agg.get('required_met')}/{agg.get('required_total')}）",
                f"- 保守–乐观区间：{agg.get('required_hit_range')}", ""]
    else:
        out += ["- 未提供：未传入 `--e2e-results`，本报告不含宏观得分点评分。",
                "  不编造宏观结论。", ""]

    out += ["## 运行监控", ""]
    tc, rc, tk = metrics["tool_calls"], metrics["role_calls"], metrics["tokens"]
    out += [
        f"- 工具调用：总 {tc['total']}（ok {tc['ok']} / error {tc['error']} / "
        f"skipped {tc['skipped']} / unknown {tc['unknown_status']}）",
        f"- 角色调用：总 {rc['total']}，按角色 {rc['by_role']}",
        f"- 主循环轮数：{metrics['rounds']['main_steps_reported']}"
        f"（assistant 事件 {metrics['rounds']['main_assistant_events']}）；"
        f"子循环步数：{metrics['rounds']['subagent_child_steps']}",
        f"- 查询提取：run_meta 自报 {metrics['extraction']['calls_reported']} 次，"
        f"逐次明细 {'有' if metrics['extraction']['available'] else '无（历史包）'}",
        f"- 耗时：角色总 {metrics['latency_ms']['roles_total']} ms",
        f"- token：prompt={tk['prompt_tokens']} completion={tk['completion_tokens']}"
        f"（来源 {tk['source']}）",
        f"- 费用估算：{metrics['cost_usd_estimate']}"
        f"（{metrics['cost_note']}）",
        f"- 异常类型：{metrics['exception_types'] or '（无）'}",
        f"- 重试：{metrics['retries']}",
        "",
        "> 工具调用成功只描述程序运行状态，不表示取数业务正确（PRD §7.2）。",
        "> 跳过不计成功；缺 usage 不填零。",
        "",
    ]
    if metrics["discrepancies"]:
        out += ["**自报值与重算值不一致**：", ""]
        for d in metrics["discrepancies"]:
            out.append(f"- `{d['field']}`：自报 {d['reported']}，重算 {d['recomputed']}")
        out.append("")

    out += ["## 中观角色评测", ""]
    pl, wk, rv = roles["planner"], roles["worker"], roles["reviewer"]

    out += ["### Planner", "",
            f"- 计划修订数：{pl['plan_revisions']}；每版节点数："
            f"{ {k: len(v) for k, v in pl['nodes_by_revision'].items()} }"]
    m = pl.get("measured") or {}
    if m:
        out += [f"- 节点数 {m.get('node_count')}（其中 required "
                f"{m.get('required_node_count')}）",
                f"- 重复节点目标：{m.get('duplicate_goals') or '（无）'}",
                f"- 悬空依赖：{m.get('dangling_deps') or '（无）'}；"
                f"自依赖：{m.get('self_deps') or '（无）'}；成环：{m.get('cycle')}",
                f"- 范围偏移（合同期间 { (pkg.contract or {}).get('period') }）："
                f"{m.get('scope_drift') or '（无）'}"]
    cov = pl["coverage"]
    out += [f"- 覆盖映射：{cov['status']}"
            + (f"（{cov['reason']}）" if cov.get("reason") else ""),
            f"- 依据：{pl['evidence_basis']}",
            ""]
    if cov.get("mapping"):
        out += ["| 参考点 | 判定 | 覆盖 | 理由 |", "|---|---|---|---|"]
        for e in cov["mapping"]:
            out.append(f"| {_esc(str(e.get('point_id')))} | {e.get('verdict')} | "
                       f"{e.get('coverage')} | {_esc(e.get('reason', ''))} |")
        out.append("")

    out += ["### Worker", ""]
    for nid, node in (wk.get("nodes") or {}).items():
        c = node.get("counts") or {}
        out += [f"- 节点 `{nid}`（{node.get('execution_status')}）："
                f"查询 {c.get('query_calls')} 次（失败 {c.get('failed_query_calls')}、"
                f"其中已恢复 {c.get('recovered_failed_query_calls')}）、"
                f"重试 {c.get('llm_retry')}、"
                f"读取 {c.get('recall_reads')}、"
                f"同工具重复调用 {c.get('redundant_same_tool')}（重复≠冗余，仅供参考）",
                f"  归因：**{node['attribution']['cause']}** — "
                f"{_esc(node['attribution']['reason'])}"]
        am = node.get("measured") or {}
        if am:
            out.append(f"  引用完整性（程序实测）：有 rid 的 fact "
                       f"{am.get('facts_with_rid')} / 缺 rid {am.get('facts_missing_rid')}；"
                       f"引用了不存在的 rid：{am.get('rids_not_in_store') or '（无）'}；"
                       f"证据引用不在库：{am.get('evidence_refs_not_in_store') or '（无）'}")
        sr = (node.get("self_reported") or {}).get("task_completion") or {}
        if sr:
            out.append(f"  **产物自报**完成情况（{sr.get('source')}，"
                       f"评测侧未独立核验）：达标 {sr.get('criteria_covered')} / "
                       f"未达标 {sr.get('criteria_uncovered')}；"
                       f"自报状态 {sr.get('declared_status')}")
    if not (wk.get("nodes") or {}):
        out.append("- 没有节点可评（无计划对象）")
    out.append("")

    out += ["### Reviewer", "",
            f"- 审查次数：{rv['counts']['reviews']}，意见 {rv['counts']['findings']} 条"
            f"（must_fix {rv['counts']['must_fix']}）",
            f"- 判定分布：{rv['counts']['verdicts']}"]
    for rnd in rv["rounds"]:
        out.append(f"- `{rnd['review_id']}` 审查 {rnd['target_id']}@"
                   f"{rnd['target_version']} → {rnd['verdict']}；"
                   f"后续版本：{rnd['next_version'] or '（无，无法关联）'}；"
                   f"新版本内容变化：{rnd['next_content_changed']}；"
                   f"是否修好：{rnd['fix_verified'] or '未核验'}")
        if rnd.get("response_status_is_default"):
            out.append("  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 "
                       "addressed/declined），该栏不含信息，**不能读作「未修复」**")
    out.append("")
    if rv.get("fix_linkage_scope"):
        out += [f"> {rv['fix_linkage_scope']}", ""]
    if rv.get("deduped_findings"):
        out += ["", "| 类别 | 问题 | 严重性 | 出现轮次 |", "|---|---|---|---|"]
        for d in rv["deduped_findings"]:
            out.append(f"| {d['category']} | {_esc(d['statement'])} | {d['severity']} | "
                       f"{d['rounds']} |")
    out.append("")

    out += ["## 微观查询诊断", "",
            f"- 查询调用数（按调用计，不是按 rid）：{queries['calls']}",
            f"- 原因码分布：{queries['by_reason']}",
            f"- 覆盖：{queries['coverage']}",
            ""]
    if queries["records"]:
        out += ["| # | 调用 id | 节点 | 状态 | 原因码 | 结果 rid | 计划符合性 |",
                "|---|---|---|---|---|---|---|"]
        for r in queries["records"]:
            out.append(f"| {r['call_index']} | {r['query_trace_id'] or '—'} | "
                       f"{r['node_id'] or '—'} | {r['status'] or 'unknown'} | {r['reason']} | "
                       f"{r['result_ids'] or '（无）'} | {r['conformance']['status']} |")
        out.append("")
    for n in queries["notes"]:
        out.append(f"> {n}")
    out.append("")

    out += ["## 依据与未知项", "",
            "- 依据来源：`measured` 程序实测 / `model_judged` 真实模型判定 / "
            "`pipeline_check` 模拟判定（**只验证流程，不是真实质量评分**）/ "
            "`human_reviewed` 人工标注 / `unknown` 无依据 / "
            "`mixed` 各节点依据不同。",
            "- 程序实测的只有**结构与引用**（rid 是否存在、列与期间、计数）；"
            "「证据是否支持结论」「任务是否真的完成」属语义判断，"
            "无 Judge 时记 `unknown`。产物**自报**的完成情况单列，不当作独立核验。",
            ""]
    if cand:
        out += [f"- 答案候选：`{cand.get('artifact_id')}`"
                f"（来源产物 {cand.get('source_artifact_ids')}，"
                f"claims {len(cand.get('claims') or [])} 条）"
                "—— 候选是诊断对象，不替代最终交付成绩。", ""]
    all_basis: dict[str, list[str]] = {}
    for role, payload in roles.items():
        for dim, basis in (payload.get("evidence_basis") or {}).items():
            all_basis.setdefault(str(basis), []).append(f"{role}.{dim}")
    for basis, items in sorted(all_basis.items()):
        out.append(f"- `{basis}`：{', '.join(sorted(items))}")
    out += ["",
            "> 本阶段不调用真实 API：凡标 `pipeline_check` 的结论只说明评测流程可用，",
            "> **不得当作真实质量评分**。",
            ""]
    if pkg.unknown_fields:
        out += ["**历史包缺失的新埋点字段**（记 unknown，不填 0）：", ""]
        for f in pkg.unknown_fields:
            out.append(f"- `{f}`")
        out.append("")

    if pkg.diagnostics.get("result_lines", 0) > 1:
        out.append(f"> trace.jsonl 里有 {pkg.diagnostics['result_lines']} 行 `result` 事件"
                   f"（multi 与 cli 各写一行）。终态以 `run_meta.status` 为准"
                   f"（{pkg.final_status}）；trace 末行是 "
                   f"{pkg.diagnostics.get('terminal_result_status')}，仅作交叉核对。")
        if pkg.diagnostics.get("status_mismatch"):
            out.append(f"> ⚠ 两者不一致：{pkg.diagnostics['status_mismatch']}，"
                       "该包可能被人工改动过。")
        out.append("")

    defects = _known_defects(pkg)
    if defects:
        out += ["## 已登记缺陷（本次不修）", ""]
        for d in defects:
            out.append(f"- `{d['id']}`：{d['detail']}（证据：{d['evidence']}；{d['action']}）")
        out.append("")

    out += ["## 字段兼容表", "", compat_table_md(), ""]
    return "\n".join(out)


def summary_report_md(records: list[dict], *, run_ids: list[str],
                      dataset_name: str | None) -> str:
    delivered = [r for r in records if r.get("delivered")]
    undelivered = [r for r in records if not r.get("delivered")]
    errors = [r for r in records
              if (r.get("process", {}).get("metrics", {})
                  .get("discrepancies"))]

    out = [f"# 过程评测汇总报告：{dataset_name or '（未命名数据集）'}", "",
           f"- run_ids：{', '.join(run_ids) or '（无）'}",
           f"- 样本数：{len(records)}",
           "- 本版不把质量、耗时和 token 拼成单一总分，也不设统一通过线。", ""]

    groups: dict[tuple, list[dict]] = {}
    for r in records:
        key = (r.get("question_type") or "（未标注题型）",
               r.get("real_synthetic") or "（未标注真实/合成）",
               r.get("split_group") or "（未标注 split）",
               r.get("review_status") or "（未标注审核状态）")
        groups.setdefault(key, []).append(r)

    out += ["## 分组统计", "",
            "| 题型 | 真实/合成 | split | 审核 | 样本数 | 交付数 | 未交付数 | 计数差异数 |",
            "|---|---|---|---|---|---|---|---|"]
    for (qt, rs, sp, st), items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        done = sum(1 for r in items if r.get("delivered"))
        out.append(f"| {_esc(qt)} | {_esc(rs)} | {_esc(sp)} | {_esc(st)} | "
                   f"{len(items)} | {done} | {len(items) - done} | "
                   f"{sum(1 for r in items if r in errors)} |")
    out += ["",
            "> 「未交付数」不等于「答错」：其中包含合法的澄清/拒答。"
            "某题是预期拒答还是失败，需要该题的预期行为才能判定（PRD §6.1），"
            "本表不做这个判断。",
            "> 「计数差异数」是**自报值与重算值不一致**的运行数（PRD §7.2），"
            "不是评分器错误数。",
            ""]

    out += ["## 未交付与阻塞", "",
            "未交付的题不从分母消失：它们仍出现在上面的计数与下面的明细里。", ""]
    if undelivered:
        for r in undelivered:
            out.append(f"- `{r['case_id'] or r['run_id']}`：final_status="
                       f"{r['final_status']}（未交付；是否为预期行为需按题判定）")
    else:
        out.append("- （无）")
    out.append("")

    out += ["## 逐题明细", "",
            "| case_id | run_id | 最终状态 | 交付 | 查询调用 | 角色调用 | 计划修订 |",
            "|---|---|---|---|---|---|---|"]
    for r in records:
        p = r.get("process", {})
        out.append(f"| {r.get('case_id')} | {r.get('run_id')} | {r.get('final_status')} | "
                   f"{'是' if r.get('delivered') else '否'} | "
                   f"{p.get('queries', {}).get('calls')} | "
                   f"{p.get('metrics', {}).get('role_calls', {}).get('total')} | "
                   f"{p.get('plans', {}).get('revisions')} |")
    out += ["", "> 原始逐题明细保留在上表的每行记录里（results.jsonl），",
            "> 汇总不替代逐题结果。", ""]
    return "\n".join(out)


def write_reports(outdir: Path, records: list[dict], *, run_ids: list[str],
                  dataset_name: str | None, pkgs: dict | None = None) -> dict[str, Path]:
    """写逐题报告、汇总报告、兼容表与 results.jsonl。

    `pkgs`（sid → ProcessPackage）给了就渲染**完整**逐题报告；没给才退回精简版。
    之前无论如何都只写精简版，导致按 case 打开的报告缺角色评测与查询诊断。
    """
    outdir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for r in records:
        sid = r.get("case_id") or r.get("run_id") or "unknown"
        p = outdir / "cases" / f"{sid}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        proc = r.get("process") or {}
        pkg = (pkgs or {}).get(sid)
        if pkg is not None:
            p.write_text(case_report_md(pkg, metrics=proc.get("metrics") or {},
                                        roles=proc.get("roles") or {},
                                        queries=proc.get("queries") or {},
                                        e2e=r.get("e2e")), encoding="utf-8")
        else:
            p.write_text(case_report_md_from_record(r), encoding="utf-8")
        paths[sid] = p
    summary = outdir / "summary.md"
    summary.write_text(summary_report_md(records, run_ids=run_ids,
                                         dataset_name=dataset_name), encoding="utf-8")
    paths["summary"] = summary
    compat = outdir / "compat_table.md"
    compat.write_text(compat_table_md(), encoding="utf-8")
    paths["compat_table"] = compat
    results = outdir / "results.jsonl"
    results.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                       encoding="utf-8")
    paths["results"] = results
    return paths


def case_report_md_from_record(rec: dict) -> str:
    """从已构建的记录渲染逐题报告（避免写盘时重新评测一遍）。"""
    p = rec.get("process") or {}
    out = [f"# 过程评测报告：{rec.get('case_id') or rec.get('run_id')}"
           f"（run={rec.get('run_id')}）", "",
           f"- 最终状态：{rec.get('final_status')}",
           f"- 交付：{'是' if rec.get('delivered') else '否'}",
           f"- 查询调用：{(p.get('queries') or {}).get('calls')}",
           f"- 角色调用：{((p.get('metrics') or {}).get('role_calls') or {}).get('total')}",
           f"- 计划修订：{(p.get('plans') or {}).get('revisions')}",
           f"- 历史包缺失字段：{(p.get('unknown_fields') or []) or '（无）'}",
           ""]
    if rec.get("e2e"):
        agg = ((rec["e2e"].get("result") or {}).get("aggregate") or {})
        out.append(f"- 宏观必答点命中率：{agg.get('required_hit_rate')}")
    else:
        out.append("- 宏观 E2E：未提供")
    out += ["", "## 字段兼容表", "", compat_table_md(), ""]
    return "\n".join(out)

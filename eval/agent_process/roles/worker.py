"""Worker 独立评测（PRD §8.2）。

输入：节点目标、当时实际获得的上游信息、工具能力、查询与结果、产物及证据引用。
可程序判定的（rid 是否真实存在、完成标准覆盖、计数）走 measured；
查询意图与计算结论需要 Judge 时判 judged，否则记 unknown。

节点声明 completed **不等于**任务成功；证据不足时归因记 unknown，
不凭最终错误倒推根因。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.agent_process.adapt import _read_json
from eval.agent_process.query import classify_query
from eval.agent_process.roles.common import evidence_basis
from eval.agent_process.schema import ArtifactView, ProcessPackage

WORKER_DIMENSIONS = ("query_intent", "data_completeness", "result_structure",
                     "calculation", "reference_integrity", "evidence_support",
                     "task_completion", "gap_statement")

#: 归因码（PRD §8.2）：区分上游目标错误 / 上下文交接遗漏 / Worker 意图错误 /
#: Text2SQL 解析错误 / 执行器错误 / 数据不可用。
ATTRIBUTION_CAUSES = ("upstream_goal_error", "handoff_omission", "intent_error",
                      "text2sql_parse_error", "executor_error", "data_unavailable",
                      "unknown")

#: 查询原因码 → 归因码。只映射证据明确的三种；catalog_miss 等留给缺口检查，
#: 不硬凑成某个根因。
_CAUSE_BY_REASON = {
    "parse_fail": "text2sql_parse_error",
    "exec_fail": "executor_error",
    "no_data": "data_unavailable",
}
_INCONCLUSIVE_REASONS = ("unknown",)


def result_ids_in_store(run_dir: str | Path) -> set[str]:
    results = _read_json(Path(run_dir) / "results.json") or {}
    return {r["id"] for r in (results.get("results") or []) if r.get("id")}


def evaluate_artifact_measured(art: ArtifactView, *, rids_in_store: set[str]) -> dict:
    """纯程序判定：产物里的引用是否站得住。"""
    facts = list(art.facts or [])
    with_rid = [f for f in facts if f.get("rid")]
    not_in_store = sorted({f["rid"] for f in with_rid if f["rid"] not in rids_in_store})
    ev_refs = list(art.evidence_refs or [])
    report = list(art.completion_report or [])

    return {
        "artifact_id": art.artifact_id,
        "node_id": art.node_id,
        "version": art.version,
        "execution_status": art.execution_status,
        "declared_status": art.declared_status,
        "stale": art.stale,
        "fact_count": len(facts),
        "facts_with_rid": len(with_rid),
        "facts_missing_rid": len(facts) - len(with_rid),
        "rids_not_in_store": not_in_store,
        "calculations_missing_output_rid": sum(
            1 for c in (art.calculations or [])
            if not c.get("output_rid") or c["output_rid"] not in rids_in_store),
        "criteria_covered": sum(1 for c in report if c.get("satisfied")),
        "criteria_uncovered": sum(1 for c in report if not c.get("satisfied")),
        "evidence_refs_empty": not ev_refs,
        "evidence_refs_not_in_store": sorted(r for r in ev_refs if r not in rids_in_store),
        "required_missing_inputs": list(art.required_missing_inputs or []),
        "limitations": list(art.limitations or []),
    }


def _node_query_records(pkg: ProcessPackage, node_id: str) -> list[Any]:
    return [r for r in pkg.query_records if r.node_id == node_id]


def _count_node_activity(pkg: ProcessPackage, node_id: str, trace: list[dict]) -> dict:
    calls = [c for c in pkg.role_calls if c.node_id == node_id]
    call_ids = {c.agent_call_id for c in calls}
    retries = sum(1 for e in trace
                  if e.get("type") == "llm_retry" and e.get("parent_call_id") in call_ids)
    child = [e for e in trace
             if e.get("type") == "tool_result" and e.get("parent_call_id") in call_ids]
    by_tool: dict[str, int] = {}
    for e in child:
        by_tool[e.get("tool") or ""] = by_tool.get(e.get("tool") or "", 0) + 1
    queries = _node_query_records(pkg, node_id)
    reasons, last, recovered, recovered_failed = _query_outcomes(queries)
    return {
        "role_calls": len(calls),
        "child_steps": sum(c.child_steps or 0 for c in calls),
        "llm_retry": retries,
        "recall_reads": by_tool.get("recall", 0),
        "query_calls": len(queries),
        "failed_query_calls": sum(1 for r in reasons if r != "ok"),
        "recovered_failed_query_calls": recovered_failed,
        # 同节点内对同一工具的重复调用：冗余调用，仅计数，不自动扣分
        "redundant_same_tool": sum(n - 1 for n in by_tool.values() if n > 1),
        "tool_calls_by_tool": by_tool,
    }


def _judge_inputs(pkg: ProcessPackage, node: Any, latest: Any,
                  queries: list[Any]) -> tuple[dict, str]:
    """构造 Judge 的输入：**必须包含 Worker 实际发出的查询与结果**（PRD §8.2）。

    只给节点目标和产物摘要是判不了「查询意图准确性」的——那正是这次要判的东西。
    """
    rows_by_rid: dict[str, dict] = {}
    results = _read_json(Path(pkg.run_dir) / "results.json") or {}
    for r in (results.get("results") or []):
        if r.get("id"):
            rows_by_rid[r["id"]] = r

    query_view = []
    for q in queries:
        tables = []
        for rid in q.result_ids:
            rec = rows_by_rid.get(rid) or {}
            rows = rec.get("rows") or []
            tables.append({"rid": rid, "columns": list(rows[0].keys()) if rows else [],
                           "total_rows": len(rows)})
        query_view.append({
            "query_trace_id": q.query_trace_id,
            "question": q.question,
            "node_task": q.node_task,
            "status": q.status,
            "reason": classify_query(q),
            "issue_codes": list(q.issue_codes),
            "result_ids": list(q.result_ids),
            "results": tables,
        })

    point = {"node_goal": node.goal, "completion_criteria": node.completion_criteria,
             "tool_capability": "query_financial_data（自然语言取数，只读）"}
    material = {
        "queries": query_view,
        "artifact_summary": (latest.summary if latest else ""),
        "artifact_evidence_refs": list(latest.evidence_refs) if latest else [],
        "artifact_self_reported_completion": (latest.completion_report if latest else []),
        "artifact_limitations": list(latest.limitations) if latest else [],
    }
    return point, json.dumps(material, ensure_ascii=False, indent=1)


def _work_basis(judged: bool, measured: bool, judge_is_live: bool) -> dict:
    """依据等级必须与程序**实际检查到的东西**一致。

    - reference_integrity：程序真的测了（引用的 rid 是否存在）→ measured
    - data_completeness / result_structure / gap_statement：结构可数 → measured
    - query_intent / calculation / evidence_support / task_completion：需要判断语义，
      程序没做独立核验 → 只有 Judge **给出实质判定**才算 judged，否则 unknown。
      **不能因为「产物自己写了完成报告」就标 measured** —— 那是被测方自报。

    调用方传进来的 `judged` 必须是「Judge 给了实质结论」；判成 unknown 等于没判。
    """
    m = evidence_basis(measured, False)
    j = evidence_basis(False, judged, judge_is_live=judge_is_live)
    return {
        "query_intent": j,
        "data_completeness": m,
        "result_structure": m,
        "calculation": j,
        "reference_integrity": m,
        "evidence_support": j,
        "task_completion": j,
        "gap_statement": m,
    }


def _judge_gave_verdict(judged: dict | None) -> bool:
    """Judge 是否给了实质结论。`unknown` 等于没判，不能拿来标依据等级。"""
    if not judged:
        return False
    verdict = ((judged.get("query_intent") or {}).get("verdict") or "unknown")
    return verdict != "unknown"


def _query_outcomes(queries: list[Any]) -> tuple[list[str], str | None, bool, int]:
    """返回（全部原因码，最后一个有结论的原因码，是否已恢复，被恢复的失败数）。

    恢复判定的关键是**最后一次有结论的查询**：先失败后成功=已恢复（失败只是观察，
    不是根因）；先成功后失败=未恢复（照旧归因）。
    """
    reasons = [classify_query(q) for q in queries]
    conclusive = [r for r in reasons if r not in _INCONCLUSIVE_REASONS]
    last = conclusive[-1] if conclusive else None
    recovered = last == "ok"
    failed = sum(1 for r in reasons if r != "ok")
    return reasons, last, recovered, (failed if recovered else 0)


def _attribute(pkg: ProcessPackage, node: Any, measured: dict,
               queries: list[Any]) -> dict:
    """按证据归因；证据不足就说未知，不倒推。"""
    plan = pkg.plans[-1] if pkg.plans else None
    nodes = {n.node_id: n for n in (plan.nodes if plan else [])}

    # 1) 上游没交付 → 交接遗漏（本节点看不到上游产物），不算 Worker 的错
    upstream = [d for d in (node.depends_on if node else []) if d in nodes]
    incomplete = [d for d in upstream if nodes[d].execution_status != "completed"]
    if incomplete:
        return {"cause": "handoff_omission",
                "reason": f"上游节点未完成：{incomplete}，本节点拿不到上游产物",
                "evidence": {"incomplete_upstream": incomplete}}

    # 2) 查询链路给出的确定证据 —— 只在**未被后续成功查询补救**时才算根因
    reasons, last, recovered, recovered_failed = _query_outcomes(queries)
    if last is not None and not recovered:
        cause = _CAUSE_BY_REASON.get(last)
        if cause:
            return {"cause": cause,
                    "reason": f"最后一次查询原因码 {last}"
                              + (f"（此前 {recovered_failed} 次失败已恢复）"
                                 if recovered_failed else ""),
                    "evidence": {"reasons": reasons, "last_reason": last,
                                 "recovered_failures": recovered_failed}}

    # 3) 产物自报缺必需输入
    if measured.get("required_missing_inputs"):
        return {"cause": "data_unavailable",
                "reason": "产物声明缺少必需输入",
                "evidence": {"required_missing_inputs": measured["required_missing_inputs"]}}

    # 4) 上游产物本身有缺口 → 上游目标错误
    for d in upstream:
        src = nodes[d].latest_artifact_id
        if src:
            for art in pkg.artifacts:
                if art.artifact_id == src and art.required_missing_inputs:
                    return {"cause": "upstream_goal_error",
                            "reason": f"上游产物 {src} 自带必需输入缺口",
                            "evidence": {"upstream_artifact": src}}

    if recovered and recovered_failed:
        return {"cause": "unknown",
                "reason": f"{recovered_failed} 次查询失败后重试成功并完成，"
                          "失败已恢复，不构成根因",
                "evidence": {"reasons": reasons, "recovered_failures": recovered_failed}}

    status = (node.execution_status if node else "") or measured.get("execution_status")
    if status in ("failed", "blocked") or measured.get("facts_missing_rid"):
        return {"cause": "unknown",
                "reason": "节点未达成且证据不足：无查询记录、无上游缺口、"
                          "无产物自报缺失，无法判定根因",
                "evidence": {"execution_status": status}}
    return {"cause": "unknown",
            "reason": "证据不足，未发现可归因的失败证据",
            "evidence": {}}


def evaluate_node(pkg: ProcessPackage, node_id: str, *, judge: Any = None,
                  judge_is_live: bool = False) -> dict:
    from eval.agent_process.adapt import _read_trace_lines

    plan = pkg.plans[-1] if pkg.plans else None
    node = next((n for n in (plan.nodes if plan else []) if n.node_id == node_id), None)
    if node is None:
        return {
            "node_id": node_id, "measured": {}, "counts": {}, "queries": [],
            "attribution": {"cause": "unknown", "reason": "该运行包里没有这个节点",
                            "evidence": {}},
            "judged": None, "dimensions": list(WORKER_DIMENSIONS),
            "evidence_basis": {d: "unknown" for d in WORKER_DIMENSIONS},
        }

    arts = [a for a in pkg.artifacts if a.node_id == node_id]
    latest = max(arts, key=lambda a: (a.plan_revision or 0, a.attempt or 0)) if arts else None
    measured = (evaluate_artifact_measured(latest, rids_in_store=result_ids_in_store(pkg.run_dir))
                if latest else {})
    queries = _node_query_records(pkg, node_id)
    counts = _count_node_activity(pkg, node_id, _read_trace_lines(Path(pkg.run_dir)))
    attribution = _attribute(pkg, node, measured, queries)

    judged = None
    if judge is not None:
        question = (pkg.contract or {}).get("query") or ""
        point, material = _judge_inputs(pkg, node, latest, queries)
        res = judge.score_point(question, point, "", material) or {}
        judged = {"query_intent": {"verdict": res.get("verdict", "unknown"),
                                   "reason": res.get("reason", "")}}
        if judged["query_intent"]["verdict"] == "contradicted":
            attribution = {"cause": "intent_error",
                           "reason": "判定：查询意图与节点目标不符",
                           "evidence": {"verdict": "contradicted"}}

    measured_ok = latest is not None
    basis = _work_basis(_judge_gave_verdict(judged), measured_ok, judge_is_live)
    notes = ["节点声明 completed 不等于任务成功；本评测只看证据是否站得住",
             "轮数/重试/冗余调用只计数，不自动影响质量判断（PRD §8.2）",
             "`reference_integrity` 只说明引用的 rid 是否存在；"
             "「证据是否支持结论」属语义判断，无 Judge 时为 unknown"]
    if counts.get("recovered_failed_query_calls"):
        notes.append(f"{counts['recovered_failed_query_calls']} 次查询失败后重试成功，"
                     "失败已恢复，不计入根因")

    return {
        "node_id": node_id,
        "goal": node.goal,
        "completion_criteria": node.completion_criteria,
        "execution_status": node.execution_status,
        "attempt": node.attempt,
        "measured": measured,
        # 被测产物**自己写的**完成情况：如实转述，但标明来源，不当作独立核验
        "self_reported": {
            "task_completion": {
                "source": "artifact.completion_report",
                "criteria_covered": measured.get("criteria_covered"),
                "criteria_uncovered": measured.get("criteria_uncovered"),
                "declared_status": measured.get("declared_status"),
                "note": "由产物自报，评测侧未独立核验",
            },
        },
        "counts": counts,
        "queries": [{"query_trace_id": q.query_trace_id, "question": q.question,
                     "status": q.status, "reason": classify_query(q),
                     "result_ids": list(q.result_ids)} for q in queries],
        "attribution": attribution,
        "judged": judged,
        "dimensions": list(WORKER_DIMENSIONS),
        "evidence_basis": basis,
        "notes": notes,
    }


def evaluate_package(pkg: ProcessPackage, *, judge: Any = None,
                     judge_is_live: bool = False) -> dict:
    plan = pkg.plans[-1] if pkg.plans else None
    node_ids = [n.node_id for n in (plan.nodes if plan else [])]
    nodes = {nid: evaluate_node(pkg, nid, judge=judge, judge_is_live=judge_is_live)
             for nid in node_ids}
    return {
        "nodes": nodes,
        "dimensions": list(WORKER_DIMENSIONS),
        # 顶层依据：报告要按角色汇总依据，Worker 不能只在节点里才有
        "evidence_basis": _aggregate_basis(nodes),
        "basis_scope": "trajectory",
    }


def _aggregate_basis(nodes: dict[str, dict]) -> dict:
    """把各节点的依据等级汇总到角色级：一致就取该值，不一致标 mixed。"""
    out: dict[str, str] = {}
    for dim in WORKER_DIMENSIONS:
        seen = {(n.get("evidence_basis") or {}).get(dim) for n in nodes.values()}
        seen.discard(None)
        if not seen:
            out[dim] = "unknown"
        elif len(seen) == 1:
            out[dim] = seen.pop()
        else:
            out[dim] = "mixed"
    return out


def evaluate_sample(sample: dict, judge: Any = None, *,
                    judge_is_live: bool = False) -> dict:
    """固定样例评测：输入是样例自带的节点/上游/查询/结果/产物。"""
    art_src = sample.get("artifact") or {}
    art = ArtifactView(**{k: v for k, v in art_src.items()
                          if k in ArtifactView.model_fields})
    rids = {r.get("id") for r in (sample.get("results") or []) if r.get("id")}
    measured = evaluate_artifact_measured(art, rids_in_store=rids)

    from eval.agent_process.schema import QueryRecord

    queries = [QueryRecord(**{k: v for k, v in q.items()
                              if k in QueryRecord.model_fields})
               for q in (sample.get("queries") or [])]

    class _N:
        depends_on = sample.get("depends_on") or []
        execution_status = art.execution_status
        latest_artifact_id = art.artifact_id
        goal = (sample.get("node") or {}).get("goal", "")
        completion_criteria = (sample.get("node") or {}).get("completion_criteria", "")

    # 样例自带的上游状态：annotations/upstream 里显式声明，避免评测器自己编
    upstream = sample.get("upstream") or {}
    incomplete = [d for d in _N.depends_on
                  if (upstream.get("node_status") or {}).get(d) != "completed"]
    up_gaps = [a.get("artifact_id") for a in (upstream.get("artifacts") or [])
               if a.get("required_missing_inputs")]
    if incomplete:
        attribution = {"cause": "handoff_omission",
                       "reason": f"上游节点未完成：{incomplete}",
                       "evidence": {"incomplete_upstream": incomplete}}
    elif up_gaps:
        attribution = {"cause": "upstream_goal_error",
                       "reason": f"上游产物自带必需输入缺口：{up_gaps}",
                       "evidence": {"upstream_artifact_gaps": up_gaps}}
    else:
        reasons = [classify_query(q) for q in queries]
        attribution = {"cause": "unknown", "reason": "证据不足", "evidence": {}}
        for reason, cause in (("parse_fail", "text2sql_parse_error"),
                              ("exec_fail", "executor_error"),
                              ("no_data", "data_unavailable")):
            if reason in reasons:
                attribution = {"cause": cause, "reason": f"查询原因码 {reason}",
                               "evidence": {"reasons": reasons}}
                break

    judged = None
    if judge is not None:
        res = judge.score_point(sample.get("question", ""),
                                {"node_goal": _N.goal}, "", art.summary or "") or {}
        judged = {"query_intent": {"verdict": res.get("verdict", "unknown"),
                                   "reason": res.get("reason", "")}}
        if judged["query_intent"]["verdict"] == "contradicted":
            attribution = {"cause": "intent_error",
                           "reason": "判定：查询意图与节点目标不符",
                           "evidence": {"verdict": "contradicted"}}

    measured_basis = evidence_basis(True, False)
    judged_basis = evidence_basis(False, _judge_gave_verdict(judged),
                                  judge_is_live=judge_is_live)
    expected = (sample.get("annotations") or {}).get("cause")
    return {
        "sample_id": sample.get("sample_id"),
        "node_id": (sample.get("node") or {}).get("node_id"),
        "measured": measured,
        "self_reported": {
            "task_completion": {
                "source": "artifact.completion_report",
                "criteria_covered": measured.get("criteria_covered"),
                "criteria_uncovered": measured.get("criteria_uncovered"),
                "declared_status": measured.get("declared_status"),
                "note": "由产物自报，评测侧未独立核验",
            },
        },
        "attribution": attribution,
        "judged": judged,
        "dimensions": list(WORKER_DIMENSIONS),
        "evidence_basis": {
            "query_intent": judged_basis,
            "data_completeness": measured_basis,
            "result_structure": measured_basis,
            "calculation": judged_basis,
            "reference_integrity": measured_basis,
            "evidence_support": judged_basis,
            "task_completion": judged_basis,
            "gap_statement": measured_basis,
        },
        "annotation": sample.get("annotations") or {},
        # 复现情况：程序归因是否与标注一致。不一致时必须显式报出，不能隐藏。
        "cause_matches_annotation": attribution["cause"] == expected,
        "expected_cause": expected,
        # intent_error 只能由 Judge 判 contradicted 得出；离线模拟判必然降级成 unknown
        "requires_live_judge": expected == "intent_error",
    }

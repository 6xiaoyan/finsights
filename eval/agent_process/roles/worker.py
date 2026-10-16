"""Worker 独立评测（PRD §8.2）。

输入：节点目标、当时实际获得的上游信息、工具能力、查询与结果、产物及证据引用。
可程序判定的（rid 是否真实存在、完成标准覆盖、计数）走 measured；
查询意图与计算结论需要 Judge 时判 judged，否则记 unknown。

节点声明 completed **不等于**任务成功；证据不足时归因记 unknown，
不凭最终错误倒推根因。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from eval.agent_process.adapt import _read_json
from eval.agent_process.query import classify_query
from eval.agent_process.roles.common import evidence_basis
from eval.agent_process.schema import ArtifactView, ProcessPackage

WORKER_DIMENSIONS = ("query_intent", "data_completeness", "result_structure",
                     "calculation", "evidence_support", "task_completion",
                     "gap_statement")

#: 归因码（PRD §8.2）：区分上游目标错误 / 上下文交接遗漏 / Worker 意图错误 /
#: Text2SQL 解析错误 / 执行器错误 / 数据不可用。
ATTRIBUTION_CAUSES = ("upstream_goal_error", "handoff_omission", "intent_error",
                      "text2sql_parse_error", "executor_error", "data_unavailable",
                      "unknown")


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
    return {
        "role_calls": len(calls),
        "child_steps": sum(c.child_steps or 0 for c in calls),
        "llm_retry": retries,
        "recall_reads": by_tool.get("recall", 0),
        "query_calls": len(queries),
        # 同节点内对同一工具的重复调用：冗余调用，仅计数，不自动扣分
        "redundant_same_tool": sum(n - 1 for n in by_tool.values() if n > 1),
        "tool_calls_by_tool": by_tool,
    }


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

    # 2) 查询链路给出的确定证据
    reasons = [classify_query(r) for r in queries]
    for reason, cause in (("parse_fail", "text2sql_parse_error"),
                          ("exec_fail", "executor_error"),
                          ("no_data", "data_unavailable")):
        if reason in reasons:
            return {"cause": cause, "reason": f"查询原因码 {reason}",
                    "evidence": {"reasons": reasons}}

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
        material = (latest.summary if latest else "") or ""
        res = judge.score_point(question, {"node_goal": node.goal,
                                           "completion_criteria": node.completion_criteria},
                                "", material) or {}
        judged = {"query_intent": {"verdict": res.get("verdict", "unknown"),
                                   "reason": res.get("reason", "")}}
        if judged["query_intent"]["verdict"] == "contradicted":
            attribution = {"cause": "intent_error",
                           "reason": "判定：查询意图与节点目标不符",
                           "evidence": {"verdict": "contradicted"}}

    measured_basis = evidence_basis(True, False) if latest else "unknown"
    judged_basis = evidence_basis(False, judged is not None, judge_is_live=judge_is_live)
    return {
        "node_id": node_id,
        "goal": node.goal,
        "completion_criteria": node.completion_criteria,
        "execution_status": node.execution_status,
        "attempt": node.attempt,
        "measured": measured,
        "counts": counts,
        "queries": [{"query_trace_id": q.query_trace_id, "question": q.question,
                     "status": q.status, "reason": classify_query(q),
                     "result_ids": list(q.result_ids)} for q in queries],
        "attribution": attribution,
        "judged": judged,
        "dimensions": list(WORKER_DIMENSIONS),
        "evidence_basis": {
            "query_intent": judged_basis,
            "data_completeness": measured_basis,
            "result_structure": measured_basis,
            "calculation": judged_basis,
            "evidence_support": measured_basis,
            "task_completion": measured_basis,
            "gap_statement": measured_basis,
        },
        "notes": ["节点声明 completed 不等于任务成功；本评测只看证据是否站得住",
                  "轮数/重试/冗余调用只计数，不自动影响质量判断（PRD §8.2）"],
    }


def evaluate_package(pkg: ProcessPackage, *, judge: Any = None,
                     judge_is_live: bool = False) -> dict:
    plan = pkg.plans[-1] if pkg.plans else None
    node_ids = [n.node_id for n in (plan.nodes if plan else [])]
    return {
        "nodes": {nid: evaluate_node(pkg, nid, judge=judge, judge_is_live=judge_is_live)
                  for nid in node_ids},
        "dimensions": list(WORKER_DIMENSIONS),
    }


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
    judged_basis = evidence_basis(False, judged is not None, judge_is_live=judge_is_live)
    expected = (sample.get("annotations") or {}).get("cause")
    return {
        "sample_id": sample.get("sample_id"),
        "node_id": (sample.get("node") or {}).get("node_id"),
        "measured": measured,
        "attribution": attribution,
        "judged": judged,
        "dimensions": list(WORKER_DIMENSIONS),
        "evidence_basis": {
            "query_intent": judged_basis,
            "data_completeness": measured_basis,
            "result_structure": measured_basis,
            "calculation": judged_basis,
            "evidence_support": measured_basis,
            "task_completion": measured_basis,
            "gap_statement": measured_basis,
        },
        "annotation": sample.get("annotations") or {},
        # 复现情况：程序归因是否与标注一致。不一致时必须显式报出，不能隐藏。
        "cause_matches_annotation": attribution["cause"] == expected,
        "expected_cause": expected,
        # intent_error 只能由 Judge 判 contradicted 得出；离线模拟判必然降级成 unknown
        "requires_live_judge": expected == "intent_error",
    }

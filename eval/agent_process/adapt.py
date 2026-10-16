"""运行包 → 规范化对象。

复用 `eval.financial_e2e.adapter.load_run_artifact` 取包级事实（A 的适配器），
但**事件级**细节自己读 `trace.jsonl` 原始行：A 的 `TraceEvent` 不保留
`tool` / `args_head` / `query_trace_id`，走一遍反而丢失（见 ledger 的 ruling）。

任何缺失都不伪造：缺文件记进 `unknown_fields`，缺字段置 None。
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.agent_process.schema import (ArtifactView, PlanNodeView, PlanView,
                                       ProcessPackage, QueryRecord, ReviewView,
                                       RoleCall)
from eval.financial_e2e.adapter import load_run_artifact

# ---------------------------------------------------------------- 字段兼容表

# status 语义：
#   available          —— 今天的运行包里已有
#   derived            —— 由包内数据推导（推导依据写在 note）
#   new_instrumentation—— 本次补的埋点才产生；历史包为 unknown
#   unknown_legacy     —— 历史包必然缺失，只能记 unknown
FIELD_COMPAT: list[dict] = [
    # ---- 包级
    {"area": "package", "source": "run_meta.json:run_id", "normalized": "run_id",
     "status": "available", "note": ""},
    {"area": "package", "source": "调用方 --case-id", "normalized": "case_id",
     "status": "available", "note": "运行包本身不含 case_id，由评测侧绑定"},
    {"area": "package", "source": "--run-dir", "normalized": "run_dir",
     "status": "available", "note": ""},
    {"area": "package", "source": "run_meta.json:status", "normalized": "final_status",
     "status": "available",
     "note": "以 run_meta 为准；trace 末行 result 仅作交叉核对"},
    {"area": "package", "source": "answer.md（仅 status==answered）", "normalized": "final_answer",
     "status": "derived", "note": "未提交时为 None，不填补"},
    {"area": "package", "source": "run_meta.json:verified", "normalized": "verified",
     "status": "available", "note": ""},
    {"area": "package", "source": "analysis_state.json:contract", "normalized": "contract",
     "status": "available", "note": "仅 multi_agent 有"},
    {"area": "package", "source": "analysis_state.json:latest_revision", "normalized": "latest_revision",
     "status": "available", "note": ""},
    {"area": "package", "source": "analysis_state.json:plans", "normalized": "plans",
     "status": "available", "note": "按 revision 字符串键"},
    {"area": "package", "source": "analysis_state.json:artifacts", "normalized": "artifacts",
     "status": "available", "note": "version 字段不落盘，见 artifact_version()"},
    {"area": "package", "source": "analysis_state.json:reviews", "normalized": "reviews",
     "status": "available", "note": ""},
    {"area": "package", "source": "analysis_state.json:answer_candidate", "normalized": "answer_candidate",
     "status": "available", "note": ""},
    {"area": "package", "source": "trace.jsonl:subagent_start/end", "normalized": "role_calls",
     "status": "available", "note": "按 agent_call_id 配对；缺 end 的项 token/耗时为 None"},
    {"area": "package", "source": "analysis/query_trace.jsonl 或 results.json", "normalized": "query_records",
     "status": "derived",
     "note": "无 query_trace.jsonl 时从 results.json 派生；派生记录只覆盖成功路径（仅成功路径落库）"},
    {"area": "package", "source": "run_meta.json:usage", "normalized": "usage",
     "status": "available", "note": "缺失时为空字典，不填 0"},
    {"area": "package", "source": "run_meta.json", "normalized": "run_meta",
     "status": "available", "note": ""},
    {"area": "package", "source": "本适配器计算", "normalized": "diagnostics",
     "status": "derived", "note": "result 行数、状态不一致等"},
    {"area": "package", "source": "本适配器计算", "normalized": "unknown_fields",
     "status": "derived", "note": "缺失的埋点文件清单"},

    # ---- 角色调用
    {"area": "role_call", "source": "trace.jsonl:subagent_start.role", "normalized": "role_calls[].role",
     "status": "available", "note": ""},
    {"area": "role_call", "source": "trace.jsonl:subagent_start.node_id", "normalized": "role_calls[].node_id",
     "status": "available", "note": "仅 worker 有"},
    {"area": "role_call", "source": "trace.jsonl:subagent_start.target_*", "normalized": "role_calls[].target_version",
     "status": "available", "note": "仅 reviewer 有"},
    {"area": "role_call", "source": "trace.jsonl:subagent_end", "normalized": "role_calls[].latency_ms",
     "status": "available", "note": "缺 end 时为 None"},
    {"area": "role_call", "source": "subagent_context 事件", "normalized": "role_calls[].context_refs",
     "status": "new_instrumentation", "note": "历史包为 None"},

    # ---- 查询记录
    {"area": "query", "source": "query_trace.jsonl:query_trace_id", "normalized": "query_records[].query_trace_id",
     "status": "new_instrumentation", "note": "调用级 id；历史包为 None（不可用 rid 代替，rid 会复用）"},
    {"area": "query", "source": "query_trace.jsonl:attempts/extractor_events", "normalized": "query_records[].attempts",
     "status": "new_instrumentation", "note": "历史包只有 run_meta.extraction_calls 总数"},
    {"area": "query", "source": "query_trace.jsonl:plan", "normalized": "query_records[].plan",
     "status": "new_instrumentation",
     "note": "ok 路径可从 results.json[].args.plan 派生；非 ok 路径历史包不可恢复"},
    {"area": "query", "source": "results.json[].sql", "normalized": "query_records[].sql_chars",
     "status": "derived", "note": "只记长度，全文留在 results.json；未产生 SQL 时为 None"},
    {"area": "query", "source": "results.json[].rows", "normalized": "query_records[].columns",
     "status": "derived", "note": "取首行键；无行时为空表"},
    {"area": "query", "source": "trace.jsonl 子 tool_result.query_trace_id", "normalized": "query_records[].linkage",
     "status": "new_instrumentation", "note": "历史包恒为 unknown，不按顺序/文本猜归属"},
    {"area": "query", "source": "results.json[].id", "normalized": "query_records[].result_ids",
     "status": "available", "note": "非 ok 路径为空；不伪造 rid"},
    {"area": "query", "source": "（历史包不存在）", "normalized": "query_records[].status",
     "status": "unknown_legacy",
     "note": "派生记录只来自成功路径，故按 control-flow 推为 ok；失败调用在历史包中无任何记录"},
]


def artifact_version(plan_revision: int | None, attempt: int | None) -> str:
    """复刻 `agent/multi.py::_artifact_version`：`rev{plan_revision}-attempt{attempt}`。

    该字符串不落盘在 artifact 上，只有 `Review.target_version` 记录了它。
    两处必须一致，否则审查↔版本关联会错位。
    """
    return f"rev{plan_revision}-attempt{attempt}"


# ---------------------------------------------------------------- 读取辅助

def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_jsonl(path: Path) -> list[dict] | None:
    """返回 None 表示文件不存在（区别于空文件返回 []）。"""
    if not path.is_file():
        return None
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _read_trace_lines(run_dir: Path) -> list[dict]:
    """直接读 trace.jsonl 原始行（A 的 TraceEvent 不保留 tool/args_head/query_trace_id）。"""
    return _read_jsonl(run_dir / "trace.jsonl") or []


# ---------------------------------------------------------------- 组装

def _build_plans(state: dict) -> list[PlanView]:
    plans: list[PlanView] = []
    raw = state.get("plans") or {}
    for key, p in raw.items():
        try:
            revision = int(p.get("revision", key))
        except (TypeError, ValueError):
            continue
        plans.append(PlanView(
            revision=revision,
            plan_id=p.get("plan_id", ""),
            revision_reason=p.get("revision_reason", ""),
            created_ts=p.get("created_ts"),
            nodes=[PlanNodeView(**{k: n.get(k) for k in PlanNodeView.model_fields
                                   if k in n}) for n in (p.get("nodes") or [])],
        ))
    plans.sort(key=lambda x: x.revision)
    return plans


def _build_artifacts(state: dict) -> list[ArtifactView]:
    out: list[ArtifactView] = []
    for aid, a in (state.get("artifacts") or {}).items():
        rev, att = a.get("plan_revision"), a.get("attempt")
        out.append(ArtifactView(
            artifact_id=a.get("artifact_id", aid),
            node_id=a.get("node_id", ""),
            plan_revision=rev,
            attempt=att,
            execution_status=a.get("execution_status", ""),
            version=artifact_version(rev, att),
            stale=bool(a.get("stale", False)),
            evidence_refs=list(a.get("evidence_refs") or []),
            facts=list(a.get("facts") or []),
            calculations=list(a.get("calculations") or []),
            summary=a.get("summary", ""),
            declared_status=a.get("declared_status", ""),
            completion_report=list(a.get("completion_report") or []),
            required_missing_inputs=list(a.get("required_missing_inputs") or []),
            optional_missing_inputs=list(a.get("optional_missing_inputs") or []),
            next_actions=list(a.get("next_actions") or []),
            limitations=list(a.get("limitations") or []),
        ))
    out.sort(key=lambda x: x.artifact_id)
    return out


def _build_reviews(state: dict) -> list[ReviewView]:
    out: list[ReviewView] = []
    for rid, r in (state.get("reviews") or {}).items():
        out.append(ReviewView(
            review_id=r.get("review_id", rid),
            target_type=r.get("target_type", ""),
            target_id=r.get("target_id", ""),
            target_version=r.get("target_version", ""),
            verdict=r.get("verdict", ""),
            findings=list(r.get("findings") or []),
            unresolved_items=list(r.get("unresolved_items") or []),
            created_ts=r.get("created_ts"),
        ))
    out.sort(key=lambda x: x.review_id)
    return out


def _build_role_calls(trace: list[dict]) -> list[RoleCall]:
    """subagent_start / subagent_end 按 agent_call_id 配对；子 tool_result 收集 query_trace_id。"""
    calls: dict[str, RoleCall] = {}
    order: list[str] = []
    ctx_by_call: dict[str, dict] = {}

    for ev in trace:
        etype = ev.get("type")
        acid = ev.get("agent_call_id")
        if etype == "subagent_start" and acid:
            if acid not in calls:
                calls[acid] = RoleCall(
                    agent_call_id=acid,
                    role=ev.get("role", ""),
                    main_step=ev.get("step"),
                    plan_revision=ev.get("plan_revision"),
                    node_id=ev.get("node_id"),
                    target_type=ev.get("target_type"),
                    target_id=ev.get("target_id"),
                    target_version=ev.get("target_version"),
                )
                order.append(acid)
        elif etype == "subagent_end" and acid and acid in calls:
            c = calls[acid]
            # ok 缺失时保持 None（不把未知当 False/True）
            c.ok = ev.get("ok") if isinstance(ev.get("ok"), bool) else None
            c.child_steps = ev.get("child_steps")
            c.prompt_tokens = ev.get("prompt_tokens")
            c.completion_tokens = ev.get("completion_tokens")
            c.latency_ms = ev.get("latency_ms")
            c.error = ev.get("error") or None
        elif etype == "subagent_context" and acid:
            ctx_by_call[acid] = ev.get("context_refs") or {}
        elif etype == "tool_result":
            parent = ev.get("parent_call_id")
            qtid = ev.get("query_trace_id")
            if parent and qtid and parent in calls and qtid not in calls[parent].query_trace_ids:
                calls[parent].query_trace_ids.append(qtid)

    for acid, refs in ctx_by_call.items():
        if acid in calls:
            calls[acid].context_refs = refs
    return [calls[a] for a in order]


def _query_events(trace: list[dict]) -> list[dict]:
    """trace 里所有 query_financial_data 的 tool_result 事件，按出现顺序。

    这是历史包里唯一的**调用级**证据：一次调用一个事件，而 results.json 按
    计划去重（同计划复用 rid），按 rid 数会把调用数少数。
    """
    return [e for e in trace
            if e.get("type") == "tool_result" and e.get("tool") == "query_financial_data"]


def _parse_args_head(head: str | None) -> tuple[dict, bool]:
    """解析 `args_head`。返回 (payload, truncated)。

    `args_head` 在 agent/subagents.py 里被截到 300 字符；截断后不是合法 JSON，
    此时不猜内容，只标 truncated。
    """
    if not head:
        return {}, False
    try:
        obj = json.loads(head)
    except json.JSONDecodeError:
        return {}, True
    return (obj if isinstance(obj, dict) else {}), False


def _results_by_rid(results: dict | None, rids: list[str]) -> dict | None:
    want = set(rids)
    for rec in ((results or {}).get("results") or []):
        if rec.get("id") in want:
            return rec
    return None


def _columns_of(rec: dict | None) -> list[str]:
    rows = (rec or {}).get("rows") or []
    return list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []


def _derive_from_query_events(trace: list[dict], results: dict | None,
                              calls: list[RoleCall]) -> list[QueryRecord]:
    """按 tool_result 事件为每次查询调用建一条记录（历史包路径）。"""
    by_call = {c.agent_call_id: c for c in calls}
    out: list[QueryRecord] = []
    for idx, ev in enumerate(_query_events(trace)):
        args, truncated = _parse_args_head(ev.get("args_head"))
        rid = ev.get("rid")
        ok = ev.get("ok") if isinstance(ev.get("ok"), bool) else None
        notes: list[str] = []
        if truncated:
            notes.append("args_head 被截断（≤300 字符），原始问题与节点任务不可得")

        # status 推导（有依据，不是猜）：
        #   有 rid         -> executor 只在完全成功时才落库 -> "ok"
        #   ok 且无 rid    -> 只可能是 empty 分支 -> "empty"
        #   ok=False       -> 失败，但历史包未记录失败类别 -> None
        if rid:
            status = "ok"
        elif ok is True:
            status = "empty"
        elif ok is False:
            status = None
            notes.append("调用失败，但历史包未记录失败类别（无提取明细）")
        else:
            status = None
            notes.append("调用结果状态缺失")

        res = _results_by_rid(results, [rid] if rid else [])
        acid = ev.get("parent_call_id")
        call = by_call.get(acid) if acid else None
        out.append(QueryRecord(
            query_trace_id=None,
            call_index=idx,
            step=ev.get("step"),
            question=args.get("question") if not truncated else None,
            node_task=args.get("node_task") if not truncated else None,
            status=status,
            call_ok=ok,
            issue_codes=[],
            plan=(res.get("args") or {}).get("plan") if res else None,
            plan_digest=None,
            attempts=None,
            extractor_events=[],
            prompt_version=None,
            catalog_version=None,
            data_version=(res or {}).get("data_version"),
            result_ids=[rid] if rid else [],
            sql_chars=len(res["sql"]) if res and res.get("sql") else None,
            total_rows=len(res.get("rows") or []) if res else None,
            truncated=res.get("truncated") if res else None,
            source="derived",
            columns=_columns_of(res),
            agent_call_id=acid,
            node_id=call.node_id if call else None,
            role=call.role if call else (ev.get("role") or None),
            linkage="tool_result_parent" if acid else "unknown",
            derivation_notes=notes,
        ))
    return out


def _fallback_from_results(results: dict | None) -> list[QueryRecord]:
    """连查询事件都没有时的最后退路：只能按 results.json 行建记录，无法归属。"""
    out: list[QueryRecord] = []
    for rec in ((results or {}).get("results") or []):
        if rec.get("tool") != "query_financial_data":
            continue
        sql = rec.get("sql")
        out.append(QueryRecord(
            query_trace_id=None, call_index=None, step=None, question=None, node_task=None,
            status="ok", call_ok=None, plan=(rec.get("args") or {}).get("plan"),
            data_version=rec.get("data_version"),
            result_ids=[rec["id"]] if rec.get("id") else [],
            sql_chars=len(sql) if sql else None,
            total_rows=len(rec.get("rows") or []),
            truncated=rec.get("truncated"), source="derived",
            columns=_columns_of(rec), linkage="unknown",
            derivation_notes=["trace 中无 query_financial_data 事件，只能按 results.json "
                              "行派生：调用次数与失败调用不可得"],
        ))
    return out


def _load_query_records(run_dir: Path, results: dict | None,
                        trace: list[dict], calls: list[RoleCall]) -> list[QueryRecord]:
    rows = _read_jsonl(run_dir / "analysis" / "query_trace.jsonl")
    if rows is None:
        derived = _derive_from_query_events(trace, results, calls)
        return derived if derived else _fallback_from_results(results)

    # 新埋点路径：用事件上的调用级 query_trace_id 建立到角色调用的连接
    event_by_qtid: dict[str, dict] = {}
    for ev in _query_events(trace):
        if ev.get("query_trace_id"):
            event_by_qtid.setdefault(ev["query_trace_id"], ev)

    by_call = {c.agent_call_id: c for c in calls}
    out: list[QueryRecord] = []
    for row in rows:
        qtid = row.get("query_trace_id")
        ev = event_by_qtid.get(qtid) if qtid else None
        acid = (ev or {}).get("parent_call_id")
        call = by_call.get(acid) if acid else None
        rids = list(row.get("result_ids") or [])
        res = _results_by_rid(results, rids)
        out.append(QueryRecord(
            query_trace_id=qtid,
            call_index=row.get("call_index"),
            step=row.get("step"),
            question=row.get("question"),
            node_task=row.get("node_task"),
            status=row.get("status"),
            call_ok=(ev.get("ok") if isinstance((ev or {}).get("ok"), bool) else None),
            issue_codes=list(row.get("issue_codes") or []),
            plan=row.get("plan"),
            plan_digest=row.get("plan_digest") or None,
            attempts=row.get("attempts"),
            extractor_events=list(row.get("extractor_events") or []),
            prompt_version=row.get("prompt_version"),
            catalog_version=row.get("catalog_version"),
            data_version=row.get("data_version"),
            result_ids=rids,
            sql_chars=row.get("sql_chars"),
            total_rows=row.get("total_rows"),
            truncated=row.get("truncated"),
            source="run_package",
            columns=_columns_of(res),
            agent_call_id=acid,
            node_id=call.node_id if call else None,
            role=call.role if call else None,
            linkage="query_trace_id" if acid else "unknown",
        ))
    return out


def load_process_package(run_dir: str | Path, case_id: str = "") -> ProcessPackage:
    """把一个运行包读成 `ProcessPackage`。缺什么就标什么，不伪造。"""
    run_dir = Path(run_dir)
    unknown: list[str] = []

    artifact = load_run_artifact(run_dir, case_id)
    run_meta = _read_json(run_dir / "run_meta.json") or {}
    results = _read_json(run_dir / "results.json")
    trace = _read_trace_lines(run_dir)

    state_path = run_dir / "analysis" / "analysis_state.json"
    state = _read_json(state_path)
    if state is None:
        unknown.append("analysis/analysis_state.json")
        state = {}

    if not (run_dir / "analysis" / "query_trace.jsonl").is_file():
        unknown.append("analysis/query_trace.jsonl")
    if not (run_dir / "analysis" / "visible_context.jsonl").is_file():
        unknown.append("analysis/visible_context.jsonl")

    calls = _build_role_calls(trace)
    query_records = _load_query_records(run_dir, results, trace, calls)

    # 终态以 run_meta 为准；trace 的 result 行数记录下来，不隐藏重复行
    result_lines = [e for e in trace if e.get("type") == "result"]
    diagnostics: dict = {"result_lines": len(result_lines)}
    if result_lines:
        terminal = result_lines[-1].get("status")
        diagnostics["terminal_result_status"] = terminal
        if run_meta.get("status") and terminal and terminal != run_meta["status"]:
            diagnostics["status_mismatch"] = {"run_meta": run_meta["status"],
                                              "trace_terminal": terminal}

    return ProcessPackage(
        run_id=artifact.run_id or run_dir.name,
        case_id=case_id,
        run_dir=str(run_dir),
        final_status=artifact.final_status or run_meta.get("status", ""),
        final_answer=artifact.final_answer,
        verified=artifact.process_objects.get("verified", run_meta.get("verified")),
        contract=state.get("contract"),
        latest_revision=state.get("latest_revision"),
        plans=_build_plans(state),
        artifacts=_build_artifacts(state),
        reviews=_build_reviews(state),
        answer_candidate=state.get("answer_candidate"),
        role_calls=calls,
        query_records=query_records,
        usage=dict(artifact.usage or {}),
        run_meta=run_meta,
        diagnostics=diagnostics,
        unknown_fields=unknown,
    )

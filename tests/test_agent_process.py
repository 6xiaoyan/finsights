"""eval/agent_process 的离线测试：适配、监控、查询诊断、角色评测、报告、CLI。

全部离线：不联网，不调用真实模型；固定样例与模拟 Judge 的结论必须带 pipeline_check 标记。
运行包夹具的形状对齐 runs/cli-20261016-134035（multi_agent + text2sql）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ------------------------------------------------------------------ 夹具

_DEFAULT_QUERY_SPECS: list[dict] = [
    {"ok": True, "rid": "r2",
     "args": {"question": "联想 FY2024Q2 存货", "node_task": "取存货"}},
    # 第二次调用复用同一计划 → executor 按 tool|args|data_version 去重，rid 相同
    {"ok": True, "rid": "r2",
     "args": {"question": "联想 FY2024Q2 存货", "node_task": "取存货"}},
]


def _query_events(specs: list[dict], with_instrumentation: bool) -> list[dict]:
    out: list[dict] = []
    for i, s in enumerate(specs):
        # `args_head` 允许直接给定（模拟被截断的原始串）；否则由 args 序列化
        head = s.get("args_head")
        if head is None:
            head = json.dumps(s.get("args", {}), ensure_ascii=False)
        ev = {
            "type": "tool_result", "role": "worker", "step": 0,
            "tool": "query_financial_data", "ok": s.get("ok", True),
            "rid": s.get("rid"), "call_id": f"call_w{i + 1}",
            "args_head": head,
            "result_head": s.get("result_head", "[查询状态] ok"),
            "main_step": 2,
            "parent_call_id": s.get("parent_call_id", "worker@2#n1"),
        }
        if with_instrumentation:
            ev["query_trace_id"] = f"q{i + 1}"
        out.append(ev)
    return out


def _extractor_events(spec: dict) -> list[dict]:
    return spec.get("extractor_events") or [
        {"type": "extractor_call", "attempt": 1, "status": "parsed"}]


def _write_run_package(
    tmp_path: Path,
    *,
    case_id: str = "LV-T01",
    status: str = "refuse",
    verified: bool = False,
    with_instrumentation: bool = False,
    with_usage: bool = True,
    with_pricing: bool = True,
    two_result_lines: bool = True,
    first_result_status: str | None = None,
    query_specs: list[dict] | None = None,
    extra_trace_events: list[dict] | None = None,
    reported_subagent_calls: int | None = None,
) -> Path:
    """构造最小 multi_agent/text2sql 运行包。

    - with_instrumentation=False：不写 analysis/query_trace.jsonl 与
      analysis/visible_context.jsonl，模拟历史包。
    - query_specs：每次 query_financial_data 调用的形态（ok/rid/args），默认两次同
      rid 调用，复刻真实包里「同计划复用 rid」的现象。
    - two_result_lines=True：trace.jsonl 末尾出现两行 type=result（复刻真实包）。
    - extra_trace_events：追加到 result 行之前的额外事件（注入重复/畸形事件用）。
    - reported_subagent_calls：覆盖 run_meta 自报的 subagent_calls（制造差异用）。
    """
    specs = query_specs if query_specs is not None else _DEFAULT_QUERY_SPECS
    run = tmp_path / "cli-20261016-134035"
    (run / "analysis").mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "run_id": "20261016-134035",
        "created": "2026-10-16T13:43:20",
        "agent": "v1",
        "model": "deepseek-flash",
        "config": {"llm": {"pricing_usd_per_mtok": {"input": 0.15, "output": 0.60}}},
        "commit": "de2a81b",
        "dirty": False,
        "data_version": "2633728-1792117547120291300-d728e5137cbb",
        "agent_mode": "multi_agent",
        "query_backend": "text2sql",
        "enabled_tools": ["calc", "final_answer", "list_metrics", "query_financial_data"],
        "catalog_version": "ac8a63098ffd",
        "extraction_prompt_version": "6aae694677ce",
        # extraction_calls 数的是**提取模型请求**次数（agent 里按 LLM 调用自增，
        # 格式修复会算两次），不是工具调用次数——夹具必须同口径，否则会自造差异。
        "extraction_calls": sum(
            len([e for e in _extractor_events(s) if e.get("type") == "extractor_call"])
            for s in specs),
        "verified": verified,
        "status": status,
        "multi_agent": {
            "cfg_rounds": {"main_max_steps": 20, "planner_max_steps": 4,
                           "worker_max_steps": 6, "reviewer_max_steps": 4},
            "subagent_calls": (reported_subagent_calls if reported_subagent_calls is not None
                               else 3),
            "subagent_role_calls": {"planner": 1, "worker": 1, "reviewer": 1},
            "subagent_child_steps": 6,
            "plan_revisions": 1,
            "reviews_total": 1,
            "findings_total": 1,
            "artifacts_total": 1,
            "artifacts_stale": 0,
            "semantic_review_status": "accepted",
            "task_coverage_status": "covered",
            "final_answer_rejects": 1,
            "unresolved_must_fix": 1,
            "stop_reason": "主循环轮数用尽（max_steps=20，未提交最终答案）",
            "role_prompt_sha256_12": {"planner": "ce0422809738",
                                      "worker": "ce20181ce3e2",
                                      "reviewer": "2d6dee0e0b3b"},
            "process_files": ["analysis_state.json", "analysis_review.md"],
        },
    }
    if not with_pricing:
        meta["config"] = {"llm": {}}
    if with_usage:
        meta["usage"] = {"steps": 20, "prompt_tokens": 383994, "completion_tokens": 22438,
                         "latency_s": 164.82, "sql_errors": 0,
                         "refuse_reason": "主循环轮数用尽（max_steps=20，未提交最终答案）"}
    (run / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                       encoding="utf-8")

    (run / "question.json").write_text(json.dumps(
        {"run_id": "20261016-134035", "question": "联想 FY2024Q2 存货同比变化如何？",
         "as_of": "2024-11-15", "db": "data/finsights.duckdb",
         "query_backend": "text2sql"}, ensure_ascii=False, indent=1), encoding="utf-8")

    (run / "answer.md").write_text(
        "# 联想 FY2024Q2 存货同比变化如何？\n\n- status: refuse\n- 核验: 未通过\n"
        "- run_id: 20261016-134035\n\n多 Agent 运行未完成：主循环轮数用尽\n",
        encoding="utf-8")

    # ---- trace.jsonl
    events: list[dict] = [{"type": "meta", "run_id": "20261016-134035",
                           "question": "联想 FY2024Q2 存货同比变化如何？",
                           "ts": "2026-10-16T13:40:35"}]
    events += [
        {"type": "assistant", "step": 1, "content": "开工", "tool_calls": [], "usage": {}},
        {"type": "subagent_start", "role": "planner", "step": 1,
         "agent_call_id": "planner@1#plan"},
        {"type": "subagent_end", "role": "planner", "step": 1,
         "agent_call_id": "planner@1#plan", "ok": True, "child_steps": 2,
         "prompt_tokens": 900, "completion_tokens": 120, "latency_ms": 3100, "error": ""},
        {"type": "subagent_start", "role": "worker", "step": 2,
         "agent_call_id": "worker@2#n1", "node_id": "n1", "plan_revision": 1, "attempt": 1},
    ]
    events += _query_events(specs, with_instrumentation)
    events += [
        {"type": "subagent_end", "role": "worker", "step": 2,
         "agent_call_id": "worker@2#n1", "node_id": "n1", "plan_revision": 1, "attempt": 1,
         "ok": True, "child_steps": 3, "prompt_tokens": 1500, "completion_tokens": 300,
         "latency_ms": 7000, "error": ""},
        {"type": "subagent_start", "role": "reviewer", "step": 6,
         "agent_call_id": "reviewer@6#art1", "target_type": "artifact",
         "target_id": "art1", "target_version": "rev1-attempt1"},
        {"type": "subagent_end", "role": "reviewer", "step": 6,
         "agent_call_id": "reviewer@6#art1", "target_type": "artifact",
         "target_id": "art1", "target_version": "rev1-attempt1",
         "ok": True, "child_steps": 1, "prompt_tokens": 800, "completion_tokens": 90,
         "latency_ms": 2100, "error": ""},
        {"type": "tool_result", "step": 3, "tool": "plan_analysis", "ok": True,
         "call_id": "call_m1", "args_head": "{}", "result_len": 120, "result_head": "ok"},
        {"type": "tool_skipped", "step": 0, "tool": "query_financial_data", "ok": None,
         "result_id": None, "initial_packet": True, "reason": "skipped",
         "skipped": "text2sql: initial packet not pre-loaded"},
    ]
    if two_result_lines:
        events.append({"type": "result", "status": first_result_status or status,
                       "verified": verified,
                       "stop_reason": "主循环轮数用尽（max_steps=20，未提交最终答案）"})
    if extra_trace_events:
        events += extra_trace_events
    events.append({"type": "result", "status": status, "verified": verified})

    (run / "trace.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")

    # ---- results.json（r1 是真正的初始包 list_metrics，created_step 同为 0）
    (run / "results.json").write_text(json.dumps({
        "any_truncated": False,
        "results": [
            {"id": "r1", "tool": "list_metrics", "args": {}, "sql": None, "unit": "",
             "data_version": "2633728-1792117547120291300-d728e5137cbb", "created_step": 0,
             "digest": "list", "initial_packet": True,
             "rows": [{"metric": "inventory"}]},
            {"id": "r2", "tool": "query_financial_data",
             "args": {"plan": {"metrics": ["inventory"], "companies": ["Lenovo"],
                               "period_basis": "fiscal", "periods": ["FY2024Q2"],
                               "group_by": ["region"], "filters": []}},
             "sql": "SELECT company, metric, period, value FROM facts",
             "unit": "", "data_version": "2633728-1792117547120291300-d728e5137cbb",
             "created_step": 0, "digest": "q1digest", "initial_packet": True,
             "rows": [{"company": "Lenovo", "metric": "inventory", "period": "FY2024Q2",
                       "region": "APAC", "value": 100.0}]},
        ]}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- analysis/analysis_state.json
    node = {"node_id": "n1", "goal": "取联想 FY2024Q2 存货并算同比", "depends_on": [],
            "input_refs": [], "completion_criteria": "给出存货数值与同比",
            "required_for_answer": True, "required_output_refs": ["inventory"],
            "requires_review": True, "slice_index": 0, "execution_status": "completed",
            "attempt": 1, "latest_artifact_id": "art1", "review_status": "accepted"}
    node2 = {"node_id": "n2", "goal": "写结论", "depends_on": ["n1"], "input_refs": ["art1"],
             "completion_criteria": "结论有依据", "required_for_answer": False,
             "required_output_refs": [], "requires_review": False, "slice_index": 0,
             "execution_status": "pending", "attempt": 0, "latest_artifact_id": None,
             "review_status": "not_reviewed"}
    state = {
        "schema_version": "as1", "contract_version": "c1",
        "contract": {"query": "联想 FY2024Q2 存货同比变化如何？", "company": "Lenovo",
                     "period": "FY2024Q2", "comparison": "yoy", "scope_notes": [],
                     "output_requirements": "", "as_of": "2024-11-15",
                     "data_version": "2633728-1792117547120291300-d728e5137cbb",
                     "clarification_status": "none"},
        "plans": {"1": {"plan_id": "plan1", "revision": 1, "contract_version": "c1",
                        "nodes": [node, node2], "revision_reason": "初始计划",
                        "created_ts": "2026-10-16T13:41:00"}},
        "latest_revision": 1,
        "artifacts": {"art1": {
            "artifact_id": "art1", "node_id": "n1", "plan_revision": 1, "attempt": 1,
            "execution_status": "completed",
            "facts": [{"rid": "r2", "field": "inventory", "value": 100.0, "unit": "USD mn",
                       "company": "Lenovo", "period": "FY2024Q2", "data_nature": ""}],
            "calculations": [{"input_rids": ["r2"], "expression": "r2/r2-1",
                              "output_rid": "r2", "caliber": "yoy"}],
            "interpretations": ["存货上升"], "hypotheses": [],
            "limitations": [], "missing_inputs": [], "declared_status": "completed",
            "completion_report": [{"criterion": "给出存货数值与同比", "satisfied": True,
                                   "evidence_refs": ["r2"], "note": ""}],
            "required_missing_inputs": [], "optional_missing_inputs": [],
            "next_actions": [], "continuation_id": None, "evidence_refs": ["r2"],
            "summary": "存货 100", "stale": False, "created_ts": "2026-10-16T13:42:00"}},
        "reviews": {"rev1": {
            "review_id": "rev1", "target_type": "artifact", "target_id": "art1",
            "target_version": "rev1-attempt1", "verdict": "needs_revision",
            "findings": [{"finding_id": "rev1-f1", "category": "evidence_quality",
                          "severity": "must_fix", "statement": "同比缺基期",
                          "explanation": "", "suggested_action": "补 FY2023Q2",
                          "evidence_refs": ["r2"], "node_id": "n1",
                          "response_status": "open"}],
            "unresolved_items": ["同比缺基期"], "created_ts": "2026-10-16T13:42:30"}},
        "answer_candidate": None, "plan_change": {"revision": 1, "carried": [],
                                                 "updated": [], "added": ["n1", "n2"],
                                                 "stale_artifacts": []},
        "continuations": {},
    }
    (run / "analysis" / "analysis_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    if with_instrumentation:
        lines = []
        for i, s in enumerate(specs):
            rid = s.get("rid")
            lines.append(json.dumps({
                "query_trace_id": f"q{i + 1}", "call_index": i, "step": 0,
                "question": (s.get("args") or {}).get("question"),
                "node_task": (s.get("args") or {}).get("node_task"),
                "status": s.get("status", "ok" if rid else "empty"),
                "issue_codes": s.get("issue_codes", []),
                "plan": {"metrics": ["inventory"], "companies": ["Lenovo"],
                         "period_basis": "fiscal", "periods": ["FY2024Q2"],
                         "group_by": ["region"], "filters": []},
                "plan_digest": "9e3ef01addb3" if rid else None,
                "attempts": len([e for e in _extractor_events(s)
                                 if e.get("type") == "extractor_call"]),
                "extractor_events": _extractor_events(s),
                "prompt_version": "6aae694677ce", "catalog_version": "ac8a63098ffd",
                "data_version": "2633728-1792117547120291300-d728e5137cbb",
                "result_ids": [rid] if rid else [],
                "sql_chars": 48 if rid else None,
                "total_rows": 1 if rid else 0, "truncated": False,
            }, ensure_ascii=False))
        (run / "analysis" / "query_trace.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        (run / "analysis" / "visible_context.jsonl").write_text(json.dumps({
            "agent_call_id": "worker@2#n1", "role": "worker", "main_step": 2,
            "plan_revision": 1, "node_id": "n1", "target_id": None, "target_version": None,
            "system_prompt_sha256_12": "ce20181ce3e2",
            "user_content": "任务目标：取联想 FY2024Q2 存货", "user_chars": 20,
            "user_truncated": False, "resumed": False, "resume_message_count": 0,
        }, ensure_ascii=False) + "\n", encoding="utf-8")

    return run


REFERENCE_RUN = Path("runs/cli-20261016-134035")


# ------------------------------------------------------------------ Task 1

def test_loads_plan_nodes_artifacts_reviews(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")

    assert pkg.latest_revision == 1
    assert len(pkg.plans) == 1
    assert [n.node_id for n in pkg.plans[0].nodes] == ["n1", "n2"]
    assert pkg.plans[0].nodes[0].review_status == "accepted"
    assert len(pkg.artifacts) == 1
    assert pkg.artifacts[0].artifact_id == "art1"
    assert pkg.artifacts[0].version == "rev1-attempt1"
    assert pkg.artifacts[0].evidence_refs == ["r2"]
    assert len(pkg.reviews) == 1
    assert pkg.reviews[0].verdict == "needs_revision"
    assert pkg.reviews[0].target_version == "rev1-attempt1"
    assert pkg.reviews[0].findings[0]["finding_id"] == "rev1-f1"


def test_role_calls_keyed_by_agent_call_id(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    by_id = {c.agent_call_id: c for c in pkg.role_calls}

    assert set(by_id) == {"planner@1#plan", "worker@2#n1", "reviewer@6#art1"}

    w = by_id["worker@2#n1"]
    assert w.role == "worker"
    assert w.node_id == "n1"
    assert w.plan_revision == 1
    assert w.main_step == 2
    assert w.ok is True
    assert w.child_steps == 3
    assert w.prompt_tokens == 1500
    assert w.latency_ms == 7000
    # 该角色调用下的查询调用被收集进来（调用级 id）
    assert w.query_trace_ids == []


def test_reviewer_call_carries_target_version(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    rv = next(c for c in pkg.role_calls if c.role == "reviewer")

    assert rv.target_type == "artifact"
    assert rv.target_id == "art1"
    assert rv.target_version == "rev1-attempt1"


def test_final_status_follows_run_meta_not_node_states(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path, status="refuse"), case_id="LV-T01")

    # 节点 completed + 内部审查 accepted，都不能改记成功
    assert pkg.plans[0].nodes[0].execution_status == "completed"
    assert pkg.run_meta["multi_agent"]["semantic_review_status"] == "accepted"
    assert pkg.final_status == "refuse"
    assert pkg.final_answer is None
    assert pkg.verified is False


def test_multi_package_two_result_lines_uses_terminal(tmp_path):
    from eval.agent_process.adapt import load_process_package

    run = _write_run_package(tmp_path, status="refuse", two_result_lines=True,
                             first_result_status="answered")
    pkg = load_process_package(run, case_id="LV-T01")

    assert pkg.diagnostics["result_lines"] == 2
    # 末行才是终态；首行只是中间态
    assert pkg.final_status == "refuse"


def test_single_result_line_reported(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, two_result_lines=False), case_id="LV-T01")
    assert pkg.diagnostics["result_lines"] == 1


def test_initial_packet_flag_not_trusted(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")

    # r1(list_metrics) 与 r2(query_financial_data) 的 created_step 都是 0、
    # initial_packet 都是 true；只有按 tool 名归类才对。
    assert {r for rec in pkg.query_records for r in rec.result_ids} == {"r2"}


def test_field_compat_covers_every_normalized_field(tmp_path):
    from eval.agent_process.adapt import FIELD_COMPAT
    from eval.agent_process.schema import ProcessPackage

    allowed = {"available", "derived", "new_instrumentation", "unknown_legacy"}
    assert all(e["status"] in allowed for e in FIELD_COMPAT)
    assert all({"area", "source", "normalized", "status"} <= set(e) for e in FIELD_COMPAT)

    covered = {e["normalized"] for e in FIELD_COMPAT}
    missing = set(ProcessPackage.model_fields) - covered
    assert missing == set(), f"兼容表缺字段: {sorted(missing)}"


def test_query_records_derived_per_call_not_per_rid(tmp_path):
    """历史包没有 query_trace.jsonl，但每次调用都有 tool_result 事件（11 次 vs 9 个 rid）。"""
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False), case_id="LV-T01")

    assert len(pkg.query_records) == 2                      # 按调用计，不是按 rid
    assert {r for rec in pkg.query_records for r in rec.result_ids} == {"r2"}
    assert [rec.call_index for rec in pkg.query_records] == [0, 1]
    assert all(rec.source == "derived" for rec in pkg.query_records)
    # 与 run_meta 自报的提取次数一致，不产生假差异
    assert len(pkg.query_records) == pkg.run_meta["extraction_calls"]


def test_derived_query_record_links_via_parent_call_id(tmp_path):
    """parent_call_id 是显式结构字段，不是按顺序猜 —— 老包也能做节点归属。"""
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False), case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.linkage == "tool_result_parent"
    assert rec.agent_call_id == "worker@2#n1"
    assert rec.node_id == "n1"
    assert rec.role == "worker"


def test_derived_query_record_question_from_args_head(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False), case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.question == "联想 FY2024Q2 存货"
    assert rec.node_task == "取存货"


def test_derived_query_record_truncated_args_head_not_guessed(tmp_path):
    """args_head 被截断成非法 JSON 时，question 记 None 并留下说明，不猜。"""
    from eval.agent_process.adapt import load_process_package

    # agent/subagents.py 把 args_head 截到 300 字符，真实事件里就是这种半截串
    head = json.dumps({"question": "x" * 400, "node_task": "y"}, ensure_ascii=False)[:300]
    assert not head.endswith("}")          # 确认确实是截断的
    specs = [{"ok": True, "rid": "r2", "args_head": head}]
    run = _write_run_package(tmp_path, with_instrumentation=False, query_specs=specs)

    pkg = load_process_package(run, case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.question is None
    assert rec.node_task is None
    assert any("截断" in n for n in rec.derivation_notes)
    # 截断不影响调用级 id 与结果归属
    assert rec.linkage == "tool_result_parent"
    assert rec.result_ids == ["r2"]


def test_derived_status_ok_when_rid_present(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False), case_id="LV-T01")
    assert pkg.query_records[0].status == "ok"
    assert pkg.query_records[0].call_ok is True


def test_derived_status_empty_when_ok_but_no_rid(tmp_path):
    """ok=True 且无 rid 只可能是 empty（executor 仅完全成功才落库）。"""
    from eval.agent_process.adapt import load_process_package

    specs = [{"ok": True, "rid": None,
              "args": {"question": "无数据的查询"}, "result_head": "[查询状态] empty"}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False, query_specs=specs),
        case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.status == "empty"
    assert rec.result_ids == []
    assert rec.plan is None          # 没有结果行可参照


def test_derived_failed_call_status_unknown_with_note(tmp_path):
    """ok=False 时失败类别未记录 —— 不猜是解析失败还是执行失败。"""
    from eval.agent_process.adapt import load_process_package

    specs = [{"ok": False, "rid": None, "args": {"question": "坏查询"},
              "result_head": "[查询状态] unknown_value"}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False, query_specs=specs),
        case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.status is None
    assert rec.call_ok is False
    assert rec.result_ids == []
    assert any("未记录" in n for n in rec.derivation_notes)


def test_absent_instrumentation_fields_are_none_not_zero(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=False), case_id="LV-T01")
    rec = pkg.query_records[0]

    assert "analysis/query_trace.jsonl" in pkg.unknown_fields
    assert "analysis/visible_context.jsonl" in pkg.unknown_fields

    # 只有新埋点才产生的字段：一律 None/空，不得用 0 冒充「已测量」
    assert rec.query_trace_id is None
    assert rec.plan_digest is None
    assert rec.attempts is None
    assert rec.extractor_events == []
    assert rec.prompt_version is None
    assert rec.catalog_version is None

    # 但可从 results.json 真实测到的必须照实填，不能一律 None 了事
    assert rec.sql_chars == len("SELECT company, metric, period, value FROM facts")
    assert rec.total_rows == 1
    assert rec.data_version == "2633728-1792117547120291300-d728e5137cbb"


def test_run_package_query_records_carry_call_level_fields(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True), case_id="LV-T01")
    rec = pkg.query_records[0]

    assert rec.source == "run_package"
    assert rec.query_trace_id == "q1"
    assert rec.linkage == "query_trace_id"
    assert rec.attempts == 1
    assert rec.plan_digest == "9e3ef01addb3"
    assert rec.prompt_version == "6aae694677ce"
    assert rec.extractor_events[0]["type"] == "extractor_call"


def test_columns_from_linked_results_rows(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")

    assert "region" in pkg.query_records[0].columns
    assert "value" in pkg.query_records[0].columns


def test_absent_usage_not_zero_filled(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_usage=False), case_id="LV-T01")

    assert pkg.usage == {}
    assert pkg.usage.get("prompt_tokens") is None


def test_unknown_fields_empty_when_instrumentation_present(tmp_path):
    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True), case_id="LV-T01")
    assert pkg.unknown_fields == []


def test_falls_back_to_results_rows_when_no_query_events(tmp_path):
    """连 tool_result 事件都没有时才退回 results.json 派生，并诚实标 unknown。"""
    from eval.agent_process.adapt import load_process_package

    run = _write_run_package(tmp_path, with_instrumentation=False)
    lines = [json.loads(x) for x in
             (run / "trace.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    keep = [e for e in lines if e.get("tool") != "query_financial_data"]
    (run / "trace.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in keep) + "\n", encoding="utf-8")

    pkg = load_process_package(run, case_id="LV-T01")

    assert len(pkg.query_records) == 1
    assert pkg.query_records[0].linkage == "unknown"
    assert pkg.query_records[0].call_index is None


# ------------------------------------------------------------------ Task 2

def _metrics(tmp_path, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.metrics import compute_metrics

    return compute_metrics(load_process_package(_write_run_package(tmp_path, **kw),
                                                case_id="LV-T01"))


def test_tool_calls_deduped_by_parent_and_call_id(tmp_path):
    """主循环转发与子循环事件可能重复，同一 (parent, call_id) 只算一次。"""
    dup = {"type": "tool_result", "role": "worker", "step": 0,
           "tool": "query_financial_data", "ok": True, "rid": "r2", "call_id": "call_w1",
           "args_head": "{}", "result_head": "[查询状态] ok",
           "main_step": 2, "parent_call_id": "worker@2#n1"}
    base = _metrics(tmp_path / "a")
    withdup = _metrics(tmp_path / "b", extra_trace_events=[dup])

    assert withdup["tool_calls"]["total"] == base["tool_calls"]["total"]
    assert withdup["tool_calls"]["deduped_duplicates"] == 1


def test_tool_skipped_not_counted_as_success(tmp_path):
    """跳过是 skipped，既不算成功也不算失败；`ok` 缺失才归 unknown_status。"""
    m = _metrics(tmp_path)["tool_calls"]

    assert m["skipped"] == 1
    assert m["by_tool"]["query_financial_data"] == {"ok": 2, "skipped": 1}
    # 夹具里没有「ok 字段缺失」的事件 —— skipped 自带原因，不该混进 unknown_status
    assert m["unknown_status"] == 0
    assert m["total"] == 4          # 3 次 tool_result + 1 次 tool_skipped
    assert m["tool_result_events"] == 3


def test_missing_ok_goes_to_unknown_status(tmp_path):
    no_ok = {"type": "tool_result", "role": "worker", "step": 0, "tool": "calc",
             "call_id": "call_x1", "args_head": "{}", "result_head": "",
             "main_step": 2, "parent_call_id": "worker@2#n1"}
    base = _metrics(tmp_path / "a")
    got = _metrics(tmp_path / "b", extra_trace_events=[no_ok])

    assert got["tool_calls"]["unknown_status"] == base["tool_calls"]["unknown_status"] + 1
    assert got["tool_calls"]["ok"] == base["tool_calls"]["ok"]
    assert got["tool_calls"]["error"] == base["tool_calls"]["error"]


def test_exception_types_parsed_from_tool_error_text(tmp_path):
    bad = {"type": "tool_result", "role": "worker", "step": 0, "tool": "calc",
           "ok": False, "call_id": "call_e1", "args_head": "{}",
           "result_head": "工具错误: ValueError: 参数不合法",
           "main_step": 2, "parent_call_id": "worker@2#n1"}
    m = _metrics(tmp_path, extra_trace_events=[bad])

    assert m["tool_calls"]["error"] == 1
    assert m["exception_types"] == {"ValueError": 1}


def test_role_calls_pair_start_and_end(tmp_path):
    m = _metrics(tmp_path)["role_calls"]

    assert m["total"] == 3
    assert m["by_role"] == {"planner": 1, "worker": 1, "reviewer": 1}
    assert m["pairs_missing_end"] == 0


def test_role_call_without_end_has_no_latency(tmp_path):
    orphan = {"type": "subagent_start", "role": "worker", "step": 9,
              "agent_call_id": "worker@9#n9", "node_id": "n9", "plan_revision": 1}
    m = _metrics(tmp_path, extra_trace_events=[orphan])

    assert m["role_calls"]["total"] == 4
    assert m["role_calls"]["pairs_missing_end"] == 1
    # 缺 end 就不该有耗时，不能填 0
    assert "worker@9#n9" not in m["latency_ms"]["by_role_call"]


def test_rid_reuse_not_treated_as_call_count(tmp_path):
    """同计划复用 rid：调用数按调用计，rd 只作结果引用。"""
    m = _metrics(tmp_path)["queries"]

    assert m["calls"] == 2
    assert m["unique_result_ids"] == 1
    assert m["rid_reuse_calls"] == 1


def test_query_calls_match_reported_extraction_calls(tmp_path):
    m = _metrics(tmp_path)
    assert m["queries"]["calls"] == m["extraction"]["calls_reported"]
    assert m["extraction"]["calls_reported"] == 2


def test_missing_usage_is_null_not_zero(tmp_path):
    t = _metrics(tmp_path, with_usage=False)["tokens"]

    assert t["prompt_tokens"] is None
    assert t["completion_tokens"] is None
    assert t["source"] == "missing"


def test_subagent_tokens_not_added_to_run_total(tmp_path):
    """run_meta.usage 是总量；子 Agent token 是其中的子集，不相加。"""
    t = _metrics(tmp_path)["tokens"]

    assert t["prompt_tokens"] == 383994                      # 不是 383994 + 3200
    assert t["subagent_prompt_tokens"] == 900 + 1500 + 800
    assert t["source"] == "run_meta"
    assert any("子集" in n for n in t["notes"])


def test_reported_vs_recomputed_discrepancy_flagged(tmp_path):
    m = _metrics(tmp_path, reported_subagent_calls=5)

    disc = {d["field"]: d for d in m["discrepancies"]}
    assert disc["subagent_calls"]["reported"] == 5
    assert disc["subagent_calls"]["recomputed"] == 3


def test_no_discrepancy_when_reported_matches(tmp_path):
    m = _metrics(tmp_path)
    assert [d["field"] for d in m["discrepancies"]] == []


def test_cost_estimate_null_when_pricing_absent(tmp_path):
    assert _metrics(tmp_path, with_pricing=False)["cost_usd_estimate"] is None
    assert _metrics(tmp_path / "b")["cost_usd_estimate"] is not None


def test_cost_estimate_uses_reported_tokens(tmp_path):
    m = _metrics(tmp_path)
    # 383994/1e6*0.15 + 22438/1e6*0.60
    assert m["cost_usd_estimate"] == pytest.approx(0.0575991 + 0.0134628, rel=1e-6)


def test_reviewer_evidence_reads_counted(tmp_path):
    reads = [{"type": "tool_result", "role": "reviewer", "step": 0,
              "tool": "read_analysis_object", "ok": True, "rid": None,
              "call_id": "call_r1", "args_head": '{"object_id": "art1"}',
              "result_head": "...", "main_step": 6, "parent_call_id": "reviewer@6#art1"},
             {"type": "tool_result", "role": "worker", "step": 0, "tool": "recall",
              "ok": True, "rid": "r2", "call_id": "call_c1", "args_head": '{}',
              "result_head": "...", "main_step": 2, "parent_call_id": "worker@2#n1"}]
    m = _metrics(tmp_path, extra_trace_events=reads)["result_reads"]

    assert m["reviewer_evidence_reads"] == 1
    assert m["read_analysis_object_calls"] == 1
    assert m["recall_calls"] == 1


def test_denominators_exposed(tmp_path):
    m = _metrics(tmp_path)

    # 分母必须与原始计数并列，便于看出「跳过没进分母/分子」
    assert m["denominators"]["tool_calls_total"] == m["tool_calls"]["total"]
    assert m["denominators"]["tool_calls_ok_plus_error_plus_skipped_plus_unknown"] == \
        m["tool_calls"]["total"]
    assert m["denominators"]["role_calls_total"] == m["role_calls"]["total"]


def test_rounds_separate_main_and_sub(tmp_path):
    m = _metrics(tmp_path)["rounds"]

    # 主循环轮数与子循环步数分开，不能用 max_steps 掩盖上百步子调用
    assert m["main_assistant_events"] == 1
    assert m["subagent_child_steps"] == 2 + 3 + 1
    assert m["main_steps_reported"] == 20
    assert m["subagent_child_steps_reported"] == 6


def test_retries_counted(tmp_path):
    retries = [{"type": "llm_retry", "attempt": 1}, {"type": "stop_reject", "step": 3},
               {"type": "protocol_correction", "role": "planner", "step": 1,
                "main_step": 1, "parent_call_id": "planner@1#plan"}]
    m = _metrics(tmp_path, extra_trace_events=retries)["retries"]

    assert m["llm_retry"] == 1
    assert m["stop_reject"] == 1
    assert m["protocol_correction"] == 1


def test_metrics_has_no_single_total_score(tmp_path):
    m = _metrics(tmp_path)
    assert "total_score" not in m
    assert "score" not in m


# ------------------------------------------------------------------ Task 6

def _diag(tmp_path, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.query import diagnose_queries

    return diagnose_queries(load_process_package(_write_run_package(tmp_path, **kw),
                                                 case_id="LV-T01"))


def _rec(**kw):
    from eval.agent_process.schema import QueryRecord

    return QueryRecord(**kw)


def test_classify_ok_and_empty_and_failures():
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status="ok")) == "ok"
    assert classify_query(_rec(status="empty")) == "no_data"
    assert classify_query(_rec(status="needs_clarification")) == "ambiguous"
    assert classify_query(_rec(status="unknown_value")) == "catalog_miss"
    assert classify_query(_rec(status="error")) == "exec_fail"


def test_classify_unsupported_keeps_its_own_code():
    """unsupported 是「不支持的组合」，不能塌成 unknown —— PRD §7.3 要求目录外与它分开。"""
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status="unsupported")) == "unsupported"
    assert classify_query(_rec(status="unsupported",
                               issue_codes=["unsupported_detail"])) == "unsupported"


def test_classify_parse_fail_from_extractor_protocol():
    """提取器协议失败要单独成码，不能被 unsupported 吞掉。"""
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status="unsupported",
                               issue_codes=["extractor_protocol"])) == "parse_fail"


def test_classify_no_data_from_issue_code():
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status="empty", issue_codes=["empty_filter"])) == "no_data"
    assert classify_query(_rec(status="empty", issue_codes=["no_data"])) == "no_data"


def test_classify_failed_unclassified():
    """历史包只知道失败，不知道失败类别 —— 单独一个码，不猜。"""
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status=None, call_ok=False)) == "failed_unclassified"


def test_classify_unknown_when_status_and_ok_both_missing():
    from eval.agent_process.query import classify_query

    assert classify_query(_rec(status=None, call_ok=None)) == "unknown"


def test_catalog_miss_stays_distinct_from_unsupported(tmp_path):
    """同一次运行里两类失败必须分别计数。"""
    specs = [
        {"ok": False, "rid": None, "args": {"question": "目录外指标"},
         "result_head": "[查询状态] unknown_value"},
        {"ok": True, "rid": "r2", "args": {"question": "正常查询"}},
    ]
    diag = _diag(tmp_path, query_specs=specs)

    assert diag["by_reason"]["failed_unclassified"] == 1     # 老包不带状态，只知失败
    assert diag["by_reason"]["ok"] == 1
    assert diag["by_reason"].get("catalog_miss", 0) == 0


def test_new_package_reason_codes_from_status(tmp_path):
    specs = [
        {"ok": True, "rid": "r2", "status": "ok"},
        {"ok": True, "rid": None, "status": "empty"},
        {"ok": False, "rid": None, "status": "unknown_value",
         "issue_codes": ["unknown_metric"]},
        {"ok": False, "rid": None, "status": "unsupported",
         "issue_codes": ["extractor_protocol"]},
    ]
    diag = _diag(tmp_path, with_instrumentation=True, query_specs=specs)

    assert diag["by_reason"] == {"ok": 1, "no_data": 1, "catalog_miss": 1, "parse_fail": 1}


def test_empty_status_has_no_result_ids_and_not_counted_ok(tmp_path):
    """empty 的 ok=True、rid=None：不能因为 ok 就当作取到数。"""
    specs = [{"ok": True, "rid": None, "args": {"question": "无数据"},
              "result_head": "[查询状态] empty"}]
    diag = _diag(tmp_path, query_specs=specs)

    assert diag["by_reason"] == {"no_data": 1}
    assert diag["records"][0]["result_ids"] == []


def test_counts_are_per_call_not_per_rid(tmp_path):
    """同计划复用 rid：按调用计。"""
    diag = _diag(tmp_path)

    assert diag["calls"] == 2
    assert sum(diag["by_reason"].values()) == 2


def test_periods_returned_outside_requested_flagged():
    from eval.agent_process.query import plan_conformance

    rec = _rec(plan={"periods": ["FY2024Q1", "FY2024Q3"], "metrics": ["inventory"],
                     "group_by": []})
    rows = [{"period": "FY2024Q1", "value": 1.0}, {"period": "FY2024Q2", "value": 2.0}]

    out = plan_conformance(rec, rows)
    assert out["periods_requested"] == ["FY2024Q1", "FY2024Q3"]
    assert out["period_violations"] == ["FY2024Q2"]
    assert out["status"] == "mismatch"


def test_columns_missing_detected():
    from eval.agent_process.query import plan_conformance

    rec = _rec(plan={"periods": ["FY2024Q2"], "metrics": ["inventory"],
                     "group_by": ["region"]})
    out = plan_conformance(rec, [{"company": "Lenovo", "value": 1.0}])

    assert out["columns_missing"] == ["region"]
    assert "company" in out["columns_present"]
    assert out["status"] == "mismatch"


def test_conformance_ok_when_periods_and_columns_match():
    from eval.agent_process.query import plan_conformance

    rec = _rec(plan={"periods": ["FY2024Q2"], "metrics": ["inventory"],
                     "group_by": ["region"]})
    out = plan_conformance(rec, [{"period": "FY2024Q2", "region": "APAC", "value": 1.0}])

    assert out["status"] == "ok"
    assert out["period_violations"] == []
    assert out["columns_missing"] == []


def test_conformance_unknown_without_plan_or_rows():
    from eval.agent_process.query import plan_conformance

    assert plan_conformance(_rec(plan=None), [{"value": 1.0}])["status"] == "unknown"
    assert plan_conformance(_rec(plan={"periods": ["FY2024Q2"]}), [])["status"] == "unknown"


def test_derived_records_marked_low_confidence(tmp_path):
    """派生记录只覆盖成功路径，必须在诊断里说明，避免把分母当完整。"""
    diag = _diag(tmp_path)

    rec = diag["records"][0]
    assert rec["source"] == "derived"
    assert any("派生" in n for n in diag["notes"])
    assert diag["coverage"]["has_run_package_records"] is False


def test_run_package_records_marked_full_confidence(tmp_path):
    diag = _diag(tmp_path, with_instrumentation=True)
    assert diag["coverage"]["has_run_package_records"] is True


def test_query_count_not_scored_by_volume(tmp_path):
    """多写查询次数不自动加分（PRD §7.3）：诊断只给计数，不给质量分。"""
    diag = _diag(tmp_path)

    assert "calls" in diag
    assert "score" not in diag and "quality" not in diag


# ------------------------------------------------------------------ Task 7：Planner

PLANNER_SAMPLES = Path("eval/agent_process/samples/planner.jsonl")


def _planner_pkg(tmp_path, *, revisions=1, contract_period="FY2024Q2"):
    """构造带指定计划修订数与节点内容的包。"""
    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["contract"]["period"] = contract_period
    base = state["plans"]["1"]
    for rev in range(2, revisions + 1):
        plan = json.loads(json.dumps(base))
        plan["revision"] = rev
        plan["plan_id"] = f"plan{rev}"
        state["plans"][str(rev)] = plan
    state["latest_revision"] = revisions
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return run


def _planner_eval(run, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles import planner

    return planner.evaluate_package(load_process_package(run, case_id="LV-T01"), **kw)


def _plan_view(nodes, revision=1):
    from eval.agent_process.schema import PlanNodeView, PlanView

    return PlanView(revision=revision,
                    nodes=[PlanNodeView(**n) for n in nodes])


def test_plan_measured_counts_nodes_and_required():
    from eval.agent_process.roles.planner import evaluate_plan_measured

    out = evaluate_plan_measured(_plan_view([
        {"node_id": "n1", "goal": "取数", "required_for_answer": True},
        {"node_id": "n2", "goal": "写结论", "required_for_answer": False},
    ]))

    assert out["node_count"] == 2
    assert out["required_node_count"] == 1


def test_plan_measured_detects_duplicate_goals():
    from eval.agent_process.roles.planner import evaluate_plan_measured

    out = evaluate_plan_measured(_plan_view([
        {"node_id": "n1", "goal": "取存货同比"},
        {"node_id": "n2", "goal": "  取存货同比  "},      # 归一化后相同
        {"node_id": "n3", "goal": "取收入同比"},
    ]))

    assert out["duplicate_goals"] == [["n1", "n2"]]


def test_plan_measured_detects_dangling_and_self_deps():
    from eval.agent_process.roles.planner import evaluate_plan_measured

    out = evaluate_plan_measured(_plan_view([
        {"node_id": "n1", "goal": "A", "depends_on": ["n9"]},
        {"node_id": "n2", "goal": "B", "depends_on": ["n2"]},
    ]))

    assert out["dangling_deps"] == ["n1->n9"]
    assert out["self_deps"] == ["n2"]


def test_plan_measured_detects_cycle():
    from eval.agent_process.roles.planner import evaluate_plan_measured

    ok = evaluate_plan_measured(_plan_view([
        {"node_id": "n1", "goal": "A", "depends_on": ["n2"]},
        {"node_id": "n2", "goal": "B", "depends_on": ["n1"]},
    ]))
    assert ok["cycle"] is True

    acyclic = evaluate_plan_measured(_plan_view([
        {"node_id": "n1", "goal": "A"},
        {"node_id": "n2", "goal": "B", "depends_on": ["n1"]},
    ]))
    assert acyclic["cycle"] is False


def test_scope_drift_flags_period_outside_contract():
    from eval.agent_process.roles.planner import evaluate_plan_measured

    out = evaluate_plan_measured(
        _plan_view([
            {"node_id": "n1", "goal": "取 FY2024Q2 存货"},
            {"node_id": "n2", "goal": "取 FY2023Q4 存货做对比"},
        ]),
        contract={"period": "FY2024Q2"})

    assert out["scope_drift"] == ["n2"]


def test_scope_drift_empty_without_contract():
    """没有合同就不判断范围偏移，不猜。"""
    from eval.agent_process.roles.planner import evaluate_plan_measured

    out = evaluate_plan_measured(_plan_view([{"node_id": "n1", "goal": "FY2023Q4"}]))
    assert out["scope_drift"] == []


def test_planner_plan_revisions_and_nodes_by_revision(tmp_path):
    out = _planner_eval(_planner_pkg(tmp_path, revisions=2))

    assert out["plan_revisions"] == 2
    assert set(out["nodes_by_revision"]) == {1, 2}
    assert out["nodes_by_revision"][1] == ["n1", "n2"]


def test_coverage_unknown_without_points(tmp_path):
    out = _planner_eval(_planner_pkg(tmp_path))

    assert out["coverage"]["status"] == "unknown"
    assert out["evidence_basis"]["coverage"] == "unknown"
    assert out["coverage"]["mapping"] == []


def test_coverage_judged_with_fake_judge_is_pipeline_check(tmp_path):
    """模拟 Judge 的结论只能是 pipeline_check，不得冒充真实模型评分。"""
    from eval.agent_process.roles.common import make_judge

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {
        "verdict": "met", "reason": "计划里有对应节点"})
    points = [{"point_id": "p1", "atomic_judgment": "给出存货同比"},
              {"point_id": "p2", "atomic_judgment": "给出收入同比"}]
    out = _planner_eval(_planner_pkg(tmp_path), judge=judge, points=points)

    assert out["coverage"]["status"] == "judged"
    assert out["evidence_basis"]["coverage"] == "pipeline_check"
    assert [m["point_id"] for m in out["coverage"]["mapping"]] == ["p1", "p2"]
    assert all(m["verdict"] == "met" for m in out["coverage"]["mapping"])


def test_coverage_mapping_carries_evidence_reason(tmp_path):
    from eval.agent_process.roles.common import make_judge

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {
        "verdict": "missing", "reason": "计划没有任何节点覆盖该点"})
    out = _planner_eval(_planner_pkg(tmp_path), judge=judge,
                        points=[{"point_id": "p1", "atomic_judgment": "x"}])

    m = out["coverage"]["mapping"][0]
    assert m["verdict"] == "missing"
    assert m["reason"] == "计划没有任何节点覆盖该点"


def test_coverage_does_not_look_at_final_answer(tmp_path):
    """计划覆盖与最终答案覆盖分开：本函数不读 final_answer。"""
    out = _planner_eval(_planner_pkg(tmp_path))
    blob = json.dumps(out, ensure_ascii=False)
    assert "final_answer" not in blob
    assert "answer_candidate" not in blob


def test_evidence_basis_helper():
    from eval.agent_process.roles.common import evidence_basis

    assert evidence_basis(True, False) == "measured"
    assert evidence_basis(False, True) == "pipeline_check"
    assert evidence_basis(False, True, judge_is_live=True) == "model_judged"
    assert evidence_basis(False, False) == "unknown"


def test_samples_file_has_recommended_split():
    from eval.agent_process.roles.common import load_samples

    samples = load_samples(PLANNER_SAMPLES)
    assert len(samples) >= 5
    for s in samples:
        assert {"sample_id", "question", "contract", "plan", "annotations"} <= set(s)
        assert s["annotations"]["coverage"]

    assert any(v == "uncovered" for s in samples
               for v in s["annotations"]["coverage"].values())
    assert any("duplicate_node" in s["annotations"].get("expected_flags", [])
               for s in samples)
    assert any("implicit" in s["annotations"]["coverage"].values() for s in samples)


def test_evaluate_sample_runs_pipeline():
    from eval.agent_process.roles.common import load_samples, make_judge
    from eval.agent_process.roles.planner import evaluate_sample

    sample = load_samples(PLANNER_SAMPLES)[0]
    judge = make_judge("fake", point_fn=lambda q, p, f, a: {"verdict": "met",
                                                           "reason": "看得见"})
    out = evaluate_sample(sample, judge)

    assert out["sample_id"] == sample["sample_id"]
    assert out["measured"]["node_count"] >= 1
    assert out["coverage"]["status"] == "judged"
    assert out["annotation"] == sample["annotations"]


# ------------------------------------------------------------------ Task 8：Worker

WORKER_SAMPLES = Path("eval/agent_process/samples/worker.jsonl")

_WORKER_CAUSES = ("upstream_goal_error", "handoff_omission", "intent_error",
                  "text2sql_parse_error", "executor_error", "data_unavailable")


def _art(**kw):
    from eval.agent_process.schema import ArtifactView

    base = {"artifact_id": "art1", "node_id": "n1", "plan_revision": 1, "attempt": 1,
            "execution_status": "completed", "version": "rev1-attempt1"}
    base.update(kw)
    return ArtifactView(**base)


def test_facts_with_and_without_rid_counted():
    from eval.agent_process.roles.worker import evaluate_artifact_measured

    out = evaluate_artifact_measured(
        _art(facts=[{"rid": "r2", "field": "inventory", "value": 1},
                    {"rid": "r3", "field": "revenue", "value": 2},
                    {"field": "cogs", "value": 3}]),
        rids_in_store={"r2", "r3"})

    assert out["facts_with_rid"] == 2
    assert out["facts_missing_rid"] == 1
    assert out["rids_not_in_store"] == []


def test_rid_not_in_store_flagged():
    from eval.agent_process.roles.worker import evaluate_artifact_measured

    out = evaluate_artifact_measured(
        _art(facts=[{"rid": "r99", "field": "inventory", "value": 1}],
             evidence_refs=["r99"]),
        rids_in_store={"r1", "r2"})

    assert out["rids_not_in_store"] == ["r99"]
    assert out["evidence_refs_not_in_store"] == ["r99"]


def test_calculations_missing_output_rid():
    from eval.agent_process.roles.worker import evaluate_artifact_measured

    out = evaluate_artifact_measured(
        _art(calculations=[{"input_rids": ["r2"], "expression": "a/b"},
                           {"input_rids": ["r2"], "expression": "a-b",
                            "output_rid": "r2"}]),
        rids_in_store={"r2"})

    assert out["calculations_missing_output_rid"] == 1


def test_completion_criteria_coverage_from_completion_report():
    from eval.agent_process.roles.worker import evaluate_artifact_measured

    out = evaluate_artifact_measured(
        _art(completion_report=[{"criterion": "给出数值", "satisfied": True},
                                {"criterion": "给出同比", "satisfied": False}],
             evidence_refs=["r2"]),
        rids_in_store={"r2"})

    assert out["criteria_covered"] == 1
    assert out["criteria_uncovered"] == 1


def test_node_completed_does_not_imply_success(tmp_path):
    """execution_status=completed 不等于任务成功：产物无任何 rid 时必须看得见。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifacts"]["art1"]["facts"] = [{"field": "inventory", "value": 1.0}]
    state["artifacts"]["art1"]["evidence_refs"] = []
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["measured"]["facts_missing_rid"] == 1
    assert out["evidence_basis"]["reference_integrity"] == "measured"
    assert "success" not in out          # 不提供「completed 即成功」的字段
    assert out["measured"]["execution_status"] == "completed"


def test_worker_counts_are_per_node(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    rec = [{"type": "llm_retry", "attempt": 1, "main_step": 2,
            "parent_call_id": "worker@2#n1"},
           {"type": "tool_result", "role": "worker", "step": 0, "tool": "recall",
            "ok": True, "rid": "r2", "call_id": "c1", "args_head": "{}",
            "result_head": "", "main_step": 2, "parent_call_id": "worker@2#n1"}]
    run = _write_run_package(tmp_path, extra_trace_events=rec)
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    c = out["counts"]
    assert c["role_calls"] == 1
    assert c["llm_retry"] == 1
    assert c["recall_reads"] == 1
    assert c["query_calls"] == 2
    assert c["redundant_same_tool"] == 1     # query_financial_data 调了两次


def test_attribution_unknown_when_evidence_insufficient(tmp_path):
    """不凭最终错误倒推根因：没有证据就说未知。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["artifacts"]["art1"]["execution_status"] = "failed"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["attribution"]["cause"] == "unknown"
    assert "证据不足" in out["attribution"]["reason"]


def _mark_node_not_delivered(run):
    """把 n1 的产物标成未完成。

    只有**节点没交付**时才有「根因」可归；节点已交付时，某条查询失败只是观察。
    """
    p = run / "analysis" / "analysis_state.json"
    state = json.loads(p.read_text(encoding="utf-8"))
    state["artifacts"]["art1"]["execution_status"] = "failed"
    state["artifacts"]["art1"]["completion_report"] = [
        {"criterion": "给出存货数值与同比", "satisfied": False}]
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return run


def test_attribution_data_unavailable_from_no_data(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": None, "status": "empty",
              "args": {"question": "无数据"}}]
    run = _mark_node_not_delivered(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs))
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["attribution"]["cause"] == "data_unavailable"


def test_attribution_parse_error_from_text2sql(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": False, "rid": None, "status": "unsupported",
              "issue_codes": ["extractor_protocol"], "args": {"question": "坏查询"}}]
    run = _mark_node_not_delivered(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs))
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["attribution"]["cause"] == "text2sql_parse_error"


def test_attribution_executor_error(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": False, "rid": None, "status": "error",
              "issue_codes": ["query_error"], "args": {"question": "SQL 失败"}}]
    run = _mark_node_not_delivered(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs))
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["attribution"]["cause"] == "executor_error"


def test_attribution_handoff_omission_when_upstream_incomplete(tmp_path):
    """依赖节点没交付 → 上下文交接遗漏，而不是把错算到 Worker 头上。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    # n1 改为依赖一个未完成的 n0
    state["plans"]["1"]["nodes"][0]["depends_on"] = ["n0"]
    state["plans"]["1"]["nodes"].append({
        "node_id": "n0", "goal": "上游取数", "depends_on": [], "input_refs": [],
        "completion_criteria": "给出数", "required_for_answer": True,
        "required_output_refs": [], "requires_review": False, "slice_index": 0,
        "execution_status": "pending", "attempt": 0, "latest_artifact_id": None,
        "review_status": "not_reviewed"})
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")
    assert out["attribution"]["cause"] == "handoff_omission"


def test_worker_judged_dimensions_unknown_without_judge(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    out = evaluate_node(load_process_package(_write_run_package(tmp_path),
                                             case_id="LV-T01"), "n1")

    assert out["judged"] is None
    assert out["evidence_basis"]["query_intent"] == "unknown"
    assert out["evidence_basis"]["calculation"] == "unknown"


# --- 审查发现 1：查询意图判定必须看到实际查询 ---

class CapturingJudge:
    """记录 Judge 实际收到的输入，用于验证它到底看到了什么。"""

    def __init__(self, verdict="unknown"):
        self.calls = []
        self.verdict = verdict

    def score_point(self, question, point, facts, answer):
        self.calls.append({"question": question, "point": point,
                           "facts": facts, "answer": answer})
        return {"verdict": self.verdict, "reason": "captured"}

    def score_whole(self, question, answer):
        return {"dimensions": {}}


def test_query_intent_judge_sees_actual_queries(tmp_path):
    """PRD §8.2：Worker 评测输入必须含「查询与结果」，否则判不了查询意图。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": "r2",
              "args": {"question": "联想 FY2024Q2 存货", "node_task": "取存货"}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    judge = CapturingJudge()
    evaluate_node(pkg, "n1", judge=judge)

    blob = json.dumps(judge.calls[0], ensure_ascii=False)
    # 实际发出的查询文本必须进入 Judge 视野
    assert "联想 FY2024Q2 存货" in blob
    # 结果引用也要在，才能判断查询是否真的取到数
    assert "r2" in blob


def test_query_intent_judge_sees_empty_result_as_such(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": None, "status": "empty",
              "args": {"question": "无数据的查询"}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    judge = CapturingJudge()
    evaluate_node(pkg, "n1", judge=judge)

    blob = json.dumps(judge.calls[0], ensure_ascii=False)
    assert "empty" in blob          # 空结果必须如实呈现，不能显示成成功


# --- 审查发现 2：已恢复的失败不能当作根因 ---

def test_recovered_failure_is_not_attributed_as_cause(tmp_path):
    """同一问题重试成功：失败是观察，不是根因。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    same_q = "联想 FY2024Q2 存货"
    specs = [{"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}},
             {"ok": True, "rid": "r2", "status": "ok", "args": {"question": same_q}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    out = evaluate_node(pkg, "n1")

    assert out["attribution"]["cause"] != "data_unavailable", \
        "同意图重试成功，不该仍归因为数据不可用"
    assert out["counts"]["failed_query_calls"] == 1
    assert out["counts"]["recovered_failed_query_calls"] == 1
    assert out["counts"]["unresolved_failed_query_calls"] == 0
    assert any("恢复" in n or "重试" in n for n in out["notes"])


def test_unrelated_later_success_is_not_a_recovery(tmp_path):
    """查 A 失败后去查 B 成功：B 成功不代表 A 补齐，不能记成「已恢复」。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": None, "status": "empty",
              "args": {"question": "查指标 A"}},
             {"ok": True, "rid": "r2", "status": "ok",
              "args": {"question": "查指标 B"}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    out = evaluate_node(pkg, "n1")

    assert out["counts"]["recovered_failed_query_calls"] == 0
    assert out["counts"]["unresolved_failed_query_calls"] == 1
    # 节点最终交付了，所以不把整节点归因成数据不可用；但必须说明 A 的失败没被证明补上
    assert out["attribution"]["cause"] != "data_unavailable"
    assert any(("未" in n) and ("补齐" in n or "重试" in n) for n in out["notes"])


def test_unresolved_failure_on_failed_node_is_attributed(tmp_path):
    """节点没交付且存在未被补齐的失败查询：照旧归因，修复不能把真问题一起吞掉。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": "r2", "status": "ok",
              "args": {"question": "先取到数"}},
             {"ok": True, "rid": None, "status": "empty",
              "args": {"question": "再取就空了"}}]
    run = _mark_node_not_delivered(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs))
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["attribution"]["cause"] == "data_unavailable"
    assert out["counts"]["recovered_failed_query_calls"] == 0
    assert out["counts"]["unresolved_failed_query_calls"] == 1


def test_distinct_queries_are_counted_separately(tmp_path):
    """同一节点查两个不同指标：两次失败是两件事，不是一个意图的两次重试。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    specs = [{"ok": True, "rid": None, "status": "empty",
              "args": {"question": "查指标 A"}},
             {"ok": True, "rid": None, "status": "empty",
              "args": {"question": "查指标 B"}}]
    run = _mark_node_not_delivered(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs))
    out = evaluate_node(load_process_package(run, case_id="LV-T01"), "n1")

    assert out["counts"]["failed_query_calls"] == 2
    assert out["counts"]["unresolved_failed_query_calls"] == 2
    assert out["counts"]["recovered_failed_query_calls"] == 0


def test_fail_success_fail_counts_only_the_trailing_failure(tmp_path):
    """同一问题「失败 → 成功 → 再失败」：中间的恢复必须算数。

    第一次失败被中间那次成功补上了，只有最后一次（成功之后再失败）是未解决的。
    只按「该意图最后一次结果」一刀切，会把两次失败都记成未解决。
    """
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    same_q = "联想 FY2024Q2 存货"
    specs = [{"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}},
             {"ok": True, "rid": "r2", "status": "ok", "args": {"question": same_q}},
             {"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    out = evaluate_node(pkg, "n1")

    c = out["counts"]
    assert c["failed_query_calls"] == 2
    assert c["recovered_failed_query_calls"] == 1, "中间成功补上的那次应算已恢复"
    assert c["unresolved_failed_query_calls"] == 1, "只有成功之后的那次失败未解决"


def test_multiple_successes_recover_their_preceding_failures(tmp_path):
    """失败→成功→失败→成功：两次失败都被各自后面那次成功补上，没有未解决项。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    same_q = "联想 FY2024Q2 存货"
    specs = [{"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}},
             {"ok": True, "rid": "r2", "status": "ok", "args": {"question": same_q}},
             {"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}},
             {"ok": True, "rid": "r2", "status": "ok", "args": {"question": same_q}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    out = evaluate_node(pkg, "n1")

    c = out["counts"]
    assert c["failed_query_calls"] == 2
    assert c["recovered_failed_query_calls"] == 2
    assert c["unresolved_failed_query_calls"] == 0


def test_leading_failures_then_success_all_recovered(tmp_path):
    """连续两次失败后成功：两次都算恢复，不是只算最后一次。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    same_q = "联想 FY2024Q2 存货"
    specs = [{"ok": True, "rid": None, "status": "empty", "args": {"question": same_q}},
             {"ok": False, "rid": None, "status": "error",
              "issue_codes": ["query_error"], "args": {"question": same_q}},
             {"ok": True, "rid": "r2", "status": "ok", "args": {"question": same_q}}]
    pkg = load_process_package(
        _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs),
        case_id="LV-T01")
    out = evaluate_node(pkg, "n1")

    c = out["counts"]
    assert c["failed_query_calls"] == 2
    assert c["recovered_failed_query_calls"] == 2
    assert c["unresolved_failed_query_calls"] == 0


# --- 审查发现 3：依据等级必须与代码实际检查的内容一致 ---

def test_task_completion_is_not_claimed_as_measured(tmp_path):
    """程序只解析了产物**自报**的完成情况，没独立核验任务是否完成。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    out = evaluate_node(load_process_package(_write_run_package(tmp_path),
                                             case_id="LV-T01"), "n1")

    assert out["evidence_basis"]["task_completion"] == "unknown"
    assert out["evidence_basis"]["evidence_support"] == "unknown"
    # 自报内容单独放，并标明来源是被测产物自己写的
    sr = out["self_reported"]
    assert sr["task_completion"]["source"] == "artifact.completion_report"
    assert sr["task_completion"]["criteria_covered"] == 1


def test_reference_integrity_is_measured(tmp_path):
    """程序确实测到的只是「引用的 rid 是否存在」——名字要如实。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    out = evaluate_node(load_process_package(_write_run_package(tmp_path),
                                             case_id="LV-T01"), "n1")
    assert out["evidence_basis"]["reference_integrity"] == "measured"
    assert "reference_integrity" in out["dimensions"]
    assert "evidence_support" in out["dimensions"]


def test_single_judge_verdict_only_marks_query_intent(tmp_path):
    """只调用一次 Judge（只判 query_intent）：其余语义维度不得因此变成「已评测」。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    out = evaluate_node(pkg, "n1", judge=CapturingJudge(verdict="met"))

    assert out["evidence_basis"]["query_intent"] == "pipeline_check"
    for dim in ("calculation", "evidence_support", "task_completion"):
        assert out["evidence_basis"][dim] == "unknown", \
            f"{dim} 没有被单独评判，不能标成已评测"


def test_judge_can_report_multiple_dimensions(tmp_path):
    """Judge 若真的返回了其他维度的结论，才把那些维度标成已评测。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.common import make_judge
    from eval.agent_process.roles.worker import evaluate_node

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {
        "verdict": "met", "reason": "意图一致",
        "dimensions": {"calculation": "met", "evidence_support": "partial"}})
    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    out = evaluate_node(pkg, "n1", judge=judge)

    assert out["evidence_basis"]["query_intent"] == "pipeline_check"
    assert out["evidence_basis"]["calculation"] == "pipeline_check"
    assert out["evidence_basis"]["evidence_support"] == "pipeline_check"
    # 没返回的维度仍然 unknown
    assert out["evidence_basis"]["task_completion"] == "unknown"


def test_judge_dimension_with_unknown_verdict_stays_unknown(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.common import make_judge
    from eval.agent_process.roles.worker import evaluate_node

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {
        "verdict": "met", "dimensions": {"calculation": "unknown"}})
    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    out = evaluate_node(pkg, "n1", judge=judge)

    assert out["evidence_basis"]["query_intent"] == "pipeline_check"
    assert out["evidence_basis"]["calculation"] == "unknown"


def test_unknown_verdict_does_not_earn_a_basis(tmp_path):
    """Judge 判 unknown 等于没判：不能因为「跑过 Judge」就标 pipeline_check。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    pkg = load_process_package(_write_run_package(tmp_path), case_id="LV-T01")
    out = evaluate_node(pkg, "n1", judge=CapturingJudge(verdict="unknown"))

    assert out["evidence_basis"]["evidence_support"] == "unknown"
    assert out["evidence_basis"]["task_completion"] == "unknown"
    assert out["evidence_basis"]["query_intent"] == "unknown"
    assert out["evidence_basis"]["reference_integrity"] == "measured"


# --- 审查发现 4：不同量纲的计数不能拿来互比 ---

def test_extraction_discrepancy_uses_attempts_not_tool_calls(tmp_path):
    """一次工具调用可发多次提取请求（格式修复）：拿工具调用数比提取数会误报差异。"""
    evs = [{"type": "extractor_call", "attempt": 1, "status": "invalid"},
           {"type": "extractor_call", "attempt": 2, "status": "parsed"}]
    specs = [{"ok": True, "rid": "r2", "args": {"question": "q"},
              "extractor_events": evs}]
    m = _metrics(tmp_path, with_instrumentation=True, query_specs=specs)

    assert m["queries"]["calls"] == 1              # 1 次工具调用
    assert m["extraction"]["attempts_seen"] == 2   # 2 次提取请求
    # 两者量纲不同，不得据此报差异
    assert [d["field"] for d in m["discrepancies"] if d["field"] == "extraction_calls"] == []


def test_extraction_discrepancy_still_flagged_on_real_mismatch(tmp_path):
    evs = [{"type": "extractor_call", "attempt": 1, "status": "parsed"}]
    specs = [{"ok": True, "rid": "r2", "args": {"question": "q"},
              "extractor_events": evs}]
    run = _write_run_package(tmp_path, with_instrumentation=True, query_specs=specs)
    meta_path = run / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["extraction_calls"] = 9                    # 自报 9，实际 1
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.metrics import compute_metrics

    m = compute_metrics(load_process_package(run, case_id="LV-T01"))
    d = next(x for x in m["discrepancies"] if x["field"] == "extraction_calls")
    assert d["reported"] == 9 and d["recomputed"] == 1


def test_extraction_not_compared_when_detail_absent(tmp_path):
    """历史包没有提取明细：不比较、不报差异，只标不可比。"""
    m = _metrics(tmp_path)                          # 无埋点
    assert m["extraction"]["attempts_seen"] is None
    assert [d for d in m["discrepancies"] if d["field"] == "extraction_calls"] == []
    assert any("提取" in n for n in m["notes"])


# ================= 第一轮复审（14 项）修复：按影响排序 =================

# --- F2：修复情况不能由一个 agent 从不写入的默认值得出 ---

def test_reviewer_fix_linkage_is_not_claimed_measured(tmp_path):
    """`response_status` 的默认值是 open 且 agent 从不写 addressed：
    拿它断言「未修复」等于用默认值冒充事实。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import evaluate_package

    out = evaluate_package(load_process_package(_reviewer_pkg(tmp_path), case_id="LV-T01"))

    # 「关联」是实测的；「是否修好」不是
    assert out["evidence_basis"]["review_round_linkage"] == "measured"
    assert out["evidence_basis"]["fix_verification"] == "unknown"
    rnd = out["rounds"][0]
    assert rnd["fix_verified"] is None
    assert rnd["declared_response"]["rev1-f1"] == "open"
    assert rnd["response_status_is_default"] is True
    assert any("response_status" in n and "默认" in n for n in out["notes"])
    # 不再输出会被读成结论的「未修复」字段
    assert "not_fixed" not in rnd and "fixed" not in rnd


def test_declared_addressed_is_reported_as_declaration_not_verification(tmp_path):
    """产物若声明 addressed，只如实转述该声明；仍不据此断言已修复。"""
    out = _link(tmp_path, finding_status="addressed")

    rnd = out["rounds"][0]
    assert rnd["declared_response"]["rev1-f1"] == "addressed"
    assert rnd["response_status_is_default"] is False
    assert rnd["fix_verified"] is None


def test_reviewer_trajectory_basis_does_not_claim_precision(tmp_path):
    """轨迹路径没有标注、没算 precision/recall：不能标 measured。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import evaluate_package

    out = evaluate_package(load_process_package(_reviewer_pkg(tmp_path), case_id="LV-T01"))
    for dim in ("misses", "false_positives"):
        assert out["evidence_basis"][dim] == "unknown"
    assert out["basis_scope"] == "trajectory"


def test_reviewer_round_records_content_change(tmp_path):
    """能不能关联到新版本、新版本内容是否变了，是可测的；是否修好不是。"""
    out = _link(tmp_path)

    rnd = out["rounds"][0]
    assert rnd["next_version"] == "rev1-attempt2"
    assert rnd["next_content_changed"] is True
    assert rnd["fix_verified"] is None          # 未做前后语义比对，不假装知道


def test_reviewer_same_content_next_version_flags_unchanged(tmp_path):
    run = _reviewer_pkg(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for k in ("facts", "evidence_refs", "summary", "limitations",
              "required_missing_inputs", "completion_report"):
        state["artifacts"]["art2"][k] = state["artifacts"]["art1"][k]
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import link_review_rounds

    out = link_review_rounds(load_process_package(run, case_id="LV-T01"))
    assert out["rounds"][0]["next_content_changed"] is False


# --- F8：plan 目标的「后续版本」不能编 ---

def test_plan_review_next_version_not_fabricated(tmp_path):
    """审查 plan rev1 而 latest_revision 也是 1：没有新版本，不许报「后续版本 1」。"""
    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reviews"]["rev1"] = {"review_id": "rev1", "target_type": "plan",
                               "target_id": "plan1", "target_version": "1",
                               "verdict": "needs_revision", "findings": [],
                               "unresolved_items": [], "created_ts": ""}
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import link_review_rounds

    out = link_review_rounds(load_process_package(run, case_id="LV-T01"))
    assert out["rounds"][0]["next_version"] is None
    assert out["unlinkable"][0]["review_id"] == "rev1"


def test_plan_review_next_version_when_revision_exists(tmp_path):
    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reviews"]["rev1"] = {"review_id": "rev1", "target_type": "plan",
                               "target_id": "plan1", "target_version": "1",
                               "verdict": "needs_revision", "findings": [],
                               "unresolved_items": [], "created_ts": ""}
    plan2 = json.loads(json.dumps(state["plans"]["1"]))
    plan2.update({"revision": 2, "plan_id": "plan2"})
    state["plans"]["2"] = plan2
    state["latest_revision"] = 2
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import link_review_rounds

    out = link_review_rounds(load_process_package(run, case_id="LV-T01"))
    assert out["rounds"][0]["next_version"] == "2"


# --- F3：缺数据不能因为求和而变成 0 ---

def test_orphan_role_call_marks_sums_incomplete(tmp_path):
    """子调用没有 end（崩溃/超时）：耗时与步数是下界，不能报成 0 且不加说明。"""
    orphan = {"type": "subagent_start", "role": "worker", "step": 9,
              "agent_call_id": "worker@9#n9", "node_id": "n9", "plan_revision": 1}
    m = _metrics(tmp_path, extra_trace_events=[orphan])

    assert m["role_calls"]["pairs_missing_end"] == 1
    assert m["latency_ms"]["complete"] is False
    assert m["rounds"]["child_steps_complete"] is False
    assert m["tokens"]["subagent_complete"] is False
    assert any("下界" in n or "不完整" in n for n in m["notes"])


def test_complete_package_marks_sums_complete(tmp_path):
    m = _metrics(tmp_path)
    assert m["latency_ms"]["complete"] is True
    assert m["rounds"]["child_steps_complete"] is True


def test_report_surfaces_incomplete_sums(tmp_path):
    orphan = {"type": "subagent_start", "role": "worker", "step": 9,
              "agent_call_id": "worker@9#n9", "node_id": "n9", "plan_revision": 1}
    md = _report(tmp_path, extra_trace_events=[orphan])
    assert "下界" in md or "不完整" in md


# --- F4：脱敏必须递归到嵌套字段 ---

def test_redact_record_is_recursive():
    from agent.multi import redact_record

    rec = {"user_content": "sk-abcdefgh12345678",
           "plan": {"note": "key sk-zzzzzzzz99999999"},
           "extractor_events": [{"error": "api_key: nestedsecret1"}],
           "nested": {"deep": [{"x": "Bearer tok.abc"}]}}
    out = redact_record(rec)
    blob = json.dumps(out, ensure_ascii=False)
    for leaked in ("sk-abcdefgh12345678", "sk-zzzzzzzz99999999",
                   "nestedsecret1", "tok.abc"):
        assert leaked not in blob, f"嵌套字段泄漏: {leaked}"
    assert blob.count("***REDACTED***") >= 4


def test_redact_record_keeps_non_strings():
    from agent.multi import redact_record

    out = redact_record({"n": 1, "b": True, "none": None, "lst": [1, "sk-abcdefgh12345678"]})
    assert out["n"] == 1 and out["b"] is True and out["none"] is None
    assert out["lst"][0] == 1 and "sk-abcdefgh12345678" not in out["lst"][1]


# --- F6：报告的依据段必须覆盖 Worker，且 Reviewer 轨迹不得超额声称 ---

def test_worker_package_eval_exposes_top_level_basis(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_package

    out = evaluate_package(load_process_package(_write_run_package(tmp_path),
                                                case_id="LV-T01"))
    assert out["evidence_basis"], "报告要按角色汇总依据，Worker 不能没有顶层依据"
    assert "reference_integrity" in out["evidence_basis"]


def test_report_evidence_section_covers_all_three_roles(tmp_path):
    md = _report(tmp_path)
    section = md.split("## 依据与未知项")[1].split("## 字段兼容表")[0]
    for role in ("planner", "worker", "reviewer"):
        assert f"{role}." in section, f"依据段缺 {role}"


# --- F7：--gold 给了但绑不上，理由必须是真实的 ---

def test_gold_without_case_id_reports_binding_problem(tmp_path):
    run = _write_run_package(tmp_path)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"case_id": "LV-T01",
                                "scoring_points": [{"point_id": "p1",
                                                    "atomic_judgment": "x"}]},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    # 不传 --case-id → 无法绑定
    assert _cli(["roles", "--run-dir", str(run), "--mode", "trajectory",
                 "--gold", str(gold), "--out", str(out)]) == 0

    d = json.loads((out / "roles_trajectory.json").read_text(encoding="utf-8"))
    reason = d["roles"]["planner"]["coverage"]["reason"]
    assert d["gold_provided"] is True
    assert "--case-id" in reason or "绑定" in reason
    assert "gold 未就绪" not in reason        # 真实理由是绑不上，不是没有 gold


def test_gold_binds_when_case_id_given(tmp_path):
    run = _write_run_package(tmp_path)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"case_id": "LV-T01",
                                "scoring_points": [{"point_id": "p1",
                                                    "atomic_judgment": "x"}]},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    assert _cli(["roles", "--run-dir", str(run), "--case-id", "LV-T01",
                 "--mode", "trajectory", "--gold", str(gold), "--out", str(out)]) == 0

    d = json.loads((out / "roles_trajectory.json").read_text(encoding="utf-8"))
    assert d["roles"]["planner"]["coverage"]["status"] == "judged"


# --- F13：去重键在被转发时丢 parent 也要成立 ---

def test_dedupe_survives_missing_parent_call_id(tmp_path):
    dup = {"type": "tool_result", "role": "worker", "step": 0,
           "tool": "query_financial_data", "ok": True, "rid": "r2", "call_id": "call_w1",
           "args_head": "{}", "result_head": "x"}          # 没有 parent_call_id
    base = _metrics(tmp_path / "a")
    got = _metrics(tmp_path / "b", extra_trace_events=[dup])

    assert got["tool_calls"]["total"] == base["tool_calls"]["total"]
    assert got["tool_calls"]["deduped_duplicates"] == 1


# --- F14：缺 rows 键不能变成 0 行 ---

def test_fallback_result_rows_missing_is_none_not_zero(tmp_path):
    run = _write_run_package(tmp_path)
    results_path = run / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    for rec in results["results"]:
        rec.pop("rows", None)
    results_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    # 去掉查询事件，逼适配器走 results.json 退路
    trace_path = run / "trace.jsonl"
    keep = [json.loads(x) for x in
            trace_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    keep = [e for e in keep if e.get("tool") != "query_financial_data"]
    trace_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in keep) + "\n",
                          encoding="utf-8")

    from eval.agent_process.adapt import load_process_package

    pkg = load_process_package(run, case_id="LV-T01")
    assert pkg.query_records[0].total_rows is None
    assert pkg.query_records[0].columns == []


def test_worker_judged_dimensions_pipeline_check_with_fake_judge(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.common import make_judge
    from eval.agent_process.roles.worker import evaluate_node

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {"verdict": "met",
                                                           "reason": "意图一致"})
    out = evaluate_node(load_process_package(_write_run_package(tmp_path),
                                             case_id="LV-T01"), "n1", judge=judge)

    assert out["judged"]["query_intent"]["verdict"] == "met"
    assert out["evidence_basis"]["query_intent"] == "pipeline_check"


def test_unknown_node_id_is_reported_not_crashed(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.worker import evaluate_node

    out = evaluate_node(load_process_package(_write_run_package(tmp_path),
                                             case_id="LV-T01"), "n99")
    assert out["measured"] == {}
    assert out["attribution"]["cause"] == "unknown"


def test_worker_samples_cover_required_classes():
    from eval.agent_process.roles.common import load_samples

    samples = load_samples(WORKER_SAMPLES)
    assert len(samples) >= 6
    for s in samples:
        assert {"sample_id", "node", "artifact", "annotations"} <= set(s)
    causes = {s["annotations"]["cause"] for s in samples}
    assert set(_WORKER_CAUSES) <= causes, f"缺类别: {sorted(set(_WORKER_CAUSES) - causes)}"


def test_worker_evaluate_sample_runs():
    from eval.agent_process.roles.common import load_samples, make_judge
    from eval.agent_process.roles.worker import evaluate_sample

    sample = load_samples(WORKER_SAMPLES)[0]
    judge = make_judge("fake", point_fn=lambda q, p, f, a: {"verdict": "met",
                                                           "reason": "ok"})
    out = evaluate_sample(sample, judge)

    assert out["sample_id"] == sample["sample_id"]
    assert out["attribution"]["cause"] == sample["annotations"]["cause"]
    assert out["annotation"] == sample["annotations"]
    assert out["cause_matches_annotation"] is True


def test_worker_samples_reproduce_all_but_intent_error_offline():
    """离线模拟判下：可程序判定的归因必须全部复现；只有 intent_error 需要真实 Judge。

    这条测试把「本阶段拿不到」与「评测器判错」区分开，避免把前者当后者。
    """
    from eval.agent_process.roles.common import load_samples, make_judge
    from eval.agent_process.roles.worker import evaluate_sample

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {"verdict": "unknown",
                                                           "reason": "离线"})
    out = [evaluate_sample(s, judge) for s in load_samples(WORKER_SAMPLES)]

    mismatched = [o for o in out if not o["cause_matches_annotation"]]
    assert [o["sample_id"] for o in mismatched] == ["WK-02"]
    assert mismatched[0]["requires_live_judge"] is True
    assert mismatched[0]["expected_cause"] == "intent_error"
    assert mismatched[0]["attribution"]["cause"] == "unknown"   # 诚实降级，不猜
    assert len(out) - len(mismatched) >= 6


def test_intent_error_reachable_with_judge_contradicted():
    from eval.agent_process.roles.common import load_samples, make_judge
    from eval.agent_process.roles.worker import evaluate_sample

    judge = make_judge("fake", point_fn=lambda q, p, f, a: {"verdict": "contradicted",
                                                           "reason": "意图不符"})
    sample = next(s for s in load_samples(WORKER_SAMPLES) if s["sample_id"] == "WK-02")
    out = evaluate_sample(sample, judge)

    assert out["attribution"]["cause"] == "intent_error"
    assert out["cause_matches_annotation"] is True
    # 但仍然是 pipeline_check：模拟判不是真实模型判定
    assert out["evidence_basis"]["query_intent"] == "pipeline_check"


def test_samples_agreement_summary_reports_mismatch(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    assert _cli(["roles", "--run-dir", str(run), "--mode", "samples",
                 "--out", str(out)]) == 0

    d = json.loads((out / "roles_samples_worker.json").read_text(encoding="utf-8"))
    assert d["agreement"] == 6 and d["total"] == 7
    assert d["requires_live_judge"] == ["WK-02"]
    assert d["mismatched"] == ["WK-02"]


# ------------------------------------------------------------------ Task 9：Reviewer

REVIEWER_SAMPLES = Path("eval/agent_process/samples/reviewer.jsonl")


def test_precision_recall_on_annotated_sample():
    from eval.agent_process.roles.reviewer import score_against_annotations

    annotated = [{"category": "wrong_period", "statement": "用了 FY2023Q4"},
                 {"category": "omission", "statement": "漏了存货"},
                 {"category": "unit_error", "statement": "单位错"}]
    detected = [{"category": "wrong_period", "statement": "用了 FY2023Q4"},
                {"category": "omission", "statement": "漏了收入"}]
    out = score_against_annotations(detected, annotated)

    assert out["true_positive"] == 1
    assert out["false_positive"] == 1
    assert out["false_negative"] == 2
    assert out["precision"] == 0.5
    assert out["recall"] == pytest.approx(1 / 3)


def test_precision_none_when_nothing_detected():
    from eval.agent_process.roles.reviewer import score_against_annotations

    out = score_against_annotations([], [{"category": "omission", "statement": "漏了"}])
    # 一个都没检出：precision 无定义（不填 0 冒充算过），recall 确为 0
    assert out["precision"] is None
    assert out["recall"] == 0.0
    assert out["missed"][0]["category"] == "omission"


def test_clean_sample_false_positive_only():
    from eval.agent_process.roles.reviewer import score_against_annotations

    out = score_against_annotations([{"category": "omission", "statement": "其实没问题"}], [])
    assert out["true_positive"] == 0
    assert out["precision"] == 0.0
    assert out["recall"] is None       # 没有应检出项，recall 无定义
    assert out["unexpected"][0]["statement"] == "其实没问题"


def _reviewer_pkg(tmp_path, *, next_attempt=True, finding_status="open"):
    """默认包 + 可选的同节点后续产物（用于审查↔版本关联）。"""
    run = _write_run_package(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reviews"]["rev1"]["findings"][0]["response_status"] = finding_status
    if next_attempt:
        nxt = json.loads(json.dumps(state["artifacts"]["art1"]))
        nxt.update({"artifact_id": "art2", "attempt": 2, "summary": "已补基期"})
        state["artifacts"]["art2"] = nxt
        state["plans"]["1"]["nodes"][0]["latest_artifact_id"] = "art2"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return run


def _link(tmp_path, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import link_review_rounds

    return link_review_rounds(load_process_package(_reviewer_pkg(tmp_path, **kw),
                                                   case_id="LV-T01"))


def test_review_round_linked_to_next_version(tmp_path):
    out = _link(tmp_path)

    rnd = out["rounds"][0]
    assert rnd["review_id"] == "rev1"
    assert rnd["target_id"] == "art1"
    assert rnd["target_version"] == "rev1-attempt1"
    assert rnd["next_version"] == "rev1-attempt2"
    assert out["unlinkable"] == []


def test_finding_response_status_recorded_verbatim(tmp_path):
    out = _link(tmp_path, finding_status="addressed")
    rnd = out["rounds"][0]
    assert rnd["declared_response"] == {"rev1-f1": "addressed"}
    assert rnd["response_status_is_default"] is False


def test_open_finding_is_default_not_evidence(tmp_path):
    """open 是默认值：报告必须标明它不含信息。"""
    out = _link(tmp_path, finding_status="open")
    rnd = out["rounds"][0]
    assert rnd["declared_response"] == {"rev1-f1": "open"}
    assert rnd["response_status_is_default"] is True
    assert rnd["fix_verified"] is None


def test_declined_finding_recorded_not_concluded(tmp_path):
    out = _link(tmp_path, finding_status="declined")
    rnd = out["rounds"][0]
    assert rnd["declared_response"] == {"rev1-f1": "declined"}
    # 声明被驳回，不等于我们核验过它没被修复
    assert rnd["fix_verified"] is None


def test_unlinkable_review_reported_not_faked(tmp_path):
    """没有后续产物就无法判断是否修复：不伪造改进率。"""
    out = _link(tmp_path, next_attempt=False)

    assert out["rounds"][0]["next_version"] is None
    assert out["unlinkable"][0]["review_id"] == "rev1"
    blob = json.dumps(out, ensure_ascii=False)
    assert "improvement_rate" not in blob
    assert any("无法关联" in n or "不伪造" in n for n in out["notes"])


def test_duplicate_findings_deduped_with_rounds_kept(tmp_path):
    run = _reviewer_pkg(tmp_path)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    dup = json.loads(json.dumps(state["reviews"]["rev1"]))
    dup.update({"review_id": "rev2", "target_id": "art2",
                "target_version": "rev1-attempt2"})
    dup["findings"][0]["finding_id"] = "rev2-f1"
    state["reviews"]["rev2"] = dup
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import link_review_rounds

    out = link_review_rounds(load_process_package(run, case_id="LV-T01"))
    dupes = [d for d in out["deduped_findings"]
             if d["statement"] == "同比缺基期"]
    assert len(dupes) == 1
    assert len(dupes[0]["rounds"]) == 2          # 轮次保留，不丢
    assert dupes[0]["occurrences"] == 2


def test_reviewer_counts_include_severity_and_verdict(tmp_path):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import evaluate_package

    out = evaluate_package(load_process_package(_reviewer_pkg(tmp_path),
                                                case_id="LV-T01"))
    c = out["counts"]
    assert c["reviews"] == 1
    assert c["findings"] == 1
    assert c["must_fix"] == 1
    assert c["verdicts"] == {"needs_revision": 1}


def test_accepted_is_treated_as_tested_output(tmp_path):
    """accepted 是被测输出，不是标准答案：不据此推断问题已修复。"""
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.roles.reviewer import evaluate_package

    run = _reviewer_pkg(tmp_path, next_attempt=False)
    state_path = run / "analysis" / "analysis_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reviews"]["rev1"]["verdict"] = "accepted"
    state["reviews"]["rev1"]["findings"] = []
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    out = evaluate_package(load_process_package(run, case_id="LV-T01"))
    assert out["evidence_basis"]["verdict"] == "measured"
    assert "improvement_rate" not in json.dumps(out, ensure_ascii=False)
    assert any("accepted" in n and "不是标准答案" in n for n in out["notes"])


def test_reviewer_samples_cover_required_categories():
    """PRD §8.3 的七个场景由 scenario 字段表达，不能只看 annotated_issues。

    `alternative_algorithm_ok` 的正确行为是「不报问题」，它永远不该出现在标注问题里。
    """
    from eval.agent_process.roles.common import load_samples
    from eval.agent_process.roles.reviewer import REVIEW_CATEGORIES

    samples = load_samples(REVIEWER_SAMPLES)
    assert len(samples) >= 8
    scenarios = {s["scenario"] for s in samples}
    assert set(REVIEW_CATEGORIES) <= scenarios, \
        f"缺场景: {sorted(set(REVIEW_CATEGORIES) - scenarios)}"

    annotated = {i["category"] for s in samples for i in (s.get("annotated_issues") or [])}
    assert annotated <= set(REVIEW_CATEGORIES)
    assert "alternative_algorithm_ok" not in annotated, "它是「不算问题」场景，不该被标注为问题"

    clean = [s for s in samples if not (s.get("annotated_issues") or [])]
    assert len(clean) >= 2, "必须有「无错误样例」才能测误报"


def test_reviewer_evaluate_sample_runs():
    from eval.agent_process.roles.common import load_samples
    from eval.agent_process.roles.reviewer import evaluate_sample

    samples = load_samples(REVIEWER_SAMPLES)
    out = evaluate_sample(samples[0])

    assert out["sample_id"] == samples[0]["sample_id"]
    assert out["evidence_basis"]["precision_recall"] == "measured"
    assert out["evidence_basis"]["detection"] == "pipeline_check"
    assert out["annotation"] == samples[0]["annotations"]


# ------------------------------------------------------------------ Task 10：报告

def _bundle(pkg):
    from eval.agent_process.metrics import compute_metrics
    from eval.agent_process.query import diagnose_queries
    from eval.agent_process.roles import planner, reviewer, worker

    return {
        "metrics": compute_metrics(pkg),
        "roles": {"planner": planner.evaluate_package(pkg),
                  "worker": worker.evaluate_package(pkg),
                  "reviewer": reviewer.evaluate_package(pkg)},
        "queries": diagnose_queries(pkg),
    }


def _record(tmp_path, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.report import build_record

    pkg = load_process_package(_write_run_package(tmp_path, **kw), case_id="LV-T01")
    return build_record(pkg, **_bundle(pkg))


def _report(tmp_path, **kw):
    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.report import case_report_md

    e2e = kw.pop("e2e", None)
    pkg = load_process_package(_write_run_package(tmp_path, **kw), case_id="LV-T01")
    return case_report_md(pkg, e2e=e2e, **_bundle(pkg))


def test_case_report_has_required_sections(tmp_path):
    md = _report(tmp_path)

    for section in ("# 过程评测报告", "## 最终状态与答案", "## 运行监控",
                    "## 中观角色评测", "## 微观查询诊断", "## 依据与未知项",
                    "## 字段兼容表"):
        assert section in md, f"缺章节: {section}"


def test_case_report_shows_nodes_completed_and_final_undelivered(tmp_path):
    """PRD §9.3 回放验收：节点完成/审查 accepted 与「最终未交付」必须同时呈现。"""
    md = _report(tmp_path)

    assert "completed" in md
    assert "accepted" in md
    assert "最终未提交" in md or "未交付" in md
    assert "主循环轮数用尽" in md              # 终止原因
    assert "final_answer" in md               # 校验被打回
    assert "都不代表" in md or "不代表" in md


def test_case_report_labels_evidence_basis(tmp_path):
    md = _report(tmp_path)

    assert "pipeline_check" in md or "measured" in md
    assert "不是真实质量评分" in md


def test_case_report_no_single_total_score(tmp_path):
    """不得出现被拼出来的总分；同时必须**显式声明**不做拼分，而不是悄悄没有。"""
    md = _report(tmp_path)

    for bad in ("总分：", "总分:", "| 总分", "## 总分", "综合得分"):
        assert bad not in md, f"报告里出现了单一总分: {bad}"
    assert "不拼成单一总分" in md
    # 质量、监控、查询分区呈现
    assert "## 运行监控" in md and "## 中观角色评测" in md and "## 微观查询诊断" in md


def test_blocked_or_undelivered_not_dropped(tmp_path):
    from eval.agent_process.report import summary_report_md

    rec = _record(tmp_path)
    md = summary_report_md([rec], run_ids=[rec["run_id"]], dataset_name=None)

    assert "未交付" in md
    assert rec["case_id"] in md


def test_unknown_fields_section_lists_unknown_fields(tmp_path):
    md = _report(tmp_path)          # 默认包没有新埋点
    assert "analysis/query_trace.jsonl" in md
    assert "历史包" in md or "unknown" in md


def test_summary_groups_by_type_and_split(tmp_path):
    from eval.agent_process.report import summary_report_md

    a = _record(tmp_path / "a")
    a.update({"question_type": "归因", "split_group": "dev"})
    b = _record(tmp_path / "b")
    b.update({"case_id": "LV-T02", "question_type": "预测", "split_group": "holdout"})

    md = summary_report_md([a, b], run_ids=[a["run_id"]], dataset_name="demo")
    assert "归因" in md and "预测" in md
    assert "dev" in md and "holdout" in md


def test_summary_reports_sample_counts(tmp_path):
    from eval.agent_process.report import summary_report_md

    rec = _record(tmp_path)
    md = summary_report_md([rec], run_ids=[rec["run_id"]], dataset_name=None)

    for col in ("样本数", "交付数", "未交付数", "计数差异数"):
        assert col in md, f"缺列: {col}"
    # 列名必须如实：未交付含合法澄清/拒答，不能直接叫「失败数」；
    # 计数差异是自报 vs 重算，不是评分器错误
    assert "失败数" not in md
    assert "评分错误数" not in md


def test_join_with_e2e_results_by_case_and_run(tmp_path):
    from eval.agent_process.report import join_e2e

    rec = _record(tmp_path)
    e2e = [{"case_id": "LV-T01", "run_id": rec["run_id"],
            "result": {"aggregate": {"required_hit_rate": 0.75}}}]
    hit = join_e2e(rec, e2e)
    assert hit["result"]["aggregate"]["required_hit_rate"] == 0.75

    # run_id 不同的那条不该被错配
    assert join_e2e(rec, [{"case_id": "LV-T01", "run_id": "other"}]) is None


def test_case_report_shows_e2e_when_joined(tmp_path):
    e2e = {"case_id": "LV-T01", "run_id": "20261016-134035",
           "result": {"aggregate": {"required_hit_rate": 0.75,
                                    "required_total": 4, "required_met": 3}}}
    md = _report(tmp_path, e2e=e2e)

    assert "宏观" in md
    assert "0.75" in md or "75" in md


def test_case_report_marks_e2e_absent_honestly(tmp_path):
    md = _report(tmp_path, e2e=None)
    assert "未提供" in md


def test_compat_table_lists_unknown_legacy_fields():
    from eval.agent_process.report import compat_table_md

    md = compat_table_md()
    assert "unknown_legacy" in md
    assert "new_instrumentation" in md
    assert "query_trace_id" in md
    # 两套同名不同义的 id 必须在兼容表里说明
    assert "plan_digest" in md
    assert "同名不同义" in md or "内容寻址" in md


# ------------------------------------------------------------------ Task 11：CLI

def _cli(argv):
    from eval.agent_process.cli import main

    return main(["python -m eval.agent_process", *argv])


def test_parser_has_six_subcommands():
    from eval.agent_process.cli import build_parser

    sub = build_parser()._subparsers._group_actions[0]
    assert set(sub.choices) == {"adapt", "metrics", "roles", "queries", "report", "replay"}


def test_adapt_writes_json(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    rc = _cli(["adapt", "--run-dir", str(run), "--case-id", "LV-T01", "--out", str(out)])

    assert rc == 0
    data = json.loads((out / "adapt.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "20261016-134035"
    assert data["final_status"] == "refuse"


def test_metrics_writes_json(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    assert _cli(["metrics", "--run-dir", str(run), "--out", str(out)]) == 0

    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    for k in ("tool_calls", "role_calls", "tokens", "discrepancies"):
        assert k in m


def test_queries_writes_json(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    assert _cli(["queries", "--run-dir", str(run), "--out", str(out)]) == 0

    q = json.loads((out / "queries.json").read_text(encoding="utf-8"))
    assert "by_reason" in q and "records" in q


def test_roles_samples_mode_offline(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    rc = _cli(["roles", "--run-dir", str(run), "--mode", "samples", "--judge", "fake",
               "--out", str(out)])

    assert rc == 0
    for role in ("planner", "worker", "reviewer"):
        data = json.loads((out / f"roles_samples_{role}.json").read_text(encoding="utf-8"))
        assert data["samples"], f"{role} 样例结果为空"
        assert data["mode"] == "samples"
        assert data["judge"] == "fake"
        assert "pipeline_check" in json.dumps(data, ensure_ascii=False) or \
               "not_real_quality_score" in data


def test_roles_trajectory_mode(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    assert _cli(["roles", "--run-dir", str(run), "--mode", "trajectory",
                 "--judge", "fake", "--out", str(out)]) == 0

    data = json.loads((out / "roles_trajectory.json").read_text(encoding="utf-8"))
    assert set(data["roles"]) == {"planner", "worker", "reviewer"}
    assert data["judge"] == "fake"
    assert data["is_real_quality_score"] is False


def test_roles_single_role_selection(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "out"
    assert _cli(["roles", "--run-dir", str(run), "--mode", "trajectory",
                 "--role", "reviewer", "--out", str(out)]) == 0

    data = json.loads((out / "roles_trajectory.json").read_text(encoding="utf-8"))
    assert set(data["roles"]) == {"reviewer"}


def test_report_writes_markdown(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "r.md"
    assert _cli(["report", "--run-dir", str(run), "--case-id", "LV-T01",
                 "--out", str(out)]) == 0

    md = out.read_text(encoding="utf-8")
    assert "# 过程评测报告" in md
    assert "## 字段兼容表" in md


def test_replay_runs_all_steps(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "replay"
    assert _cli(["replay", "--run-dir", str(run), "--case-id", "LV-T01",
                 "--out-dir", str(out)]) == 0

    for name in ("adapt.json", "metrics.json", "queries.json", "roles_trajectory.json",
                 "roles_samples_planner.json", "report.md", "compat_table.md",
                 "results.jsonl"):
        assert (out / name).exists(), f"replay 缺产物: {name}"


def test_replay_report_shows_undelivered_and_completed(tmp_path):
    run = _write_run_package(tmp_path)
    out = tmp_path / "replay"
    _cli(["replay", "--run-dir", str(run), "--case-id", "LV-T01", "--out-dir", str(out)])

    md = (out / "report.md").read_text(encoding="utf-8")
    assert "completed" in md and "accepted" in md
    assert "不代表" in md


def test_live_judge_not_instantiated_by_default(tmp_path, monkeypatch):
    """默认离线：不得偷偷建 LLMClient。"""
    import agent.llm as llm_mod

    def _boom(*a, **k):
        raise AssertionError("默认路径不应实例化 LLMClient")

    monkeypatch.setattr(llm_mod, "LLMClient", _boom)
    run = _write_run_package(tmp_path)
    assert _cli(["roles", "--run-dir", str(run), "--mode", "both", "--judge", "fake",
                 "--out", str(tmp_path / "out")]) == 0


def test_missing_run_dir_fails_loudly(tmp_path):
    rc = _cli(["metrics", "--run-dir", str(tmp_path / "nope"), "--out", str(tmp_path / "o")])
    assert rc != 0


# ------------------------------------------------------------------ Task 12：真实包冒烟

def test_real_package_smoke():
    """对固定回放包 runs/cli-20261016-134035 的端到端断言。

    运行包本身在 .gitignore 里（runs/），缺失时跳过；数值来自 PRD §2 的实测记录。
    """
    if not REFERENCE_RUN.is_dir():
        pytest.skip(f"固定回放包不存在：{REFERENCE_RUN}（runs/ 不入库）")

    from eval.agent_process.adapt import load_process_package
    from eval.agent_process.metrics import compute_metrics
    from eval.agent_process.query import diagnose_queries

    pkg = load_process_package(REFERENCE_RUN)
    m = compute_metrics(pkg)

    # 终态：未交付，且不因节点 completed / 审查 accepted 改记成功
    assert pkg.final_status == "refuse"
    assert pkg.final_answer is None
    assert pkg.verified is False

    # 与 run_meta 自报值一致（重算不上报假差异）
    assert m["role_calls"]["by_role"] == {"planner": 1, "reviewer": 7, "worker": 5}
    assert m["role_calls"]["total"] == 13
    assert m["tokens"]["prompt_tokens"] == 383994
    assert m["tokens"]["completion_tokens"] == 22438
    assert m["rounds"]["subagent_child_steps"] == 52
    assert m["discrepancies"] == []

    # 同计划复用 rid：11 次调用只有 9 个结果 → 必须按调用计
    q = diagnose_queries(pkg)
    assert q["calls"] == 11
    assert m["queries"]["unique_result_ids"] == 9
    assert m["queries"]["rid_reuse_calls"] == 2
    assert q["by_reason"]

    # 历史包缺新埋点：记 unknown，不填 0
    assert "analysis/query_trace.jsonl" in pkg.unknown_fields
    assert "analysis/visible_context.jsonl" in pkg.unknown_fields
    assert all(r.query_trace_id is None for r in pkg.query_records)
    assert m["extraction"]["attempts_seen"] is None
    for rec in pkg.query_records:
        assert rec.sql_chars is not None      # 可从 results.json 真测到的必须照实填
        assert rec.linkage == "tool_result_parent"
        assert rec.node_id                          # 老包也能做节点归属

    # 跳过不进成功
    assert m["tool_calls"]["skipped"] == 1
    assert (m["tool_calls"]["ok"] + m["tool_calls"]["error"]
            + m["tool_calls"]["skipped"]
            + m["tool_calls"]["unknown_status"]) == m["tool_calls"]["total"]

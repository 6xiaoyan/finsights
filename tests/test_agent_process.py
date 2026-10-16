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
        "extraction_calls": len(specs),
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
                "attempts": 1,
                "extractor_events": [{"type": "extractor_call", "attempt": 1,
                                      "status": "parsed"}],
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

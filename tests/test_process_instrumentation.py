"""agent/ 侧新增埋点的测试（Task 3–5）。

硬约束：埋点只能观测。这里用「同一输入下工具返回文本逐字相同」「模型收到的 messages
逐字相同」来钉住这一点，而不是靠人工检查。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.query.catalog import BusinessCatalog
from agent.result_store import ResultStore
from agent.tools import data
from agent.tools.data import ToolContext, execute, make_data_version

OK_PLAN = {
    "status": "ok", "issues": [],
    "plan": {"metrics": ["inventory"], "companies": ["Lenovo"], "period_basis": "fiscal",
             "periods": ["FY2024Q1", "FY2024Q2"], "group_by": [], "filters": [],
             "comparisons": [{"current": "FY2024Q2", "baseline": "FY2024Q1",
                              "operation": "difference"}], "sort": None},
}

BAD_PLAN = {"status": "unknown_value",
            "issues": [{"code": "unknown_metric", "message": "目录里没有 foo"}],
            "plan": None}


class MockLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, role="agent", temperature=None, **kw):
        self.calls.append({"messages": messages, "tools": tools, "role": role})
        return self.replies.pop(0)


def _resp(payload):
    return SimpleNamespace(content="```json\n" + json.dumps(payload, ensure_ascii=False)
                                   + "\n```", tool_calls=[], usage={})


def _tool_call_resp(name, args, call_id="call_q1"):
    tc = SimpleNamespace(id=call_id, name=name, arguments=json.dumps(args, ensure_ascii=False))
    return SimpleNamespace(content="", tool_calls=[tc], usage={})


def _ctx(query_db, metrics_path, llm, *, enabled=("query_financial_data",),
         backend="text2sql"):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    ctx = ToolContext(db_path=query_db,
                      store=ResultStore(data_version=make_data_version(query_db)),
                      enabled_tools=list(enabled), llm=llm, catalog=cat,
                      query_backend=backend)
    # SubAgentRunner 从 parent.tool_ctx 取 catalog/query_cfg；自指即可
    ctx.tool_ctx = ctx
    return ctx


# ------------------------------------------------------------- Task 3：Text2SQL 埋点

def test_query_call_records_trace_entry(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN)]))
    out = execute("query_financial_data", {"question": "联想两季存货差额",
                                          "node_task": "取存货"}, ctx)

    log = ctx.stats["query_trace"]
    assert len(log) == 1
    rec = log[0]
    assert rec["query_trace_id"] == "q1"
    assert rec["call_index"] == 0
    assert rec["question"] == "联想两季存货差额"
    assert rec["node_task"] == "取存货"
    assert rec["status"] == "ok"
    assert rec["plan"]["metrics"] == ["inventory"]
    assert rec["result_ids"] == [out.rid]
    assert rec["sql_chars"] and rec["sql_chars"] > 0
    assert rec["catalog_version"] == ctx.catalog.catalog_version()
    assert rec["data_version"] == ctx.store.data_version
    assert rec["prompt_version"]
    assert rec["total_rows"] is not None


def test_outcome_carries_query_trace_id(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN)]))
    out = execute("query_financial_data", {"question": "q"}, ctx)
    assert out.query_trace_id == "q1"


def test_second_call_gets_new_query_trace_id(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN), _resp(OK_PLAN)]))
    o1 = execute("query_financial_data", {"question": "第一次"}, ctx)
    o2 = execute("query_financial_data", {"question": "第二次"}, ctx)

    assert (o1.query_trace_id, o2.query_trace_id) == ("q1", "q2")
    assert [r["call_index"] for r in ctx.stats["query_trace"]] == [0, 1]
    assert [r["question"] for r in ctx.stats["query_trace"]] == ["第一次", "第二次"]


def test_outcome_text_unchanged_by_instrumentation(query_db, metrics_path, monkeypatch):
    """埋点不得改变工具返回文本：文本必须与直接渲染同一结果逐字相同。"""
    from agent.query.executor import QueryToolResult

    fixed = QueryToolResult(status="ok", result_ids=["r9"], columns=["company", "value"],
                            rows=[{"company": "Lenovo", "value": 1.0}],
                            total_rows=1, truncated=False,
                            provenance={"data_version": "dv", "catalog_version": "cv",
                                        "prompt_version": "pv", "query_trace_id": "abc"})
    ctx = _ctx(query_db, metrics_path, MockLLM([]))

    import agent.query.executor as ex
    import agent.query.extractor as extr
    from agent.query.schema import ExtractionResult, QueryPlan
    plan = QueryPlan(**OK_PLAN["plan"])
    monkeypatch.setattr(extr, "extract_plan",
                        lambda *a, **k: (ExtractionResult(status="ok", issues=[], plan=plan),
                                         [{"type": "extractor_result", "status": "ok"}]))
    monkeypatch.setattr(ex, "execute_plan", lambda *a, **k: fixed)

    out = execute("query_financial_data", {"question": "q"}, ctx)

    assert out.text == data._render_query_result(fixed, ctx)


def test_non_ok_extraction_records_status_and_no_result_ids(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(BAD_PLAN)]))
    out = execute("query_financial_data", {"question": "查 foo"}, ctx)

    rec = ctx.stats["query_trace"][0]
    assert rec["status"] == "unknown_value"
    assert rec["issue_codes"] == ["unknown_metric"]
    assert rec["plan"] is None
    assert rec["result_ids"] == []
    assert rec["sql_chars"] is None          # 执行器没产生 SQL 时不伪造
    assert out.ok is False and out.rid is None


def test_empty_result_records_no_result_ids(query_db, metrics_path):
    """empty 是 ok=True 但无 rid —— 埋点必须照实记，不能等同取到数。"""
    plan = json.loads(json.dumps(OK_PLAN))
    plan["plan"]["companies"] = ["Dell"]
    plan["plan"]["comparisons"] = []
    plan["plan"]["periods"] = ["FY2024Q2"]
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(plan)]))
    out = execute("query_financial_data", {"question": "Dell 存货"}, ctx)

    rec = ctx.stats["query_trace"][0]
    assert rec["status"] == "empty"
    assert rec["result_ids"] == []
    assert out.ok is True and out.rid is None


def test_extractor_events_attached_to_record(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN)]))
    execute("query_financial_data", {"question": "q"}, ctx)

    rec = ctx.stats["query_trace"][0]
    kinds = [e["type"] for e in rec["extractor_events"]]
    assert "extractor_call" in kinds
    assert rec["attempts"] == 1
    assert rec["prompt_version"] == next(
        e["prompt_version"] for e in rec["extractor_events"] if e["type"] == "extractor_call")


def test_format_repair_counted_as_two_attempts(query_db, metrics_path):
    """首次响应非法 JSON → 修复一次 → attempts=2，与 PRD 的格式修复计数一致。"""
    bad = SimpleNamespace(content="不是 JSON", tool_calls=[], usage={})
    ctx = _ctx(query_db, metrics_path, MockLLM([bad, _resp(OK_PLAN)]))
    execute("query_financial_data", {"question": "q"}, ctx)

    rec = ctx.stats["query_trace"][0]
    assert rec["attempts"] == 2
    statuses = [e.get("status") for e in rec["extractor_events"]
                if e["type"] == "extractor_call"]
    assert statuses == ["invalid", "parsed"]


def test_other_tools_do_not_gain_query_trace_id(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([]), enabled=("list_metrics",))
    out = execute("list_metrics", {}, ctx)

    assert out.query_trace_id is None
    assert ctx.stats.get("query_trace", []) == []


def test_child_tool_result_event_has_query_trace_id(query_db, metrics_path):
    """Worker 子循环里的查询事件带调用级 id，主循环转发后仍可关联。"""
    from agent.subagents import SubAgentRunner

    # 顺序：子 Agent 请求工具 → 提取器要一次模型调用 → 子 Agent 收尾
    llm = MockLLM([_tool_call_resp("query_financial_data",
                                   {"question": "联想两季存货差额", "node_task": "取存货"}),
                   _resp(OK_PLAN),
                   _resp({"facts": [], "summary": "done"})])
    ctx = _ctx(query_db, metrics_path, llm)
    runner = SubAgentRunner(llm, ctx, "worker", "你是 Worker", max_steps=4)
    r = runner.run("取联想两季存货")

    tool_events = [e for e in r.events
                   if e["type"] == "tool_result" and e["tool"] == "query_financial_data"]
    assert len(tool_events) == 1
    assert tool_events[0]["query_trace_id"] == "q1"
    assert tool_events[0]["rid"] is not None


def test_child_event_for_other_tools_has_no_query_trace_id(query_db, metrics_path):
    from agent.subagents import SubAgentRunner

    llm = MockLLM([_tool_call_resp("list_metrics", {}),
                   _resp({"facts": [], "summary": "done"})])
    ctx = _ctx(query_db, metrics_path, llm, enabled=("list_metrics", "query_financial_data"))
    runner = SubAgentRunner(llm, ctx, "worker", "你是 Worker", max_steps=4)
    r = runner.run("看看有哪些指标")

    ev = next(e for e in r.events if e["type"] == "tool_result")
    assert "query_trace_id" not in ev


def test_child_context_shares_parent_stats(query_db, metrics_path):
    """子上下文与父共享同一 stats 字典，埋点才不会丢。"""
    from agent.subagents import SubAgentRunner

    llm = MockLLM([_tool_call_resp("query_financial_data", {"question": "联想存货"}),
                   _resp(OK_PLAN),
                   _resp({"facts": [], "summary": "done"})])
    ctx = _ctx(query_db, metrics_path, llm)
    runner = SubAgentRunner(llm, ctx, "worker", "你是 Worker", max_steps=4)
    runner.run("取数")

    assert runner.child.stats is ctx.stats
    assert len(ctx.stats["query_trace"]) == 1


# ------------------------------------------------------------- Task 4：可见上下文埋点

class FakeTrace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


def _run_ctx(query_db, metrics_path, llm):
    """带 trace 的运行上下文：形状对齐 agent.multi.run_multi 里的 ctx。"""
    ctx = _ctx(query_db, metrics_path, llm)
    ctx.trace = FakeTrace()
    ctx.stats = {"prompt_tokens": 0, "completion_tokens": 0}
    return ctx


def test_subagent_context_event_emitted_per_role_call(query_db, metrics_path):
    from agent import multi

    llm = MockLLM([_resp({"revision_reason": "初始", "nodes": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)

    multi._run_role("planner", "你是 Planner", "TaskContract：{...}", llm, ctx, 1, 2)

    starts = [e for e in ctx.trace.events if e["type"] == "subagent_start"]
    ctxs = [e for e in ctx.trace.events if e["type"] == "subagent_context"]
    assert len(ctxs) == 1
    assert ctxs[0]["role"] == "planner"
    assert ctxs[0]["agent_call_id"] == starts[0]["agent_call_id"]
    assert ctxs[0]["system_prompt_sha256_12"]


def test_subagent_context_event_per_role_carries_ident(query_db, metrics_path):
    from agent import multi

    llm = MockLLM([_resp({"verdict": "accepted", "findings": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)
    # Reviewer 实际收到的正文形如 multi._tool_review 拼的那段
    user = ("审查对象（artifact，art1，版本 rev1-attempt1）完整内容摘要：\n"
            "facts: [{rid: r2, ...}]")

    multi._run_role("reviewer", "你是 Reviewer", user, llm, ctx, 6, 2,
                    target_type="artifact", target_id="art1",
                    target_version="rev1-attempt1")

    ev = next(e for e in ctx.trace.events if e["type"] == "subagent_context")
    assert ev["target_type"] == "artifact"
    assert ev["target_id"] == "art1"
    assert ev["target_version"] == "rev1-attempt1"
    refs = ev["context_refs"]
    # ident 派生的显式审查对象
    assert refs["target_id"] == "art1"
    assert refs["target_version"] == "rev1-attempt1"
    # 正文派生的「这次看得见哪些上游产物」
    assert refs["artifact_ids"] == ["art1"]
    assert refs["rids"] == ["r2"]


def test_context_refs_extract_rids_and_artifacts():
    from agent.multi import _context_refs

    refs = _context_refs("看 r2 和 r15，还有 art3、answer-cand7，节点 n1 与 n2", {})

    assert refs["rids"] == ["r2", "r15"]
    assert refs["artifact_ids"] == ["art3"]
    assert refs["answer_candidate_ids"] == ["answer-cand7"]
    assert refs["node_ids"] == ["n1", "n2"]


def test_context_refs_dedupe_preserves_first_occurrence():
    from agent.multi import _context_refs

    refs = _context_refs("r2 先出现，r15 其次，r2 又来一次", {})
    assert refs["rids"] == ["r2", "r15"]


def test_context_refs_do_not_match_node_ids_inside_words():
    from agent.multi import _context_refs

    # "plan1" 里的 "n1" 不算节点；"attempt1" 同理
    refs = _context_refs("plan1 与 attempt1 里的 n1 才算", {})
    assert refs["node_ids"] == ["n1"]


def test_context_refs_carry_contract_data_version():
    from agent.multi import _context_refs

    refs = _context_refs('TaskContract：{"data_version": "2633728-abc"}', {})
    assert refs["contract_data_version"] == "2633728-abc"


def test_redact_masks_secrets():
    from agent.multi import _redact_text

    out = _redact_text('key sk-abcdefgh12345678 and Bearer abc.def\napi_key: secret123')

    assert "sk-abcdefgh12345678" not in out
    assert "abc.def" not in out
    assert "secret123" not in out
    assert "***REDACTED***" in out


def test_redact_leaves_normal_financial_text_alone():
    from agent.multi import _redact_text

    text = "联想 FY2024Q2 存货 100.5 百万美元，同比上升 12%。rid r2 来自 art1。"
    assert _redact_text(text) == text


def test_context_event_does_not_change_model_input(query_db, metrics_path):
    """埋点不得改变模型输入：messages 必须恰好是 [system=sys_prompt, user=...]。"""
    from agent import multi

    llm = MockLLM([_resp({"revision_reason": "初始", "nodes": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)
    user = "TaskContract：{...}\n当前状态：{...}"

    multi._run_role("planner", "你是 Planner", user, llm, ctx, 1, 2)

    sent = llm.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "你是 Planner"}
    assert sent[1] == {"role": "user", "content": user}


def test_context_event_preview_is_bounded(query_db, metrics_path):
    from agent import multi

    llm = MockLLM([_resp({"revision_reason": "初始", "nodes": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)
    long_user = "x" * 5000

    multi._run_role("planner", "你是 Planner", long_user, llm, ctx, 1, 2)

    ev = next(e for e in ctx.trace.events if e["type"] == "subagent_context")
    assert len(ev["user_preview"]) <= 1200
    assert ev["user_truncated"] is False      # 5000 < 64000，整份仍留存
    assert ev["user_chars"] == 5000


def test_visible_context_stats_holds_full_redacted_content(query_db, metrics_path):
    """整份实际内容只存 stats（导出处），trace 事件只带预览，两处不重复。"""
    from agent import multi

    llm = MockLLM([_resp({"revision_reason": "初始", "nodes": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)
    user = "TaskContract：{...}\n密钥 sk-abcdefgh12345678 不应落盘"

    multi._run_role("planner", "你是 Planner", user, llm, ctx, 1, 2)

    recs = ctx.stats["visible_context"]
    assert len(recs) == 1
    assert "sk-abcdefgh12345678" not in recs[0]["user_content"]
    # user_chars 是模型实际收到的长度；user_content_chars 是落盘长度（脱敏后）
    assert recs[0]["user_chars"] == len(user)
    assert recs[0]["user_content_chars"] == len(recs[0]["user_content"])
    ev = next(e for e in ctx.trace.events if e["type"] == "subagent_context")
    assert len(ev["user_preview"]) <= 1200
    assert "user_content" not in ev          # 不在 trace 里再存一份


def test_visible_context_truncates_very_long_content(query_db, metrics_path):
    from agent import multi

    llm = MockLLM([_resp({"revision_reason": "初始", "nodes": []})])
    ctx = _run_ctx(query_db, metrics_path, llm)

    multi._run_role("planner", "你是 Planner", "y" * 70000, llm, ctx, 1, 2)

    rec = ctx.stats["visible_context"][0]
    assert rec["user_truncated"] is True
    assert rec["user_chars"] == 70000            # 模型确实收到了这么多
    assert rec["user_content_chars"] == 64000    # 但落盘只留这么多
    assert len(rec["user_content"]) == 64000


def test_visible_context_marks_resumed_calls(query_db, metrics_path):
    """续接调用时模型还看得到历史 messages，记录必须说明它是片面的。"""
    from agent import multi

    llm = MockLLM([_resp({"facts": [], "summary": "done"})])
    ctx = _run_ctx(query_db, metrics_path, llm)
    prior = [{"role": "system", "content": "你是 Worker"},
             {"role": "user", "content": "旧任务"}, {"role": "assistant", "content": "旧回复"}]

    multi._run_role("worker", "你是 Worker", "继续", llm, ctx, 3, 2,
                    resume_messages=prior, node_id="n1", plan_revision=1, attempt=2)

    rec = ctx.stats["visible_context"][0]
    assert rec["resumed"] is True
    assert rec["resume_message_count"] == 3
    ev = next(e for e in ctx.trace.events if e["type"] == "subagent_context")
    assert ev["resumed"] is True


def test_system_prompt_hash_matches_run_package_convention():
    """system_prompt_sha256_12 必须与 run_meta.role_prompt_sha256_12 同一算法。"""
    import hashlib
    from pathlib import Path

    text = Path("agent/prompts/multi/planner.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    # 该值来自 runs/cli-20261016-134035 的 run_meta.json
    assert digest == "ce0422809738"


# ------------------------------------------------------------- Task 5：运行包导出

_QUERY_REC = {
    "query_trace_id": "q1", "call_index": 0, "step": 0,
    "question": "联想 FY2024Q2 存货", "node_task": "取存货",
    "status": "ok", "issue_codes": [], "plan": {"metrics": ["inventory"]},
    "plan_digest": "9e3ef01addb3", "attempts": 1,
    "extractor_events": [{"type": "extractor_call", "attempt": 1, "status": "parsed"}],
    "prompt_version": "pv", "catalog_version": "cv", "data_version": "dv",
    "result_ids": ["r2"], "sql_chars": 48, "total_rows": 1, "truncated": False,
}

_VISIBLE_REC = {
    "agent_call_id": "worker@2#n1", "role": "worker", "main_step": 2,
    "plan_revision": 1, "node_id": "n1", "target_id": None, "target_version": None,
    "system_prompt_sha256_12": "ce20181ce3e2",
    "user_content": "任务目标：取存货（密钥 sk-abcdefgh12345678 已脱敏）",
    "user_chars": 40, "user_content_chars": 40, "user_truncated": False,
    "resumed": False, "resume_message_count": 0,
}


def _export(query_db, tmp_path, *, queries=(), visible=(), agent_mode="multi_agent"):
    """直接调 _export_package：只关心导出这一环。"""
    from agent.cli import _export_package
    from agent.loop import CONFIG_PATH, make_run_ctx

    ctx = make_run_ctx(str(query_db), llm=None, run_id="t1",
                       config_path=CONFIG_PATH, enabled_tools=["query_financial_data"])
    ctx.agent_mode = agent_mode
    ctx.query_backend = "text2sql"
    ctx.tool_ctx.stats["query_trace"] = list(queries)
    ctx.stats["visible_context"] = list(visible)

    out_dir = tmp_path / "cli-t1"
    client = SimpleNamespace(model="mock-model")
    out = SimpleNamespace(answer=SimpleNamespace(status="answered", answer_md="答案正文",
                                                 claims=[]), verified=True)
    info = {"trace": [{"type": "assistant", "step": 1, "content": "hi"}]}
    meta = _export_package(out_dir, "联想存货如何？", None, Path(query_db), client,
                           str(CONFIG_PATH), ctx, out, info, "t1")
    return out_dir, meta


def test_export_writes_query_trace_jsonl(query_db, tmp_path):
    recs = [dict(_QUERY_REC), dict(_QUERY_REC, query_trace_id="q2", call_index=1)]
    out_dir, _ = _export(query_db, tmp_path, queries=recs)

    path = out_dir / "analysis" / "query_trace.jsonl"
    assert path.exists()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["query_trace_id"] == "q1"
    assert lines[1]["result_ids"] == ["r2"]


def test_export_writes_visible_context_jsonl(query_db, tmp_path):
    out_dir, _ = _export(query_db, tmp_path, visible=[dict(_VISIBLE_REC)])

    path = out_dir / "analysis" / "visible_context.jsonl"
    assert path.exists()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    assert lines[0]["agent_call_id"] == "worker@2#n1"
    assert lines[0]["user_content"]
    # 整份内容只在这里存一次：不要再塞一份预览
    assert "user_preview" not in lines[0]


def test_exported_visible_context_is_redacted(query_db, tmp_path):
    rec = dict(_VISIBLE_REC,
               user_content='api_key: secret123 与 sk-abcdefgh12345678 都不该落盘')
    out_dir, _ = _export(query_db, tmp_path, visible=[rec])

    text = (out_dir / "analysis" / "visible_context.jsonl").read_text(encoding="utf-8")
    assert "sk-abcdefgh12345678" not in text
    assert "secret123" not in text
    assert "***REDACTED***" in text


def test_run_meta_records_new_counts(query_db, tmp_path):
    recs = [dict(_QUERY_REC), dict(_QUERY_REC, query_trace_id="q2")]
    _, meta = _export(query_db, tmp_path, queries=recs, visible=[dict(_VISIBLE_REC)])

    assert meta["query_trace_calls"] == 2
    assert meta["visible_context_calls"] == 1


def test_process_files_lists_new_files(query_db, tmp_path):
    _, meta = _export(query_db, tmp_path, queries=[dict(_QUERY_REC)],
                      visible=[dict(_VISIBLE_REC)])

    files = meta["multi_agent"]["process_files"]
    assert "query_trace.jsonl" in files
    assert "visible_context.jsonl" in files


def test_export_omits_files_when_no_data(query_db, tmp_path):
    """没有数据就不写空文件；但计数字段要写 0 —— 0 表示「确实没有」，不是「没测」。"""
    out_dir, meta = _export(query_db, tmp_path)

    assert not (out_dir / "analysis" / "query_trace.jsonl").exists()
    assert not (out_dir / "analysis" / "visible_context.jsonl").exists()
    assert meta["query_trace_calls"] == 0
    assert meta["visible_context_calls"] == 0


def test_query_log_read_from_tool_ctx_and_context_log_from_run_ctx(query_db, tmp_path):
    """两处 stats 是不同字典：查询在 tool_ctx.stats，可见上下文在 run ctx.stats。

    读错字典会静默丢数据，所以把这条钉住。
    """
    from agent.cli import _export_package
    from agent.loop import CONFIG_PATH, make_run_ctx

    ctx = make_run_ctx(str(query_db), llm=None, run_id="t2", config_path=CONFIG_PATH)
    ctx.agent_mode = "multi_agent"
    ctx.tool_ctx.stats["query_trace"] = [dict(_QUERY_REC)]
    ctx.stats["visible_context"] = [dict(_VISIBLE_REC)]
    out_dir = tmp_path / "cli-t2"
    out = SimpleNamespace(answer=SimpleNamespace(status="answered", answer_md="a",
                                                 claims=[]), verified=True)
    _export_package(out_dir, "q", None, Path(query_db), SimpleNamespace(model="m"),
                    str(CONFIG_PATH), ctx, out, {"trace": []}, "t2")
    (out_dir / "analysis" / "query_trace.jsonl").read_text(encoding="utf-8")
    (out_dir / "analysis" / "visible_context.jsonl").read_text(encoding="utf-8")

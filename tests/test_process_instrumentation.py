"""agent/ 侧新增埋点的测试（Task 3–5）。

硬约束：埋点只能观测。这里用「同一输入下工具返回文本逐字相同」「模型收到的 messages
逐字相同」来钉住这一点，而不是靠人工检查。
"""
from __future__ import annotations

import json
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

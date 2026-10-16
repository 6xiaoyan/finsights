"""Task 5：query_financial_data 工具注册与分发。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.query.catalog import BusinessCatalog
from agent.result_store import ResultStore
from agent.tools import base, data
from agent.tools.data import ToolContext, execute, make_data_version

OK_PLAN = {
    "status": "ok", "issues": [],
    "plan": {"metrics": ["inventory"], "companies": ["Lenovo"], "period_basis": "fiscal",
             "periods": ["FY2024Q1", "FY2024Q2"], "group_by": [], "filters": [],
             "comparisons": [{"current": "FY2024Q2", "baseline": "FY2024Q1",
                              "operation": "difference"}], "sort": None},
}


class MockLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.temps = []

    def chat(self, messages, tools=None, role="agent", temperature=None):
        self.calls.append(messages)
        self.temps.append(temperature)
        return self.replies.pop(0)


def _resp(payload):
    text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    return SimpleNamespace(content=text, tool_calls=[], usage={})


def _ctx(query_db, metrics_path, llm, enabled=("query_financial_data",)):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    return ToolContext(db_path=query_db, store=ResultStore(data_version=make_data_version(query_db)),
                       enabled_tools=list(enabled), llm=llm, catalog=cat)


def test_tool_registered():
    assert "query_financial_data" in base.ARGS_MODELS
    assert "query_financial_data" in base.DESCRIPTIONS
    assert "query_financial_data" in data._HANDLERS
    assert "query_financial_data" not in base.STORED_TOOLS


def test_tool_requires_question():
    with pytest.raises(Exception):
        base.validate_args("query_financial_data", {})


def test_tool_disabled_when_not_in_enabled_tools(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([]), enabled=("list_metrics",))
    out = execute("query_financial_data", {"question": "x"}, ctx)
    assert out.ok is False


def test_tool_runs_with_mock_llm(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN)]))
    out = execute("query_financial_data", {"question": "联想两季存货差额"}, ctx)
    assert out.ok is True and out.rid is not None
    assert isinstance(out.text, str) and out.rid.startswith("r")


def test_non_ok_status_no_rid(query_db, metrics_path):
    bad = {"status": "unknown_value", "issues": [{"code": "unknown_metric", "message": "foo"}],
           "plan": None}
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(bad)]))
    out = execute("query_financial_data", {"question": "查 foo"}, ctx)
    assert out.ok is False and out.rid is None


def test_empty_status_is_ok_without_rid(query_db, metrics_path):
    plan = json.loads(json.dumps(OK_PLAN))
    plan["plan"]["companies"] = ["Dell"]
    plan["plan"]["metrics"] = ["inventory"]
    plan["plan"]["comparisons"] = []
    plan["plan"]["periods"] = ["FY2024Q2"]
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(plan)]))
    out = execute("query_financial_data", {"question": "Dell 存货"}, ctx)
    assert out.ok is True and out.rid is None


def test_extraction_calls_counted(query_db, metrics_path):
    ctx = _ctx(query_db, metrics_path, MockLLM([_resp(OK_PLAN)]))
    execute("query_financial_data", {"question": "q"}, ctx)
    assert ctx.stats.get("extraction_calls") == 1


def test_query_cfg_temperature_forwarded(query_db, metrics_path):
    llm = MockLLM([_resp(OK_PLAN)])
    ctx = _ctx(query_db, metrics_path, llm)
    ctx.query_cfg = {"extraction_temperature": 0.0, "max_format_repairs": 1}
    execute("query_financial_data", {"question": "q"}, ctx)
    assert llm.temps == [0.0]

"""Task 3：提取提示词与提取器（固定模型响应，不联网）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent.query.catalog import BusinessCatalog
from agent.query.extractor import extract_plan
from agent.query.prompt import PROMPT_VERSION, build_extractor_prompt

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

    def chat(self, messages, tools=None, role="agent", temperature=None):
        self.calls.append({"messages": messages, "tools": tools, "role": role})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(payload):
    text = payload if isinstance(payload, str) else "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    return SimpleNamespace(content=text, tool_calls=[],
                           usage={"prompt_tokens": 100, "completion_tokens": 10})


def _cat(query_db, metrics_path):
    return BusinessCatalog.build(query_db, metrics_path=metrics_path)


def test_ok_plan_parsed(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    llm = MockLLM([_resp(OK_PLAN)])
    result, events = extract_plan("联想 FY2024Q2 存货", "取 Lenovo 存货两季", cat, llm)
    assert result.status == "ok" and result.plan is not None
    assert result.plan.metrics == ["inventory"]
    assert any(e["type"] == "extractor_call" for e in events)


def test_non_ok_forces_null_plan(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    bad = {"status": "needs_clarification", "issues": ["ambiguous_period"], "plan": OK_PLAN["plan"]}
    llm = MockLLM([_resp(bad)])
    result, _ = extract_plan("某公司某季度", "取数", cat, llm)
    assert result.status == "needs_clarification" and result.plan is None
    assert len(llm.calls) == 1  # 业务歧义不重试


def test_unknown_metric_is_unknown_value(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    bad = json.loads(json.dumps(OK_PLAN))
    bad["plan"]["metrics"] = ["foo"]
    llm = MockLLM([_resp(bad)])
    result, _ = extract_plan("查 foo", "取 foo", cat, llm)
    assert result.status == "unknown_value" and result.plan is None
    assert any(i.code == "unknown_metric" for i in result.issues)


def test_conflict_returns_clarification(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    clar = {"status": "needs_clarification", "issues": ["conflicting_period"], "plan": None}
    llm = MockLLM([_resp(clar)])
    result, _ = extract_plan("FY2024Q2", "FY2023Q4", cat, llm)
    assert result.status == "needs_clarification" and result.plan is None


def test_format_error_repaired_once(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    llm = MockLLM([_resp("这不是 JSON"), _resp(OK_PLAN)])
    result, events = extract_plan("q", "t", cat, llm)
    assert result.status == "ok"
    assert len(llm.calls) == 2
    assert any(e.get("attempt") == 2 for e in events)


def test_invalid_json_twice_gives_unsupported(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    llm = MockLLM([_resp("nope"), _resp("still nope")])
    result, _ = extract_plan("q", "t", cat, llm)
    assert result.status == "unsupported" and result.plan is None
    assert len(llm.calls) == 2


def test_prompt_mentions_catalog_and_version(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    s = build_extractor_prompt(cat)
    assert "inventory" in s and "Lenovo" in s
    assert isinstance(PROMPT_VERSION, str) and len(PROMPT_VERSION) >= 8


def test_zero_repairs_does_not_retry(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    llm = MockLLM([_resp("这不是 JSON")])
    result, _ = extract_plan("q", "t", cat, llm, max_format_repairs=0)
    assert result.status == "unsupported" and len(llm.calls) == 1


def test_temperature_forwarded_to_model(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    llm = MockLLM([_resp(OK_PLAN)])
    captured = {}
    orig = llm.chat

    def spy(messages, tools=None, role="agent", temperature=None):
        captured["temperature"] = temperature
        return orig(messages, tools=tools, role=role, temperature=temperature)

    llm.chat = spy
    extract_plan("q", "t", cat, llm, temperature=0.0)
    assert captured["temperature"] == 0.0

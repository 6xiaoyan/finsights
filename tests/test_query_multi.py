"""Task 7：多 Agent 角色受控工具白名单（按 query_backend 分派）。"""
from __future__ import annotations

from types import SimpleNamespace

from agent.cli import enabled_tools_for
from agent.result_store import ResultStore
from agent.subagents import ROLE_TOOLS, SubAgentRunner


def _parent(backend):
    enabled = enabled_tools_for(backend)
    return SimpleNamespace(db_path="x.duckdb", store=ResultStore("dv"), cfg={}, as_of=None,
                           enabled_tools=enabled, query_backend=backend, llm=object(),
                           tool_ctx=SimpleNamespace(catalog=None, stats={}))


def _runner(backend, role):
    # read_analysis_object 与生产路径一致：由调用方通过 extra_tools 注入（不在 DESCRIPTIONS 里）
    return SubAgentRunner(llm=None, parent_ctx=_parent(backend), role=role,
                          system_prompt="", max_steps=1,
                          extra_tools={"read_analysis_object": lambda a: ""})


def test_text2sql_worker_tools():
    assert _runner("text2sql", "worker")._allowed_tools() == [
        "query_financial_data", "list_metrics", "calc", "recall"]


def test_metric_worker_tools():
    assert _runner("metric", "worker")._allowed_tools() == [
        "list_metrics", "query_metric", "calc", "recall"]


def test_reviewer_evidence_only_both_backends():
    for backend in ("metric", "text2sql"):
        assert _runner(backend, "reviewer")._allowed_tools() == ["recall", "read_analysis_object"]


def test_default_backend_legacy_tools():
    assert _runner(None, "worker")._allowed_tools() == ROLE_TOOLS["worker"]
    assert _runner(None, "reviewer")._allowed_tools() == ROLE_TOOLS["reviewer"]


def test_query_financial_data_not_available_in_metric_backend():
    assert "query_financial_data" not in _runner("metric", "worker")._allowed_tools()


def test_child_ctx_inherits_llm_catalog_backend():
    p = _parent("text2sql")
    p.tool_ctx.catalog = "CAT"
    p.llm = "LLM"
    r = SubAgentRunner(llm=None, parent_ctx=p, role="worker", system_prompt="", max_steps=1)
    assert r.child.catalog == "CAT"
    assert r.child.llm == "LLM"
    assert r.child.query_backend == "text2sql"

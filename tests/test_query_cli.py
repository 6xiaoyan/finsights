"""Task 6：--query-backend 受控工具白名单与 CLI 参数。"""
from __future__ import annotations

import pytest

from agent.cli import DISABLED_TOOLS, build_parser, enabled_tools_for
from agent.tools.base import DESCRIPTIONS


def test_default_backend_keeps_legacy_and_hides_text2sql():
    tools = enabled_tools_for(None)
    assert "query_metric" in tools
    assert "query_financial_data" not in tools
    assert not (set(DISABLED_TOOLS) & set(tools))
    assert tools == sorted(set(DESCRIPTIONS) - set(DISABLED_TOOLS) - {"query_financial_data"})


def test_metric_backend_controlled_set():
    assert set(enabled_tools_for("metric")) == {
        "list_metrics", "query_metric", "calc", "recall",
        "todo_write", "final_answer", "load_skill"}


def test_text2sql_backend_controlled_set():
    tools = set(enabled_tools_for("text2sql"))
    assert tools == {"query_financial_data", "list_metrics", "calc", "recall",
                     "todo_write", "final_answer", "load_skill"}
    # 取数旁路工具在 text2sql 路径下必须不可用
    for bypass in ("query_metric", "run_sql", "variance", "working_capital",
                   "seasonal_check", "peer_compare", "check_identities"):
        assert bypass not in tools


def test_cli_accepts_query_backend():
    p = build_parser()
    assert p.parse_args(["--question", "x"]).query_backend is None
    assert p.parse_args(["--question", "x", "--query-backend", "metric"]).query_backend == "metric"
    assert p.parse_args(["--question", "x", "--query-backend", "text2sql"]).query_backend == "text2sql"
    with pytest.raises(SystemExit):
        p.parse_args(["--question", "x", "--query-backend", "nope"])

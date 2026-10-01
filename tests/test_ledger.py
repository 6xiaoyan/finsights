"""P4.5a 验收：证据账本（verify 6.1：V4.32–V4.34）。全部离线；工具取数走真实 DB。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.ledger import (DATA_TOOLS, HYPOTHESIS_TOOLS, LEDGER_BUILDERS,  # noqa: E402
                          Ledger, _SECTION_TITLES, to_ledger_entry, verify_ledger)
from agent.loop import make_run_ctx, run  # noqa: E402
from agent.tools import data as tool_data  # noqa: E402
from agent.tools.base import TodoItem  # noqa: E402
from test_loop import DB, MockLLM, asst, call, _final  # noqa: E402

# 每个分析工具一组能在真实 DB 上成功的参数（V4.34 遍历用）
ANALYSIS_ARGS = {
    "variance": {"metric": "inventory", "company": "Lenovo", "period": "FY24Q2", "basis": "both"},
    "seasonal_check": {"metric": "inventory", "company": "Lenovo", "period": "FY24Q2"},
    "peer_compare": {"metric": "inventory", "calendar_quarter": "2024Q3"},
    "working_capital": {"company": "Lenovo", "periods": ["FY24Q1", "FY24Q2"]},
    "check_identities": {"company": "Lenovo", "periods": ["FY24Q2"]},
    "get_filing_notes": {"company": "Lenovo", "period": "FY24Q2", "keywords": ["存货"]},
}
DATA_ARGS = {
    "list_metrics": {},
    "query_metric": {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 6},
    "run_sql": {"sql": "SELECT company_id, fiscal_year, fiscal_quarter, value"
                       " FROM facts WHERE account_code='inventory' LIMIT 8"},
    "calc": {"expression": "15741.9 / 11720.9 - 1"},
}


def _seed_all(ctx, tools):
    for t in tools:
        out = tool_data.execute(t, (ANALYSIS_ARGS | DATA_ARGS).get(t, {}), ctx.tool_ctx)
        assert out.ok, f"{t}: {out.text[:120]}"
        ctx.ledger.note_scope(t, (ANALYSIS_ARGS | DATA_ARGS).get(t, {}), unit=out.stored.unit)
    return ctx


def _full_ctx():
    ctx = make_run_ctx(DB, None)
    _seed_all(ctx, list(DATA_ARGS) + list(ANALYSIS_ARGS))
    ctx.todos = [TodoItem(content="检验季节性", status="done"),
                 TodoItem(content="给出标签", status="in_progress")]
    ctx.ledger.note_failure("run_sql", "工具错误: Catalog Error: Table xyz 不存在\n  at guard.py:12")
    ctx.ledger.note_failure("run_sql", "工具错误: Catalog Error: Table xyz 不存在\n  at guard.py:12")
    return ctx


# ---------------- V4.33 账本内容：五节 + 假设检验行带 rid + verifier 全过

def test_v4_33_sections_and_verify():
    ctx = _full_ctx()
    text = ctx.ledger.render(ctx.store, ctx.todos)
    for sec in _SECTION_TITLES:
        assert f"## {sec}" in text, sec
    hyp = text.split("## 假设检验记录（由分析工具的结果自动生成）")[1].split("## 失败记录")[0]
    rows = [ln for ln in hyp.splitlines() if ln.startswith("- ")]
    assert len(rows) >= len(ANALYSIS_ARGS)
    import re
    for ln in rows:
        assert re.search(r"\br\d+\b", ln), ln
    # 数据/假设两节的每个数字都能在其 rid 中找到
    assert verify_ledger(text, ctx.store) == []
    # 五节顺序正确（标题去掉括号后缀比对）
    order = [ln[3:].split("（")[0] for ln in text.splitlines() if ln.startswith("## ")]
    assert order == _SECTION_TITLES


def test_verify_ledger_catches_fabricated_numbers():
    ctx = _full_ctx()
    text = ctx.ledger.render(ctx.store, ctx.todos)
    lines = text.splitlines()
    i = lines.index("## 已取得的数据")
    lines.insert(i + 1, "- r1 存货上升 987654.3%（编造行）")
    lines.insert(i + 2, "- 没有 rid 的编造行 12.5")
    problems = verify_ledger("\n".join(lines), ctx.store)
    assert any("987654.3" in p for p in problems), problems
    assert any("没有 rid" in p for p in problems), problems


# ---------------- V4.34 每个分析工具都能写账本（to_ledger_entry 实现且非空）

def test_v4_34_every_analysis_tool_has_entry():
    assert set(LEDGER_BUILDERS) == DATA_TOOLS | HYPOTHESIS_TOOLS
    assert HYPOTHESIS_TOOLS <= set(tool_data._HANDLERS)
    for t in sorted(HYPOTHESIS_TOOLS):
        ctx = make_run_ctx(DB, None)
        out = tool_data.execute(t, ANALYSIS_ARGS[t], ctx.tool_ctx)
        assert out.ok, f"{t}: {out.text[:120]}"
        entry = to_ledger_entry(out.stored)
        assert entry.strip(), t
        assert out.stored.id in entry, f"{t} 的账本行必须带 rid: {entry}"


# ---------------- V4.32 账本由代码生成：render 零 LLM 调用、确定性

def test_v4_32_ledger_is_code_generated():
    # mock LLM 跑一次完整归因流程（不触发 L5），统计 LLM 调用次数
    seq = [
        asst(calls=[call("c0", "load_skill", {"name": "attribution"})]),
        asst(calls=[call("c1", "variance", ANALYSIS_ARGS["variance"])]),
        asst(calls=[call("c2", "seasonal_check", ANALYSIS_ARGS["seasonal_check"])]),
        asst(calls=[call("c3", "peer_compare", ANALYSIS_ARGS["peer_compare"])]),
        asst(calls=[call("c4", "get_filing_notes", ANALYSIS_ARGS["get_filing_notes"])]),
        asst(calls=[_final(md="按归因流程完成了分析，结论标签 seasonal。")]),
    ]
    ctx = make_run_ctx(DB, MockLLM(seq))
    out = run("联想 FY24Q2 存货为什么上升？", ctx)
    assert out.answer.status == "answered" and out.verified
    n_calls = len(ctx.llm.views)
    text1 = ctx.ledger.render(ctx.store, ctx.todos)
    text2 = ctx.ledger.render(ctx.store, ctx.todos)
    assert text1 == text2                       # 纯函数：同 store 同文本
    assert len(ctx.llm.views) == n_calls        # 生成账本时 LLM 调用次数 = 0
    assert "## 假设检验记录" in text1
    assert "seasonal" in text1                  # 分析工具的检验结论进了账本


# ---------------- 失败记录与口径

def test_ledger_scope_and_failures():
    led = Ledger(as_of="2024-11-15", data_version="abc123def456")
    led.note_scope("query_metric", {"companies": ["Lenovo", "HP"], "period_basis": "calendar"},
                   unit="usd_mn")
    led.note_scope("query_metric", {"companies": ["Dell"], "period_basis": "calendar"},
                   unit="usd_mn")
    led.note_failure("calc", "工具错误: CalcError: 不允许的函数\n trace...")
    led.note_failure("calc", "工具错误: CalcError: 不允许的函数\n trace...")
    assert led.companies == ["Lenovo", "HP", "Dell"]
    assert led.basis == "calendar" and led.units == ["usd_mn"]
    assert len(led.failures) == 1               # 去重
    text = led.render(type("S", (), {"all": lambda self: [], "data_version": "abc123def456"})(), [])
    assert "## 失败记录" in text
    assert "- calc: 工具错误: CalcError: 不允许的函数" in text
    assert "as_of: 2024-11-15" in text

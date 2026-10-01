"""P4.1 验收：Result Store / 渲染 / 数据类工具（verify 6.1：V4.1–V4.10）。全部离线。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.result_store import ResultStore  # noqa: E402
from agent.tools import data as td  # noqa: E402
from agent.tools.base import (ARGS_MODELS, STORED_TOOLS, all_args_models, all_tool_schemas,
                              tool_schemas)  # noqa: E402
from agent.tools.data import ToolContext, execute, make_data_version  # noqa: E402
from agent.tools.db import connect  # noqa: E402

DB = "data/finsights.duckdb"


@pytest.fixture()
def ctx():
    return ToolContext(db_path=DB, store=ResultStore(make_data_version(DB)))


def _make_ctx_counting_queries(monkeypatch) -> tuple[ToolContext, dict]:
    counter = {"n": 0}
    orig = td.query_df

    def counting(sql, db_path, params=None):
        counter["n"] += 1
        return orig(sql, db_path, params)

    monkeypatch.setattr(td, "query_df", counting)
    c = ToolContext(db_path=DB, store=ResultStore(make_data_version(DB)))
    return c, counter


# ---------------- V4.1 存取与缓存

def test_v4_1_cache_same_args_returns_same_rid_one_db_read(monkeypatch):
    ctx, counter = _make_ctx_counting_queries(monkeypatch)
    args = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 4}
    o1 = execute("query_metric", args, ctx)
    n_after_first = counter["n"]
    o2 = execute("query_metric", args, ctx)
    assert o1.rid == o2.rid
    # 第二次完全没再查库（缓存命中在 handler 之前）
    assert counter["n"] == n_after_first
    assert counter["n"] > 0


def test_v4_1_different_args_new_rid(ctx):
    a = execute("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                "last_n": 3}, ctx)
    b = execute("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                "last_n": 4}, ctx)
    assert a.rid != b.rid


# ---------------- V4.2 find_value 单位换算

def _single_store(value: float) -> ResultStore:
    st = ResultStore("test")
    st.put("calc", {"expression": "x"}, pd.DataFrame([{"value": value}]))
    return st


def test_v4_2_usd_mn_vs_usd_100mn():
    st = _single_store(6123.4)
    assert st.find_value("r1", 61.234, "usd_100mn") is True


def test_v4_2_ratio_vs_pct():
    st = _single_store(0.182)
    assert st.find_value("r1", 18.2, "pct") is True


def test_v4_2_outside_tolerance():
    # verify V4.2 第三例写的是 6150，但它距 6123.4 只有 0.43%（并未"超出 0.5%"），
    # 用例数字与口径自相矛盾 → 登记 Q9。此处用明确超出 0.5% 的 6250 验证同一条规则。
    st = _single_store(6123.4)
    assert st.find_value("r1", 6250.0, "usd_mn") is False
    assert st.find_value("r1", 6150.0, "usd_mn") is True  # 0.43% < 0.5%，按口径应"找到"


def test_v4_2_missing_rid_returns_error_not_exception():
    st = _single_store(1.0)
    ok, best, msg = st.check_value("r99", 1.0)
    assert ok is False and best is None and "不存在" in msg


def test_v4_2_nearest_for_feedback():
    st = _single_store(0.18231)
    ok, best, _ = st.check_value("r1", 25.0, "pct")
    assert ok is False and abs(best - 18.231) < 0.01  # 反馈给模型的最接近值


# ---------------- V4.3 渲染格式

def test_v4_3_first_line_regex_and_truncation(ctx):
    df = pd.DataFrame({"company": ["Lenovo"] * 60,
                       "fiscal_year": list(range(2010, 2025)) * 4,
                       "fiscal_quarter": ([1, 2, 3, 4] * 15),
                       "inventory": np.arange(60, dtype=float)})
    res = ctx.store.put("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                         "last_n": 60}, df)
    text = td.render(res, ctx.cfg)
    assert re.match(r"^\[r\d+\] \w+\(.*\)$", text.splitlines()[0])
    body_rows = [ln for ln in text.splitlines() if ln.startswith("|")
                 and "---" not in ln and "company" not in ln]
    assert len(body_rows) <= 50
    assert "recall(" in text


def test_v4_3_small_result_not_truncated(ctx):
    o = execute("peer_compare", {"metric": "inventory", "calendar_quarter": "2024Q3"}, ctx)
    assert "recall(" not in o.text  # 3 行小结果不需要分页提示


# ---------------- V4.4 / V4.5 schema

PLAN_TOOLS_P4 = set(STORED_TOOLS) | {"recall", "todo_write", "final_answer", "load_skill"}  # forecast P6


def test_v4_4_schemas_complete_with_usage_hints():
    schemas = {t["function"]["name"]: t["function"] for t in all_tool_schemas()}
    assert PLAN_TOOLS_P4 == set(schemas)
    for name, fn in schemas.items():
        assert "何时使用" in fn["description"], name
        assert "properties" in fn["parameters"] or fn["parameters"]["type"] == "object"
        assert name in all_args_models()


def test_v4_5_no_db_or_as_of_in_args():
    for name, model in all_args_models().items():
        fields = set(model.model_fields)
        assert not {"db", "db_path", "as_of"} & fields, name


# ---------------- V4.6 calc 安全

@pytest.mark.parametrize("expr", [
    "__import__('os')",
    "open('x')",
    "(lambda: 1)()",
    "1/0",
    "r99.x[Lenovo,FY24Q2]",
    "r1.__class__[Lenovo,FY24Q2]",
])
def test_v4_6_calc_rejected_as_text(ctx, expr):
    o = execute("calc", {"expression": expr}, ctx)
    assert o.ok is False and o.text.startswith("工具错误")


# ---------------- V4.7 calc 正确性

def test_v4_7_calc_over_stored_refs(ctx):
    o = execute("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                 "period_from": "FY2023Q1", "period_to": "FY2024Q4"}, ctx)
    rid = o.rid
    df = ctx.store.get(rid).df
    v24q2 = float(df[(df.fiscal_year == 2024) & (df.fiscal_quarter == 2)].inventory.iloc[0])
    v23q2 = float(df[(df.fiscal_year == 2023) & (df.fiscal_quarter == 2)].inventory.iloc[0])
    expr = f"{rid}.inventory[Lenovo,FY2024Q2] / {rid}.inventory[Lenovo,FY2023Q2] - 1"
    got = execute("calc", {"expression": expr}, ctx)
    assert got.ok and got.rid != rid
    value = float(ctx.store.get(got.rid).df["value"].iloc[0])
    assert abs(value - (v24q2 / v23q2 - 1)) < 1e-12


# ---------------- V4.8 run_sql 护栏

@pytest.mark.parametrize("sql", [
    "INSERT INTO facts VALUES ('X',1,1,'a',1,'2024-01-01','original','s','r',NULL,FALSE)",
    "DROP TABLE facts",
    "ATTACH 'other.db'",
    "COPY facts TO 'x.csv'",
    "PRAGMA database_list",
    "SELECT 1; DROP TABLE facts",
])
def test_v4_8_run_sql_rejected(ctx, sql):
    o = execute("run_sql", {"sql": sql}, ctx)
    assert o.ok is False and o.text.startswith("工具错误")


# ---------------- V4.9 自动 LIMIT

def test_v4_9_auto_limit_200(ctx):
    o = execute("run_sql", {"sql": "SELECT * FROM facts"}, ctx)
    assert o.ok and len(o.stored.df) <= 200


# ---------------- V4.10 只读连接

def test_v4_10_connection_is_read_only():
    con = connect(DB)
    try:
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE t(x INT)")
    finally:
        con.close()


# ---------------- 其他行为抽查

def test_recall_no_new_rid_and_offset_window(ctx):
    df = pd.DataFrame({"company": ["Lenovo"] * 60, "fiscal_year": list(range(2010, 2025)) * 4,
                       "fiscal_quarter": [1, 2, 3, 4] * 15, "inventory": np.arange(60.0)})
    res = ctx.store.put("query_metric", {"metrics": ["inventory"]}, df)
    o = execute("recall", {"result_id": res.id, "offset": 50}, ctx)
    assert o.ok and o.rid == res.id
    n_before = len(ctx.store._by_id)
    execute("recall", {"result_id": res.id, "offset": 0}, ctx)
    assert len(ctx.store._by_id) == n_before  # recall 不产生新 rid


def test_get_filing_notes_roundtrip(ctx):
    o = execute("get_filing_notes", {"company": "Lenovo", "period": "FY2023Q1"}, ctx)
    assert o.ok and len(o.stored.df) >= 1


def test_check_identities_passes_on_good_data(ctx):
    o = execute("check_identities", {"company": "Dell", "periods": ["FY2024Q1-FY2024Q4"]}, ctx)
    assert o.ok and "violation" not in o.stored.df.columns  # 全通过
    text = ctx.store.get(o.rid).digest
    assert "无数值" in text or text == "" or "结果" in text


def test_working_capital_matches_fincalc(ctx):
    o = execute("working_capital", {"company": "Lenovo", "periods": ["FY24Q1-FY25Q1"]}, ctx)
    df = o.stored.df
    assert {"dio", "dso", "dpo", "ccc"} <= set(df.columns)
    import fincalc.calc as fc
    ref = fc.working_capital(df.drop(columns=[c for c in ("dio", "dso", "dpo", "ccc",
                                                          "accounts_receivable_prev",
                                                          "inventory_prev", "accounts_payable_prev",
                                                          "accounts_receivable_avg", "inventory_avg",
                                                          "accounts_payable_avg")]))
    m = df["period_end"] == ref["period_end"].iloc[-1]
    assert abs(float(df.loc[m, "ccc"].iloc[0]) - float(ref["ccc"].iloc[-1])) < 1e-9


def test_working_capital_single_period_keeps_avg_basis(ctx):
    """V4.45 回归：单期调用也必须用平均余额口径（自动向前补一期），不得退化为期末值。"""
    o = execute("working_capital", {"company": "Lenovo", "periods": ["FY25Q1"]}, ctx)
    assert o.ok
    df = o.stored.df
    row = df.iloc[-1]
    assert (int(row["fiscal_year"]), int(row["fiscal_quarter"])) == (2025, 1)
    con = duckdb.connect(DB, read_only=True)

    def vals(fy: int, q: int) -> dict:
        r = con.execute(
            "SELECT f.account_code, f.value FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter) "
            "WHERE f.version='original' AND p.company_id='Lenovo' AND p.fiscal_year=? AND p.fiscal_quarter=?",
            [fy, q]).fetchall()
        return {a: v for a, v in r}

    cur, prev = vals(2025, 1), vals(2024, 4)
    con.close()
    days = float(row["days"])
    dso = (cur["accounts_receivable"] + prev["accounts_receivable"]) / 2 / cur["revenue"] * days
    dio = (cur["inventory"] + prev["inventory"]) / 2 / cur["cogs"] * days
    dpo = (cur["accounts_payable"] + prev["accounts_payable"]) / 2 / cur["cogs"] * days
    ccc = dso + dio - dpo
    assert abs(float(row["ccc"]) - ccc) / abs(ccc) < 1e-6
    # 期末值口径（退化情形）会给出不同的 dso——确认没有走 fallback
    end_dso = cur["accounts_receivable"] / cur["revenue"] * days
    assert abs(float(row["dso"]) - end_dso) / end_dso > 0.005


def test_unknown_tool_returns_text(ctx):
    o = execute("no_such_tool", {}, ctx)
    assert o.ok is False and "未知工具" in o.text

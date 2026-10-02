"""P6.1 验收：as_of 快照与防泄露（verify V6.1–V6.5）。全部离线，不触网。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.result_store import ResultStore  # noqa: E402
from agent.tools import guard  # noqa: E402
from agent.tools.data import ToolContext, execute  # noqa: E402
from agent.tools.db import connect as agent_connect  # noqa: E402
from etl.snapshot import make_snapshot  # noqa: E402

BASE = Path("data/finsights.duckdb")


@pytest.fixture(scope="module")
def snap_2022(tmp_path_factory):
    return make_snapshot(date(2022, 12, 31), BASE, tmp_path_factory.mktemp("snap"))


# ---------------- V6.1 快照中没有未来数据

@pytest.mark.parametrize("asof", ["2019-06-30", "2021-12-31", "2023-06-30",
                                  "2024-12-31", "2026-03-31"])
def test_v6_1_no_future_available_dates(tmp_path, asof):
    p = make_snapshot(date.fromisoformat(asof), BASE, tmp_path)
    con = duckdb.connect(str(p), read_only=True)
    mx = con.execute("SELECT max(available_date) FROM facts").fetchone()[0]
    assert mx is not None and mx <= date.fromisoformat(asof)
    n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
    assert n > 0
    con.close()


# ---------------- V6.2 重述数据处理

def test_v6_2_restated_after_asof_excluded(tmp_path):
    src = tmp_path / "base.duckdb"
    con = duckdb.connect(str(src))
    con.execute("CREATE TABLE accounts(code TEXT); CREATE TABLE companies(company_id TEXT)")
    con.execute("""CREATE TABLE facts(company_id TEXT, fiscal_year INT, fiscal_quarter INT,
        account_code TEXT, value DOUBLE, available_date DATE, version TEXT, source TEXT,
        source_ref TEXT, derivation TEXT, is_derived BOOLEAN)""")
    con.execute("""CREATE TABLE facts_detail(company_id TEXT, fiscal_year INT, fiscal_quarter INT,
        account_code TEXT, region TEXT, product_line TEXT, value DOUBLE, synthetic BOOLEAN)""")
    con.execute("CREATE TABLE filing_notes(company_id TEXT, fiscal_year INT, fiscal_quarter INT, note TEXT)")
    con.execute("CREATE TABLE periods(company_id TEXT, fiscal_year INT, fiscal_quarter INT, period_end DATE, calendar_quarter TEXT, days INT)")
    con.execute("""INSERT INTO facts VALUES
        ('X',2020,1,'revenue',100,DATE '2020-05-01','original','s','r',NULL,FALSE),
        ('X',2020,1,'revenue',110,DATE '2021-03-01','restated','s','r',NULL,FALSE)""")
    con.execute("""INSERT INTO periods VALUES ('X',2020,1,DATE '2020-03-31','2020Q1',90)""")
    con.close()
    snap = make_snapshot(date(2020, 6, 30), src, tmp_path / "snap")
    rows = duckdb.connect(str(snap), read_only=True).execute(
        "SELECT value, version FROM facts").fetchall()
    assert rows == [(100.0, "original")]  # restated 不出现，位置保留 original 值


# ---------------- V6.3 关联表也被过滤

def test_v6_3_associated_tables_filtered(snap_2022):
    base = duckdb.connect(str(BASE), read_only=True)
    snap = duckdb.connect(str(snap_2022), read_only=True)
    cutoff = date(2022, 12, 31)
    for tbl in ("facts_detail", "filing_notes", "periods"):
        pairs = snap.execute(
            f"SELECT DISTINCT company_id, fiscal_year, fiscal_quarter FROM {tbl}").fetchall()
        for cid, fy, fq in pairs:
            fd = base.execute("""SELECT min(available_date) FROM facts WHERE company_id=?
                AND fiscal_year=? AND fiscal_quarter=?""", [cid, fy, fq]).fetchone()[0]
            assert fd is not None and fd <= cutoff, f"{tbl} 泄露 {cid} FY{fy}Q{fq}（首次披露 {fd}）"
    base.close()
    snap.close()


# ---------------- V6.4 防泄露总测试（每个数据工具 + run_sql 全表扫描）

def test_v6_4_tools_cannot_see_past_asof(snap_2022):
    ctx = ToolContext(db_path=str(snap_2022), store=ResultStore("v64"))
    # query_metric：请求覆盖到快照之后的期间时，返回行不超过 as_of
    o = execute("query_metric", {"metrics": ["revenue"], "companies": ["Dell"],
                                 "last_n": 60}, ctx)
    assert o.ok
    df = o.stored.df if o.stored else None
    assert df is not None and len(df) > 0
    # 快照中 Dell revenue 的行数 = 工具返回行数 → 无 as_of 之后的行混入
    import duckdb as _dk
    n_snap = _dk.connect(str(snap_2022), read_only=True).execute(
        "SELECT count(*) FROM facts WHERE company_id='Dell' AND account_code='revenue'"
        " AND version='original'").fetchone()[0]
    assert len(df) == n_snap
    # run_sql：information_schema 全表枚举，凡带期间列的表都查不到 as_of 之后的期末
    tables = execute("run_sql", {"sql": "SELECT table_name FROM information_schema.tables"},
                     ctx)
    names = [r[0] for r in __import__("pandas").DataFrame(tables.stored.df).values]
    probes = {
        "facts": "SELECT max(available_date) AS m FROM facts",
        "periods": "SELECT max(period_end) AS m FROM periods",
        "facts_detail": "SELECT max(p.period_end) AS m FROM facts_detail d "
                        "JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)",
    }
    for t, sql in probes.items():
        if t in names:
            r = execute("run_sql", {"sql": sql}, ctx)
            m = r.stored.df["m"].iloc[0]
            assert m is not None and pd.Timestamp(m).date() <= date(2022, 12, 31), t
    # 快照中不存在 as_of 之后的 (公司, 期间)——连 periods 也过滤（V6.3 联动）
    r = execute("run_sql", {"sql": "SELECT count(*) AS n FROM periods WHERE period_end > "
                                   "DATE '2022-12-31'"}, ctx)
    assert int(r.stored.df["n"].iloc[0]) == 0


# ---------------- V6.5 agent 无法切换数据库

def test_v6_5_attach_and_parquet_rejected(snap_2022):
    ctx = ToolContext(db_path=str(snap_2022), store=ResultStore("v65"))
    for sql in ["ATTACH 'data/finsights.duckdb' AS other",
                "SELECT * FROM read_parquet('data/raw/x.parquet')",
                "SELECT * FROM parquet_scan('data/raw/x')"]:
        o = execute("run_sql", {"sql": sql}, ctx)
        assert not o.ok and ("不允许" in o.text or "禁止" in o.text or "只允许" in o.text), sql
    # 第二层防线：agent 的 connect() 关闭外部访问后，文件函数在连接层也被拒。
    # 注意：DuckDB 只读连接对**已存在文件**的 ATTACH 不报错（实测），所以 ATTACH
    # 的拦截完全依赖 guard.validate_select（上面已验证工具层拒绝）。
    con = agent_connect(str(snap_2022))
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_parquet('data/raw/x.parquet')")
    con.close()
    assert guard.validate_select("SELECT 1")  # 正常查询不受影响

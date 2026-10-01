"""P2 语义层测试（verify V2.5–V2.10）。"""
from __future__ import annotations

import duckdb
import pytest
import sqlglot
import yaml

from semantic.compiler import MetricRequest, compile
from semantic.catalog import list_catalog

DB = "data/finsights.duckdb"


def _metrics():
    return yaml.safe_load(open("semantic/metrics.yaml", encoding="utf-8"))["metrics"]


def test_v2_5_metrics_completeness():
    """V2.5：附录 A 全部 base 指标 + 7 个 derived，均有 unit 和 dims。"""
    m = {x["name"]: x for x in _metrics()}
    base_required = [
        "cash", "short_term_investments", "accounts_receivable", "financing_receivables", "inventory",
        "other_current_assets", "total_current_assets", "ppe_net", "goodwill", "intangibles",
        "other_noncurrent_assets", "total_assets", "accounts_payable", "short_term_debt",
        "accrued_liabilities", "deferred_revenue_current", "deferred_revenue_noncurrent",
        "other_current_liabilities", "total_current_liabilities", "long_term_debt",
        "other_noncurrent_liabilities", "total_liabilities", "noncontrolling_interest",
        "total_equity", "total_liabilities_and_equity",
        "revenue", "cogs", "gross_profit", "operating_income", "net_income",
    ]
    derived_required = ["gross_margin", "dso", "dio", "dpo", "ccc", "current_ratio", "debt_to_equity"]
    for name in base_required:
        assert name in m and m[name]["type"] == "base", name
    for name in derived_required:
        assert name in m and m[name]["type"] == "derived", name
    for x in m.values():
        assert x.get("unit") and x.get("dims"), x["name"]


def test_v2_6_compiler_error_suggests_nearby():
    """V2.6：指标名拼错时，报错信息包含正确名称。"""
    with pytest.raises(ValueError) as e:
        compile(MetricRequest(metrics=["inventroy"]))
    assert "inventory" in str(e.value)


def test_v2_7_compiler_single_select():
    """V2.7：10 个不同请求编译出的 SQL 全部是单条 SELECT（sqlglot 解析）。"""
    reqs = [
        MetricRequest(metrics=["inventory"]),
        MetricRequest(metrics=["inventory", "revenue"], companies=["Lenovo"]),
        MetricRequest(metrics=["dio"], companies=["Dell"], last_n=8),
        MetricRequest(metrics=["dso", "dpo", "ccc"]),
        MetricRequest(metrics=["gross_margin"], period_from="FY24Q1", period_to="FY25Q4"),
        MetricRequest(metrics=["current_ratio"], period_from="2024Q1", period_to="2024Q4"),
        MetricRequest(metrics=["debt_to_equity"], period_basis="calendar"),
        MetricRequest(metrics=["revenue"], group_by=["region"]),
        MetricRequest(metrics=["inventory"], filters={"region": ["APAC"]}),
        MetricRequest(metrics=["revenue", "inventory", "dio"], last_n=4, period_from="2023Q1"),
    ]
    for req in reqs:
        sql = compile(req)
        parsed = sqlglot.parse(sql, read="duckdb")
        assert len(parsed) == 1, f"应为单条语句: {sql[:80]}"
        assert str(parsed[0].key).lower() == "select", f"应为 SELECT: {sql[:80]}"


def test_v2_8_semantic_matches_direct_sql():
    """V2.8：随机抽 20 个 (公司, 期间, base 指标)，语义层结果与直接查 facts 一致。"""
    import random
    con = duckdb.connect(DB, read_only=True)
    pool = con.execute("""SELECT p.company_id, p.fiscal_year, p.fiscal_quarter, f.account_code, f.value
        FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.version='original' AND f.account_code IN ('inventory','revenue','total_assets','accounts_payable')""").fetchall()
    sample = random.Random(42).sample(pool, 20)
    for cid, fy, q, acct, value in sample:
        req = MetricRequest(metrics=[acct], companies=[cid], period_from=f"FY{fy}Q{q}", period_to=f"FY{fy}Q{q}")
        df = con.execute(compile(req)).df()
        assert len(df) == 1, (cid, fy, q, acct)
        assert abs(df.iloc[0][acct] - value) < 1e-9, (cid, fy, q, acct)
    con.close()


def test_v2_9_derived_matches_hand_calc():
    """V2.9：挑 3 个 (公司, 期间)，手算 DIO / DSO / 毛利率 与语义层一致（误差 < 1e-6）。"""
    con = duckdb.connect(DB, read_only=True)
    cases = [("Lenovo", 2024, 3), ("HP", 2023, 4), ("Dell", 2025, 2)]
    for cid, fy, q in cases:
        sql = compile(MetricRequest(metrics=["dio", "dso", "gross_margin"],
                                    companies=[cid], period_from=f"FY{fy}Q{q}", period_to=f"FY{fy}Q{q}"))
        got = con.execute(sql).df().iloc[0]
        raw = con.execute("""SELECT
              MAX(CASE WHEN account_code='inventory' THEN value END) inv,
              MAX(CASE WHEN account_code='accounts_receivable' THEN value END) ar,
              MAX(CASE WHEN account_code='cogs' THEN value END) cogs,
              MAX(CASE WHEN account_code='revenue' THEN value END) rev,
              MAX(CASE WHEN account_code='gross_profit' THEN value END) gp
            FROM facts WHERE company_id=? AND fiscal_year=? AND fiscal_quarter=? AND version='original'""",
            [cid, fy, q]).df().iloc[0]
        # 上期值 = period_end 序的前一期（facts 无 period_end，须经 periods 关联）
        prev = con.execute(f"""SELECT MAX(CASE WHEN f.account_code='inventory' THEN f.value END) inv_prev,
            MAX(CASE WHEN f.account_code='accounts_receivable' THEN f.value END) ar_prev
            FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
            WHERE f.company_id='{cid}' AND f.version='original'
              AND p.period_end = (SELECT MAX(period_end) FROM periods WHERE company_id='{cid}'
                  AND (fiscal_year < {fy} OR (fiscal_year = {fy} AND fiscal_quarter < {q})))""").df().iloc[0]
        days = con.execute(f"SELECT days FROM periods WHERE company_id='{cid}' AND fiscal_year={fy} AND fiscal_quarter={q}").fetchone()[0]
        dio = ((raw["inv"] + prev["inv_prev"]) / 2) / raw["cogs"] * days
        dso = ((raw["ar"] + prev["ar_prev"]) / 2) / raw["rev"] * days
        gm = raw["gp"] / raw["rev"]
        assert abs(got["dio"] - dio) < 1e-6, (cid, fy, q, got["dio"], dio)
        assert abs(got["dso"] - dso) < 1e-6
        assert abs(got["gross_margin"] - gm) < 1e-6
    con.close()


def test_v2_10_calendar_alignment():
    """V2.10：自然季度 2024Q3 的存货 → 恰好 3 行（每家公司 1 行）。"""
    con = duckdb.connect(DB, read_only=True)
    sql = compile(MetricRequest(metrics=["inventory"], period_from="2024Q3", period_to="2024Q3"))
    df = con.execute(sql).df()
    assert len(df) == 3
    assert set(df["company"]) == {"Lenovo", "HP", "Dell"}
    con.close()


def test_catalog_lists_all():
    cat = list_catalog()
    assert len(cat) >= 37
    names = {c["name"] for c in cat}
    assert {"inventory", "dio", "ccc"} <= names

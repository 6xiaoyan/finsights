"""P2 fincalc 测试（verify V2.11/V2.12/V2.13 + 基础函数）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fincalc.calc import (
    check_identities,
    check_identities_detail,
    contribution,
    peer_compare,
    qoq,
    seasonal_baseline,
    working_capital,
    yoy,
)


def test_qoq_yoy_basic():
    df = pd.DataFrame({
        "company": ["X"] * 6,
        "period_end": pd.date_range("2023-03-31", periods=6, freq="QE"),
        "value": [100.0, 110.0, 121.0, 100.0, 110.0, 121.0],
    })
    out = qoq(df)
    assert out.iloc[1]["change_pct"] == pytest.approx(0.10)
    out2 = yoy(df)
    assert out2.iloc[4]["change_pct"] == pytest.approx(0.10)  # 同比 +10%
    assert out2.iloc[0]["value_prev"] != out2.iloc[0]["value_prev"]  # 首期无同比基期（NaN）


def test_v2_11_contribution_closes():
    """V2.11：随机 100 组，各分项贡献之和 = 总变化（误差 < 1e-9）。"""
    rng = np.random.default_rng(7)
    for _ in range(100):
        n_periods, n_parts = 6, 4
        parts = rng.uniform(10, 100, (n_periods, n_parts))
        total = parts.sum(axis=1)
        total_df = pd.DataFrame({
            "company": "X", "period_end": pd.date_range("2024-03-31", periods=n_periods, freq="QE"),
            "value": total})
        parts_df = pd.DataFrame({
            "company": "X", "period_end": np.repeat(pd.date_range("2024-03-31", periods=n_periods, freq="QE"), n_parts),
            "part": [f"p{i}" for _ in range(n_periods) for i in range(n_parts)],
            "value": parts.reshape(-1)})
        out = contribution(total_df, parts_df)
        got = out.groupby("period_end")["contribution"].sum()
        assert len(got) == n_periods - 1
        d_total = np.diff(total)  # total 为 ndarray：相邻期间总变化
        assert np.abs(got.to_numpy() - d_total).max() < 1e-9  # 加总 = 总变化（精确闭合）


def test_v2_13_seasonal_baseline():
    """V2.13：每年 Q2 环比恒 +5%，当期 +20% → z 显著 >2，分位 = 100%。"""
    # 6 年：每一年 Q1=100, Q2=105（Q1→Q2 环比 +5%）
    values, quarters = [], []
    hist_changes = [0.04, 0.05, 0.06, 0.05, 0.05]  # 历年同季度环比（有方差）
    for chg in hist_changes:
        values += [100.0, 100.0 * (1 + chg)]
        quarters += [1, 2]
    values += [105.0, 126.0]  # 最后一年：Q1=105，Q2=126 → 环比 +20%
    quarters += [1, 2]
    res = seasonal_baseline(values, quarters, current_quarter=2)
    assert res["n"] >= 5
    assert res["current_change"] == pytest.approx(126.0 / 105.0 - 1)
    assert res["z_score"] > 2
    assert res["percentile"] == pytest.approx(1.0)


def test_working_capital_hand_calc():
    """V2.9 配套：手工构造两期，DIO/DSO/DPO/CCC 与手算一致。"""
    df = pd.DataFrame({
        "company": ["X", "X"],
        "period_end": pd.to_datetime(["2024-06-30", "2024-09-30"]),
        "days": [91, 92],
        "accounts_receivable": [400.0, 460.0],
        "inventory": [200.0, 260.0],
        "accounts_payable": [300.0, 330.0],
        "revenue": [1000.0, 1100.0],
        "cogs": [700.0, 770.0],
    })
    out = working_capital(df).iloc[1]
    assert out["dio"] == pytest.approx((260 + 200) / 2 / 770 * 92)
    assert out["dso"] == pytest.approx((460 + 400) / 2 / 1100 * 92)
    assert out["dpo"] == pytest.approx((330 + 300) / 2 / 770 * 92)
    assert out["ccc"] == pytest.approx(out["dio"] + out["dso"] - out["dpo"])


def test_peer_compare_ranking():
    rows = peer_compare({"Lenovo": 110.0, "HP": 102.0, "Dell": 95.0},
                        {"Lenovo": 100.0, "HP": 100.0, "Dell": 100.0})
    assert [r["company"] for r in rows] == ["Lenovo", "HP", "Dell"]  # 按变化降序
    assert rows[0]["rank"] == 1 and rows[0]["change_pct"] == pytest.approx(0.10)


def test_check_identities_flags():
    df = pd.DataFrame([{
        "company": "X", "fiscal_year": 2024, "fiscal_quarter": 1,
        "total_assets": 100.0, "total_liabilities": 60.0, "total_equity": 30.0,  # A≠L+E
        "total_current_assets": 50.0, "cash": 10.0, "inventory": 20.0,
        "other_current_assets": 20.0, "ppe_net": 30.0, "goodwill": 10.0,
        "intangibles": 5.0, "other_noncurrent_assets": 5.0,
    }])
    v = check_identities(df)
    assert any("A-L-E" in s for s in v)


def test_v2_12_fincalc_purity():
    """V2.12：fincalc 无 IO（duckdb/open(/requests/httpx/read_csv）。"""
    import pathlib
    for f in pathlib.Path("fincalc").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for bad in ("duckdb", "requests", "httpx", "read_csv", "open("):
            assert bad not in text, f"{f.name} 含 {bad}"


def test_hp_tl_no_drnc_double_count():
    """步骤 1 回归（真实数据）：HP 分项加总不得重复计入非流动递延收入（批阅实测 1473 假违例）。"""
    import duckdb
    con = duckdb.connect("data/finsights.duckdb", read_only=True)
    df = con.execute("""SELECT p.company_id AS company, p.fiscal_year, p.fiscal_quarter,
        MAX(CASE WHEN f.account_code='total_assets' THEN f.value END) total_assets,
        MAX(CASE WHEN f.account_code='total_liabilities' THEN f.value END) total_liabilities,
        MAX(CASE WHEN f.account_code='total_equity' THEN f.value END) total_equity,
        MAX(CASE WHEN f.account_code='total_current_liabilities' THEN f.value END) total_current_liabilities,
        MAX(CASE WHEN f.account_code='long_term_debt' THEN f.value END) long_term_debt,
        MAX(CASE WHEN f.account_code='other_noncurrent_liabilities' THEN f.value END) other_noncurrent_liabilities,
        MAX(CASE WHEN f.account_code='deferred_revenue_noncurrent' THEN f.value END) deferred_revenue_noncurrent
        FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.company_id='HP' AND f.version='original'
        GROUP BY 1,2,3 ORDER BY 2,3""").df()
    con.close()
    detail = check_identities_detail(df)
    tl_rows = [r for r in detail if r["identity"].startswith("total_liabilities")]
    assert tl_rows and all(r["ok"] for r in tl_rows), [r for r in tl_rows if not r["ok"]]
    # 反向测试：故意把 TL 抬高 1473（批阅发现的真实错误量级），必须被抓到
    broken = df.copy()
    mask = (broken["fiscal_year"] == 2024) & (broken["fiscal_quarter"] == 3)
    broken.loc[mask, "total_liabilities"] = broken.loc[mask, "total_liabilities"] + 1473.0
    v = check_identities(broken)
    assert any("FY2024Q3" in x and "total_liabilities" in x for x in v), v


def test_identity_nan_not_silent():
    """步骤 1：分项值为 NaN/缺失不得静默按 0 通过。"""
    df = pd.DataFrame([{
        "company": "Dell", "fiscal_year": 2024, "fiscal_quarter": 2,
        "total_assets": 100.0, "total_liabilities": 60.0, "total_equity": 40.0,
        "total_current_assets": 50.0, "cash": 10.0, "inventory": 20.0,
        "other_current_assets": 20.0,  # accounts_receivable/financing_receivables 缺失（正常，残差吸收）
        "total_current_liabilities": float("nan"),  # present-but-NaN → 必须报
    }])
    detail = check_identities_detail(df)
    tcl = [r for r in detail if r["identity"].startswith("total_current_liabilities")][0]
    assert not tcl["ok"] and "total_current_liabilities" in tcl["missing"]

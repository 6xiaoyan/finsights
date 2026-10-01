"""P2 fincalc 测试（verify V2.11/V2.12/V2.13 + 基础函数）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fincalc.calc import (
    check_identities,
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

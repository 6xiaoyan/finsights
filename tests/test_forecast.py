"""P6.2 验收：fincalc.forecast（verify V6.6–V6.8）。全部离线确定性，无 LLM。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fincalc.forecast import forecast, mase_vs_naive  # noqa: E402

# 合成季度序列：线性趋势 + 稳定季节形态 + 小噪声（可复现：固定公式，非随机）
BASE = [100, 120, 90, 140]


def series(n: int) -> list[float]:
    return [BASE[i % 4] + 3.0 * (i // 4) + (i % 3) - 1 for i in range(n)]


PERIODS = [(2020 + i // 4, i % 4 + 1) for i in range(64)]


# ---------------- V6.6 基线预测正确

def test_v6_6_seasonal_naive_equals_last_year():
    y = series(40)
    r = forecast(y, PERIODS[:40], method="seasonal_naive", horizon=4, level=0.8)
    assert r["method"] == "seasonal_naive"
    # 点预测 = 去年同季度值（完全相等）
    assert r["point"] == [round(v, 4) for v in y[-4:]]
    assert all(lo <= p + 1e-9 for lo, p in zip(r["lower"], r["point"]))
    assert all(p <= up + 1e-9 for p, up in zip(r["point"], r["upper"]))


# ---------------- V6.7 短序列退回

def test_v6_7_auto_falls_back_on_short_series():
    y = series(10)
    r = forecast(y, PERIODS[:10], method="auto", horizon=4)
    assert r["method"] == "seasonal_naive"
    assert "退回" in r["note"]


# ---------------- V6.8 区间合理

@pytest.mark.parametrize("method", ["ets", "sarima"])
def test_v6_8_interval_ordering(method):
    y = series(40)
    r = forecast(y, PERIODS[:40], method=method, horizon=4, level=0.8)
    assert r["level"] == 0.8
    for lo, p, up in zip(r["lower"], r["point"], r["upper"]):
        assert lo <= p <= up


def test_auto_selects_and_reports_mase():
    y = series(40)
    r = forecast(y, PERIODS[:40], method="auto", horizon=4)
    assert r["method"] in ("seasonal_naive", "ets", "sarima")
    assert "auto 选定" in r["note"] or "退回" in r["note"]
    assert r["mase_train"] is None or r["mase_train"] > 0


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        forecast(series(40), PERIODS[:40], method="prophet")


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        forecast(series(20), PERIODS[:10], method="seasonal_naive")


def test_mase_vs_naive_baseline():
    y = np.array(series(40))
    m0 = mase_vs_naive(list(y[-4:]), list(y[-4:]), list(y[:-4]))
    assert m0 == pytest.approx(0.0)                      # 完全命中 → 0
    m1 = mase_vs_naive(list(y[-4:] + 1.0), list(y[-4:]), list(y[:-4]))
    assert m1 > 0                                        # 误差 1 单位 → 正数


def test_forecast_on_real_shape_negative_values():
    # 经营现金流类序列可为负：additive 模型不应崩溃
    y = [-5.0, 12.0, -3.0, 20.0] * 5
    r = forecast(y, PERIODS[:20], method="sarima", horizon=2)
    assert len(r["point"]) == 2 and len(r["lower"]) == 2

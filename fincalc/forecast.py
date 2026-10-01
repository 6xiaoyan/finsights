"""P6 实现预测（plan 4.4/8.2）：seasonal_naive / ets / sarima / auto + 区间与 MASE。

P2 阶段仅保留接口占位（plan 0 规则 1：不提前实现后续阶段功能）。
"""
from __future__ import annotations


def forecast(values: list[float], periods: list[tuple], method: str, horizon: int, level: float) -> dict:
    """P6：点预测、80% 区间、所用方法、训练段内 MASE。序列 < 12 期时退回 seasonal_naive 并注明。"""
    raise NotImplementedError("forecast 在 P6 实现（plan 8.2）")

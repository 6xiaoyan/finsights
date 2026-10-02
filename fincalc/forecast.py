"""P6 预测（plan 8.2）：seasonal_naive / ets / sarima / auto + 区间与 MASE。

分工（plan 8.2 ⚠️ 裁定版）：数字只来自本模块，LLM 只选方法和解释，不得调整数字。
季度序列 seasonal 周期固定 m=4。训练段滚动验证（one-step，origin 递增）选 auto 方法，
MASE 分母 = seasonal_naive 在同窗口的平均绝对误差（Hyndman 定义）。
"""
from __future__ import annotations

import math
import warnings

import numpy as np

M = 4
MIN_TRAIN = 12       # 短于此退回 seasonal_naive（V6.7）
MIN_FIT = 8          # 模型拟合所需最少观测


def _naive_errs(y: np.ndarray) -> float:
    """seasonal_naive one-step 在样本内平均绝对误差（MASE 分母，去 0 保护）。"""
    if len(y) <= M:
        return float("nan")
    e = np.abs(y[M:] - y[:-M])
    m = float(e.mean())
    return m if m > 0 else 1e-12


def _seasonal_naive(y: np.ndarray, horizon: int) -> tuple[np.ndarray, float]:
    """点预测 = 去年同季度值（V6.6 完全相等）；σ 用 one-step 误差（缩放 sqrt(h)）。"""
    n = len(y)
    point = np.array([y[n - M + (h % M)] if n >= M else y[-1] for h in range(horizon)],
                     dtype=float)
    if n > M:
        errs = y[M:] - y[:-M]
        sigma = float(np.sqrt(np.mean(errs ** 2)))
    else:
        sigma = float(np.std(y)) if n > 1 else 0.0
    return point, sigma


def _ets(y: np.ndarray, horizon: int) -> tuple[np.ndarray, None]:
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = ETSModel(y, error="add", trend="add", seasonal="add",
                       seasonal_periods=M, damped_trend=True).fit(disp=False)
    # statsmodels 0.15 的 ETS get_prediction/conf_int 对纯 numpy 输入不可用：
    # 点预测用 predict，区间走下方 σ 兜底（残差 one-step × √h）
    point = np.asarray(res.predict(start=len(y), end=len(y) + horizon - 1), dtype=float)
    return point, res


def _sarima(y: np.ndarray, horizon: int) -> tuple[np.ndarray, object]:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = SARIMAX(y, order=(1, 0, 1), seasonal_order=(1, 1, 0, M),
                      enforce_stationarity=False,
                      enforce_invertibility=False).fit(disp=False)
    fc = res.get_forecast(horizon)
    return np.asarray(fc.predicted_mean, dtype=float), res


def _rolling_mase(y: np.ndarray, fitter, horizon: int) -> float | None:
    """训练段滚动 origin 的 one-step MASE；点数不足返回 None。"""
    start = max(M + 4, len(y) - 12)
    if len(y) <= start:
        return None
    true, pred = [], []
    for t in range(start, len(y)):
        try:
            p, _ = fitter(y[:t], 1)
            pred.append(float(p[0]))
            true.append(float(y[t]))
        except Exception:
            return None
    denom = _naive_errs(y[:start])
    if denom is None or math.isnan(denom):
        return None
    return float(np.mean(np.abs(np.array(true) - np.array(pred)))) / denom


_FITTERS = {"seasonal_naive": lambda y, h: (_seasonal_naive(y, h)[0], None),
            "ets": _ets, "sarima": _sarima}


def forecast(values: list[float], periods: list[tuple], method: str = "auto",
             horizon: int = 4, level: float = 0.8) -> dict:
    """点预测、区间、所用方法与训练段 MASE。periods 仅用于长度对齐与元信息。"""
    y = np.asarray(list(values), dtype=float)
    if len(y) != len(periods):
        raise ValueError("values 与 periods 长度不一致")
    note = ""
    if method not in _FITTERS and method != "auto":
        raise ValueError(f"未知方法: {method}（可用 seasonal_naive/ets/sarima/auto）")
    if method == "auto":
        if len(y) < MIN_TRAIN:
            method = "seasonal_naive"
            note = f"序列 {len(y)} 期 < {MIN_TRAIN}，auto 退回 seasonal_naive（V6.7）"
        else:
            scores = {m: _rolling_mase(y, f, horizon) for m, f in _FITTERS.items()}
            ok = {m: s for m, s in scores.items() if s is not None}
            if not ok:
                method = "seasonal_naive"
                note = "滚动验证不可用，退回 seasonal_naive"
            else:
                method = min(ok, key=lambda m: ok[m])
                note = "auto 选定: " + ", ".join(f"{m}={s:.3f}" for m, s in ok.items())
    elif len(y) < MIN_TRAIN and method in ("ets", "sarima"):
        note = f"序列 {len(y)} 期 < {MIN_TRAIN}（模型仍可拟合，但方差估计不稳）"

    if method == "seasonal_naive":
        point, sigma = _seasonal_naive(y, horizon)
        zs = _z(level)
        lower = point - zs * sigma * np.sqrt(np.arange(1, horizon + 1))
        upper = point + zs * sigma * np.sqrt(np.arange(1, horizon + 1))
        mase = None
    else:
        point, res = _FITTERS[method](y, horizon)
        try:
            ci = res.get_forecast(horizon).conf_int(alpha=1 - level)
            lower = np.asarray(ci[:, 0], dtype=float)
            upper = np.asarray(ci[:, 1], dtype=float)
        except Exception:  # 拟合退化：用 one-step 残差 σ×√h 兜底但仍报 method
            resid = getattr(res, "resid", None)
            if resid is not None and len(resid) > M:
                sigma = float(np.sqrt(np.mean(np.asarray(resid, dtype=float)[M:] ** 2)))
            else:
                sigma = _seasonal_naive(y, horizon)[1]
            zs = _z(level)
            lower = point - zs * sigma * np.sqrt(np.arange(1, horizon + 1))
            upper = point + zs * sigma * np.sqrt(np.arange(1, horizon + 1))
            note = (note + "; " if note else "") + "区间来自退化估计（模型 conf_int 不可用）"
        mase = _rolling_mase(y, _FITTERS[method], horizon)
    return {"method": method, "horizon": horizon, "level": level,
            "point": [round(float(v), 4) for v in point],
            "lower": [round(float(v), 4) for v in lower],
            "upper": [round(float(v), 4) for v in upper],
            "mase_train": None if mase is None else round(mase, 4),
            "last_periods": [list(p) for p in periods][-2:],
            "note": note}


def _z(level: float) -> float:
    from statistics import NormalDist
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def mase_vs_naive(true: list[float], pred: list[float], train: list[float]) -> float | None:
    """外部回测用：MASE（分母 = 训练段 seasonal_naive 平均绝对误差）。"""
    denom = _naive_errs(np.asarray(train, dtype=float))
    if denom is None or math.isnan(denom):
        return None
    return float(np.mean(np.abs(np.asarray(true, dtype=float) - np.asarray(pred, dtype=float)))) / denom

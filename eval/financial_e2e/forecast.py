"""历史时点预测题的事后误差统计（PRD §5.3）。

预测的真实实绩只保存在评测侧 `case.forecast_truth`，不进入事前得分点。
这里在评分后计算：绝对误差、相对误差（处理真实值为零/负）、区间是否覆盖真实值、区间宽度。
抽不到明确点预测时标 method=not_extracted，不臆测数值。
"""
from __future__ import annotations

import re

_POINT_RE = re.compile(r"预测值[:：]\s*(-?[\d,]+(?:\.\d+)?)")
_INTERVAL_RE = re.compile(
    r"(?:预测区间|区间)[:：]\s*(-?[\d,]+(?:\.\d+)?)\s*(?:~|～|至|-|—)\s*(-?[\d,]+(?:\.\d+)?)")
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _unit_scale(window: str, unit: str) -> float:
    """按邻近文字把答案单位换算到断言单位（仅处理常见倍数）。"""
    if unit == "usd_mn":
        if "亿美元" in window or "億美元" in window:
            return 100.0
        if "十亿美元" in window:
            return 1000.0
    return 1.0


def extract_point(text: str, unit: str) -> tuple[float | None, str]:
    text = text or ""
    m = _POINT_RE.search(text)
    if m:
        val = _to_float(m.group(1))
        return val * _unit_scale(text[max(0, m.start() - 20):m.end() + 20], unit), "contract_line"
    # 无契约行：取第一个较大的数字作为启发式点预测（标注 method，避免冒充精确抽取）
    for m in _NUM_RE.finditer(text):
        v = _to_float(m.group(0))
        if abs(v) >= 1000:
            return v * _unit_scale(text[max(0, m.start() - 20):m.end() + 20], unit), "heuristic"
    return None, "not_extracted"


def extract_interval(text: str, unit: str) -> tuple[tuple[float, float] | None, str]:
    text = text or ""
    m = _INTERVAL_RE.search(text)
    if m:
        lo, hi = _to_float(m.group(1)), _to_float(m.group(2))
        window = text[max(0, m.start() - 20):m.end() + 20]
        scale = _unit_scale(window, unit)
        return (min(lo, hi) * scale, max(lo, hi) * scale), "contract_line"
    return None, "not_extracted"


def forecast_metrics(case, answer_text: str) -> dict | None:
    """返回 None 表示该题无评测侧真实实绩（如目标期间尚未披露）。"""
    truth = case.forecast_truth
    if not truth:
        return None
    actual = truth["value"]
    unit = truth.get("unit", "usd_mn")
    point, pm = extract_point(answer_text, unit)
    interval, im = extract_interval(answer_text, unit)

    abs_err = pct_err = None
    if point is not None:
        abs_err = abs(point - actual)
        denom = abs(actual)
        pct_err = (abs_err / denom) if denom > 0 else None   # 真实值为 0 时不报相对误差
    covered = None
    width = None
    if interval is not None:
        lo, hi = interval
        covered = lo <= actual <= hi
        width = hi - lo
    return {
        "metric": truth.get("metric"), "period": truth.get("period"), "unit": unit,
        "actual": actual, "point": point, "point_method": pm,
        "abs_err": abs_err, "pct_err": (round(pct_err, 4) if pct_err is not None else None),
        "pct_err_note": ("真实值为零，未计算相对误差" if point is not None and actual == 0 else None),
        "interval": list(interval) if interval else None, "interval_method": im,
        "covered": covered, "width": width,
        "truth_source": truth.get("source"),
        "note": "事后误差统计，仅评测侧；不进入事前得分点或生成预测的 Agent。",
    }

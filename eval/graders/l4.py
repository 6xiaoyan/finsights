"""P6.3：L4 打分器（plan 8.3）。

correct = status=answered ∧ 三行契约齐全（预测值/预测区间/方法） ∧ 点预测在 gold 容差内
∧ 三个预测数字的 claims 都引用 forecast 工具结果的 rid（V6.11）。
MASE 分母 = 截至 as_of 的训练段 seasonal_naive 平均绝对误差（V6.10 基线口径）。
"forecast 工具直接输出"一组来自 scripts/backtest_forecast.py 写的
eval/reports/forecast_backtest.json（零 LLM 成本回测）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from eval.schema import Answer, Question
from fincalc.forecast import mase_vs_naive

BASE_DB = Path("data/finsights.duckdb")
BACKTEST_JSON = Path("eval/reports/forecast_backtest.json")

RE_POINT = re.compile(r"^预测值[:：]\s*(-?\d[\d,]*(?:\.\d+)?)\s*$", re.M)
RE_INT = re.compile(r"^预测区间[:：]\s*(-?\d[\d,]*(?:\.\d+)?)\s*~\s*(-?\d[\d,]*(?:\.\d+)?)\s*$", re.M)
RE_METHOD = re.compile(r"^方法[:：]\s*\S.*$", re.M)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _training_series(company: str, metric: str, as_of: str, db: Path = BASE_DB) -> list[float]:
    """as_of 时点可见的 original 序列（MASE 分母用；gold 侧代码，允许读基础库）。"""
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT f.value FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)"
            " WHERE f.version='original' AND p.company_id=? AND f.account_code=?"
            " AND f.available_date <= ? ORDER BY p.period_end",
            [company, metric, str(as_of)]).df()
    finally:
        con.close()
    return [float(v) for v in df["value"]]


def _claims_ref(answer: Answer, value: float, rids: set) -> bool:
    tol = max(1e-9, 0.005 * abs(value))
    return any(abs(c.value - value) <= tol and c.ref in rids for c in answer.claims)


def grade(question: Question, answer: Answer, trace: list | None = None) -> dict:
    base = {"mase": None, "covered": False, "provenance_ok": False, "abs_pct_err": None}
    if answer.status != "answered":
        return {"correct": False, "detail": f"status={answer.status}", **base}
    text = answer.answer_md
    mp, mi, mm = RE_POINT.search(text), RE_INT.search(text), RE_METHOD.search(text)
    if not (mp and mi and mm):
        miss = [n for n, g in (("预测值", mp), ("预测区间", mi), ("方法", mm)) if g is None]
        return {"correct": False, "detail": f"缺少契约行: {'、'.join(miss)}", **base}
    point = _num(mp.group(1))
    lo, hi = _num(mi.group(1)), _num(mi.group(2))
    actual = question.gold.value
    abs_pct = abs(point - actual) / abs(actual)
    within = abs(point - actual) <= question.gold.tolerance * abs(actual)
    covered = lo <= actual <= hi
    forecast_rids = {e.get("result_id") for e in (trace or [])
                     if e.get("type") == "tool_result" and e.get("tool") == "forecast"
                     and e.get("ok")}
    prov = all(_claims_ref(answer, v, forecast_rids) for v in (point, lo, hi))
    mase = None
    mase_err = ""
    try:
        train = _training_series(question.gold_method["company"],
                                 question.gold_method["metric"], question.as_of)
        mase = mase_vs_naive([actual], [point], train)
    except Exception as e:  # 取不到训练段：MASE 记 None，不吞成"0"，在 detail 里说明
        mase_err = f"；MASE 计算失败: {type(e).__name__}: {e}"
    correct = within and prov
    detail = (f"点预测 {point:g} vs 实际 {actual:g}（误差 {abs_pct:.1%}，容差 "
              f"{question.gold.tolerance:.0%}{'✓' if within else '✗'}）；"
              f"区间含实际={'是' if covered else '否'}；V6.11 溯源={'通过' if prov else '未通过'}"
              + mase_err)
    return {"correct": correct, "detail": detail, "mase": None if mase is None else round(mase, 4),
            "covered": covered, "provenance_ok": prov, "abs_pct_err": round(abs_pct, 4),
            "point": point}


def aggregate_agent(records: list[dict]) -> dict:
    """records = runner summary 里 category=L4 的行（含 correct 与 metrics）。"""
    n = len(records)
    ms = [r.get("metrics") or {} for r in records]
    mases = [m["mase"] for m in ms if m.get("mase") is not None]
    return {
        "n": n,
        "acc": sum(bool(r.get("correct")) for r in records) / n if n else None,
        "mase_mean": sum(mases) / len(mases) if mases else None,
        "coverage": sum(bool(m.get("covered")) for m in ms) / n if n else None,
        "provenance": sum(bool(m.get("provenance_ok")) for m in ms) / n if n else None,
    }


def load_backtest(path: Path = BACKTEST_JSON) -> dict | None:
    """V6.10 第二组：forecast 工具直接输出的全量回测成绩（未跑过返回 None）。"""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("overall")

"""P6.3 验收：L4 题库无泄露（V6.9）、打分器契约与溯源（V6.11）、forecast 工具接线。全离线。"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.result_store import ResultStore  # noqa: E402
from agent.tools.data import ToolContext, execute  # noqa: E402
from eval.graders.l4 import aggregate_agent, grade, load_backtest  # noqa: E402
from eval.runner import load_questions  # noqa: E402
from eval.schema import Answer, Claim  # noqa: E402
from etl.snapshot import make_snapshot  # noqa: E402

BASE = Path("data/finsights.duckdb")
QS = {q.id: q for q in load_questions("l4")}


def _fy(label: str) -> tuple[int, int]:
    m = re.match(r"^FY(\d{4})Q([1-4])$", label)
    return int(m.group(1)), int(m.group(2))


def _fact(con, company, account, fy, fq, version="original"):
    return con.execute(
        "SELECT value, available_date FROM facts WHERE version=? AND company_id=?"
        " AND account_code=? AND fiscal_year=? AND fiscal_quarter=?",
        [version, company, account, fy, fq]).fetchall()


# ---------------- 题库形状（plan：L4=4 题，营收/应收/存货 × 不同公司）

def test_dataset_shape():
    assert len(QS) == 4
    for q in QS.values():
        assert q.category == "L4" and q.db == "base" and q.as_of
        assert q.gold and q.gold.unit == "usd_mn" and q.gold_method["fn"] == "forecast"
        assert "预测值" in q.question and "预测区间" in q.question


# ---------------- V6.9 回测题目没有泄露

@pytest.mark.parametrize("qid", sorted(QS))
def test_v69_no_leak(qid, tmp_path):
    q = QS[qid]
    fy, fq = _fy(q.gold_method["period"])
    steps = q.gold_method.get("steps", 1)
    as_of = date.fromisoformat(q.as_of)
    con = duckdb.connect(str(BASE), read_only=True)
    rows = _fact(con, q.gold_method["company"], q.gold_method["metric"], fy, fq)
    assert len(rows) == 1
    value, avail = rows[0]
    assert avail > as_of, "目标值在 as_of 时点尚未披露"
    assert abs(float(value) - q.gold.value) < 1e-6, "gold 与库中实际值不一致"
    # 上一季度（训练段末期）在 as_of 前已披露
    pfy, pfq = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
    prev = _fact(con, q.gold_method["company"], q.gold_method["metric"], pfy, pfq)
    assert prev and prev[0][1] <= as_of
    assert steps == 1 or steps >= 1
    con.close()
    snap = make_snapshot(as_of, BASE, tmp_path)
    scon = duckdb.connect(str(snap), read_only=True)
    assert _fact(scon, q.gold_method["company"], q.gold_method["metric"], fy, fq) == [], \
        "目标值出现在快照中"
    assert scon.execute("SELECT max(available_date) FROM facts").fetchone()[0] <= as_of
    scon.close()


# ---------------- 打分器契约（V6.11 溯源 + 容差 + 覆盖）

def _answer(point: float, lo: float, hi: float, method: str, ref: str) -> Answer:
    md = (f"根据模型，结果如下。\n预测值: {point}\n预测区间: {lo} ~ {hi}\n方法: {method}\n"
          f"（区间宽度反映{ref}的不确定性）")
    return Answer(answer_md=md, status="answered", claims=[
        Claim(text="预测值", value=point, unit="usd_mn", ref=ref),
        Claim(text="区间下界", value=lo, unit="usd_mn", ref=ref),
        Claim(text="区间上界", value=hi, unit="usd_mn", ref=ref)])


def _trace(rid: str = "r7", tool: str = "forecast") -> list[dict]:
    return [{"type": "tool_result", "step": 3, "tool": tool, "result_id": rid, "ok": True}]


def test_grader_correct_path():
    q = QS["l4_003"]  # HP inventory FY2026Q2，gold 9203
    g = grade(q, _answer(8900.0, 8000.0, 9800.0, "ets", "r7"), _trace())
    assert g["correct"] and g["covered"] and g["provenance_ok"]
    assert g["mase"] is not None and g["mase"] > 0


def test_grader_provenance_violation():
    q = QS["l4_003"]
    g = grade(q, _answer(8900.0, 8000.0, 9800.0, "ets", "r2"), _trace("r2", "query_metric"))
    assert not g["correct"] and not g["provenance_ok"]
    # 数字都对但没有 forecast rid 引用（V6.11）→ 不算对


def test_grader_out_of_tolerance():
    q = QS["l4_003"]
    g = grade(q, _answer(11000.0, 10000.0, 12000.0, "sarima", "r7"), _trace())
    assert not g["correct"] and not g["covered"] and g["provenance_ok"]


def test_grader_missing_contract_line():
    q = QS["l4_003"]
    ans = _answer(8900.0, 8000.0, 9800.0, "ets", "r7")
    ans.answer_md = ans.answer_md.replace("预测区间: 8000.0 ~ 9800.0\n", "")
    g = grade(q, ans, _trace())
    assert not g["correct"] and "预测区间" in g["detail"]


def test_grader_refused_status():
    q = QS["l4_001"]
    g = grade(q, Answer(answer_md="数据不足", status="refuse"), [])
    assert not g["correct"] and "refuse" in g["detail"]


def test_aggregate_and_backtest_loader():
    recs = [{"correct": True, "metrics": {"mase": 0.5, "covered": True, "provenance_ok": True}},
            {"correct": False, "metrics": {"mase": None, "covered": False, "provenance_ok": False}}]
    agg = aggregate_agent(recs)
    assert agg["n"] == 2 and agg["acc"] == 0.5 and agg["coverage"] == 0.5
    assert agg["mase_mean"] == 0.5 and agg["provenance"] == 0.5
    assert load_backtest(Path("eval/reports/__not_here__.json")) is None


# ---------------- forecast 工具接线（rid 进 store；快照下只见 as_of 前历史）

def test_forecast_tool_base_db():
    ctx = ToolContext(db_path=str(BASE), store=ResultStore("l4t"))
    o = execute("forecast", {"company": "HP", "metric": "inventory",
                             "method": "seasonal_naive", "horizon": 2}, ctx)
    assert o.ok and o.rid
    df = o.stored.df
    assert len(df) == 2 and list(df["step"]) == [1, 2]
    con = duckdb.connect(str(BASE), read_only=True)
    vals = [r[0] for r in con.execute(
        "SELECT f.value FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)"
        " WHERE f.version='original' AND f.account_code='inventory' AND p.company_id='HP'"
        " ORDER BY p.period_end").fetchall()]
    con.close()
    assert df["point"].iloc[0] == pytest.approx(vals[-4], rel=1e-6)  # 去年同季度（V6.6）
    assert o.stored.unit == "usd_mn"
    # 第二次同参数调用命中 store 缓存（STORED_TOOLS）
    o2 = execute("forecast", {"company": "HP", "metric": "inventory",
                              "method": "seasonal_naive", "horizon": 2}, ctx)
    assert o2.rid == o.rid


def test_forecast_tool_respects_as_of(tmp_path):
    snap = make_snapshot(date(2026, 2, 25), BASE, tmp_path)
    ctx = ToolContext(db_path=str(snap), store=ResultStore("l4t2"))
    o = execute("forecast", {"company": "HP", "metric": "inventory",
                             "method": "seasonal_naive", "horizon": 1}, ctx)
    assert o.ok
    row = o.stored.df.iloc[0]
    assert row["period"] == "FY2026Q2"           # 快照末期 FY2026Q1 的下一季度
    assert row["point"] == pytest.approx(8175.0)  # 去年同季度 FY2025Q2（快照内 vals[-4]）
    con = duckdb.connect(str(snap), read_only=True)
    n = con.execute("SELECT count(*) FROM facts WHERE account_code='inventory'"
                    " AND company_id='HP'").fetchone()[0]
    con.close()
    assert row["history_len"] == n                # 只用了快照内可见历史

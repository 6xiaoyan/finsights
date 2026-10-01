"""P3.1：L1/L2 模板出题（plan 5.2）——10 题，标准答案全部由代码查库或用 fincalc 计算。

规模是用户决策（30 题总集的一部分）：L1 = 4、L2 = 6，**不要扩充**。
覆盖要求（V3.3）：
- L1：至少 1 题自然季度写法、至少 1 题"亿美元"；
- L2：DIO/DSO 趋势、毛利率、环比、三家对比各至少 1 题。

gold 复现（V3.2）：重新执行 L1 的 gold_sql、对 L2 重跑本模块的 fincalc 计算路径，
与数据集里存的 gold.value 比较。
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from fincalc.calc import peer_compare, qoq, working_capital, yoy

DB = Path("data/finsights.duckdb")
DATASETS = Path("eval/datasets")


def _frame(con, company: str, fy_from: int, q_from: int, fy_to: int, q_to: int) -> pd.DataFrame:
    """按财年区间取一公司的周转计算输入帧（period_end 升序）。"""
    sql = f"""SELECT p.company_id AS company, p.period_end, p.days, p.fiscal_year, p.fiscal_quarter,
       MAX(CASE WHEN f.account_code='inventory' THEN f.value END) AS inventory,
       MAX(CASE WHEN f.account_code='accounts_receivable' THEN f.value END) AS accounts_receivable,
       MAX(CASE WHEN f.account_code='accounts_payable' THEN f.value END) AS accounts_payable,
       MAX(CASE WHEN f.account_code='revenue' THEN f.value END) AS revenue,
       MAX(CASE WHEN f.account_code='cogs' THEN f.value END) AS cogs,
       MAX(CASE WHEN f.account_code='gross_profit' THEN f.value END) AS gross_profit
FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
WHERE f.version='original' AND p.company_id='{company}'
  AND (p.fiscal_year*10+p.fiscal_quarter BETWEEN {fy_from*10+q_from} AND {fy_to*10+q_to})
GROUP BY 1,2,3,4,5 ORDER BY p.period_end"""
    return con.execute(sql).df()


def _one(con, company: str, fy: int, q: int, account: str) -> float:
    return float(con.execute(
        "SELECT f.value FROM facts f JOIN periods p USING(company_id,fiscal_year,fiscal_quarter)"
        " WHERE p.company_id=? AND p.fiscal_year=? AND p.fiscal_quarter=? AND f.account_code=? AND f.version='original'",
        [company, fy, q, account]).fetchone()[0])


def _sql_l1(company: str, fy: int, q: int, account: str, scale: float = 1.0) -> str:
    return (f"SELECT f.value{'/100.0' if scale != 1.0 else ''} AS value FROM facts f "
            f"JOIN periods p USING(company_id,fiscal_year,fiscal_quarter) "
            f"WHERE p.company_id='{company}' AND p.fiscal_year={fy} AND p.fiscal_quarter={q} "
            f"AND f.account_code='{account}' AND f.version='original'")


def _sql_calendar(company: str, cq: str, account: str) -> str:
    return (f"SELECT f.value AS value FROM facts f "
            f"JOIN periods p USING(company_id,fiscal_year,fiscal_quarter) "
            f"WHERE p.company_id='{company}' AND p.calendar_quarter='{cq}' "
            f"AND f.account_code='{account}' AND f.version='original'")


def build_questions(con) -> list[dict]:
    qs: list[dict] = []

    # ---------- L1：单科目、单期、单公司 ----------
    v = _one(con, "Lenovo", 2024, 3, "inventory")
    qs.append(dict(id="l1_0001", category="L1",
                   question="联想 FY2024 第三季度（截至 2023 年 12 月的季度）的存货是多少百万美元？",
                   gold=dict(value=round(v, 2), unit="usd_mn", tolerance=0.005),
                   gold_sql=_sql_l1("Lenovo", 2024, 3, "inventory"),
                   tags=["inventory", "lenovo", "fiscal"]))

    v = float(con.execute(_sql_calendar("HP", "2024Q3", "total_assets")).fetchone()[0])
    qs.append(dict(id="l1_0002", category="L1",
                   question="按自然季度计算，2024 年第三季度（7–9 月）末 HP 的总资产是多少百万美元？",
                   gold=dict(value=round(v, 2), unit="usd_mn", tolerance=0.005),
                   gold_sql=_sql_calendar("HP", "2024Q3", "total_assets"),
                   tags=["total_assets", "hp", "calendar"]))

    v = _one(con, "Dell", 2025, 1, "revenue") / 100.0
    qs.append(dict(id="l1_0003", category="L1",
                   question="戴尔 FY2025 第一季度的营收是多少亿美元？（保留两位小数）",
                   gold=dict(value=round(v, 2), unit="usd_100mn", tolerance=0.005),
                   gold_sql=_sql_l1("Dell", 2025, 1, "revenue", scale=100.0),
                   tags=["revenue", "dell", "unit_conversion"]))

    v = _one(con, "HP", 2025, 2, "accounts_payable")
    qs.append(dict(id="l1_0004", category="L1",
                   question="HP FY2025 第二季度末的应付账款是多少百万美元？",
                   gold=dict(value=round(v, 2), unit="usd_mn", tolerance=0.005),
                   gold_sql=_sql_l1("HP", 2025, 2, "accounts_payable"),
                   tags=["accounts_payable", "hp", "fiscal"]))

    # ---------- L2：计算 / 分析 ----------
    df = _frame(con, "Lenovo", 2024, 4, 2025, 1)
    ccc = float(working_capital(df).iloc[-1]["ccc"])
    qs.append(dict(id="l2_0001", category="L2",
                   question="联想 FY2025 第一季度的现金转换周期 CCC 是多少天？（DSO+DIO−DPO，周转天数用平均余额和实际天数）",
                   gold=dict(value=round(ccc, 2), unit="days", tolerance=0.005),
                   gold_method={"fn": "working_capital", "company": "Lenovo", "window": ["FY2024Q4", "FY2025Q1"], "col": "ccc"},
                   gold_sql=None, tags=["ccc", "lenovo", "working_capital"]))

    df = _frame(con, "Dell", 2024, 1, 2025, 2)
    wc = working_capital(df)
    dso_yoy = yoy(wc.rename(columns={"dso": "value"}))[["change_abs"]].iloc[-1, 0]
    qs.append(dict(id="l2_0002", category="L2",
                   question="戴尔 FY2025 第二季度的 DSO（应收账款周转天数）比 FY2024 第二季度同期变化了多少天？（正数表示变长）",
                   gold=dict(value=round(float(dso_yoy), 2), unit="days", tolerance=0.005),
                   gold_method={"fn": "working_capital+yoy", "company": "Dell", "window": ["FY2024Q1..FY2025Q2"], "col": "dso"},
                   tags=["dso", "trend", "dell", "yoy"]))

    df = _frame(con, "HP", 2025, 2, 2025, 3)
    dio = float(working_capital(df).iloc[-1]["dio"])
    qs.append(dict(id="l2_0003", category="L2",
                   question="HP FY2025 第三季度的存货周转天数 DIO 是多少天？（平均余额/销售成本×实际天数）",
                   gold=dict(value=round(dio, 2), unit="days", tolerance=0.005),
                   gold_method={"fn": "working_capital", "company": "HP", "window": ["FY2025Q2", "FY2025Q3"], "col": "dio"},
                   tags=["dio", "trend", "hp"]))

    gp = _one(con, "Lenovo", 2025, 1, "gross_profit")
    rev = _one(con, "Lenovo", 2025, 1, "revenue")
    qs.append(dict(id="l2_0004", category="L2",
                   question="联想 FY2025 第一季度的毛利率是多少（百分比，保留两位小数）？",
                   gold=dict(value=round(gp / rev * 100, 2), unit="pct", tolerance=0.005),
                   gold_method={"fn": "gross_margin", "company": "Lenovo", "period": "FY2025Q1"},
                   tags=["gross_margin", "lenovo"]))

    df = _frame(con, "Dell", 2025, 1, 2025, 2)
    chg = float(qoq(df.rename(columns={"revenue": "value"})).iloc[-1]["change_pct"]) * 100
    qs.append(dict(id="l2_0005", category="L2",
                   question="戴尔 FY2025 第二季度的营收环比变化是多少（百分比）？",
                   gold=dict(value=round(chg, 2), unit="pct", tolerance=0.005),
                   gold_method={"fn": "qoq", "company": "Dell", "window": ["FY2025Q1", "FY2025Q2"], "metric": "revenue"},
                   tags=["qoq", "revenue", "dell"]))

    cur, prev = {}, {}
    rows = con.execute("""SELECT p.company_id, p.fiscal_year, p.fiscal_quarter, p.period_end, f.value
FROM facts f JOIN periods p USING(company_id,fiscal_year,fiscal_quarter)
WHERE f.version='original' AND f.account_code='revenue'
  AND p.company_id IN ('Lenovo','HP','Dell')
  AND (p.calendar_quarter='2024Q3'
       OR (p.company_id='Dell' AND p.fiscal_year=2025 AND p.fiscal_quarter=1)
       OR (p.company_id='HP' AND p.fiscal_year=2024 AND p.fiscal_quarter=2)
       OR (p.company_id='Lenovo' AND p.fiscal_year=2025 AND p.fiscal_quarter=1))
ORDER BY p.company_id, p.period_end""").df()
    for cid, g in rows.groupby("company_id"):
        g = g.sort_values("period_end")
        cur[cid], prev[cid] = float(g.iloc[-1]["value"]), float(g.iloc[-2]["value"])
    peers = peer_compare(cur, prev)
    top = peers[0]
    qs.append(dict(id="l2_0006", category="L2",
                   question="按自然季度看，2024 年第三季度三家公司（联想、HP、戴尔）中营收环比变化最大的是多少（百分比）？各家的环比怎么排？",
                   gold=dict(value=round(top["change_pct"] * 100, 2), unit="pct", tolerance=0.005),
                   gold_method={"fn": "peer_compare", "metric": "revenue", "calendar_quarter": "2024Q3",
                                "expected_top": top["company"]},
                   tags=["peer_compare", "revenue", "three_companies"]))

    for q in qs:
        q.setdefault("db", "base")
        q.setdefault("as_of", None)
    return qs


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    qs = build_questions(con)
    con.close()
    DATASETS.mkdir(parents=True, exist_ok=True)
    for cat, fname in (("L1", "l1.jsonl"), ("L2", "l2.jsonl")):
        rows = [q for q in qs if q["category"] == cat]
        (DATASETS / fname).write_text(
            "\n".join(json.dumps(q, ensure_ascii=False) for q in rows) + "\n", encoding="utf-8")
        print(f"{fname}: {len(rows)} 题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

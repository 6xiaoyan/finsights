"""重建 financial_real_v1 数据集（30 题）与 sources.json。

用法：`.venv\\Scripts\\python eval/datasets/financial_real_v1/build_cases.py`
从 data/finsights.duckdb 计算精确数值，从 data/raw/lenovo/*.pdf 定位页码锚点，
不会把任何题的审核状态写成 approved（生成器不得自批）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

import duckdb  # noqa: E402

from eval.contracts import NumericAssertion, ScoringPoint  # noqa: E402
from eval.financial_e2e.pageindex import find_value_page  # noqa: E402
from eval.financial_e2e.schema import CaseRecord, save_dataset  # noqa: E402
from eval.financial_e2e.sources import load_manifest, source_for, write_sources_index  # noqa: E402

MAN = load_manifest()
SCOPE = "联想集团合并口径公开财务数据（单位：百万美元）"
OUT = Path("eval/datasets/financial_real_v1/cases.jsonl")

_con = duckdb.connect("data/finsights.duckdb", read_only=True)


def g(metric, fy, q):
    r = _con.execute("SELECT value FROM facts WHERE company_id='Lenovo' AND account_code=? "
                     "AND fiscal_year=? AND fiscal_quarter=? AND version='original'",
                     [metric, fy, q]).fetchone()
    return float(r[0]) if r and r[0] is not None else None


def ysum(metric, fy):
    r = _con.execute("SELECT SUM(value) FROM facts WHERE company_id='Lenovo' AND account_code=? "
                     "AND fiscal_year=? AND fiscal_quarter BETWEEN 1 AND 4 AND version='original'",
                     [metric, fy]).fetchone()
    return float(r[0]) if r and r[0] is not None else None


def qsum(metric, fy, quarters):
    return sum(g(metric, fy, q) for q in quarters)


def pct(x):
    return f"{x:.2f}%"


def f2(x):
    return f"{x:,.2f}"


def d2(x):
    return f"{x:+,.2f}"


def pp(x):
    return f"{x:+.2f}pp"


def page_of(fy, q, value, fallback=None):
    d = MAN[(fy, q)]
    return find_value_page(d["file"], value) or fallback


def src(fy, q, page, row=None, col=None, note=None, period=None):
    if page is not None:
        page = int(str(page).lstrip("pP"))
    return source_for(fy, q, page=page, table_row=row, table_col=col, note=note,
                      period=period, manifest=MAN)


def sp(pid, judgment, *, req="required", weight=1.0, ev=None, eq=None, contra=None,
       cond=None, verify=None, prereq=None):
    return ScoringPoint(point_id=pid, atomic_judgment=judgment, condition=cond, required=req,
                        weight=weight, evidence_source=ev or [], acceptable_equivalents=eq or [],
                        contradiction_examples=contra or [], verification=verify,
                        prerequisites=prereq or [])


def na(metric, period, value, unit, *, baseline=None, direction="unknown", formula=None,
       tol=0.005, tol_abs=None, cat="core"):
    return NumericAssertion(metric=metric, company="Lenovo", period=period, value=value,
                            unit=unit, baseline_period=baseline, direction=direction, formula=formula,
                            tolerance=tol, tolerance_abs=tol_abs, category=cat)


CASES: list[CaseRecord] = []


def add(**kw):
    CASES.append(CaseRecord(**kw))


def IS(fy, q, fallback=12):
    """利润表页锚点：优先用毛利所在页（比摘要页更可能是完整利润表），退回收入页。"""
    return page_of(fy, q, g("gross_profit", fy, q)) or page_of(fy, q, g("revenue", fy, q), fallback)

# ============================================================ 多期比较（6）
# M1：FY2026 9M 毛利率 vs 三单季（S05）
r26_9m = qsum("revenue", 2026, (1, 2, 3))
gp26_9m = qsum("gross_profit", 2026, (1, 2, 3))
r25_9m = qsum("revenue", 2025, (1, 2, 3))
gp25_9m = qsum("gross_profit", 2025, (1, 2, 3))
gm26_9m, gm25_9m = gp26_9m / r26_9m * 100, gp25_9m / r25_9m * 100
qdelta = {q: (g("gross_profit", 2026, q) / g("revenue", 2026, q) * 100 -
              g("gross_profit", 2025, q) / g("revenue", 2025, q) * 100) for q in (1, 2, 3)}
add(
    case_id="LV-E2E-FY2026-9M-GM-WEIGHT-001",
    question=("联想集团 FY2026 前九个月毛利率同比下降，但三个单季的同比降幅互不相同、"
              "且都不等于前九个月整体降幅。请分别算出 FY2026 与 FY2025 前九个月的毛利率、"
              "三个单季的毛利率同比降幅，并解释为什么整体降幅与各单季降幅不相等。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026 前九个月收入 {f2(r26_9m)}、毛利 {f2(gp26_9m)}，毛利率 {pct(gm26_9m)}；"
        f"FY2025 前九个月毛利 {f2(gp25_9m)}、收入 {f2(r25_9m)}，毛利率 {pct(gm25_9m)}，"
        f"整体同比 {pp(gm26_9m - gm25_9m)}。"
        f"单季同比降幅：Q1 {pp(qdelta[1])}、Q2 {pp(qdelta[2])}、Q3 {pp(qdelta[3])}。"
        "原因：前九个月毛利率是各季毛利与收入按金额加权的比率；两年分别按当年收入权重"
        "算出整体毛利率后再相减。各季收入权重不同，整体降幅是加权结果，既不等于简单平均，"
        "也不等于任一单季降幅。"),
    sources=[src(2026, 3, IS(2026, 3), "收入/毛利", note="FY26 9M 利润表", period="FY2026 9M"),
             src(2026, 1, IS(2026, 1), "收入/毛利", period="FY2026Q1", note="FY26Q1 利润表"),
             src(2026, 2, IS(2026, 2), "收入/毛利", period="FY2026Q2", note="FY26Q2 利润表")],
    scoring_points=[
        sp("p1", f"给出 FY2026 前九个月毛利率约 {gm26_9m:.2f}%", ev=["9M 毛利率"]),
        sp("p2", f"给出 FY2025 前九个月毛利率约 {gm25_9m:.2f}%", ev=["上年同期 9M 毛利率"]),
        sp("p3", f"给出整体同比降幅约 {gm26_9m - gm25_9m:+.2f}pp", ev=["9M 同比"]),
        sp("p4", f"分别给出三个单季毛利率同比降幅：Q1 {qdelta[1]:+.2f}pp、Q2 {qdelta[2]:+.2f}pp、"
                 f"Q3 {qdelta[3]:+.2f}pp", ev=["单季降幅"]),
        sp("p5", "解释整体降幅是两年各自按当年收入权重加权后相减的结果，"
                 "与单季降幅、简单平均都不相等", prereq=["p1", "p4"],
           contra=["用单季降幅算术平均当作整体降幅"]),
        sp("p6", "说明单季数据来自各季公告、9M 由三季求和", req="optional"),
    ],
    numeric_assertions=[
        na("gross_margin", "FY2026 9M", gm26_9m, "pct", direction="down"),
        na("gross_margin", "FY2025 9M", gm25_9m, "pct", direction="down", cat="supporting"),
        na("gross_margin_delta", "FY2026 9M", gm26_9m - gm25_9m, "pct_point", baseline="FY2025 9M",
           direction="down"),
        na("gross_margin_delta", "FY2026Q1", qdelta[1], "pct_point", baseline="FY2025Q1",
           direction="down", cat="supporting"),
        na("gross_margin_delta", "FY2026Q2", qdelta[2], "pct_point", baseline="FY2025Q2",
           direction="down", cat="supporting"),
        na("gross_margin_delta", "FY2026Q3", qdelta[3], "pct_point", baseline="FY2025Q3",
           direction="down", cat="supporting"),
    ],
    forbidden_claims=["用三个单季降幅的算术平均冒充前九个月整体降幅"],
    reasoning_requirements=["多期求和", "加权 vs 简单平均", "单季与累计口径"],
    question_type="multi_period", origin="reused", selected_id="S05",
    event_family="LV-FY2026Q3-gm-drift", split_group="FY2026",
    data_visibility={"basis": "current_retrospective"},
)

# M2：FY2026 H1 vs Q2 背离（S03）
r26h1 = qsum("revenue", 2026, (1, 2))
r25h1 = qsum("revenue", 2025, (1, 2))
ni26h1 = qsum("net_income", 2026, (1, 2))
ni25h1 = qsum("net_income", 2025, (1, 2))
add(
    case_id="LV-E2E-FY2026-H1-VS-Q2-001",
    question=("联想集团 FY2026 上半年（截至 2025-09-30）收入与净利润同比明显增长，"
              "但同期单列的第二季度收入增速更低、净利润同比略降。请分开算出上半年与"
              "第二季度的收入、净利润同比，并说明'上半年高增长'与'第二季度利润下降'是否矛盾。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2026 上半年收入 {f2(r26h1)}（同比 {(r26h1/r25h1-1)*100:+.2f}%），"
        f"净利润 {f2(ni26h1)}（同比 {(ni26h1/ni25h1-1)*100:+.2f}%）。"
        f"FY2026Q2 收入 {f2(g('revenue',2026,2))}（同比 {(g('revenue',2026,2)/g('revenue',2025,2)-1)*100:+.2f}%），"
        f"净利润 {f2(g('net_income',2026,2))}（同比 "
        f"{(g('net_income',2026,2)/g('net_income',2025,2)-1)*100:+.2f}%）。"
        "两者不矛盾：上半年是两季之和，Q1 的强劲增长抬高了半年增速，掩盖了 Q2 单季的"
        "利润走弱；累计口径与单季口径必须分开陈述。"),
    sources=[src(2026, 2, IS(2026, 2), "收入/期内溢利", period="FY2026 H1 与 Q2",
                 note="FY26Q2 中期公告（H1 与 Q2 单季）")],
    scoring_points=[
        sp("p1", "给出上半年收入同比约 +18%", ev=["H1 收入同比"]),
        sp("p2", "给出上半年净利润同比约 +44%", ev=["H1 净利润同比"]),
        sp("p3", "给出第二季度收入同比约 +15%（低于半年增速）", ev=["Q2 收入同比"]),
        sp("p4", "给出第二季度净利润同比约 -1%（略降）", ev=["Q2 净利润同比"],
           contra=["称第二季度利润同比明显增长"]),
        sp("p5", "说明累计与单季口径不同、二者并不矛盾", prereq=["p1", "p4"]),
    ],
    numeric_assertions=[
        na("revenue_growth_pct", "FY2026 H1", (r26h1 / r25h1 - 1) * 100, "pct",
           baseline="FY2025 H1", direction="up"),
        na("net_income_growth_pct", "FY2026 H1", (ni26h1 / ni25h1 - 1) * 100, "pct",
           baseline="FY2025 H1", direction="up"),
        na("revenue_growth_pct", "FY2026Q2",
           (g("revenue", 2026, 2) / g("revenue", 2025, 2) - 1) * 100, "pct",
           baseline="FY2025Q2", direction="up"),
        na("net_income_growth_pct", "FY2026Q2",
           (g("net_income", 2026, 2) / g("net_income", 2025, 2) - 1) * 100, "pct",
           baseline="FY2025Q2", direction="down", tol=0.15),
    ],
    forbidden_claims=["把上半年累计增速当作单季增速", "称第二季度净利润明显增长"],
    reasoning_requirements=["累计 vs 单季", "同比"],
    question_type="multi_period", origin="reused", selected_id="S03",
    event_family="LV-FY2026Q2-h1-vs-q2-divergence", split_group="FY2026",
    data_visibility={"basis": "current_retrospective"},
)

# M3：累计转单季（FY2026Q4 = 全年 − 9M）
r26_q4 = g("revenue", 2026, 4)
gp26_q4 = g("gross_profit", 2026, 4)
add(
    case_id="LV-E2E-FY2026Q4-IMPLIED-QUARTER-001",
    question=("联想集团 FY2026 全年收入已知，前三季度累计收入已知。请用全年减前三季累计"
              "推出 FY2026Q4 单季收入与单季毛利率，并说明为什么直接用全年数字当作第四季度"
              "会出错。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026 全年收入 {f2(ysum('revenue',2026))}，前三季累计 {f2(r26_9m)}，"
        f"推出 FY2026Q4 单季收入 {f2(r26_q4)}；全年毛利 {f2(ysum('gross_profit',2026))}，"
        f"前三季累计毛利 {f2(gp26_9m)}，推出 Q4 单季毛利 {f2(gp26_q4)}，"
        f"Q4 单季毛利率 {pct(gp26_q4/r26_q4*100)}。直接用全年数字会高估 Q4 规模并混淆口径，"
        "因为全年是四个季度之和，与单季不可比。"),
    sources=[src(2026, 4, 17, "收入/毛利（全年欄）", note="FY26 全年利润表", period="FY2026 全年"),
             src(2026, 3, 16, "收入/毛利（9M 欄）", period="FY2026 9M")],
    scoring_points=[
        sp("p1", f"给出 FY2026Q4 单季收入约 {f2(r26_q4)} 百万美元", ev=["Q4 单季收入"]),
        sp("p2", "用全年减前三季累计的方法说明推导过程", prereq=["p1"]),
        sp("p3", f"给出 FY2026Q4 单季毛利率约 {gp26_q4/r26_q4*100:.1f}%", ev=["Q4 毛利率"]),
        sp("p4", "指出全年数字不等于单季，口径不可混用", contra=["把全年收入当作第四季度收入"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2026Q4", r26_q4, "usd_mn", formula="全年-9M", direction="up"),
        na("gross_margin", "FY2026Q4", gp26_q4 / r26_q4 * 100, "pct", direction="unknown",
           cat="supporting"),
    ],
    forbidden_claims=["把 FY2026 全年收入当作第四季度收入"],
    reasoning_requirements=["累计转单季", "口径区分"],
    question_type="multi_period", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q4-quarter-implied",
    data_visibility={"basis": "current_retrospective"},
)

# M4：单季 vs 全年 增长不同步（FY2025Q4）
add(
    case_id="LV-E2E-FY2025Q4-SINGLE-VS-ANNUAL-001",
    question=("联想集团 FY2025 全年收入与净利润均同比增长，但第四季度单季净利润同比大幅下降。"
              "请分别给出全年与第四季度的收入、净利润同比，并说明为什么单季下跌可以与全年"
              "增长同时出现。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2025 全年收入 {f2(ysum('revenue',2025))}（同比 "
        f"{(ysum('revenue',2025)/ysum('revenue',2024)-1)*100:+.2f}%），净利润 "
        f"{f2(ysum('net_income',2025))}（同比 "
        f"{(ysum('net_income',2025)/ysum('net_income',2024)-1)*100:+.2f}%）。"
        f"FY2025Q4 单季收入 {f2(g('revenue',2025,4))}（同比 "
        f"{(g('revenue',2025,4)/g('revenue',2024,4)-1)*100:+.2f}%），净利润 "
        f"{f2(g('net_income',2025,4))}（同比 "
        f"{(g('net_income',2025,4)/g('net_income',2024,4)-1)*100:+.2f}%）。"
        f"Q4 单季净利润直接用第四季度单季损益表；若改用全年减 9M 推导，"
        f"结果为 {f2(ysum('net_income',2025)-qsum('net_income',2025,(1,2,3)))}，两种口径一致。"
        "全年由四季构成，前三季的高低可以抵消第四季度的走弱，故单季与全年方向可以不同。"),
    sources=[src(2025, 4, 17, "收入/淨利（全年欄）", period="FY2025 全年"),
             src(2025, 4, 11, "收入（單季欄）", period="FY2025Q4"),
             src(2025, 4, 14, "期內溢利（單季欄）", period="FY2025Q4")],
    scoring_points=[
        sp("p1", f"给出 FY2025 全年收入同比约 {(ysum('revenue',2025)/ysum('revenue',2024)-1)*100:+.2f}%",
           ev=["全年收入"]),
        sp("p2", f"给出 FY2025 全年净利润同比约 "
                 f"{(ysum('net_income',2025)/ysum('net_income',2024)-1)*100:+.2f}%", ev=["全年净利润"]),
        sp("p3", f"给出 FY2025Q4 单季收入同比约 "
                 f"{(g('revenue',2025,4)/g('revenue',2024,4)-1)*100:+.2f}%", ev=["Q4 收入"]),
        sp("p4", f"给出 FY2025Q4 单季净利润同比约 "
                 f"{(g('net_income',2025,4)/g('net_income',2024,4)-1)*100:+.2f}%（下降）",
           ev=["Q4 净利润"], contra=["称 Q4 净利润同比增长"]),
        sp("p5", "解释全年是多季合计，单季走弱可被其它季度抵消，累计与单季结论不矛盾",
           prereq=["p2", "p4"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2025", ysum("revenue", 2025), "usd_mn", direction="up"),
        na("net_income", "FY2025", ysum("net_income", 2025), "usd_mn", direction="up"),
        na("revenue", "FY2025Q4", g("revenue", 2025, 4), "usd_mn", direction="up",
           baseline="FY2024Q4"),
        na("net_income", "FY2025Q4", g("net_income", 2025, 4), "usd_mn", direction="down",
           baseline="FY2024Q4"),
        na("revenue_growth_pct", "FY2025",
           (ysum("revenue", 2025) / ysum("revenue", 2024) - 1) * 100, "pct",
           baseline="FY2024", direction="up"),
        na("net_income_growth_pct", "FY2025",
           (ysum("net_income", 2025) / ysum("net_income", 2024) - 1) * 100, "pct",
           baseline="FY2024", direction="up"),
        na("revenue_growth_pct", "FY2025Q4",
           (g("revenue", 2025, 4) / g("revenue", 2024, 4) - 1) * 100, "pct",
           baseline="FY2024Q4", direction="up"),
        na("net_income_growth_pct", "FY2025Q4",
           (g("net_income", 2025, 4) / g("net_income", 2024, 4) - 1) * 100, "pct",
           baseline="FY2024Q4", direction="down"),
    ],
    forbidden_claims=["用全年增长否定第四季度单季下滑"],
    reasoning_requirements=["累计 vs 单季", "同比"],
    question_type="multi_period", origin="new", split_group="FY2025",
    event_family="LV-FY2025Q4-single-vs-annual",
    data_visibility={"basis": "current_retrospective"},
)

# M5：单位与规模（季度收入量级）
add(
    case_id="LV-E2E-FY2027Q1-REVENUE-SCALE-001",
    question=("联想集团 FY2027Q1 营业收入约 269 亿美元。请把它换算为百万美元，"
              "并给出同比增长金额与百分比；说明'约 269 亿'这一表述对应的精确量级，"
              "以及财务数据中常见的单位换算要注意什么。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2027Q1 收入 26,942.766 百万美元，即约 269.43 亿美元。同比 "
        f"{d2(g('revenue',2027,1)-g('revenue',2026,1))} 百万美元（"
        f"{(g('revenue',2027,1)/g('revenue',2026,1)-1)*100:+.2f}%）。"
        "'约 269 亿'是取整表述，精确值为 269.43 亿美元（26,942.766 百万美元）。"
        "单位换算要注意：公告以千美元列示，数据库以百万美元存储，"
        "换算不能改变金额口径，且不能把百万与亿混用。"),
    sources=[src(2027, 1, IS(2027, 1), "收入", period="FY2027Q1"),
             src(2026, 1, IS(2026, 1), "收入", period="FY2026Q1")],
    scoring_points=[
        sp("p1", "给出 FY2027Q1 收入约 26,943 百万美元（约 269.4 亿美元）", ev=["收入"]),
        sp("p2", f"给出同比增加约 {d2(g('revenue',2027,1)-g('revenue',2026,1))} 百万美元"
                 f"（约 81.13 亿美元）", ev=["同比金额"]),
        sp("p3", f"给出同比增速约 "
                 f"{(g('revenue',2027,1)/g('revenue',2026,1)-1)*100:+.2f}%",
           ev=["同比增速"]),
        sp("p4", "说明千美元/百万美元/亿的换算关系且不改变口径", ev=["单位说明"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2027Q1", g("revenue", 2027, 1), "usd_mn", direction="up"),
        na("revenue_growth", "FY2027Q1", g("revenue", 2027, 1) - g("revenue", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up"),
        na("revenue_growth_pct", "FY2027Q1",
           (g("revenue", 2027, 1) / g("revenue", 2026, 1) - 1) * 100, "pct",
           baseline="FY2026Q1", direction="up"),
    ],
    forbidden_claims=["把百万美元与亿美元混算"],
    reasoning_requirements=["单位换算", "同比"],
    question_type="multi_period", origin="new", split_group="FY2027Q1",
    event_family="LV-FY2027Q1-revenue-scale",
    data_visibility={"basis": "current_retrospective"},
)

# M6：环比（FY2026Q4 vs Q3）收入与毛利背离
add(
    case_id="LV-E2E-FY2026Q4-QOQ-DIVERGENCE-001",
    question=("联想集团 FY2026Q4 收入环比下降，但毛利环比上升。请给出两者的环比金额与"
              "百分比变化，并说明为什么收入与毛利可以环比反向。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026Q4 收入 {f2(g('revenue',2026,4))}，环比 "
        f"{(g('revenue',2026,4)/g('revenue',2026,3)-1)*100:+.2f}%（"
        f"{d2(g('revenue',2026,4)-g('revenue',2026,3))}）；毛利 {f2(g('gross_profit',2026,4))}，"
        f"环比 {(g('gross_profit',2026,4)/g('gross_profit',2026,3)-1)*100:+.2f}%（"
        f"{d2(g('gross_profit',2026,4)-g('gross_profit',2026,3))}）。"
        "收入环比下降而毛利上升，意味着毛利率环比提升：收入结构或成本变化使毛利占比上升，"
        "故二者可以反向。"),
    sources=[src(2026, 4, 17, "收入/毛利（全年欄）", period="FY2026Q4"),
             src(2026, 4, 11, "收入（单季欄）", period="FY2026Q4"),
             src(2026, 3, IS(2026, 3), "收入/毛利", period="FY2026Q3")],
    scoring_points=[
        sp("p1", f"同时给出 FY2026Q4 收入环比金额与百分比："
                 f"{d2(g('revenue',2026,4)-g('revenue',2026,3))} 百万美元、"
                 f"{(g('revenue',2026,4)/g('revenue',2026,3)-1)*100:+.2f}%",
           ev=["收入环比金额", "收入环比百分比"]),
        sp("p2", f"同时给出 FY2026Q4 毛利环比金额与百分比："
                 f"{d2(g('gross_profit',2026,4)-g('gross_profit',2026,3))} 百万美元、"
                 f"{(g('gross_profit',2026,4)/g('gross_profit',2026,3)-1)*100:+.2f}%",
           ev=["毛利环比金额", "毛利环比百分比"]),
        sp("p3", "指出二者反向意味着毛利率环比提升，故收入降、毛利升可共存", prereq=["p1", "p2"]),
        sp("p4", "说明环比与同比是不同比较基准，不能混用", req="optional"),
    ],
    numeric_assertions=[
        na("revenue", "FY2026Q4", g("revenue", 2026, 4), "usd_mn", direction="down",
           baseline="FY2026Q3"),
        na("gross_profit", "FY2026Q4", g("gross_profit", 2026, 4), "usd_mn", direction="up",
           baseline="FY2026Q3", formula="由全年欄减前三季累计推出"),
        na("revenue_growth", "FY2026Q4", g("revenue", 2026, 4) - g("revenue", 2026, 3),
           "usd_mn", baseline="FY2026Q3", direction="down", formula="本期-上季"),
        na("gross_profit_growth", "FY2026Q4", g("gross_profit", 2026, 4) - g("gross_profit", 2026, 3),
           "usd_mn", baseline="FY2026Q3", direction="up", formula="本期-上季"),
        na("revenue_growth_pct", "FY2026Q4",
           (g("revenue", 2026, 4) / g("revenue", 2026, 3) - 1) * 100, "pct",
           baseline="FY2026Q3", direction="down"),
        na("gross_profit_growth_pct", "FY2026Q4",
           (g("gross_profit", 2026, 4) / g("gross_profit", 2026, 3) - 1) * 100, "pct",
           baseline="FY2026Q3", direction="up"),
    ],
    forbidden_claims=["把环比变化当作同比变化"],
    reasoning_requirements=["环比", "比率与金额", "比较基准区分"],
    question_type="multi_period", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q4-qoq-divergence",
    data_visibility={"basis": "current_retrospective"},
)

# ============================================================ 变化分解与归因（10）
# A1：S04 FY2025Q3 费用率 56bps
def _opex(fy: int, quarters: tuple[int, ...]) -> float:
    """净经营费用 = 毛利 - 经营利润（含其他经营收支净额）。"""
    return (sum(g("gross_profit", fy, q) for q in quarters)
            - sum(g("operating_income", fy, q) for q in quarters))


def _rev(fy: int, quarters: tuple[int, ...]) -> float:
    return sum(g("revenue", fy, q) for q in quarters)


def _opex_ratio(fy: int, quarters: tuple[int, ...]) -> float:
    return _opex(fy, quarters) / _rev(fy, quarters) * 100


add(
    case_id="LV-E2E-FY2025Q3-OPEX-RATIO-001",
    question=("管理层称联想 FY2025Q3 '经营费用对收入比率同比下降 56 个基点至 12.1%'。"
              "请只用收入、毛利、经营利润（经营费用=毛利−经营利润）分别在 Q3 单季口径与"
              "截至 12 月 31 日止九个月累计口径下复算该比率的同比变化，"
              "判断管理层的说法对应哪个口径、是否成立，并说明比率下降是否等于费用收缩。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"Q3 单季经营费用 {f2(_opex(2025, (3,)))}（上年同期 {f2(_opex(2024, (3,)))}），"
        f"费用率 {_opex_ratio(2024, (3,)):.4f}%→{_opex_ratio(2025, (3,)):.4f}%，"
        f"同比 {( _opex_ratio(2025, (3,)) - _opex_ratio(2024, (3,))) * 100:+.2f}bps。"
        f"9M 累计费用率 {_opex_ratio(2024, (1, 2, 3)):.4f}%→{_opex_ratio(2025, (1, 2, 3)):.4f}%，"
        f"同比 {( _opex_ratio(2025, (1, 2, 3)) - _opex_ratio(2024, (1, 2, 3))) * 100:+.2f}bps。"
        "管理层的 56bps 对应 Q3 单季口径（-56.26bps），不适用于 9M 口径（-117.02bps）。"
        f"费用率下降不等于费用收缩：Q3 单季经营费用金额由 {f2(_opex(2024, (3,)))} 增至 "
        f"{f2(_opex(2025, (3,)))}（{d2(_opex(2025, (3,)) - _opex(2024, (3,)))}），"
        "费用金额实际增加，比率下降来自收入增长更快。"),
    sources=[src(2025, 3, IS(2025, 3), "收入/毛利/经营溢利（單季與 9M 欄）",
                 period="FY2025Q3 与 9M")],
    scoring_points=[
        sp("p1", f"给出 Q3 单季费用率同比：{_opex_ratio(2024, (3,)):.2f}%→"
                 f"{_opex_ratio(2025, (3,)):.2f}%，约 -56.26bps", ev=["Q3 单季费用率"]),
        sp("p2", f"给出 9M 累计费用率同比：{_opex_ratio(2024, (1, 2, 3)):.2f}%→"
                 f"{_opex_ratio(2025, (1, 2, 3)):.2f}%，约 -117.02bps", ev=["9M 累计费用率"]),
        sp("p3", "指出管理层的 56bps 对应 Q3 单季口径、在该口径成立，9M 口径不成立",
           prereq=["p1", "p2"]),
        sp("p4", f"给出 Q3 单季经营费用金额由 {f2(_opex(2024, (3,)))} 增至 "
                 f"{f2(_opex(2025, (3,)))}（+{_opex(2025, (3,)) - _opex(2024, (3,)):.2f}），"
                 "说明比率下降不等于费用金额收缩", ev=["金额 vs 比率"]),
    ],
    numeric_assertions=[
        na("opex_ratio", "FY2025Q3", _opex_ratio(2025, (3,)), "pct", direction="down"),
        na("opex_ratio", "FY2024Q3", _opex_ratio(2024, (3,)), "pct", direction="down",
           cat="supporting"),
        na("opex_ratio", "FY2025 9M", _opex_ratio(2025, (1, 2, 3)), "pct", direction="down"),
        na("opex_ratio", "FY2024 9M", _opex_ratio(2024, (1, 2, 3)), "pct", direction="down",
           cat="supporting"),
        na("opex_ratio_delta", "FY2025Q3", _opex_ratio(2025, (3,)) - _opex_ratio(2024, (3,)),
           "pct_point", baseline="FY2024Q3", direction="down"),
        na("opex_ratio_delta", "FY2025 9M",
           _opex_ratio(2025, (1, 2, 3)) - _opex_ratio(2024, (1, 2, 3)), "pct_point",
           baseline="FY2024 9M", direction="down"),
        na("operating_expense", "FY2025Q3", _opex(2025, (3,)), "usd_mn", direction="up",
           formula="毛利-经营利润", baseline="FY2024Q3"),
        na("operating_expense", "FY2024Q3", _opex(2024, (3,)), "usd_mn", direction="up",
           formula="毛利-经营利润", cat="supporting"),
    ],
    forbidden_claims=["仅凭费用率下降就断定费用金额收缩"],
    reasoning_requirements=["单季 vs 累计", "费用率 vs 金额", "复算"],
    question_type="attribution", origin="reused", selected_id="S04",
    event_family="LV-FY2025Q3-opex-ratio-56bps", split_group="FY2025Q3",
    data_visibility={"basis": "current_retrospective"},
)

# A2：FY2024 全年 收入→毛利→经营利润 桥
add(
    case_id="LV-E2E-FY2024-ANNUAL-BRIDGE-001",
    question=("联想集团 FY2024 全年收入同比下降，毛利率同比上升，经营利润同比下降。"
              "请用全年口径把收入、毛利、经营费用、经营利润的同比变化列成一条桥，"
              "并判断经营利润下降主要由哪一层驱动。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"金额桥（FY2024 对 FY2023）：收入 {f2(ysum('revenue',2024))}（同比 "
        f"{(ysum('revenue',2024)-ysum('revenue',2023)):+,.2f}）；毛利 "
        f"{f2(ysum('gross_profit',2024))}（{d2(ysum('gross_profit',2024)-ysum('gross_profit',2023))}）；"
        f"净经营费用 {f2(_opex(2024,(1,2,3,4)))}（{d2(_opex(2024,(1,2,3,4))-_opex(2023,(1,2,3,4)))}）；"
        f"经营利润 {f2(ysum('operating_income',2024))}"
        f"（{d2(ysum('operating_income',2024)-ysum('operating_income',2023))}）。"
        f"金额上 Δ经营利润 = Δ毛利 - Δ净经营费用 = "
        f"{d2(ysum('gross_profit',2024)-ysum('gross_profit',2023))} - "
        f"({d2(_opex(2024,(1,2,3,4))-_opex(2023,(1,2,3,4)))}) = "
        f"{d2(ysum('operating_income',2024)-ysum('operating_income',2023))}，"
        "主驱动是毛利减少（-697.909），费用金额下降（-34.870）只起到小幅缓冲。"
        f"毛利率由 {ysum('gross_profit',2023)/ysum('revenue',2023)*100:.2f}% 升至 "
        f"{ysum('gross_profit',2024)/ysum('revenue',2024)*100:.2f}%；"
        f"费用率由 {_opex_ratio(2023,(1,2,3,4)):.2f}% 升至 {_opex_ratio(2024,(1,2,3,4)):.2f}%。"
        "费用率上升说明利润率被侵蚀，但不等于费用金额增加——两者必须分开陈述。"),
    sources=[src(2024, 4, 17, "收入/毛利/经营溢利（全年欄）", period="FY2024 全年"),
             src(2023, 4, 18, "收入/毛利/经营溢利（全年欄）", period="FY2023 全年")],
    scoring_points=[
        sp("p1", f"给出 FY2024 全年收入同比减少约 "
                 f"{ysum('revenue',2024)-ysum('revenue',2023):,.2f}"
                 f"（{(ysum('revenue',2024)/ysum('revenue',2023)-1)*100:+.2f}%）", ev=["收入金额"]),
        sp("p2", f"给出毛利同比减少约 "
                 f"{ysum('gross_profit',2024)-ysum('gross_profit',2023):,.2f}，"
                 f"同时毛利率由 {ysum('gross_profit',2023)/ysum('revenue',2023)*100:.2f}% 升至 "
                 f"{ysum('gross_profit',2024)/ysum('revenue',2024)*100:.2f}%", ev=["毛利金额", "毛利率"]),
        sp("p3", f"给出净经营费用同比减少约 "
                 f"{_opex(2024,(1,2,3,4))-_opex(2023,(1,2,3,4)):,.2f}（费用金额下降）",
           ev=["净经营费用金额"]),
        sp("p4", f"给出经营利润同比减少约 "
                 f"{ysum('operating_income',2024)-ysum('operating_income',2023):,.2f}",
           ev=["经营利润金额"]),
        sp("p5", "判断金额桥的主驱动是毛利减少，费用金额下降仅为缓冲；"
                 "费用率上升解释利润率恶化，不等于费用金额增长", prereq=["p2", "p3", "p4"],
           contra=["把经营利润下降归因于费用率上升或费用金额增加"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2024", ysum("revenue", 2024), "usd_mn", direction="down"),
        na("operating_income", "FY2024", ysum("operating_income", 2024), "usd_mn",
           direction="down"),
        na("gross_profit", "FY2024", ysum("gross_profit", 2024), "usd_mn", direction="down"),
        na("gross_profit_growth", "FY2024",
           ysum("gross_profit", 2024) - ysum("gross_profit", 2023), "usd_mn",
           baseline="FY2023", direction="down", formula="本期-上期"),
        na("operating_expense", "FY2024", _opex(2024, (1, 2, 3, 4)), "usd_mn", direction="down",
           formula="毛利-经营利润", baseline="FY2023"),
        na("operating_income_growth", "FY2024",
           ysum("operating_income", 2024) - ysum("operating_income", 2023), "usd_mn",
           baseline="FY2023", direction="down", formula="本期-上期"),
        na("gross_margin", "FY2024", ysum("gross_profit", 2024) / ysum("revenue", 2024) * 100,
           "pct", direction="up"),
        na("gross_margin", "FY2023", ysum("gross_profit", 2023) / ysum("revenue", 2023) * 100,
           "pct", direction="up", cat="supporting"),
        na("opex_ratio", "FY2024", _opex_ratio(2024, (1, 2, 3, 4)), "pct", direction="up"),
        na("opex_ratio", "FY2023", _opex_ratio(2023, (1, 2, 3, 4)), "pct", direction="up",
           cat="supporting"),
    ],
    forbidden_claims=["把经营利润下降简单归因于毛利率下降"],
    reasoning_requirements=["多期求和", "利润层分解"],
    question_type="attribution", origin="new", split_group="FY2024",
    event_family="LV-FY2024-annual-bridge",
    data_visibility={"basis": "current_retrospective"},
)

# A3：AR 增速 > 收入增速 → DSO 上升（FY2026Q4）
add(
    case_id="LV-E2E-FY2026Q4-DSO-001",
    question=("联想集团 FY2026Q4 应收账款同比增幅明显高于收入同比增幅。"
              "请用时点余额与单季流量（天数 90）计算 FY2026Q4 与上年同期的 DSO 及变化，"
              "并说明这是否意味着回款转弱。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"口径：DSO = 期末应收账款 ÷ 当季收入 × 90（时点余额，不做两年平均）。"
        f"FY2025Q4 应收账款 {f2(g('accounts_receivable',2025,4))}、收入 "
        f"{f2(g('revenue',2025,4))}，DSO {g('accounts_receivable',2025,4)/g('revenue',2025,4)*90:.4f} 天；"
        f"FY2026Q4 应收账款 {f2(g('accounts_receivable',2026,4))}、收入 "
        f"{f2(g('revenue',2026,4))}，DSO {g('accounts_receivable',2026,4)/g('revenue',2026,4)*90:.4f} 天，"
        f"同比 {g('accounts_receivable',2026,4)/g('revenue',2026,4)*90 - g('accounts_receivable',2025,4)/g('revenue',2025,4)*90:+.4f} 天。"
        f"应收账款同比 {(g('accounts_receivable',2026,4)/g('accounts_receivable',2025,4)-1)*100:+.2f}%，"
        f"高于收入同比 {(g('revenue',2026,4)/g('revenue',2025,4)-1)*100:+.2f}%，"
        "这是 DSO 上升的算术来源；但 DSO 上升只能说明回款速度相对收入变慢，"
        "是否构成回款转弱还需结合账龄、客户结构与季节性判断。"),
    sources=[src(2026, 4, 19, "應收賬款（期末）", period="FY2026Q4"),
             src(2025, 4, 19, "應收賬款（期末）", period="FY2025Q4")],
    scoring_points=[
        sp("p1", f"给出应收账款同比 {(g('accounts_receivable',2026,4)/g('accounts_receivable',2025,4)-1)*100:+.2f}% "
                 f"高于收入同比 {(g('revenue',2026,4)/g('revenue',2025,4)-1)*100:+.2f}%",
           ev=["AR vs 收入"]),
        sp("p2", f"给出 DSO 由 {g('accounts_receivable',2025,4)/g('revenue',2025,4)*90:.2f} 天升至 "
                 f"{g('accounts_receivable',2026,4)/g('revenue',2026,4)*90:.2f} 天，"
                 f"同比约 +4.78 天", ev=["DSO"]),
        sp("p3", "说明 DSO 上升与'回款转弱'的关系需谨慎（可能受季节性/结构影响）",
           ev=["证据边界"], contra=["仅凭 DSO 上升断定回款恶化"]),
    ],
    numeric_assertions=[
        na("dso_days", "FY2026Q4",
           g("accounts_receivable", 2026, 4) / g("revenue", 2026, 4) * 90, "days",
           direction="up", tol_abs=0.5, formula="期末应收÷单季收入×90"),
        na("dso_days", "FY2025Q4",
           g("accounts_receivable", 2025, 4) / g("revenue", 2025, 4) * 90, "days",
           direction="up", tol_abs=0.5, formula="期末应收÷单季收入×90", cat="supporting"),
        na("dso_days_delta", "FY2026Q4",
           g("accounts_receivable", 2026, 4) / g("revenue", 2026, 4) * 90
           - g("accounts_receivable", 2025, 4) / g("revenue", 2025, 4) * 90, "days",
           baseline="FY2025Q4", direction="up", tol_abs=0.5),
        na("accounts_receivable_growth_pct", "FY2026Q4",
           (g("accounts_receivable", 2026, 4) / g("accounts_receivable", 2025, 4) - 1) * 100,
           "pct", baseline="FY2025Q4", direction="up"),
        na("revenue_growth_pct", "FY2026Q4",
           (g("revenue", 2026, 4) / g("revenue", 2025, 4) - 1) * 100, "pct",
           baseline="FY2025Q4", direction="up"),
    ],
    forbidden_claims=["仅凭 DSO 上升断定回款恶化"],
    reasoning_requirements=["平均余额", "天数换算", "证据边界"],
    question_type="attribution", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q4-ar-dso",
    data_visibility={"basis": "current_retrospective"},
)

# A4：递延收入与应计负债变化
add(
    case_id="LV-E2E-FY2026Q4-DEFERRED-001",
    question=("联想集团 FY2026Q4 递延收入（流动）与其他应付款及应计费用同比变化幅度不同。"
              "请量化这两个负债科目的同比变化金额，并说明它们分别反映了什么、"
              "不能据此推断什么。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"两科目同比均为增加：流动递延收益由 {f2(g('deferred_revenue_current',2025,4))} 增至 "
        f"{f2(g('deferred_revenue_current',2026,4))}，增加 "
        f"{d2(g('deferred_revenue_current',2026,4)-g('deferred_revenue_current',2025,4))}"
        f"（{(g('deferred_revenue_current',2026,4)/g('deferred_revenue_current',2025,4)-1)*100:+.2f}%）；"
        f"其他应付款及应计费用由 {f2(g('accrued_liabilities',2025,4))} 增至 "
        f"{f2(g('accrued_liabilities',2026,4))}，增加 "
        f"{d2(g('accrued_liabilities',2026,4)-g('accrued_liabilities',2025,4))}"
        f"（{(g('accrued_liabilities',2026,4)/g('accrued_liabilities',2025,4)-1)*100:+.2f}%）。"
        "递延收益主要对应已收款未确认收入的合同义务，倾向与未来收入确认节奏相关；"
        "其他应付款及应计费用是较宽的口径，包含应计费用、其他应付款项等，不能全部解释为"
        "单一应计费用。两者增加都不能直接推断未来收入确定性或偿债压力。"),
    sources=[src(2026, 4, 20, "遞延收入/其他應付款及應計費用", period="FY2026Q4"),
             src(2025, 4, 20, "遞延收入/其他應付款及應計費用（上年同期）", period="FY2025Q4")],
    scoring_points=[
        sp("p1", f"给出流动递延收益同比增加约 "
                 f"{g('deferred_revenue_current',2026,4)-g('deferred_revenue_current',2025,4):,.2f}"
                 f"，并说明是增加", ev=["递延收入"]),
        sp("p2", f"给出其他应付款及应计费用同比增加约 "
                 f"{g('accrued_liabilities',2026,4)-g('accrued_liabilities',2025,4):,.2f}"
                 f"，并说明是增加", ev=["其他应付款及应计费用"]),
        sp("p3", "说明两科目含义不同：递延收益对应未确认收入，其他应付款及应计费用口径较宽，"
                 "不能全部解释为单一应计费用", ev=["科目含义"]),
        sp("p4", "说明不能据此推断未来收入确定性或偿债压力", ev=["证据边界"]),
    ],
    numeric_assertions=[
        na("deferred_revenue_current", "FY2026Q4", g("deferred_revenue_current", 2026, 4),
           "usd_mn", direction="up", baseline="FY2025Q4"),
        na("accrued_liabilities", "FY2026Q4", g("accrued_liabilities", 2026, 4),
           "usd_mn", direction="up", baseline="FY2025Q4"),
        na("deferred_revenue_current_growth", "FY2026Q4",
           g("deferred_revenue_current", 2026, 4) - g("deferred_revenue_current", 2025, 4),
           "usd_mn", baseline="FY2025Q4", direction="up", formula="本期-上期"),
        na("accrued_liabilities_growth", "FY2026Q4",
           g("accrued_liabilities", 2026, 4) - g("accrued_liabilities", 2025, 4),
           "usd_mn", baseline="FY2025Q4", direction="up", formula="本期-上期"),
    ],
    forbidden_claims=["把递延收入变化直接等同于未来收入确定性"],
    reasoning_requirements=["多科目同比", "证据边界"],
    question_type="attribution", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q4-liabilities-mix",
    data_visibility={"basis": "current_retrospective"},
)

# A5：FY2024 存货 vs COGS 增速差（DIO）
add(
    case_id="LV-E2E-FY2024-INVENTORY-DIO-001",
    question=("联想集团 FY2024 期末存货同比变化与全年营业成本同比变化不同向。"
              "请用时点余额与全年流量计算 FY2024 与上年的 DIO 及变化"
              "（DIO = 期末存货 ÷ 全年营业成本 × 365），"
              "并说明存货与成本增速差对 DIO 的影响。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"口径：DIO = 期末存货 ÷ 全年营业成本 × 365。FY2024 期末存货 "
        f"{f2(g('inventory',2024,4))}（同比 "
        f"{(g('inventory',2024,4)/g('inventory',2023,4)-1)*100:+.2f}%），全年营业成本 "
        f"{f2(ysum('cogs',2024))}（同比 {(ysum('cogs',2024)/ysum('cogs',2023)-1)*100:+.2f}%）。"
        f"FY2023 DIO {g('inventory',2023,4)/ysum('cogs',2023)*365:.4f} 天，"
        f"FY2024 DIO {g('inventory',2024,4)/ysum('cogs',2024)*365:.4f} 天，"
        f"同比 {g('inventory',2024,4)/ysum('cogs',2024)*365 - g('inventory',2023,4)/ysum('cogs',2023)*365:+.4f} 天。"
        "存货上升而成本下降，分子增、分母减，DIO 必然上升。"),
    sources=[src(2024, 4, 19, "存貨（期末）", period="FY2024Q4"),
             src(2024, 4, 17, "營業成本（全年欄）", period="FY2024 全年"),
             src(2023, 4, 20, "存貨（上年期末）", period="FY2023Q4"),
             src(2023, 4, 18, "營業成本（上年全年欄）", period="FY2023 全年")],
    scoring_points=[
        sp("p1", f"给出存货同比约 {(g('inventory',2024,4)/g('inventory',2023,4)-1)*100:+.2f}%",
           ev=["存货"]),
        sp("p2", f"给出营业成本同比约 {(ysum('cogs',2024)/ysum('cogs',2023)-1)*100:+.2f}%",
           ev=["营业成本"]),
        sp("p3", f"给出 DIO 由 {g('inventory',2023,4)/ysum('cogs',2023)*365:.2f} 天升至 "
                 f"{g('inventory',2024,4)/ysum('cogs',2024)*365:.2f} 天，约 +6.78 天",
           ev=["DIO"]),
        sp("p4", "说明存货上升而成本下降，分子增、分母减，DIO 必然上升", prereq=["p1", "p2"]),
    ],
    numeric_assertions=[
        na("inventory", "FY2024Q4", g("inventory", 2024, 4), "usd_mn", direction="up"),
        na("cogs", "FY2024", ysum("cogs", 2024), "usd_mn", direction="down"),
        na("dio_days", "FY2024", g("inventory", 2024, 4) / ysum("cogs", 2024) * 365, "days",
           direction="up", tol_abs=1.0, formula="期末存货÷全年营业成本×365"),
        na("dio_days", "FY2023", g("inventory", 2023, 4) / ysum("cogs", 2023) * 365, "days",
           direction="up", tol_abs=1.0, formula="期末存货÷全年营业成本×365", cat="supporting"),
        na("dio_days_delta", "FY2024",
           g("inventory", 2024, 4) / ysum("cogs", 2024) * 365
           - g("inventory", 2023, 4) / ysum("cogs", 2023) * 365, "days",
           baseline="FY2023", direction="up", tol_abs=1.0),
        na("inventory_growth_pct", "FY2024Q4",
           (g("inventory", 2024, 4) / g("inventory", 2023, 4) - 1) * 100, "pct",
           baseline="FY2023Q4", direction="up"),
        na("cogs_growth_pct", "FY2024",
           (ysum("cogs", 2024) / ysum("cogs", 2023) - 1) * 100, "pct",
           baseline="FY2023", direction="down"),
    ],
    forbidden_claims=["把存货上升直接等同于周转恶化"],
    reasoning_requirements=["流量 vs 余额", "天数换算"],
    question_type="attribution", origin="new", split_group="FY2024",
    event_family="LV-FY2024-inventory-dio",
    data_visibility={"basis": "current_retrospective"},
)

# A6：现金 + 短期投资 变化
add(
    case_id="LV-E2E-FY2027Q1-CASH-MIX-001",
    question=("联想集团 FY2027Q1 现金及银行存款同比变化明显。请量化现金与银行存款各自的"
              "同比变化金额（公告科目为'银行存款'，结构化库中映射为 short_term_investments，"
              "作答时请说明这一映射），并说明这反映的是流动性增强还是资产结构调整，"
              "以及仅凭这两个科目能否判断偿债能力。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"现金及等价物由 {f2(g('cash',2026,1))} 增至 {f2(g('cash',2027,1))}，增加 "
        f"{d2(g('cash',2027,1)-g('cash',2026,1))}"
        f"（{(g('cash',2027,1)/g('cash',2026,1)-1)*100:+.2f}%）。"
        f"银行存款（公告口径，库内映射为 short_term_investments）由 "
        f"{f2(g('short_term_investments',2026,1))} 增至 {f2(g('short_term_investments',2027,1))}，"
        f"增加 {d2(g('short_term_investments',2027,1)-g('short_term_investments',2026,1))}"
        f"（{(g('short_term_investments',2027,1)/g('short_term_investments',2026,1)-1)*100:+.2f}%）。"
        "两者合计上升，但现金增幅贡献远大于银行存款；仅凭这两个资产科目不能判断偿债能力，"
        "还需短期/长期债务、经营现金流与到期结构。列示时注意：公告资产负债表的比较列是"
        "2026-03-31（FY2026Q4 期末），同比基期 FY2026Q1 需另引 FY2026Q1 公告。"),
    sources=[src(2027, 1, 14, "現金及等價物/銀行存款", period="FY2027Q1"),
             src(2026, 1, 15, "現金及等價物/銀行存款（上年同期）", period="FY2026Q1")],
    scoring_points=[
        sp("p1", f"给出现金同比增加约 {g('cash',2027,1)-g('cash',2026,1):,.2f}"
                 f"（约 +31.15%）", ev=["现金"]),
        sp("p2", f"给出银行存款（short_term_investments）同比增加约 "
                 f"{g('short_term_investments',2027,1)-g('short_term_investments',2026,1):,.2f}"
                 f"，并说明科目映射", ev=["银行存款/短期投资"]),
        sp("p3", "说明仅凭两科目不足以判断偿债能力（需负债与现金流）", ev=["证据边界"]),
    ],
    numeric_assertions=[
        na("cash", "FY2027Q1", g("cash", 2027, 1), "usd_mn", direction="up", baseline="FY2026Q1"),
        na("cash", "FY2026Q1", g("cash", 2026, 1), "usd_mn", direction="up",
           cat="supporting"),
        na("short_term_investments", "FY2027Q1", g("short_term_investments", 2027, 1),
           "usd_mn", direction="up", baseline="FY2026Q1"),
        na("short_term_investments", "FY2026Q1", g("short_term_investments", 2026, 1),
           "usd_mn", direction="up", cat="supporting"),
        na("cash_growth", "FY2027Q1", g("cash", 2027, 1) - g("cash", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up", formula="本期-上期"),
        na("short_term_investments_growth", "FY2027Q1",
           g("short_term_investments", 2027, 1) - g("short_term_investments", 2026, 1),
           "usd_mn", baseline="FY2026Q1", direction="up", formula="本期-上期"),
    ],
    forbidden_claims=["仅凭现金与短期投资判断整体偿债能力"],
    reasoning_requirements=["多科目同比", "证据边界"],
    question_type="attribution", origin="new", split_group="FY2027Q1",
    event_family="LV-FY2027Q1-cash-mix",
    data_visibility={"basis": "current_retrospective"},
)

# A7：经营利润 vs 净利润差异（FY2026Q3）
add(
    case_id="LV-E2E-FY2026Q3-OI-VS-NI-001",
    question=("联想集团 FY2026Q3 经营利润与净利润的同比变化并不同步。"
              "请给出两者的同比变化金额与方向，并说明经营利润与净利润之间还隔着哪些项目"
              "（如税项、非控股权益等）导致二者不同。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026Q3 经营利润 {f2(g('operating_income',2026,3))}，上年同期 "
        f"{f2(g('operating_income',2025,3))}，同比 "
        f"{d2(g('operating_income',2026,3)-g('operating_income',2025,3))}"
        f"（{(g('operating_income',2026,3)/g('operating_income',2025,3)-1)*100:+.2f}%）；"
        f"合并净利润 {f2(g('net_income',2026,3))}，上年同期 {f2(g('net_income',2025,3))}，同比 "
        f"{d2(g('net_income',2026,3)-g('net_income',2025,3))}"
        f"（{(g('net_income',2026,3)/g('net_income',2025,3)-1)*100:+.2f}%），方向为下降。"
        "两者之间依次为：财务收入 28.962、财务费用 158.628、应占联营/合营公司损失 0.607、"
        "税项 170.215（948.458+28.962-158.628-0.607-170.215=647.970）。"
        "非控股权益 102.434 是合并净利润在母公司与非控股股东之间的归属拆分，"
        "不是从经营利润扣到合并净利润的费用项；若改看归母净利润，需同时更换科目与数值。"),
    sources=[src(2026, 3, 16, "經營溢利/期內溢利（單季欄）", period="FY2026Q3")],
    scoring_points=[
        sp("p1", f"给出经营利润同比增加约 "
                 f"{g('operating_income',2026,3)-g('operating_income',2025,3):,.2f}"
                 f"（+37.92%）", ev=["经营利润"]),
        sp("p2", f"给出合并净利润同比减少约 "
                 f"{g('net_income',2025,3)-g('net_income',2026,3):,.2f}"
                 f"（-7.53%），方向为下降", ev=["净利润"],
           contra=["称净利润同比增长"]),
        sp("p3", "列出两者之间的项目：财务收入、财务费用、应占联营/合营损益、税项",
           ev=["利润层"]),
        sp("p4", "说明非控股权益是合并净利润的归属拆分，不是从经营利润扣至合并净利润的费用",
           ev=["归属口径"]),
    ],
    numeric_assertions=[
        na("operating_income", "FY2026Q3", g("operating_income", 2026, 3), "usd_mn",
           direction="up", baseline="FY2025Q3"),
        na("net_income", "FY2026Q3", g("net_income", 2026, 3), "usd_mn",
           direction="down", baseline="FY2025Q3"),
        na("operating_income_growth", "FY2026Q3",
           g("operating_income", 2026, 3) - g("operating_income", 2025, 3), "usd_mn",
           baseline="FY2025Q3", direction="up", formula="本期-上期"),
        na("net_income_growth", "FY2026Q3",
           g("net_income", 2026, 3) - g("net_income", 2025, 3), "usd_mn",
           baseline="FY2025Q3", direction="down", formula="本期-上期"),
        na("operating_income_growth_pct", "FY2026Q3",
           (g("operating_income", 2026, 3) / g("operating_income", 2025, 3) - 1) * 100, "pct",
           baseline="FY2025Q3", direction="up"),
        na("net_income_growth_pct", "FY2026Q3",
           (g("net_income", 2026, 3) / g("net_income", 2025, 3) - 1) * 100, "pct",
           baseline="FY2025Q3", direction="down"),
    ],
    forbidden_claims=["把经营利润等同于净利润"],
    reasoning_requirements=["利润层", "同比"],
    question_type="attribution", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q3-oi-vs-ni",
    data_visibility={"basis": "current_retrospective"},
)

# A8：S02 全年 收入→毛利→经营费用→经营溢利 桥
_r25, _r26 = ysum("revenue", 2025), ysum("revenue", 2026)
_gp25, _gp26 = ysum("gross_profit", 2025), ysum("gross_profit", 2026)
_oi25, _oi26 = ysum("operating_income", 2025), ysum("operating_income", 2026)
add(
    case_id="LV-E2E-FY2026-ANNUAL-BRIDGE-001",
    question=("联想集团 FY2026 全年毛利率同比下降 0.7 个百分点至 15.4%，但全年经营溢利"
              "同比 +51%。毛利率下滑与盈利大增是否矛盾？请沿 收入→毛利→经营费用→经营溢利"
              "做全年口径的增速分解，指出各层同比金额变化，并说明管理层给出的组合与 ISG"
              "低利润率解释能否被这些数字证实。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026 全年收入 {f2(_r26)}（同比 {( _r26/_r25-1)*100:+.2f}%），毛利 {f2(_gp26)}"
        f"（同比 {(_gp26/_gp25-1)*100:+.2f}%），净经营费用（毛利−经营溢利，含其他经营收支"
        f"净额）{f2(_gp26-_oi26)}（同比 {d2((_gp26-_oi26)-(_gp25-_oi25))}），经营溢利 {f2(_oi26)}"
        f"（同比 {(_oi26/_oi25-1)*100:+.2f}%）。"
        f"金额桥：收入 {d2(_r26-_r25)}、毛利 {d2(_gp26-_gp25)}、净经营费用 "
        f"{d2((_gp26-_oi26)-(_gp25-_oi25))}、经营溢利 {d2(_oi26-_oi25)}；"
        "Δ经营溢利 = Δ毛利 − Δ净经营费用 = 1,711.812 − 614.222 = 1,097.590。"
        "不矛盾：毛利增速低于收入增速导致毛利率下降，但费用增速（+6.87%）更低，"
        "使经营溢利增速高于毛利增速。管理层的组合/ISG 解释属公司说明，"
        "不能仅凭这些报表数字证实为独立因果。"),
    sources=[src(2026, 4, 17, "收入/毛利/经营溢利（全年欄）", period="FY2026 全年")],
    scoring_points=[
        sp("p1", f"给出全年收入同比金额约 {_r26-_r25:,.2f}（+13,997.59）与增速约 "
                 f"{(_r26/_r25-1)*100:+.2f}%", ev=["收入金额", "收入增速"]),
        sp("p2", f"给出全年毛利同比金额约 {_gp26-_gp25:,.2f}（+1,711.81）与增速约 "
                 f"{(_gp26/_gp25-1)*100:+.2f}%", ev=["毛利金额", "毛利增速"]),
        sp("p3", f"给出毛利率同比下降约 "
                 f"{(_gp26/_r26-_gp25/_r25)*100:+.2f}pp（约 -0.65pp）", ev=["毛利率"],
           contra=["称毛利率上升"]),
        sp("p4", f"给出净经营费用同比金额约 "
                 f"{(_gp26-_oi26)-(_gp25-_oi25):,.2f}（+614.22）与经营溢利同比金额约 "
                 f"{_oi26-_oi25:,.2f}（+1,097.59）", ev=["净经营费用金额", "经营溢利金额"]),
        sp("p5", "指出毛利增速低于收入、净经营费用增速更低（约 +6.9%），"
                 "故经营溢利增速高于毛利增速，二者可同时成立", prereq=["p2", "p4"]),
        sp("p6", "管理层组合/ISG 解释需报表数字之外证据，不能据报表断定因果", ev=["证据边界"],
           contra=["声称报表已证明组合/ISG 是唯一成因"]),
    ],
    numeric_assertions=[
        na("revenue_growth_pct", "FY2026", (_r26 / _r25 - 1) * 100, "pct", baseline="FY2025",
           direction="up"),
        na("gross_profit_growth_pct", "FY2026", (_gp26 / _gp25 - 1) * 100, "pct",
           baseline="FY2025", direction="up"),
        na("operating_income_growth_pct", "FY2026", (_oi26 / _oi25 - 1) * 100, "pct",
           baseline="FY2025", direction="up"),
        na("operating_income", "FY2026", _oi26, "usd_mn", direction="up"),
        na("revenue_growth", "FY2026", _r26 - _r25, "usd_mn", baseline="FY2025",
           direction="up", formula="本期-上期"),
        na("gross_profit_growth", "FY2026", _gp26 - _gp25, "usd_mn", baseline="FY2025",
           direction="up", formula="本期-上期"),
        na("operating_expense_growth", "FY2026",
           (_gp26 - _oi26) - (_gp25 - _oi25), "usd_mn", baseline="FY2025",
           direction="up", formula="Δ(毛利-经营溢利)"),
        na("operating_income_growth", "FY2026", _oi26 - _oi25, "usd_mn", baseline="FY2025",
           direction="up", formula="本期-上期"),
        na("gross_margin_delta", "FY2026", (_gp26 / _r26 - _gp25 / _r25) * 100, "pct_point",
           baseline="FY2025", direction="down"),
    ],
    forbidden_claims=["声称报表数字已证实业务组合是毛利率变化的唯一成因",
                      "把管理层解释当作独立证实的因果事实"],
    reasoning_requirements=["多期求和", "逐层增速分解", "矛盾解释", "证据边界"],
    question_type="attribution", origin="reused", selected_id="S02",
    event_family="LV-FY2026Q4-annual-mix", split_group="FY2026",
    data_visibility={"basis": "current_retrospective"},
)

# A9：FY2027Q1 收入增长分解（量级与结构）
add(
    case_id="LV-E2E-FY2027Q1-REVENUE-GROWTH-001",
    question=("联想集团 FY2027Q1 收入同比大幅增长。请把收入增长拆成'上年同期基数'与"
              "'本期增量'两部分，量化增量金额，并说明仅凭集团合并收入能否判断增长来自哪些"
              "业务，缺什么数据。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026Q1 收入基数 {f2(g('revenue',2026,1))}，FY2027Q1 收入 "
        f"{f2(g('revenue',2027,1))}，同比增量 {d2(g('revenue',2027,1)-g('revenue',2026,1))} "
        f"百万美元，即 {((g('revenue',2027,1)-g('revenue',2026,1))/100):,.2f} 亿美元"
        f"，增速 "
        f"{(g('revenue',2027,1)/g('revenue',2026,1)-1)*100:+.2f}%。"
        "仅凭合并收入不能判断增量来自哪个业务集团，需要分部收入与抵销项数据。"),
    sources=[src(2027, 1, 12, "收入（單季欄）", period="FY2027Q1"),
             src(2026, 1, 13, "收入（單季欄）", period="FY2026Q1")],
    scoring_points=[
        sp("p1", f"给出上年同期基数 {f2(g('revenue',2026,1))} 与本期收入 "
                 f"{f2(g('revenue',2027,1))}", ev=["基数与本期"]),
        sp("p2", f"给出本期增量约 {g('revenue',2027,1)-g('revenue',2026,1):,.2f}"
                 f"（即 81.13 亿美元），不得写成 1,111 亿美元", ev=["增量"],
           contra=["把增量写成约 111 亿美元"]),
        sp("p3", "说明仅凭合并收入不能判断分部来源，缺分部数据", ev=["数据缺口"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2027Q1", g("revenue", 2027, 1), "usd_mn", direction="up"),
        na("revenue", "FY2026Q1", g("revenue", 2026, 1), "usd_mn", direction="up",
           cat="supporting"),
        na("revenue_growth", "FY2027Q1", g("revenue", 2027, 1) - g("revenue", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up"),
        na("revenue_growth_pct", "FY2027Q1",
           (g("revenue", 2027, 1) / g("revenue", 2026, 1) - 1) * 100, "pct",
           baseline="FY2026Q1", direction="up"),
    ],
    forbidden_claims=["凭合并收入断定增长来自某一分部"],
    reasoning_requirements=["同比分解", "数据缺口"],
    question_type="attribution", origin="new", split_group="FY2027Q1",
    event_family="LV-FY2027Q1-revenue-growth",
    data_visibility={"basis": "current_retrospective"},
)

# A10：S06 营运资金 DSO/DIO/DPO 与 CCC
_dso_c = (g("accounts_receivable", 2026, 4) + g("accounts_receivable", 2027, 1)) / 2 / g("revenue", 2027, 1) * 91
_dso_p = (g("accounts_receivable", 2025, 4) + g("accounts_receivable", 2026, 1)) / 2 / g("revenue", 2026, 1) * 91
_dio_c = (g("inventory", 2026, 4) + g("inventory", 2027, 1)) / 2 / g("cogs", 2027, 1) * 91
_dio_p = (g("inventory", 2025, 4) + g("inventory", 2026, 1)) / 2 / g("cogs", 2026, 1) * 91
_dpo_c = (g("accounts_payable", 2026, 4) + g("accounts_payable", 2027, 1)) / 2 / g("cogs", 2027, 1) * 91
_dpo_p = (g("accounts_payable", 2025, 4) + g("accounts_payable", 2026, 1)) / 2 / g("cogs", 2026, 1) * 91
_ccc_c, _ccc_p = _dso_c + _dio_c - _dpo_c, _dso_p + _dio_p - _dpo_p
_nwc = ((g("inventory", 2027, 1) - g("inventory", 2026, 1))
        + (g("accounts_receivable", 2027, 1) - g("accounts_receivable", 2026, 1))
        - (g("accounts_payable", 2027, 1) - g("accounts_payable", 2026, 1)))
_cash_c, _cash_p = g("cash", 2027, 1), g("cash", 2026, 1)
add(
    case_id="LV-E2E-FY2027Q1-WORKING-CAPITAL-CCC-001",
    question=("联想集团 FY2027Q1 存货、应收、应付同季均大幅增长。请按同比 DSO、DIO、DPO 与"
              "现金转换周期 CCC（天数均取 91；流量用单季收入/营业成本，余额用（期初+期末)/2）"
              "量化这轮营运资金扩张，判断主要由存货/应收占用还是应付融资支撑，"
              "并用现金余额说明能否据此判断现金压力。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2027Q1 同比：DSO {_dso_p:.2f}→{_dso_c:.2f} 天（{_dso_c-_dso_p:+.2f}）；"
        f"DIO {_dio_p:.2f}→{_dio_c:.2f} 天；DPO {_dpo_p:.2f}→{_dpo_c:.2f} 天"
        f"（{_dpo_c-_dpo_p:+.2f}）。CCC = DSO+DIO−DPO：本期 {_ccc_c:.2f} 天，上年同期 "
        f"{_ccc_p:.2f} 天，基本持平。选定存货+应收−应付净占用同比 "
        f"{d2(_nwc)} 百万美元（存货 {d2(g('inventory',2027,1)-g('inventory',2026,1))}、"
        f"应收 {d2(g('accounts_receivable',2027,1)-g('accounts_receivable',2026,1))}、"
        f"应付 {d2(g('accounts_payable',2027,1)-g('accounts_payable',2026,1))}）。"
        f"金额上：存货 {d2(g('inventory',2027,1)-g('inventory',2026,1))}、应收 "
        f"{d2(g('accounts_receivable',2027,1)-g('accounts_receivable',2026,1))}，两者合计 "
        f"{d2((g('inventory',2027,1)-g('inventory',2026,1))+(g('accounts_receivable',2027,1)-g('accounts_receivable',2026,1)))}，"
        f"应付增量 {d2(g('accounts_payable',2027,1)-g('accounts_payable',2026,1))} 只部分抵消，"
        f"净占用仍增加 {d2(_nwc)}。天数上：DPO 上升 {_dpo_c-_dpo_p:.4f} 天，"
        f"DSO+DIO 上升 {(_dso_c+_dio_c)-(_dso_p+_dio_p):.4f} 天，DPO 升幅略大，"
        f"CCC 反而略降 {abs(_ccc_c-_ccc_p):.4f} 天。现金余额由 {f2(_cash_p)} 增至 "
        f"{f2(_cash_c)}（{d2(_cash_c-_cash_p)}，{(_cash_c/_cash_p-1)*100:+.2f}%）；"
        "但余额上升只能说明账面流动性改善，不足以证明不存在现金压力。"
        "选定三科目净变化不等于全口径营运资金或经营现金流，"
        "现金压力判断仍缺现金流量表口径证据。"),
    sources=[src(2027, 1, IS(2027, 1), "收入/營業成本", period="FY2027Q1"),
             src(2027, 1, 14, "存貨/應收賬款", period="FY2027Q1"),
             src(2027, 1, 15, "應付賬款", period="FY2027Q1"),
             src(2026, 4, 19, "存貨/應收賬款（期初）", period="FY2026Q4"),
             src(2026, 1, 15, "存貨/應收賬款（上年同）", period="FY2026Q1"),
             src(2026, 1, 16, "應付賬款（上年同）", period="FY2026Q1")],
    scoring_points=[
        sp("p1", f"给出 DSO 同比上升约 {_dso_c-_dso_p:.1f} 天（{_dso_p:.1f}→{_dso_c:.1f}）",
           ev=["DSO"]),
        sp("p2", f"给出 DIO 由约 {_dio_p:.1f} 升至约 {_dio_c:.1f} 天", ev=["DIO"], prereq=["p1"]),
        sp("p3", f"给出 DPO 同比上升约 {_dpo_c-_dpo_p:.1f} 天（{_dpo_p:.1f}→{_dpo_c:.1f}）",
           ev=["DPO"]),
        sp("p4", f"给出 CCC 本期约 {_ccc_c:.1f} 天、同比基本持平（上年约 {_ccc_p:.1f}）",
           prereq=["p1", "p2", "p3"], ev=["CCC"]),
        sp("p5", f"给出选定三科目净占用同比约 {d2(_nwc)} 百万美元", ev=["净占用"],
           eq=["约 +52.6 亿美元"]),
        sp("p6", f"结论分两层：金额上应付增量（+{g('accounts_payable',2027,1)-g('accounts_payable',2026,1):,.2f}）"
                 f"小于存货+应收增量（+{((g('inventory',2027,1)-g('inventory',2026,1))+(g('accounts_receivable',2027,1)-g('accounts_receivable',2026,1))):,.2f}），"
                 f"净占用仍增加 {_nwc:,.2f}；天数上 DPO 升幅 {_dpo_c-_dpo_p:.2f} 天略大于 "
                 f"DSO+DIO 升幅 {(_dso_c+_dio_c)-(_dso_p+_dio_p):.2f} 天，CCC 略降 "
                 f"{abs(_ccc_c-_ccc_p):.2f} 天", prereq=["p3", "p4"]),
        sp("p7", f"给出上年同期/期初现金 {f2(_cash_p)} 增至本期 {f2(_cash_c)}"
                 f"（{d2(_cash_c-_cash_p)}），并说明余额上升不足以证明不存在现金压力",
           ev=["现金变化", "证据边界"],
           contra=["仅凭现金余额上升断定无现金压力"]),
        sp("p8", "说明三科目净变化不等于全口径营运资金/经营现金流，现金压力判断仍缺证据",
           ev=["证据边界"], contra=["把三科目净变化等同于经营现金流"]),
    ],
    numeric_assertions=[
        na("dso_days", "FY2027Q1", _dso_c, "days", direction="up", tol_abs=0.1),
        na("dso_days", "FY2026Q1", _dso_p, "days", direction="up", tol_abs=0.1, cat="supporting"),
        na("dio_days", "FY2027Q1", _dio_c, "days", direction="up", tol_abs=0.1),
        na("dpo_days", "FY2027Q1", _dpo_c, "days", direction="up", tol_abs=0.1),
        na("dpo_days", "FY2026Q1", _dpo_p, "days", direction="up", tol_abs=0.1, cat="supporting"),
        na("ccc_days", "FY2027Q1", _ccc_c, "days", direction="flat", tol_abs=0.2),
        na("ccc_days", "FY2026Q1", _ccc_p, "days", direction="flat", tol_abs=0.2, cat="supporting"),
        na("dso_days_delta", "FY2027Q1", _dso_c - _dso_p, "days", baseline="FY2026Q1",
           direction="up", tol_abs=0.1),
        na("dio_days_delta", "FY2027Q1", _dio_c - _dio_p, "days", baseline="FY2026Q1",
           direction="up", tol_abs=0.1),
        na("dpo_days_delta", "FY2027Q1", _dpo_c - _dpo_p, "days", baseline="FY2026Q1",
           direction="up", tol_abs=0.1),
        na("ccc_days_delta", "FY2027Q1", _ccc_c - _ccc_p, "days", baseline="FY2026Q1",
           direction="down", tol_abs=0.2),
        na("inventory_growth", "FY2027Q1", g("inventory", 2027, 1) - g("inventory", 2026, 1),
           "usd_mn", baseline="FY2026Q1", direction="up", formula="本期-上期"),
        na("accounts_receivable_growth", "FY2027Q1",
           g("accounts_receivable", 2027, 1) - g("accounts_receivable", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up", formula="本期-上期"),
        na("accounts_payable_growth", "FY2027Q1",
           g("accounts_payable", 2027, 1) - g("accounts_payable", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up", formula="本期-上期"),
        na("net_working_capital_delta", "FY2027Q1", _nwc, "usd_mn", baseline="FY2026Q1",
           formula="Δ存货+Δ应收−Δ应付", direction="up"),
        na("cash", "FY2027Q1", _cash_c, "usd_mn", direction="up", cat="supporting"),
        na("cash", "FY2026Q1", _cash_p, "usd_mn", direction="up", cat="supporting"),
        na("cash_delta", "FY2027Q1", _cash_c - _cash_p, "usd_mn", baseline="FY2026Q1",
           direction="up", formula="本期-上期", cat="supporting"),
    ],
    forbidden_claims=["把选定三科目净变化等同于全口径营运资金或经营现金流",
                      "仅凭资产负债表断定存在或不存在现金压力"],
    reasoning_requirements=["平均余额", "天数换算", "多科目联动", "现金检验", "证据边界"],
    question_type="attribution", origin="reused", selected_id="S06",
    event_family="LV-FY2027Q1-working-capital", split_group="FY2027Q1",
    notes="复用已审 S06；净占用采用修订版 5,258.727。",
    data_visibility={"basis": "current_retrospective"},
)

# ============================================================ 证据边界与综合判断（8）
# J1：单季 vs 全年 净利润（描述边界）
add(
    case_id="LV-E2E-FY2026-NI-BOUNDARY-001",
    question=("有说法称'联想 FY2026 全年净利润同比大幅增长，因此各季度都在变好'。"
              "请用 FY2026 各单季净利润同比检验该说法，并说明为什么全年增长不能推出每个"
              "季度都在改善。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2026 全年净利润 {f2(ysum('net_income',2026))}，同比 "
        f"{(ysum('net_income',2026)/ysum('net_income',2025)-1)*100:+.2f}%。"
        f"单季同比：Q1 {f2(g('net_income',2026,1))} 对 {f2(g('net_income',2025,1))}"
        f"（{(g('net_income',2026,1)/g('net_income',2025,1)-1)*100:+.2f}%）、"
        f"Q2 {f2(g('net_income',2026,2))} 对 {f2(g('net_income',2025,2))}"
        f"（{(g('net_income',2026,2)/g('net_income',2025,2)-1)*100:+.2f}%）、"
        f"Q3 {f2(g('net_income',2026,3))} 对 {f2(g('net_income',2025,3))}"
        f"（{(g('net_income',2026,3)/g('net_income',2025,3)-1)*100:+.2f}%）、"
        f"Q4 {f2(g('net_income',2026,4))} 对 {f2(g('net_income',2025,4))}"
        f"（{(g('net_income',2026,4)/g('net_income',2025,4)-1)*100:+.2f}%）。"
        "Q2、Q3 单季净利润同比下降，故'全年大幅增长所以各季都在变好'不成立；"
        "累计口径的增长可以被 Q1、Q4 的高增长拉高，不能推出每个季度都改善。"),
    sources=[src(2026, 4, 17, "年內溢利（全年欄）", period="FY2026 全年"),
             src(2026, 1, 13, "期內溢利（單季欄）", period="FY2026Q1"),
             src(2026, 2, 16, "期內溢利（單季欄）", period="FY2026Q2"),
             src(2026, 3, 16, "期內溢利（單季欄）", period="FY2026Q3"),
             src(2026, 4, 14, "年內溢利（單季欄）", period="FY2026Q4")],
    scoring_points=[
        sp("p1", f"给出 FY2026 全年净利润同比约 "
                 f"{(ysum('net_income',2026)/ysum('net_income',2025)-1)*100:+.2f}%",
           ev=["全年"]),
        sp("p2", f"给出四个单季净利润同比：Q1 "
                 f"{(g('net_income',2026,1)/g('net_income',2025,1)-1)*100:+.2f}%、Q2 "
                 f"{(g('net_income',2026,2)/g('net_income',2025,2)-1)*100:+.2f}%、Q3 "
                 f"{(g('net_income',2026,3)/g('net_income',2025,3)-1)*100:+.2f}%、Q4 "
                 f"{(g('net_income',2026,4)/g('net_income',2025,4)-1)*100:+.2f}%",
           ev=["单季"], contra=["称各季都增长"]),
        sp("p3", "指出 Q2、Q3 单季净利润同比为负，并用其否定'各季都在变好'", prereq=["p2"]),
        sp("p4", "说明累计口径不能推出单季普遍改善（Q1/Q4 高增长可拉高全年）", prereq=["p2"]),
    ],
    numeric_assertions=[
        na("net_income_growth_pct", "FY2026",
           (ysum("net_income", 2026) / ysum("net_income", 2025) - 1) * 100, "pct",
           baseline="FY2025", direction="up"),
        na("net_income_growth_pct", "FY2026Q1",
           (g("net_income", 2026, 1) / g("net_income", 2025, 1) - 1) * 100, "pct",
           baseline="FY2025Q1", direction="up"),
        na("net_income_growth_pct", "FY2026Q2",
           (g("net_income", 2026, 2) / g("net_income", 2025, 2) - 1) * 100, "pct",
           baseline="FY2025Q2", direction="down", tol=0.15),
        na("net_income_growth_pct", "FY2026Q3",
           (g("net_income", 2026, 3) / g("net_income", 2025, 3) - 1) * 100, "pct",
           baseline="FY2025Q3", direction="down"),
        na("net_income_growth_pct", "FY2026Q4",
           (g("net_income", 2026, 4) / g("net_income", 2025, 4) - 1) * 100, "pct",
           baseline="FY2025Q4", direction="up"),
        na("net_income", "FY2026", ysum("net_income", 2026), "usd_mn", direction="up"),
    ],
    forbidden_claims=["由全年增长推断每个季度都在改善"],
    reasoning_requirements=["累计 vs 单季", "证据边界"],
    question_type="judgment", origin="new", split_group="FY2026",
    event_family="LV-FY2026-ni-boundary",
    data_visibility={"basis": "current_retrospective"},
)

# J2：费用率改善是否支持"效率提升"（S04）
add(
    case_id="LV-E2E-FY2025Q3-EFFICIENCY-JUDGMENT-001",
    question=("联想 FY2025Q3 经营费用率同比下降。仅凭这一比率，能否判断公司'经营效率提升'？"
              "请给出支持与不支持的证据，并说明还需要哪些数据。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"费用率由 {_opex_ratio(2024, (3,)):.4f}% 降至 {_opex_ratio(2025, (3,)):.4f}%，"
        f"同比 {( _opex_ratio(2025, (3,)) - _opex_ratio(2024, (3,))) * 100:+.2f}bps"
        "（费用强度下降）。但该比率下降可由收入增长快于费用增长、产品或区域结构变化、"
        "一次性费用减少等原因造成，不能单独证明整体经营效率提升；"
        "还需费用明细、人员/研发投入、资产周转与现金转化等数据。"),
    sources=[src(2025, 3, IS(2025, 3), "收入/毛利/经营溢利（單季與上年同期）",
                 period="FY2025Q3")],
    scoring_points=[
        sp("p1", f"给出费用率同比量化证据：{_opex_ratio(2024, (3,)):.2f}%→"
                 f"{_opex_ratio(2025, (3,)):.2f}%，下降约 56.26bps", ev=["费用率"]),
        sp("p2", "指出费用率下降支持'费用强度下降'，但不能单独证明效率提升"
                 "（可能来自收入结构或一次性项）", ev=["证据边界"],
           contra=["仅凭费用率下降断定效率提升"]),
        sp("p3", "列出还需要的数据（费用明细、人员/研发投入等）", ev=["数据缺口"]),
    ],
    numeric_assertions=[
        na("opex_ratio", "FY2025Q3", _opex_ratio(2025, (3,)), "pct", direction="down"),
        na("opex_ratio", "FY2024Q3", _opex_ratio(2024, (3,)), "pct", direction="down",
           cat="supporting"),
        na("opex_ratio_delta", "FY2025Q3", _opex_ratio(2025, (3,)) - _opex_ratio(2024, (3,)),
           "pct_point", baseline="FY2024Q3", direction="down"),
    ],
    forbidden_claims=["仅凭费用率下降断定经营效率提升"],
    reasoning_requirements=["证据边界", "数据缺口"],
    question_type="judgment", origin="reused", selected_id="S04",
    event_family="LV-FY2025Q3-opex-efficiency", split_group="FY2025Q3",
    data_visibility={"basis": "current_retrospective"},
)

# J3：证据冲突（毛利率↓ 经营利润率↑）
add(
    case_id="LV-E2E-FY2026Q1-CONFLICT-001",
    question=("联想 FY2026Q1 毛利率同比下降、经营利润率同比上升，两个比率给出相反信号。"
              "请量化两者变化，判断哪个更能代表'盈利质量'，并说明证据冲突时应如何表述结论。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：毛利率与经营利润率变化方向相反，需分层表述而非二选一。",
    sources=[src(2026, 1, IS(2026, 1), "收入/毛利/经营溢利", period="FY2026Q1")],
    scoring_points=[
        sp("p1", "给出毛利率同比降幅", ev=["毛利率"]),
        sp("p2", "给出经营利润率同比变化（上升）", ev=["经营利润率"]),
        sp("p3", "说明二者不冲突：处于不同利润层，应分层陈述", prereq=["p1", "p2"]),
        sp("p4", "不因证据冲突而笼统下'变好/变差'结论", contra=["用单一结论掩盖分层信息"]),
    ],
    numeric_assertions=[
        na("gross_margin_delta", "FY2026Q1",
           (g("gross_profit", 2026, 1) / g("revenue", 2026, 1)
            - g("gross_profit", 2025, 1) / g("revenue", 2025, 1)) * 100, "pct_point",
           baseline="FY2025Q1", direction="down"),
        na("operating_margin_delta", "FY2026Q1",
           (g("operating_income", 2026, 1) / g("revenue", 2026, 1)
            - g("operating_income", 2025, 1) / g("revenue", 2025, 1)) * 100, "pct_point",
           baseline="FY2025Q1", direction="up"),
    ],
    forbidden_claims=["用单一'变好/变差'结论掩盖不同利润层的相反信号"],
    reasoning_requirements=["利润层", "证据冲突", "结论表述"],
    question_type="judgment", origin="reused", selected_id="S07", split_group="FY2026",
    event_family="LV-FY2026Q1-margin-vs-expense-divergence",
    notes="复用已审 S07 事件，按新协议重写；解题步骤移至评测侧。",
    data_visibility={"basis": "current_retrospective"},
)

# J4：备货 vs 周转
add(
    case_id="LV-E2E-FY2027Q1-STOCKPILING-JUDGMENT-001",
    question=("联想 FY2027Q1 存货同比大增。请判断：现有数据能否支持'公司在主动备货'这一"
              "结论？请给出支持与不支持的数字"
              "（DIO 口径：91 天，余额取季度期初与期末平均，流量取单季营业成本），"
              "并说明需要哪些额外数据。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"FY2027Q1 期末存货 {f2(g('inventory',2027,1))}，同比 "
        f"{(g('inventory',2027,1)/g('inventory',2026,1)-1)*100:+.2f}%。"
        f"DIO（91 天、期初期末平均）由 {((g('inventory',2025,4)+g('inventory',2026,1))/2/g('cogs',2026,1)*91):.4f} 天"
        f"升至 {((g('inventory',2026,4)+g('inventory',2027,1))/2/g('cogs',2027,1)*91):.4f} 天。"
        "这些数字支持'库存规模与周转天数上升'；但'主动备货'是管理层意图，"
        "需要管理层披露、订单/指引、在手订单或渠道库存等额外数据，现有报表不足以证明。"),
    sources=[src(2027, 1, 14, "存貨（期末）", period="FY2027Q1"),
             src(2026, 4, 19, "存貨（期初）", period="FY2026Q4"),
             src(2026, 1, 15, "存貨（上年同期）", period="FY2026Q1"),
             src(2027, 1, 12, "收入/毛利（單季欄；營業成本=收入-毛利）", period="FY2027Q1"),
             src(2026, 1, 13, "收入/毛利（單季欄，上年同期）", period="FY2026Q1")],
    scoring_points=[
        sp("p1", f"给出存货同比约 {(g('inventory',2027,1)/g('inventory',2026,1)-1)*100:+.2f}% "
                 f"作为支持证据", ev=["存货"]),
        sp("p2", f"给出 DIO 由约 "
                 f"{((g('inventory',2025,4)+g('inventory',2026,1))/2/g('cogs',2026,1)*91):.2f} 天"
                 f"升至约 {((g('inventory',2026,4)+g('inventory',2027,1))/2/g('cogs',2027,1)*91):.2f} 天"
                 f"（并说明所用公式）", ev=["DIO"]),
        sp("p3", "指出'主动备货'需要管理层披露或订单/指引等额外数据", ev=["数据缺口"],
           contra=["仅凭库存上升断定主动备货"]),
        sp("p4", "给出可支持的结论（库存规模与周转天数上升）", ev=["可支持结论"]),
    ],
    numeric_assertions=[
        na("inventory", "FY2027Q1", g("inventory", 2027, 1), "usd_mn", direction="up",
           baseline="FY2026Q1"),
        na("inventory_growth_pct", "FY2027Q1",
           (g("inventory", 2027, 1) / g("inventory", 2026, 1) - 1) * 100, "pct",
           baseline="FY2026Q1", direction="up"),
        na("dio_days", "FY2027Q1",
           (g("inventory", 2026, 4) + g("inventory", 2027, 1)) / 2 / g("cogs", 2027, 1) * 91,
           "days", direction="up", tol_abs=0.1, formula="平均存货÷单季营业成本×91"),
        na("dio_days", "FY2026Q1",
           (g("inventory", 2025, 4) + g("inventory", 2026, 1)) / 2 / g("cogs", 2026, 1) * 91,
           "days", direction="up", tol_abs=0.1, formula="平均存货÷单季营业成本×91",
           cat="supporting"),
    ],
    forbidden_claims=["仅凭库存上升断定公司主动备货"],
    reasoning_requirements=["证据边界", "数据缺口"],
    question_type="judgment", origin="reused", selected_id="S01",
    event_family="LV-FY2027Q1-stockpiling", split_group="FY2027Q1",
    data_visibility={"basis": "current_retrospective"},
)

# J5：现金余额能否判断偿债
add(
    case_id="LV-E2E-FY2027Q1-LIQUIDITY-JUDGMENT-001",
    question=("联想 FY2027Q1 现金余额同比上升。能否据此判断公司偿债能力增强？"
              "请给出支持与不支持的证据，并说明需要哪些数据。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=(
        f"现金及等价物由 {f2(g('cash',2026,1))} 增至 {f2(g('cash',2027,1))}，增加 "
        f"{d2(g('cash',2027,1)-g('cash',2026,1))}"
        f"（{(g('cash',2027,1)/g('cash',2026,1)-1)*100:+.2f}%）。"
        "这是支持流动性改善的证据之一；但偿债能力还需短期/长期债务、到期结构、"
        "经营现金流与利息覆盖等数据。可支持的结论是'现金余额上升'，"
        "不能据此概括全部流动性资产上升或偿债能力增强。"),
    sources=[src(2027, 1, 14, "現金及等價物", period="FY2027Q1"),
             src(2026, 1, 15, "現金及等價物（上年同期）", period="FY2026Q1")],
    scoring_points=[
        sp("p1", f"给出现金由 {f2(g('cash',2026,1))} 升至 {f2(g('cash',2027,1))}"
                 f"（+1,403.04，约 +31.15%）", ev=["现金"]),
        sp("p2", "指出还需短期/长期债务、经营现金流等才能判断偿债能力", ev=["数据缺口"],
           contra=["仅凭现金上升断定偿债能力增强"]),
        sp("p3", "给出可支持的结论：现金余额上升（不是全部流动性资产上升）", ev=["可支持结论"]),
    ],
    numeric_assertions=[
        na("cash", "FY2027Q1", g("cash", 2027, 1), "usd_mn", direction="up"),
        na("cash", "FY2026Q1", g("cash", 2026, 1), "usd_mn", direction="up", cat="supporting"),
        na("cash_growth", "FY2027Q1", g("cash", 2027, 1) - g("cash", 2026, 1), "usd_mn",
           baseline="FY2026Q1", direction="up", formula="本期-上期"),
        na("cash_growth_pct", "FY2027Q1",
           (g("cash", 2027, 1) / g("cash", 2026, 1) - 1) * 100, "pct",
           baseline="FY2026Q1", direction="up"),
    ],
    forbidden_claims=["仅凭现金余额上升断定偿债能力增强"],
    reasoning_requirements=["证据边界", "数据缺口"],
    question_type="judgment", origin="new", split_group="FY2027Q1",
    event_family="LV-FY2027Q1-liquidity", data_visibility={"basis": "current_retrospective"},
)

# J6：合成明细边界
add(
    case_id="LV-E2E-SYNTHETIC-DETAIL-BOUNDARY-001",
    question=("数据库中地区/产品线的明细数据在部分期间标记为合成。"
              "如果要用这些明细解释集团收入结构变化，需要注意什么？请说明可用与不可用的边界。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=("合成明细（synthetic=TRUE）不代表真实经营事实，只能用于流程演练；"
                      "用其推断真实结构变化不可靠，需要用公告分部数据替代。"),
    sources=[src(2027, 1, 7, "分部收入", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "指出合成明细不等于真实经营事实", ev=["合成标记"]),
        sp("p2", "说明不能据合成明细推断真实结构变化", contra=["用合成明细下真实结构结论"]),
        sp("p3", "说明应改用公告分部数据或直接说明数据缺口", ev=["可行的替代"]),
    ],
    numeric_assertions=[],
    forbidden_claims=["用合成明细得出真实业务结构结论"],
    reasoning_requirements=["数据可见性", "证据边界"],
    question_type="judgment", origin="new", split_group="global",
    event_family="LV-synthetic-boundary",
    data_visibility={"basis": "current_retrospective",
                     "note": "facts_detail.synthetic 标记；分部真实数据需公告 p7"},
)

# J7：分部/颗粒度限制（blocked）
add(
    case_id="LV-E2E-FY2027Q1-SEGMENT-GAP-001",
    question=("要用 FY2027Q1 数据分析三大业务集团对集团收入增长的贡献："
              "（1）需要哪些数据？（2）当前结构化数据库能否支持，缺什么？"
              "（3）若不能，应如何用公告分部表作答并标注边界？"
              "本题只要求说明数据需求与替代方案，不要求计算分部贡献数值。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=("（1）需要三大业务集团（IDG/ISG/SSG）本期与上年同期的分部收入、"
                      "分部间抵销项，以及集团合并收入以便勾稽。"
                      "（2）当前结构库的 facts_detail 只有 region/product_line 维度且全部为"
                      "synthetic=TRUE，facts 只有集团合并科目，没有业务集团收入与抵销字段，"
                      "因此不能自动计算分部贡献。"
                      "（3）替代方案：建议核对官方分部披露（如 FY2027Q1 公告的分部表），"
                      "手工列出分部收入并说明口径与抵销，明确结论来自公告而非结构库；"
                      "若当前工具无法读取公告正文，应如实说明该限制并建议人工核对官方分部表，"
                      "不要求给出页码。不能声称数据库已支持分部分析。"),
    sources=[src(2027, 1, 7, "分部收入（三大业务集团）", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "列出所需数据：三大业务集团本期与上年同期分部收入、分部间抵销、集团合并收入",
           ev=["所需数据"]),
        sp("p2", "指出当前结构库没有业务集团收入/抵销口径（仅地区/产品线维度，且为合成数据），"
                 "不能自动支持分部贡献计算", ev=["数据缺口"],
           eq=["结构库只有 region/product_line 且为合成数据"],
           contra=["声称数据库已有分部收入"]),
        sp("p3", "给出替代方案：建议核对官方分部披露（如公告分部表）并标注口径与证据边界；"
                 "接受“当前工具无法读取公告正文、建议人工核对”的诚实回答，不要求页码",
           ev=["替代方案", "证据边界"]),
        sp("p4", "不把合成明细或集团合并收入当作分部贡献证据", ev=["证据边界"],
           contra=["用合成明细推断分部结构"]),
    ],
    numeric_assertions=[],
    forbidden_claims=["声称当前数据库含完整分部收入",
                      "用 facts_detail 的 region/product_line 合成数据代替业务集团分部数据"],
    reasoning_requirements=["数据可见性", "数据缺口", "替代方案"],
    question_type="judgment", origin="reused", selected_id="R02",
    event_family="LV-FY2027Q1-segments", split_group="FY2027Q1",
    data_visibility={"basis": "current_retrospective",
                     "note": "分部真实数据仅在公告分部表；第 7 页只是评测侧来源锚点，"
                             "不要求被测 Agent 引用；结构库无分部维度"},
)

# J8：列/期间误用（本期欄 vs 比较欄）
add(
    case_id="LV-E2E-COLUMN-PERIOD-JUDGMENT-001",
    question=("联想业绩公告的利润表同时列出'本期欄'与'比较欄'两列。"
              "如果分析者要把 FY2026Q4 单季收入与上年同期对比，应如何避免把比较欄数字"
              "当作本期数字？请说明核验方法与常见陷阱。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=2,
    reference_answer=("先确认列头期间：全年业绩公告的年度损益表两列分别是 FY2026 与 FY2025 全年"
                      "（比较列），不是 FY2026Q4 与 FY2025Q4；单季分部表只给分部收入，"
                      "不能当作完整单季损益表。要得到 FY2026Q4 单季收入，可用有证据的方法："
                      "（a）全年收入减前三季累计（FY2026Q3 公告的 9M 收入）；"
                      "（b）直接使用明确标注 FY2026Q4 单季列头的集团季度收入合计。"
                      "核验顺序应为：先看原文列头与行名，确认期间与单位（公告为千美元），"
                      "再与数据库 original 交叉核对；不能默认数据库 original 一定正确"
                      "（本库 FY2027Q1 的 net_income 符号与公告括号亏损不一致，"
                      "属单独的数据修复事项，不在题库内改数）。"
                      "常见陷阱：把比较列当作本期、把累计列当作单季、把分部表当作完整损益表。"),
    sources=[src(2026, 4, 17, "收入（全年本期欄/比較欄）", period="FY2026 全年与 FY2025 全年"),
             src(2026, 3, 16, "收入（9M 欄）", period="FY2026 9M"),
             src(2026, 4, 11, "分部收入（非损益表）", period="FY2026Q4 单季分部",
                 note="仅用于说明第 11 页是分部表")],
    scoring_points=[
        sp("p1", "指出必须先确认列头对应的期间（本期 vs 比较），并说明年度损益表两列是 "
                 "FY2026 与 FY2025 全年", ev=["列头"]),
        sp("p2", "区分完整年度损益表与分部表：分部表不能当作完整单季损益表", ev=["报表识别"]),
        sp("p3", "给出至少一种有证据的单季收入核验方法：全年减前三季累计，或直接使用明确 "
                 "FY2026Q4 单季列头的集团收入合计；不能直接读比较列",
           ev=["累计转单季", "列头核验"]),
        sp("p4", "核验顺序：先看原文列头与行名/单位，再与数据库 original 交叉核对；"
                 "不能默认数据库 original 一定正确", ev=["核验方法"],
           contra=["仅凭数据库 original 值判定原文"]),
        sp("p5", "指出常见陷阱（比较列当本期、累计列当单季、分部表当损益表）", ev=["陷阱"]),
    ],
    numeric_assertions=[],
    forbidden_claims=["把比较欄数字当作本期数字"],
    reasoning_requirements=["表格列/期间核验", "证据边界"],
    question_type="judgment", origin="new", split_group="FY2026",
    event_family="LV-column-period-integrity",
    data_visibility={"basis": "current_retrospective",
                     "note": "sources 页码为评测侧锚点，不要求答案引用"},
)

# ============================================================ 历史时点预测（6）
def _metric_value(metric: str, fy: int, q: int) -> float:
    if metric == "revenue":
        return g("revenue", fy, q)
    if metric == "gross_margin":
        return g("gross_profit", fy, q) / g("revenue", fy, q) * 100
    raise ValueError(f"未知预测指标: {metric}")


def forecast_case(case_id, q, question, as_of, truth_value, truth_pdf, truth_page,
                  event_family, notes=None, metric="revenue", target_period=None,
                  history=None, band=None, split_group="FY2026", visible_values=None):
    """历史时点预测题：统一评分规则（目标/单位、历史来源、可复算方法、区间相容）。

    预测题没有唯一正确值，因此得分点只约束"过程可审阅"：目标与单位明确、历史输入有
    来源、方法与假设可复算、区间与假设相容。事后误差（forecast_truth）单独统计，
    不进入事前得分点。

    Args:
        history: [(fy, quarter, page, label)]，as_of 时点可见的历史输入及其公告页。
        band:    可接受的量级判据文字（替代"量级合理"这类不可审阅表述）。
        visible_values: [(period, value)]，显式给出"可见历史输入"的数值断言；
            缺省用 history 第一条（最新可见期）。
    """
    target_period = target_period or f"FY2026Q{q}"
    unit = "usd_mn" if metric == "revenue" else "pct"
    history = history or [(2026, 1, 13, "FY2026Q1")]
    if visible_values is None:
        fy0, q0 = history[0][0], history[0][1]
        visible_values = [(f"FY{fy0}Q{q0}", _metric_value(metric, fy0, q0))]
    truth = None
    if truth_value is not None:
        truth = {"metric": metric, "period": target_period, "value": truth_value,
                 "unit": unit, "source": truth_pdf, "page": truth_page,
                 "note": "仅评测侧用于事后误差统计，不进入事前得分点"}
    label = "收入（百万美元）" if metric == "revenue" else "毛利率（%）"
    band = band or ("以最近四个季度均值为中枢、±25% 仅作诊断参考（不作硬性评分）"
                    if metric == "revenue"
                    else "以最近四个季度毛利率区间为参照、±2pp 仅作诊断参考（不作硬性评分）")
    add(
        case_id=case_id, question=question, scope=SCOPE, as_of=as_of,
        data_refs=["data/finsights.duckdb"], case_revision=2,
        reference_answer=(
            f"截至 {as_of}，可见的{label}历史输入见 sources；作答需明确目标期间 "
            f"{target_period}、单一预测值或中枢、有限区间、可复算的方法与假设。"
            f"量级参考（仅作诊断，不作为硬性得分条件）：{band}。不要求命中某一具体数值；事后实绩仅用于误差统计，"
            "不得写入事前得分点。"),
        sources=[src(fy, qq, pg, "可见历史输入", period=lab, note="as_of 时点已披露")
                 for fy, qq, pg, lab in history],
        scoring_points=[
            sp("p1", f"只使用 {as_of} 及之前已披露的信息，不引用之后的数据", ev=["可见性"],
               contra=["引用 as_of 之后的数据"]),
            sp("p2", f"明确预测对象与单位：{target_period} 的{label}", ev=["目标与单位"]),
            sp("p3", "给出单一预测值或中枢；若明显偏离历史量级，解释增长、季节性等假设"
                     "（量级带仅作诊断提示，不单独判错）", ev=["点预测", "量级解释"]),
            sp("p4", "给出有限区间（下限<上限），且点预测落在区间内", ev=["区间"]),
            sp("p5", "历史输入可追溯：引用 as_of 前已披露的单季/累计数据，并给出结构化结果 "
                     "rid、期间、指标、单位与数据版本，或公告页；不强制公告页码",
               ev=["历史来源"]),
            sp("p6", "方法可复算：给出与可见数据一致的方法（如同比外推、季节形态、趋势），"
                     "写出计算式或参数；接受其他有可见数据支持、可复算且假设清楚的替代方法",
               ev=["方法"]),
            sp("p7", "区间与假设相容：区间宽度与所述不确定性一致；不声称已校准或必然命中",
               ev=["区间相容", "不确定性"],
               contra=["声称预测必然命中", "声称预测已校准"]),
        ],
        numeric_assertions=[na(metric, period, value, unit,
                               direction="up" if metric == "revenue" else "unknown",
                               cat="supporting") for period, value in visible_values],
        forbidden_claims=["把真实实绩当作预测复述", "声称预测已校准"],
        reasoning_requirements=["历史时点可见性", "目标与单位", "方法可复算", "区间与假设相容"],
        question_type="forecast", origin="new", event_family=event_family,
        split_group=split_group, forecast_truth=truth, notes=notes,
        data_visibility={"basis": "as_of", "as_of": as_of},
    )


forecast_case("LV-E2E-FY2026Q2-REVENUE-FORECAST-001", 2,
              "以 2025-08-14 为信息截止日（联想 FY2026Q1 业绩公告已发布），预测联想集团 "
              "FY2026Q2（截至 2025-09-30）营业收入，给出预测值、区间与方法。",
              "2025-08-14", g("revenue", 2026, 2), "FY26Q2_201120250729.pdf", 16,
              "LV-FY2026Q2-forecast-revenue",
              history=[(2026, 1, 13, "FY2026Q1")])
forecast_case("LV-E2E-FY2026Q3-REVENUE-FORECAST-001", 3,
              "以 2025-11-20 为信息截止日（FY2026 中期公告已发布），预测联想集团 "
              "FY2026Q3（截至 2025-12-31）营业收入，给出预测值、区间与方法。",
              "2025-11-20", g("revenue", 2026, 3), "FY26Q3_120220261202.pdf", 16,
              "LV-FY2026Q3-forecast-revenue",
              history=[(2026, 2, 16, "FY2026Q2（中期）"), (2026, 1, 13, "FY2026Q1")])
forecast_case("LV-E2E-FY2026Q4-REVENUE-FORECAST-001", 4,
              "以 2026-02-12 为信息截止日（FY2026Q3 业绩公告已发布），预测联想集团 "
              "FY2026Q4（截至 2026-03-31）营业收入，给出预测值、区间与方法。",
              "2026-02-12", g("revenue", 2026, 4), "FY26Q4_220520260732.pdf", 11,
              "LV-FY2026Q4-forecast-revenue",
              history=[(2026, 3, 16, "FY2026Q3"), (2026, 2, 16, "FY2026Q2"),
                       (2026, 1, 13, "FY2026Q1")])

# F4：FY2026Q2 毛利率预测（as_of 2025-08-14）
forecast_case("LV-E2E-FY2026Q2-GM-FORECAST-001", 2,
              "以 2025-08-14 为信息截止日，预测联想集团 FY2026Q2（截至 2025-09-30）毛利率，"
              "给出预测值（百分比）、区间与方法。",
              "2025-08-14",
              g("gross_profit", 2026, 2) / g("revenue", 2026, 2) * 100,
              "FY26Q2_201120250729.pdf", 16,
              "LV-FY2026Q2-forecast-gm", metric="gross_margin",
              history=[(2026, 1, 13, "FY2026Q1")])

# F5：FY2027Q2 收入预测（as_of 2026-08-13；目标期间未披露 → 无真实实绩）
forecast_case("LV-E2E-FY2027Q2-REVENUE-FORECAST-001", 2,
              "以 2026-08-13 为信息截止日（FY2027Q1 业绩公告已发布），预测联想集团 "
              "FY2027Q2（截至 2026-09-30）营业收入，给出预测值、区间与方法。",
              "2026-08-13", None, None, None,
              "LV-FY2027Q2-forecast-revenue",
              history=[(2026, 2, 16, "FY2026Q2 同期基数"), (2027, 1, 12, "FY2027Q1")],
              target_period="FY2027Q2",
              visible_values=[("FY2027Q1", g("revenue", 2027, 1)),
                              ("FY2026Q2", g("revenue", 2026, 2))],
              split_group="FY2027",
              notes="目标期间（FY2027Q2）尚未披露，无真实实绩；不伪造未来值，"
                    "只评预测过程，暂不算误差。")

# F6：FY2026Q1 收入预测（as_of 2025-05-22，FY2025 全年业绩公告已发布）
forecast_case("LV-E2E-FY2026Q1-REVENUE-FORECAST-001", 1,
              "以 2025-05-22 为信息截止日（FY2025 全年业绩公告已发布，FY2026Q1 尚未披露），"
              "预测联想集团 FY2026Q1（截至 2025-06-30）营业收入，给出预测值、区间与方法。",
              "2025-05-22", g("revenue", 2026, 1), "FY26Q1_140820250742.pdf", 13,
              "LV-FY2026Q1-forecast-revenue",
              history=[(2025, 4, 17, "FY2025 全年"), (2025, 1, 12, "FY2025Q1 基数")],
              visible_values=[("FY2025 全年", ysum("revenue", 2025)),
                              ("FY2025Q1", g("revenue", 2025, 1))])

# ---- 开发/留出划分（PRD §5.4）----
# 按 split_group 整组划分，禁止同一组同时出现在开发集与留出集（复审问题 4）。
# 含已审 S01–S07 的组（FY2026 / FY2025Q3 / FY2027Q1）按已审用途归开发集；
# 其余组（FY2024 / FY2025 / FY2027 / global）归留出，并逐题检查期间是否与开发集重叠。
import re  # noqa: E402

_DEV_GROUPS = {"FY2026", "FY2025Q3", "FY2027Q1"}


def _periods(case) -> set[tuple[int, int | None]]:
    out: set[tuple[int, int | None]] = set()
    for a in case.numeric_assertions:
        m = re.match(r"FY(\d{4})(?:Q([1-4]))?", a.period)
        if m:
            out.add((int(m.group(1)), int(m.group(2)) if m.group(2) else None))
    return out


def _overlaps(p: tuple[int, int | None], pool: set[tuple[int, int | None]]) -> bool:
    for fy, q in pool:
        if fy == p[0] and (q is None or p[1] is None or q == p[1]):
            return True
    return False


for _i, _c in enumerate(CASES):
    _split = "dev" if (_c.split_group in _DEV_GROUPS or _c.origin == "reused") else "holdout"
    CASES[_i] = _c.model_copy(update={"split": _split})

_dev_periods: set[tuple[int, int | None]] = set()
for _c in CASES:
    if _c.split == "dev":
        _dev_periods |= _periods(_c)
for _i, _c in enumerate(CASES):
    if _c.split == "holdout":
        _independent = not any(_overlaps(p, _dev_periods) for p in _periods(_c))
        _vis = dict(_c.data_visibility)
        _vis["independent_holdout"] = _independent
        if not _independent:
            _vis["independent_holdout_note"] = "与开发集共享期间，结论不得当作独立留出泛化证据"
        CASES[_i] = _c.model_copy(update={"data_visibility": _vis})

# ---- 写盘 ----
if len(CASES) != 30:
    print(f"警告：期望 30 题，实际 {len(CASES)} 题", file=sys.stderr)
save_dataset(CASES, OUT)
write_sources_index(CASES, "eval/datasets/financial_real_v1/sources.json", manifest=MAN)

# 数据集元信息：记录数据版本（供报告按版本对齐，PRD §6.3/§9.1）
import hashlib  # noqa: E402
import json  # noqa: E402
from collections import Counter  # noqa: E402

db = Path("data/finsights.duckdb")
meta = {
    "dataset": "financial_real_v1",
    "n_cases": len(CASES),
    "db": str(db),
    "db_sha256_12": hashlib.sha256(db.read_bytes()).hexdigest()[:12] if db.exists() else None,
    "question_type": dict(Counter(c.question_type for c in CASES)),
    "origin": dict(Counter(c.origin for c in CASES)),
    "split": dict(Counter(c.split for c in CASES)),
    "holdout_independent": sum(1 for c in CASES
                               if c.split == "holdout"
                               and c.data_visibility.get("independent_holdout")),
    "review_status": dict(Counter(c.review_status for c in CASES)),
    "blocked": [c.case_id for c in CASES if c.blocked],
    "note": "审核状态以 cases.jsonl 为准；生成器不自批；真实实绩仅评测侧。",
}
Path("eval/datasets/financial_real_v1/dataset_meta.json").write_text(
    json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"已写入 {OUT}：{len(CASES)} 题（blocked={meta['blocked']}）")
print("题型分布:", meta["question_type"])
print("split:", meta["split"], "origin:", meta["origin"])
_con.close()

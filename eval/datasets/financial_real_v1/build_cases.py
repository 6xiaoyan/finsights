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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2026 前九个月收入 {f2(r26_9m)}、毛利 {f2(gp26_9m)}，毛利率 {pct(gm26_9m)}；"
        f"FY2025 前九个月毛利率 {pct(gm25_9m)}，整体同比 {pp(gm26_9m - gm25_9m)}。"
        f"单季同比降幅：Q1 {pp(qdelta[1])}、Q2 {pp(qdelta[2])}、Q3 {pp(qdelta[3])}。"
        "原因：前九个月毛利率是各季毛利与收入按金额加权的比率，各季收入权重不同，"
        "整体降幅是加权结果，既不等于简单平均，也不等于任一单季降幅。"),
    sources=[src(2026, 3, IS(2026, 3), "收入/毛利", note="FY26 9M 利润表", period="FY2026 9M"),
             src(2026, 1, IS(2026, 1), "收入/毛利", period="FY2026Q1", note="FY26Q1 利润表"),
             src(2026, 2, IS(2026, 2), "收入/毛利", period="FY2026Q2", note="FY26Q2 利润表")],
    scoring_points=[
        sp("p1", f"给出 FY2026 前九个月毛利率约 {gm26_9m:.1f}%", ev=["9M 毛利率"]),
        sp("p2", f"给出整体同比降幅约 {gm26_9m - gm25_9m:.1f}pp", ev=["9M 同比"]),
        sp("p3", "分别给出三个单季毛利率同比降幅（Q1 约 -1.8pp，Q2、Q3 各不相同）",
           ev=["单季降幅"]),
        sp("p4", "解释整体降幅是收入加权结果，与单季降幅、简单平均都不相等", prereq=["p1", "p3"]),
        sp("p5", "不使用简单平均代替加权得到整体毛利率", contra=["用单季降幅算术平均当作整体降幅"]),
        sp("p6", "说明单季数据来自各季公告、9M 由三季求和", req="optional"),
    ],
    numeric_assertions=[
        na("gross_margin", "FY2026 9M", gm26_9m, "pct", direction="down"),
        na("gross_margin_delta", "FY2026 9M", gm26_9m - gm25_9m, "pct_point", baseline="FY2025 9M",
           direction="down"),
        na("gross_margin_delta", "FY2026Q1", qdelta[1], "pct_point", baseline="FY2025Q1",
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2026 全年收入 {f2(ysum('revenue',2026))}，前三季累计 {f2(r26_9m)}，"
        f"推出 FY2026Q4 单季收入 {f2(r26_q4)}；全年毛利 {f2(ysum('gross_profit',2026))}，"
        f"前三季累计毛利 {f2(gp26_9m)}，推出 Q4 单季毛利 {f2(gp26_q4)}，"
        f"Q4 单季毛利率 {pct(gp26_q4/r26_q4*100)}。直接用全年数字会高估 Q4 规模并混淆口径，"
        "因为全年是四个季度之和，与单季不可比。"),
    sources=[src(2026, 4, IS(2026, 4), "收入/毛利", note="FY26 全年利润表", period="FY2026 全年"),
             src(2026, 3, IS(2026, 3), "收入/毛利", period="FY2026 9M")],
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2025 全年收入 {f2(ysum('revenue',2025))}（同比 "
        f"{(ysum('revenue',2025)/ysum('revenue',2024)-1)*100:+.2f}%），净利润 "
        f"{f2(ysum('net_income',2025))}（同比 "
        f"{(ysum('net_income',2025)/ysum('net_income',2024)-1)*100:+.2f}%）。"
        f"FY2025Q4 单季收入 {f2(g('revenue',2025,4))}（同比 "
        f"{(g('revenue',2025,4)/g('revenue',2024,4)-1)*100:+.2f}%），净利润 "
        f"{f2(g('net_income',2025,4))}（同比 "
        f"{(g('net_income',2025,4)/g('net_income',2024,4)-1)*100:+.2f}%）。"
        "全年由四季构成，前三季的高低可以抵消第四季度的走弱，故单季与全年方向可以不同。"),
    sources=[src(2025, 4, 11, "收入/期内溢利", period="FY2025Q4 单季"),
             src(2025, 4, 17, "收入/期内溢利（全年欄）", period="FY2025 全年")],
    scoring_points=[
        sp("p1", "给出 FY2025 全年收入同比为正", ev=["全年收入"]),
        sp("p2", "给出 FY2025Q4 单季净利润同比下降", ev=["Q4 净利润"],
           contra=["称 Q4 净利润同比增长"]),
        sp("p3", "解释全年是多季合计，单季走弱可被其它季度抵消", prereq=["p2"]),
    ],
    numeric_assertions=[
        na("net_income_growth_pct", "FY2025Q4",
           (g("net_income", 2025, 4) / g("net_income", 2024, 4) - 1) * 100, "pct",
           baseline="FY2024Q4", direction="down"),
        na("revenue", "FY2025", ysum("revenue", 2025), "usd_mn", direction="up"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
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
        sp("p2", "给出同比增加约 +11,113 百万美元", ev=["同比金额"]),
        sp("p3", "给出同比增速约 +59%", ev=["同比增速"]),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
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
        sp("p1", f"给出 FY2026Q4 收入环比约 {d2(g('revenue',2026,4)-g('revenue',2026,3))} 或 "
                 f"{(g('revenue',2026,4)/g('revenue',2026,3)-1)*100:+.1f}%", ev=["收入环比"]),
        sp("p2", f"给出 FY2026Q4 毛利环比约 {d2(g('gross_profit',2026,4)-g('gross_profit',2026,3))}"
                 f" 或 {(g('gross_profit',2026,4)/g('gross_profit',2026,3)-1)*100:+.1f}%",
           ev=["毛利环比"]),
        sp("p3", "指出二者反向意味着毛利率环比提升，即可共存", prereq=["p1", "p2"]),
        sp("p4", "说明环比与同比是不同比较基准，不能混用", req="optional"),
    ],
    numeric_assertions=[
        na("revenue", "FY2026Q4", g("revenue", 2026, 4), "usd_mn", direction="down",
           baseline="FY2026Q3"),
        na("gross_profit", "FY2026Q4", g("gross_profit", 2026, 4), "usd_mn", direction="up",
           baseline="FY2026Q3", formula="由全年欄减前三季累计推出"),
    ],
    forbidden_claims=["把环比变化当作同比变化"],
    reasoning_requirements=["环比", "比率与金额", "比较基准区分"],
    question_type="multi_period", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q4-qoq-divergence",
    data_visibility={"basis": "current_retrospective"},
)

# ============================================================ 变化分解与归因（10）
# A1：S04 FY2025Q3 费用率 56bps
add(
    case_id="LV-E2E-FY2025Q3-OPEX-RATIO-001",
    question=("管理层称联想 FY2025Q3 '经营费用对收入比率同比下降 56 个基点至 12.1%'。"
              "请只用收入、毛利、经营利润（经营费用=毛利−经营利润）分别在 Q3 单季口径与"
              "截至 12 月 31 日止九个月累计口径下复算该比率的同比变化，"
              "判断管理层的说法对应哪个口径、是否成立，并说明比率下降是否等于费用收缩。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：单季与 9M 两个口径的费用率同比变化不同；"
                     "比率下降可由收入增长快于费用增长导致，未必是费用金额收缩。",
    sources=[src(2025, 3, IS(2025, 3), "收入/毛利/经营溢利", period="FY2025Q3 与 9M")],
    scoring_points=[
        sp("p1", "分别计算 Q3 单季与 9M 累计的经营费用率同比变化", ev=["两个口径"]),
        sp("p2", "指出管理层的 56bps 对应哪个口径并给出判断", prereq=["p1"]),
        sp("p3", "说明费用率下降不等于费用金额收缩（需看费用金额绝对变化）", ev=["金额 vs 比率"]),
        sp("p4", "给出 Q3 单季经营费用金额同比变化方向", req="optional"),
    ],
    numeric_assertions=[
        na("opex_ratio", "FY2025Q3",
           (g("gross_profit", 2025, 3) - g("operating_income", 2025, 3)) / g("revenue", 2025, 3) * 100,
           "pct", direction="down"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：收入下降、毛利率上升但费用率上升更多，经营利润下降。",
    sources=[src(2024, 4, 17, "收入/毛利/经营溢利（全年欄）", period="FY2024 全年")],
    scoring_points=[
        sp("p1", "给出 FY2024 全年收入同比为负及其金额", ev=["收入"]),
        sp("p2", "给出毛利率同比方向（上升）", ev=["毛利率"]),
        sp("p3", "给出经营利润同比为负及金额", ev=["经营利润"]),
        sp("p4", "指出经营利润下降主要由费用率上升驱动，而非毛利", prereq=["p2", "p3"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2024", ysum("revenue", 2024), "usd_mn", direction="down"),
        na("operating_income", "FY2024", ysum("operating_income", 2024), "usd_mn",
           direction="down"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：DSO 同比上升，AR 增速高于收入增速。",
    sources=[src(2026, 4, 19, "應收賬款", period="FY2026Q4"),
             src(2025, 4, 19, "應收賬款", period="FY2025Q4")],
    scoring_points=[
        sp("p1", "给出 FY2026Q4 AR 同比增幅高于收入增幅", ev=["AR vs 收入"]),
        sp("p2", "给出 DSO 同比上升的天数（约 +3 天）", ev=["DSO"]),
        sp("p3", "说明 DSO 上升与'回款转弱'的关系需谨慎（可能受季节性/结构影响）",
           ev=["证据边界"], contra=["仅凭 DSO 上升断定回款恶化"]),
    ],
    numeric_assertions=[
        na("dso_days", "FY2026Q4",
           (g("accounts_receivable", 2025, 4) + g("accounts_receivable", 2026, 4)) / 2
           / g("revenue", 2026, 4) * 90, "days", direction="up", tol_abs=0.5),
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
    question=("联想集团 FY2026Q4 递延收入（流动）与应计负债同比变化不同。"
              "请量化这两个负债科目的同比变化金额，并说明它们分别反映了什么、"
              "不能据此推断什么。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：两科目同比变化金额与方向不同。",
    sources=[src(2026, 4, 20, "遞延收入/應計費用", period="FY2026Q4")],
    scoring_points=[
        sp("p1", "给出递延收入（流动）同比变化金额", ev=["递延收入"]),
        sp("p2", "给出应计负债同比变化金额", ev=["应计负债"]),
        sp("p3", "说明这两项分别的含义与不能据此推断的内容", ev=["证据边界"]),
    ],
    numeric_assertions=[
        na("deferred_revenue_current", "FY2026Q4", g("deferred_revenue_current", 2026, 4),
           "usd_mn", direction="unknown"),
        na("accrued_liabilities", "FY2026Q4", g("accrued_liabilities", 2026, 4),
           "usd_mn", direction="unknown"),
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
              "请用时点余额与全年流量计算 FY2024 与上年的 DIO 及变化，"
              "并说明存货与成本增速差对 DIO 的影响。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：DIO 同比变化由存货增速与成本增速差决定。",
    sources=[src(2024, 4, 19, "存貨", period="FY2024Q4"),
             src(2024, 4, IS(2024, 4), "營業成本", period="FY2024 全年")],
    scoring_points=[
        sp("p1", "给出存货同比变化方向与幅度", ev=["存货"]),
        sp("p2", "给出营业成本同比变化方向", ev=["营业成本"]),
        sp("p3", "给出 DIO 同比变化方向及天数", ev=["DIO"]),
        sp("p4", "说明 DIO 由存货与成本增速的相对关系决定", prereq=["p1", "p2"]),
    ],
    numeric_assertions=[
        na("inventory", "FY2024Q4", g("inventory", 2024, 4), "usd_mn", direction="unknown"),
        na("dio_days", "FY2024", g("inventory", 2024, 4) / ysum("cogs", 2024) * 365, "days",
           direction="unknown", tol_abs=1.0),
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
    question=("联想集团 FY2027Q1 现金及短期投资同比变化明显。请量化现金与短期投资各自的"
              "同比变化金额，并说明这反映的是流动性增强还是资产结构调整，以及仅凭这两个"
              "科目能否判断偿债能力。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：现金与短期投资同比变化金额不同；仅凭两科目不能判断偿债能力。",
    sources=[src(2027, 1, 14, "現金及等價物/短期投資", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "给出现金同比变化金额", ev=["现金"]),
        sp("p2", "给出短期投资同比变化金额", ev=["短期投资"]),
        sp("p3", "说明仅凭两科目不足以判断偿债能力（需负债与现金流）", ev=["证据边界"]),
    ],
    numeric_assertions=[
        na("cash", "FY2027Q1", g("cash", 2027, 1), "usd_mn", direction="up"),
        na("short_term_investments", "FY2027Q1", g("short_term_investments", 2027, 1),
           "usd_mn", direction="unknown"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：经营利润与净利润同比不同步，中间隔着税项/非控股权益等。",
    sources=[src(2026, 3, IS(2026, 3), "經營溢利/期內溢利", period="FY2026Q3")],
    scoring_points=[
        sp("p1", "给出 FY2026Q3 经营利润同比变化金额与方向", ev=["经营利润"]),
        sp("p2", "给出 FY2026Q3 净利润同比变化金额与方向", ev=["净利润"]),
        sp("p3", "说明二者之间隔着税项、非控股权益等项目", ev=["利润层"]),
    ],
    numeric_assertions=[
        na("operating_income", "FY2026Q3", g("operating_income", 2026, 3), "usd_mn",
           direction="up"),
        na("net_income", "FY2026Q3", g("net_income", 2026, 3), "usd_mn", direction="up"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2026 全年收入 {f2(_r26)}（同比 {( _r26/_r25-1)*100:+.2f}%），毛利 {f2(_gp26)}"
        f"（同比 {(_gp26/_gp25-1)*100:+.2f}%），经营费用（毛利−经营溢利）{f2(_gp26-_oi26)}"
        f"（同比 {d2((_gp26-_oi26)-(_gp25-_oi25))}），经营溢利 {f2(_oi26)}"
        f"（同比 {(_oi26/_oi25-1)*100:+.2f}%）。不矛盾：毛利增速低于收入增速导致毛利率"
        "下降，但费用增速更低使经营溢利增速高于毛利增速。管理层的组合/ISG 解释属公司说明，"
        "不能仅凭这些报表数字证实为独立因果。"),
    sources=[src(2026, 4, 17, "收入/毛利/经营溢利（全年欄）", period="FY2026 全年")],
    scoring_points=[
        sp("p1", "给出全年收入同比约 +20.3%", ev=["收入"]),
        sp("p2", "给出全年毛利同比约 +15.4%", ev=["毛利"]),
        sp("p3", "给出毛利率同比下降约 0.7pp", ev=["毛利率"], contra=["称毛利率上升"]),
        sp("p4", "给出经营溢利同比约 +50.7%", ev=["经营溢利"]),
        sp("p5", "指出毛利增速低于收入、费用增速更低，二者可同时成立", prereq=["p2", "p4"]),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：增量约 +111 亿美元；仅凭合并收入不能判断分部来源。",
    sources=[src(2027, 1, IS(2027, 1), "收入", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "给出上年同期收入基数与本期收入", ev=["基数与本期"]),
        sp("p2", "给出本期增量金额", ev=["增量"]),
        sp("p3", "说明仅凭合并收入不能判断分部来源，缺分部数据", ev=["数据缺口"]),
    ],
    numeric_assertions=[
        na("revenue_growth", "FY2027Q1", g("revenue", 2027, 1) - g("revenue", 2026, 1), "usd_mn",
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
add(
    case_id="LV-E2E-FY2027Q1-WORKING-CAPITAL-CCC-001",
    question=("联想集团 FY2027Q1 存货、应收、应付同季均大幅增长。请按同比 DSO、DIO、DPO 与"
              "现金转换周期 CCC（天数均取 91；流量用单季收入/营业成本，余额用（期初+期末)/2）"
              "量化这轮营运资金扩张，判断主要由存货/应收占用还是应付融资支撑，"
              "并用现金余额说明能否据此判断现金压力。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=(
        f"FY2027Q1 同比：DSO {_dso_p:.2f}→{_dso_c:.2f} 天（{_dso_c-_dso_p:+.2f}）；"
        f"DIO {_dio_p:.2f}→{_dio_c:.2f} 天；DPO {_dpo_p:.2f}→{_dpo_c:.2f} 天"
        f"（{_dpo_c-_dpo_p:+.2f}）。CCC = DSO+DIO−DPO：本期 {_ccc_c:.2f} 天，上年同期 "
        f"{_ccc_p:.2f} 天，基本持平。选定存货+应收−应付净占用同比 "
        f"{d2(_nwc)} 百万美元（存货 {d2(g('inventory',2027,1)-g('inventory',2026,1))}、"
        f"应收 {d2(g('accounts_receivable',2027,1)-g('accounts_receivable',2026,1))}、"
        f"应付 {d2(g('accounts_payable',2027,1)-g('accounts_payable',2026,1))}）。"
        f"应付融资增加幅度大于存货/应收占用，使 CCC 基本不变；现金余额由 "
        f"{f2(g('cash',2026,1))} 升至 {f2(g('cash',2027,1))}。但选定三科目净变化不等于"
        "全口径营运资金或经营现金流，现金压力判断仍缺现金流量表口径证据。"),
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
        sp("p6", "结论：应付融资增长抵消了存货/应收占用，CCC 基本不变；现金余额上升",
           prereq=["p3", "p4"]),
        sp("p7", "说明三科目净变化不等于全口径营运资金/经营现金流，现金压力判断仍缺证据",
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
        na("net_working_capital_delta", "FY2027Q1", _nwc, "usd_mn", baseline="FY2026Q1",
           formula="Δ存货+Δ应收−Δ应付", direction="up"),
        na("cash", "FY2027Q1", g("cash", 2027, 1), "usd_mn", direction="up", cat="supporting"),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：至少有一个单季净利润同比为负，全年增长不能推出各季改善。",
    sources=[src(2026, 4, IS(2026, 4), "期內溢利", period="FY2026 全年与单季")],
    scoring_points=[
        sp("p1", "给出 FY2026 全年净利润同比为正", ev=["全年"]),
        sp("p2", "指出至少有一个单季净利润同比为负", ev=["单季"], contra=["称各季都增长"]),
        sp("p3", "说明累计口径不能推出单季普遍改善", prereq=["p2"]),
    ],
    numeric_assertions=[
        na("net_income_growth_pct", "FY2026",
           (ysum("net_income", 2026) / ysum("net_income", 2025) - 1) * 100, "pct",
           baseline="FY2025", direction="up"),
        na("net_income_growth_pct", "FY2026Q2",
           (g("net_income", 2026, 2) / g("net_income", 2025, 2) - 1) * 100, "pct",
           baseline="FY2025Q2", direction="down", cat="supporting", tol=0.15),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：比率下降支持'费用强度下降'但不足以单独证明效率提升。",
    sources=[src(2025, 3, IS(2025, 3), "收入/毛利/经营溢利", period="FY2025Q3")],
    scoring_points=[
        sp("p1", "给出费用率同比下降的量化证据", ev=["费用率"]),
        sp("p2", "指出费用率下降不等于效率提升（可能是收入结构或一次性项）", ev=["证据边界"],
           contra=["仅凭费用率下降断定效率提升"]),
        sp("p3", "列出还需要的数据（费用明细、人员/研发投入等）", ev=["数据缺口"]),
    ],
    numeric_assertions=[
        na("opex_ratio", "FY2025Q3",
           (g("gross_profit", 2025, 3) - g("operating_income", 2025, 3)) / g("revenue", 2025, 3) * 100,
           "pct", direction="down"),
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
              "结论？请给出支持与不支持的数字，并说明需要哪些额外数据。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：存货与 DIO 数字可支持'库存规模上升、周转天数上升'，"
                     "但'主动备货'属管理层意图，现有数据不足以证明。",
    sources=[src(2027, 1, 14, "存貨", period="FY2027Q1"),
             src(2027, 1, IS(2027, 1), "營業成本", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "给出存货同比增长与 DIO 变化作为支持证据", ev=["存货/DIO"]),
        sp("p2", "指出'主动备货'需要管理层披露或订单/指引等额外数据", ev=["数据缺口"],
           contra=["仅凭库存上升断定主动备货"]),
        sp("p3", "给出可支持的结论（库存规模与周转天数上升）", ev=["可支持结论"]),
    ],
    numeric_assertions=[
        na("inventory_growth_pct", "FY2027Q1",
           (g("inventory", 2027, 1) / g("inventory", 2026, 1) - 1) * 100, "pct",
           baseline="FY2026Q1", direction="up"),
        na("dio_days", "FY2027Q1",
           (g("inventory", 2026, 4) + g("inventory", 2027, 1)) / 2 / g("cogs", 2027, 1) * 91,
           "days", direction="up", tol_abs=0.1),
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
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="见核算：现金上升是支持证据之一，但偿债能力需结合债务与现金流。",
    sources=[src(2027, 1, 14, "現金及等價物", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "给出现金同比上升的量化证据", ev=["现金"]),
        sp("p2", "指出还需短期/长期债务、经营现金流等才能判断偿债能力", ev=["数据缺口"],
           contra=["仅凭现金上升断定偿债能力增强"]),
        sp("p3", "给出可支持的结论（流动性资产上升）", ev=["可支持结论"]),
    ],
    numeric_assertions=[
        na("cash", "FY2027Q1", g("cash", 2027, 1), "usd_mn", direction="up"),
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
    question=("要用 FY2027Q1 数据分析三大业务集团对集团收入增长贡献，需要什么数据？"
              "当前结构化数据库能否支持？请说明缺口与替代方案。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=("需要三分部两期收入与抵销项的结构化表；当前 DB 无分部数据，"
                      "只能引用公告分部表，需补数后才能自动评分。"),
    sources=[src(2027, 1, 7, "分部收入", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "指出需要分部两期收入与抵销项", ev=["所需数据"]),
        sp("p2", "指出当前结构库无分部数据（数据缺口）", ev=["数据缺口"],
           contra=["声称数据库已有分部数据"]),
        sp("p3", "给出替代方案（引用公告分部表或登记 blocked）", ev=["替代方案"]),
    ],
    numeric_assertions=[],
    forbidden_claims=["声称当前数据库含完整分部收入"],
    reasoning_requirements=["数据可见性", "数据缺口"],
    question_type="judgment", origin="reused", selected_id="R02",
    event_family="LV-FY2027Q1-segments", split_group="FY2027Q1",
    data_visibility={"basis": "current_retrospective"},
    blocked=True, blocked_reason="当前结构化库无分部收入/抵销项，需补数后才能自动评分",
)

# J8：列/期间误用（本期欄 vs 比较欄）
add(
    case_id="LV-E2E-COLUMN-PERIOD-JUDGMENT-001",
    question=("联想业绩公告的利润表同时列出'本期欄'与'比较欄'两列。"
              "如果分析者要把 FY2026Q4 单季收入与上年同期对比，应如何避免把比较欄数字"
              "当作本期数字？请说明核验方法与常见陷阱。"),
    scope=SCOPE, data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=("应先确认列头期间（本期=FY2026Q4，比较欄=FY2025Q4），"
                      "再核对金额与数据库 original 值、单位；常见陷阱是把比较欄当作本期、"
                      "或把累计列当作单季列。"),
    sources=[src(2026, 4, IS(2026, 4), "收入（本期欄/比較欄）", period="FY2026Q4")],
    scoring_points=[
        sp("p1", "指出必须先确认列头对应的期间（本期 vs 比较）", ev=["列头"]),
        sp("p2", "给出核验方法（与数据库 original 值/单位交叉核对）", ev=["核验方法"]),
        sp("p3", "指出常见陷阱（比较欄当本期、累计列当单季）", ev=["陷阱"]),
    ],
    numeric_assertions=[
        na("revenue", "FY2026Q4", g("revenue", 2026, 4), "usd_mn", direction="down"),
        na("revenue", "FY2025Q4", g("revenue", 2025, 4), "usd_mn", direction="unknown",
           cat="supporting"),
    ],
    forbidden_claims=["把比较欄数字当作本期数字"],
    reasoning_requirements=["表格列/期间核验", "证据边界"],
    question_type="judgment", origin="new", split_group="FY2026",
    event_family="LV-column-period-integrity",
    data_visibility={"basis": "current_retrospective"},
)

# ============================================================ 历史时点预测（6）
def forecast_case(case_id, q, question, as_of, truth_value, truth_pdf, truth_page,
                  event_family, notes=None, metric="revenue"):
    truth = None
    if truth_value is not None:
        truth = {"metric": metric, "period": f"FY2026Q{q}", "value": truth_value,
                 "unit": "usd_mn" if metric == "revenue" else "pct",
                 "source": truth_pdf, "page": truth_page,
                 "note": "仅评测侧用于事后误差统计，不进入事前得分点"}
    add(
        case_id=case_id, question=question, scope=SCOPE, as_of=as_of,
        data_refs=["data/finsights.duckdb"], case_revision=1,
        reference_answer=("截至 as_of 可见的历史收入与增速；给出点预测或中枢、有限区间、"
                          "方法与不确定性表述。不要求命中某一具体数值。"),
        sources=[src(2026, 1, IS(2026, 1), "收入", period="FY2026Q1", note="as_of 可见末季")],
        scoring_points=[
            sp("p1", "明确只使用 as_of 及之前可见的信息", ev=["可见性"],
               contra=["引用 as_of 之后的数据"]),
            sp("p2", "给出点预测或中枢（量级合理）", ev=["点预测"]),
            sp("p3", "给出有限区间预测", ev=["区间"]),
            sp("p4", "说明方法（同比外推/季节形态）", ev=["方法"]),
            sp("p5", "表达假设与不确定性，不声称已校准", ev=["不确定性"],
               contra=["声称预测必然命中"]),
        ],
        numeric_assertions=[na("revenue", "FY2026Q1", g("revenue", 2026, 1), "usd_mn",
                               direction="up", cat="supporting")],
        forbidden_claims=["把真实实绩当作预测复述", "声称预测已校准"],
        reasoning_requirements=["历史时点可见性", "区间预测", "不确定性"],
        question_type="forecast", origin="new", event_family=event_family,
        split_group="FY2026", forecast_truth=truth, notes=notes,
        data_visibility={"basis": "as_of", "as_of": as_of},
    )


forecast_case("LV-E2E-FY2026Q2-REVENUE-FORECAST-001", 2,
              "以 2025-08-14 为信息截止日（联想 FY2026Q1 业绩公告已发布），预测联想集团 "
              "FY2026Q2（截至 2025-09-30）营业收入，给出预测值、区间与方法。",
              "2025-08-14", g("revenue", 2026, 2), "FY26Q2_201120250729.pdf", 16,
              "LV-FY2026Q2-forecast-revenue")
forecast_case("LV-E2E-FY2026Q3-REVENUE-FORECAST-001", 3,
              "以 2025-11-20 为信息截止日（FY2026 中期公告已发布），预测联想集团 "
              "FY2026Q3（截至 2025-12-31）营业收入，给出预测值、区间与方法。",
              "2025-11-20", g("revenue", 2026, 3), "FY26Q3_120220261202.pdf", 16,
              "LV-FY2026Q3-forecast-revenue")
forecast_case("LV-E2E-FY2026Q4-REVENUE-FORECAST-001", 4,
              "以 2026-02-12 为信息截止日（FY2026Q3 业绩公告已发布），预测联想集团 "
              "FY2026Q4（截至 2026-03-31）营业收入，给出预测值、区间与方法。",
              "2026-02-12", g("revenue", 2026, 4), "FY26Q4_220520260732.pdf", 11,
              "LV-FY2026Q4-forecast-revenue")

# F4：FY2026Q2 毛利率预测（as_of 2025-08-14）
add(
    case_id="LV-E2E-FY2026Q2-GM-FORECAST-001",
    question=("以 2025-08-14 为信息截止日，预测联想集团 FY2026Q2（截至 2025-09-30）毛利率，"
              "给出预测值（百分比）、区间与方法。"),
    scope=SCOPE, as_of="2025-08-14", data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer="截至 as_of 可见各季毛利率；给出点预测、有限区间、方法与不确定性。",
    sources=[src(2026, 1, IS(2026, 1), "收入/毛利", period="FY2026Q1")],
    scoring_points=[
        sp("p1", "明确只使用 as_of 及之前可见信息", contra=["引用之后的数据"]),
        sp("p2", "给出毛利率点预测（百分比）", ev=["点预测"]),
        sp("p3", "给出有限区间预测", ev=["区间"]),
        sp("p4", "说明方法与假设", ev=["方法"]),
    ],
    numeric_assertions=[na("gross_margin", "FY2026Q1",
                           g("gross_profit", 2026, 1) / g("revenue", 2026, 1) * 100, "pct",
                           direction="unknown", cat="supporting")],
    forbidden_claims=["把真实毛利率当作预测复述"],
    reasoning_requirements=["历史时点可见性", "区间预测"],
    question_type="forecast", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q2-forecast-gm",
    forecast_truth={"metric": "gross_margin", "period": "FY2026Q2",
                    "value": g("gross_profit", 2026, 2) / g("revenue", 2026, 2) * 100,
                    "unit": "pct", "source": "FY26Q2_201120250729.pdf p16", "page": 16,
                    "note": "仅评测侧"},
    data_visibility={"basis": "as_of", "as_of": "2025-08-14"},
)

# F5：FY2027Q2 收入预测（as_of 2026-08-13；目标期间未披露 → 无真实实绩）
add(
    case_id="LV-E2E-FY2027Q2-REVENUE-FORECAST-001",
    question=("以 2026-08-13 为信息截止日（FY2027Q1 业绩公告已发布），预测联想集团 "
              "FY2027Q2（截至 2026-09-30）营业收入，给出预测值、区间与方法。"),
    scope=SCOPE, as_of="2026-08-13", data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=("截至 as_of 可见 FY2027Q1 收入与增速、FY2026Q2 同期基数；"
                      "给出点预测、有限区间、方法与不确定性。目标期间尚未披露，"
                      "无真实实绩，事后仅可做误差统计（若后续披露）。"),
    sources=[src(2027, 1, IS(2027, 1), "收入", period="FY2027Q1")],
    scoring_points=[
        sp("p1", "明确只使用 as_of 及之前可见信息", contra=["引用之后的数据"]),
        sp("p2", "给出点预测（量级合理）", ev=["点预测"]),
        sp("p3", "给出有限区间预测", ev=["区间"]),
        sp("p4", "说明方法与不确定性", ev=["方法"]),
    ],
    numeric_assertions=[na("revenue", "FY2027Q1", g("revenue", 2027, 1), "usd_mn",
                           direction="up", cat="supporting")],
    forbidden_claims=["声称预测已校准"],
    reasoning_requirements=["历史时点可见性", "区间预测"],
    question_type="forecast", origin="new", split_group="FY2027",
    event_family="LV-FY2027Q2-forecast-revenue",
    forecast_truth=None,
    notes="目标期间（FY2027Q2）尚未披露，无真实实绩；不伪造未来值。",
    data_visibility={"basis": "as_of", "as_of": "2026-08-13"},
)

# F6：FY2026Q1 收入预测（as_of 2025-05-22，FY2025 全年业绩公告已发布）
add(
    case_id="LV-E2E-FY2026Q1-REVENUE-FORECAST-001",
    question=("以 2025-05-22 为信息截止日（FY2025 全年业绩公告已发布，FY2026Q1 尚未披露），"
              "预测联想集团 FY2026Q1（截至 2025-06-30）营业收入，给出预测值、区间与方法。"),
    scope=SCOPE, as_of="2025-05-22", data_refs=["data/finsights.duckdb"], case_revision=1,
    reference_answer=("截至 as_of 可见 FY2025 全年与各单季收入、FY2025Q1 同期基数；"
                      "给出点预测、有限区间、方法与不确定性。不要求命中某一具体数值。"),
    sources=[src(2025, 4, 17, "收入（全年欄）", period="FY2025 全年（as_of 可见）"),
             src(2025, 1, IS(2025, 1), "收入", period="FY2025Q1（as_of 可见）")],
    scoring_points=[
        sp("p1", "明确只使用 as_of 及之前可见信息（FY2026Q1 尚未披露）", ev=["可见性"],
           contra=["引用 FY2026Q1 实际值"]),
        sp("p2", "给出点预测或中枢（量级合理）", ev=["点预测"]),
        sp("p3", "给出有限区间预测", ev=["区间"]),
        sp("p4", "说明方法与不确定性", ev=["方法"]),
    ],
    numeric_assertions=[na("revenue", "FY2025Q1", g("revenue", 2025, 1), "usd_mn",
                           direction="up", cat="supporting")],
    forbidden_claims=["把 FY2026Q1 真实实绩当作预测复述"],
    reasoning_requirements=["历史时点可见性", "区间预测"],
    question_type="forecast", origin="new", split_group="FY2026",
    event_family="LV-FY2026Q1-forecast-revenue",
    forecast_truth={"metric": "revenue", "period": "FY2026Q1", "value": g("revenue", 2026, 1),
                    "unit": "usd_mn", "source": "FY26Q1_140820250742.pdf p13", "page": 13,
                    "note": "仅评测侧用于事后误差统计"},
    data_visibility={"basis": "as_of", "as_of": "2025-05-22"},
)

# ---- 写盘 ----
if len(CASES) != 30:
    print(f"警告：期望 30 题，实际 {len(CASES)} 题", file=sys.stderr)
save_dataset(CASES, OUT)
write_sources_index(CASES, "eval/datasets/financial_real_v1/sources.json", manifest=MAN)
n_blocked = sum(1 for c in CASES if c.blocked)
print(f"已写入 {OUT}：{len(CASES)} 题（blocked={n_blocked}）")
from collections import Counter  # noqa: E402
print("题型分布:", dict(Counter(c.question_type for c in CASES)))
print("origin:", dict(Counter(c.origin for c in CASES)))
_con.close()

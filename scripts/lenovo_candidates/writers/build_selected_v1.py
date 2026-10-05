# -*- coding: utf-8 -*-
"""Build selected_v1/cases.jsonl: 10 reworked attribution cases per docs/lenovo_selected_cases_rework.md.

Rules honored:
- Original candidates.jsonl is never modified; evaluator-side content (original_query,
  management_reference, sources, event_family_id) is copied from it verbatim.
- S01-S07 numeric inputs are read live from DuckDB (company_id='Lenovo', version='original');
  calculations are computed in this script from those values (no hand transcription).
- R01-R03 keep status needs_numeric_data; PDF-sourced literals were verified against
  .cache/lenovo_pdf_text page anchors on 2026-10-05 (see revision_notes.md).
"""
import json
import duckdb

DB = "data/finsights.duckdb"
SRC = "data/eval/lenovo_attribution_candidates/candidates.jsonl"
OUT = "data/eval/lenovo_attribution_candidates/selected_v1/cases.jsonl"
PE = {1: lambda fy: f"{fy-1}-06-30", 2: lambda fy: f"{fy-1}-09-30",
      3: lambda fy: f"{fy-1}-12-31", 4: lambda fy: f"{fy}-03-31"}

con = duckdb.connect(DB, read_only=True)

def fact(acct, fy, q, version="original"):
    r = con.execute("SELECT value FROM facts WHERE company_id='Lenovo' AND account_code=? "
                    "AND fiscal_year=? AND fiscal_quarter=? AND version=?",
                    [acct, fy, q, version]).fetchall()
    assert len(r) == 1, (acct, fy, q, r)
    return float(r[0][0])

def fy_sum(acct, fy, qs=(1, 2, 3, 4)):
    return sum(fact(acct, fy, q) for q in qs)

DBQ = ("SELECT value FROM facts WHERE company_id='Lenovo' AND account_code='{}' "
       "AND fiscal_year={} AND fiscal_quarter={} AND version='original'")

def db_in(iid, metric, fy, q, period_type, value, pdf=None, page=None, note=None):
    d = {"input_id": iid, "metric": metric, "entity": "Lenovo 联想",
         "period_type": period_type, "fiscal_year": fy, "fiscal_quarter": q,
         "period_end": PE[q](fy) if q else None, "value": round(value, 3), "unit": "usd_mn",
         "version": "original",
         "source": {"type": "db", "query": DBQ.format(metric, fy, q)}}
    if pdf:
        d["source"]["pdf_crossref"] = {"pdf": pdf, "pdf_page": page}
    if note:
        d["source"]["note"] = note
    return d

def pdf_in(iid, metric, fy, q, period_type, value_mn, raw_kusd, pdf, page, note=None, db_match=None):
    d = {"input_id": iid, "metric": metric, "entity": "Lenovo 联想",
         "period_type": period_type, "fiscal_year": fy, "fiscal_quarter": q,
         "period_end": PE[q](fy), "value": round(value_mn, 3), "unit": "usd_mn",
         "version": "original",
         "source": {"type": "pdf", "pdf": pdf, "pdf_page": page, "raw_value": raw_kusd}}
    if db_match is not None:
        d["source"]["db_match"] = {"value": round(db_match, 3), "query": DBQ.format(metric, fy, q)}
    if note:
        d["source"]["note"] = note
    return d

def calc(cid, purpose, inputs, formula, result, unit, tol, reason):
    return {"calculation_id": cid, "purpose": purpose, "inputs": inputs,
            "formula": formula, "result": round(result, 4 if unit in ("days", "percent", "percentage_points") else 3),
            "unit": unit, "tolerance_abs": tol, "tolerance_reason": reason}

orig = {}
for line in open(SRC, encoding="utf-8"):
    r = json.loads(line)
    orig[r["case_id"]] = r

records = []

def make_case(sel, case_id, query, run_defs, split_group, window, overlap, calcs_note,
              gold, scoring, hyp, db_support, changes, removed, status="reworked_pending_review",
              inputs=None, diag=None):
    o = orig[case_id]
    rec = {
        "case_id": case_id, "case_revision": 1, "selected_id": sel,
        "status": status, "review_state": "reworked_pending_review_not_business_pass",
        "company": o.get("company"), "event_family_id": o.get("event_family_id"),
        "fiscal_year": o.get("fiscal_year"), "fiscal_quarter": o.get("fiscal_quarter"),
        "period_end": o.get("period_end"), "publication_date": o.get("publication_date"),
        "period_vs_availability": ("当前数据回顾分析（DB original 值 + 所示公告页码），非严格时点隔离评测；"
                                   "比较栏取同一公告的上期同口径列，经核对未涉重述。" if status == "reworked_pending_review"
                                   else "当前数据回顾分析；未限定历史可得性，不称严格时点评测。"),
        "original_query": o["query"],
        "query": query,
        "runner_payload": ["query", "run_side_visible"],
        "run_side_visible": {"scope": "联想集团合并口径公开财务数据（单位：百万美元）",
                             "metric_definitions": run_defs},
        "split_group": split_group, "data_window": window, "overlap_note": overlap,
        "diagnostic_query": diag,
        "diagnostic_note": ("诊断版单独运行、单独计分，不与业务题混算得分或样本数。" if diag else None),
        "required_inputs": inputs or [],
        "calculations": calcs_note,
        "gold_evaluator_side": gold,
        "scoring": scoring,
        "hypothesis_checks": hyp,
        "management_reference_evaluator_side": o.get("management_reference"),
        "db_support": db_support,
        "revision": {"changes": changes, "removed_overreach": removed,
                     "rework_spec": "docs/lenovo_selected_cases_rework.md",
                     "verification": "live DuckDB recompute 2026-10-05 via scripts/lenovo_candidates/verify_selected_v1.py"},
        "sources_evaluator_side": o.get("sources"),
    }
    rec = {k: v for k, v in rec.items() if v is not None}
    records.append(rec)
    return rec

base_scoring = lambda numeric: {
    "mode": "atomic_three_layer", "verbatim_matching": False,
    "numeric_assertions": numeric,
    "interpretation_assertions": None, "boundary_assertions": None,
    "forbidden_claims": None, "alternate_correct_decompositions_accepted": False,
    "main_eval_uses": "query（业务题为主评测）"}

# ================= S01: FY2027Q1 库存增长与周转 =================
P27 = "FY27Q1_130820261201.pdf"
i25q4, i26q1, i26q4, i27q1 = (fact("inventory", 2025, 4), fact("inventory", 2026, 1),
                              fact("inventory", 2026, 4), fact("inventory", 2027, 1))
c26q1, c27q1 = fact("cogs", 2026, 1), fact("cogs", 2027, 1)
avg0, avg1 = (i25q4 + i26q1) / 2, (i26q4 + i27q1) / 2
dio0, dio1 = avg0 / c26q1 * 91, avg1 / c27q1 * 91
s01_inputs = [
    db_in("inventory_FY2025Q4_pt", "inventory", 2025, 4, "time_point", i25q4, P27, 14),
    db_in("inventory_FY2026Q1_pt", "inventory", 2026, 1, "time_point", i26q1, P27, 14),
    db_in("inventory_FY2026Q4_pt", "inventory", 2026, 4, "time_point", i26q4, P27, 14),
    db_in("inventory_FY2027Q1_pt", "inventory", 2027, 1, "time_point", i27q1, P27, 14),
    db_in("cogs_FY2026Q1_sq", "cogs", 2026, 1, "single_quarter", c26q1, P27, 5),
    db_in("cogs_FY2027Q1_sq", "cogs", 2027, 1, "single_quarter", c27q1, P27, 5),
]
s01_calcs = [
    calc("avg_inv_prior", "上年同期平均存货", ["inventory_FY2025Q4_pt", "inventory_FY2026Q1_pt"],
         f"({i25q4} + {i26q1}) / 2", avg0, "usd_mn", 0.001, "输入三位小数，两值相加除2舍入误差<0.001"),
    calc("avg_inv_curr", "本期平均存货", ["inventory_FY2026Q4_pt", "inventory_FY2027Q1_pt"],
         f"({i26q4} + {i27q1}) / 2", avg1, "usd_mn", 0.001, "同上"),
    calc("dio_prior", "上年同期 DIO", ["avg_inv_prior", "cogs_FY2026Q1_sq"],
         f"{round(avg0,4)} / {c26q1} * 91", dio0, "days", 0.001, "统一口径（平均余额、实际91天）下按4位小数判定"),
    calc("dio_curr", "本期 DIO", ["avg_inv_curr", "cogs_FY2027Q1_sq"],
         f"{round(avg1,4)} / {c27q1} * 91", dio1, "days", 0.001, "同上"),
    calc("dio_delta", "DIO 同比变化", ["dio_prior", "dio_curr"], "55.5587 - 47.2317", dio1 - dio0, "days", 0.001, "同上"),
    calc("closing_inv_growth", "期末存货同比增速", ["inventory_FY2026Q1_pt", "inventory_FY2027Q1_pt"],
         f"({i27q1} / {i26q1} - 1) * 100", (i27q1 / i26q1 - 1) * 100, "percent", 0.005, "4位百分比展示"),
    calc("avg_inv_growth", "平均存货同比增速", ["avg_inv_prior", "avg_inv_curr"],
         f"({round(avg1,4)} / {round(avg0,4)} - 1) * 100", (avg1 / avg0 - 1) * 100, "percent", 0.005, "同上"),
    calc("cogs_growth", "单季营业成本同比增速", ["cogs_FY2026Q1_sq", "cogs_FY2027Q1_sq"],
         f"({c27q1} / {c26q1} - 1) * 100", (c27q1 / c26q1 - 1) * 100, "percent", 0.005, "同上"),
]
make_case("S01", "LV-FY2027Q1-INV-DIO-001",
    "联想 FY2027Q1 的库存增长是否反映周转恶化？请比较上年同期，量化变化，并区分现有数据能够支持与不能支持的解释。",
    ["DIO = 平均存货 ÷ 单季营业成本 × 当季实际天数；平均存货 = (季初余额 + 季末余额) / 2；"
     "本期与上年同期均为 Apr–Jun，实际天数 91（题目内统一评测定义）",
     "存货与成本均为集团合并口径；单位百万美元；财季截止日按 FY 3-31 制"],
    "FY2027Q1", "FY2025Q4–FY2027Q1 时点 + FY2026Q1/FY2027Q1 单季流量",
    "与 S06（WC-CCC）共用 FY2027Q1 营运资金事件与相同存货/COGS 行；开发/留出划分时不得跨集。",
    s01_calcs,
    {"layer1_facts": [
        f"期末存货同比 +{(i27q1/i26q1-1)*100:.2f}%，但平均存货同比仅 +{(avg1/avg0-1)*100:.2f}%，单季营业成本同比 +{(c27q1/c26q1-1)*100:.2f}%。",
        f"DIO {dio0:.4f} → {dio1:.4f} 天（+{dio1-dio0:.4f} 天）。平均存货增速高于成本增速，故周转天数拉长；期末余额增速约 80% 不等于平均余额或周转同幅恶化。",
        "公式口径：DIO = 平均存货（分子）÷ 单季营业成本（分母）× 91 天。原候选 scorable_answer 曾把平均存货称作'分母'（错置所在为结论文本行，原计算串方向本身正确），该表述已删除；数值以 live DB 直算为准，与返工目标 47.2317→55.5587 一致。"],
     "layer2_assumptions_boundaries": [
        "备货前置、零部件成本上行传导、需求走弱积压等解释与现有数字均相容；仅凭集团存货与 COGS 无法区分。",
        "不能据本期数据断言'主动备货'或'滞销积压'，也不能断言周转恶化幅度等于期末增幅。"],
     "layer3_followups": [
        "存货构成（原材料/在制/成品）占比、库龄分布、存货减值准备、分部收入与订单、采购成本明细、可比同业周转。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s01_calcs]),
     "interpretation_assertions": [
        "由平均存货增速与营业成本增速的相对关系解释 DIO 变化的方向与幅度",
        "指出期末余额增速与平均余额增速口径不同、不可互换"],
     "boundary_assertions": [
        "明确表示单一余额不能确定经营原因（备货/积压/成本传导等尚不可区分）",
        "列出可验证假设所需的额外数据"],
     "forbidden_claims": [
        "把平均存货说成 DIO 的分母（或成本说成分子）",
        "把期末存货增幅约 80% 直接当作周转恶化幅度",
        "强制命中'零部件供需失衡'等管理层原因",
        "以现有数据断言备货或积压为确定原因"]},
    [{"hypothesis": "主动备货（如 AI 服务器订单前置）", "if_true_expect": "原材料/在制占比上升、采购与预付款上升，且后续季度收入/成本同步放大"},
     {"hypothesis": "被动积压", "if_true_expect": "成品占比上升、库龄拉长、减值计提增加"},
     {"hypothesis": "成本上行传导", "if_true_expect": "单位成本上升而数量口径未增，毛利率同向承压"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": [],
     "notes": "全部输入 DB original 可查，与 FY27Q1 公告 p14（资产负债表）/p5（利润表）勾稽一致；当季天数 91 为题目内统一定义。"},
    ["口径写明并复核：DIO=平均存货（分子）÷单季营业成本（分母）×91 天；原候选的分母错置位于 scorable_answer 结论文本行（计算串本身方向正确），已删除并以 live DB 直算复核（见 revision_notes §1）",
     "四余额单值字符串拆为逐期独立输入行",
     "容差改为统一口径下按显示精度判定（删除'覆盖±1天'式表述）",
     "查询改为开放业务问题，不再题面内给公式与结论暗示",
     "期末/平均/成本三个增速全部列为可评分计算"],
    ["删除：'分母（平均存货基数变大）解释…'错误表述",
     "删除：以固定小容差同时接受多种天数口径",
     "删除：强制命中管理层'零部件供需失衡'措辞"],
    inputs=s01_inputs)

# ================= S02: FY2026 全年经营利润桥 =================
rev25, rev26 = fy_sum("revenue", 2025), fy_sum("revenue", 2026)
gp25, gp26 = fy_sum("gross_profit", 2025), fy_sum("gross_profit", 2026)
oi25, oi26 = fy_sum("operating_income", 2025), fy_sum("operating_income", 2026)
ne25, ne26 = gp25 - oi25, gp26 - oi26
dGP, dNE, dOI = gp26 - gp25, ne26 - ne25, oi26 - oi25
P26Q4 = "FY26Q4_220520260732.pdf"
def fy_in(iid, metric, fy, value, page, note):
    d = db_in(iid, metric, fy, None, "fiscal_year_sum", value, P26Q4, page, note)
    d["source"]["query"] = (f"SELECT SUM(value) FROM facts WHERE company_id='Lenovo' AND account_code='{metric}' "
                            f"AND fiscal_year={fy} AND fiscal_quarter BETWEEN 1 AND 4 AND version='original'")
    d["period_end"] = f"{fy}-03-31"
    return d
s02_inputs = [
    fy_in("revenue_FY2025_fy", "revenue", 2025, rev25, 6, "全年=四个单季 DB 值求和；与公告取整一致"),
    fy_in("revenue_FY2026_fy", "revenue", 2026, rev26, 6, "同上"),
    fy_in("gross_profit_FY2025_fy", "gross_profit", 2025, gp25, 6, "同上"),
    fy_in("gross_profit_FY2026_fy", "gross_profit", 2026, gp26, 6, "同上"),
    fy_in("operating_income_FY2025_fy", "operating_income", 2025, oi25, 13, "公告 p13 千美元值 2,164,153"),
    fy_in("operating_income_FY2026_fy", "operating_income", 2026, oi26, 13, "公告 p13 千美元值 3,261,743；FY26Q4 行为 q4_backout 派生原值"),
]
s02_calcs = [
    calc("d_revenue", "收入同比变化", ["revenue_FY2025_fy", "revenue_FY2026_fy"], f"{rev26} - {rev25}", rev26 - rev25, "usd_mn", 0.001, "三位小数直减"),
    calc("d_gross_profit", "毛利同比变化", ["gross_profit_FY2025_fy", "gross_profit_FY2026_fy"], f"{gp26} - {gp25}", dGP, "usd_mn", 0.001, "同上"),
    calc("net_opex_FY2025", "上年派生净经营费用", ["gross_profit_FY2025_fy", "operating_income_FY2025_fy"], f"{gp25} - {oi25}", ne25, "usd_mn", 0.001, "派生口径：含其他经营收支等，非纯经营费用"),
    calc("net_opex_FY2026", "本期派生净经营费用", ["gross_profit_FY2026_fy", "operating_income_FY2026_fy"], f"{gp26} - {oi26}", ne26, "usd_mn", 0.001, "同上"),
    calc("d_net_opex", "派生净费用同比变化", ["net_opex_FY2025", "net_opex_FY2026"], f"{round(ne26,3)} - {round(ne25,3)}", dNE, "usd_mn", 0.001, "同上"),
    calc("d_operating_income", "经营利润同比变化", ["operating_income_FY2025_fy", "operating_income_FY2026_fy"], f"{oi26} - {oi25}", dOI, "usd_mn", 0.001, "同上"),
    calc("bridge_tie", "利润桥勾稽", ["d_gross_profit", "d_net_opex", "d_operating_income"], f"{round(dGP,3)} - {round(dNE,3)} - {round(dOI,3)}", dGP - dNE - dOI, "usd_mn", 0.002, "勾稽残差应为0"),
    calc("gm_FY2025", "上年毛利率", ["gross_profit_FY2025_fy", "revenue_FY2025_fy"], f"{gp25} / {rev25} * 100", gp25 / rev25 * 100, "percent", 0.005, "4位展示"),
    calc("gm_FY2026", "本期毛利率", ["gross_profit_FY2026_fy", "revenue_FY2026_fy"], f"{gp26} / {rev26} * 100", gp26 / rev26 * 100, "percent", 0.005, "同上"),
    calc("gm_delta_pp", "毛利率同比变化", ["gm_FY2025", "gm_FY2026"], "15.4192 - 16.0656", (gp26 / rev26 - gp25 / rev25) * 100, "percentage_points", 0.005, "同上"),
    calc("oi_margin_FY2025", "上年经营利润率", ["operating_income_FY2025_fy", "revenue_FY2025_fy"], f"{oi25} / {rev25} * 100", oi25 / rev25 * 100, "percent", 0.005, "同上"),
    calc("oi_margin_FY2026", "本期经营利润率", ["operating_income_FY2026_fy", "revenue_FY2026_fy"], f"{oi26} / {rev26} * 100", oi26 / rev26 * 100, "percent", 0.005, "同上"),
    calc("oi_growth_pct", "经营利润同比增速", ["d_operating_income", "operating_income_FY2025_fy"], f"{round(dOI,3)} / {oi25} * 100", dOI / oi25 * 100, "percent", 0.005, "同上"),
    calc("revenue_growth_pct", "收入同比增速", ["d_revenue", "revenue_FY2025_fy"], f"{round(rev26-rev25,3)} / {rev25} * 100", (rev26 / rev25 - 1) * 100, "percent", 0.005, "同上"),
]
make_case("S02", "LV-FY2026Q4-GM-OPEX-001",
    "联想 FY2026 全年的经营利润增长由哪些可量化变化支撑？请比较上年，解释毛利率与经营利润不同向的原因，并说明能否判断改善具有持续性。",
    ["财年 FY = 四个财季（DB 单季值）之和，FY2026 截止 2026-03-31",
     "派生净经营费用 = 毛利 − 经营利润（含其他经营收支等净额，非公告费用科目明细；统一评测口径）",
     "经营利润桥：Δ经营利润 = Δ毛利 − Δ派生净费用；单位百万美元"],
    "FY2026", "FY2025 与 FY2026 全年（各四季求和）",
    "与 S03（H1/Q2）、S05（9M）、S07（Q1）期间嵌套：全年行覆盖其全部季度；划分时同簇处理。",
    s02_calcs,
    {"layer1_facts": [
        f"收入 +{rev26-rev25:.3f}（+{(rev26/rev25-1)*100:.2f}%）、毛利 +{dGP:.3f}、派生净费用 +{dNE:.3f}（+{dNE/ne25*100:.2f}%）、经营利润 +{dOI:.3f}（+{dOI/oi25*100:.2f}%）；桥勾稽 {dGP:.3f} − {dNE:.3f} = {dOI:.3f}。",
        f"毛利率 {gp25/rev25*100:.4f}% → {gp26/rev26*100:.4f}%（{(gp26/rev26-gp25/rev25)*100:.4f}pp），经营利润率 {oi25/rev25*100:.4f}% → {oi26/rev26*100:.4f}%。两者不同向：金额层毛利仍增、净费用增速低于毛利增速。"],
     "layer2_assumptions_boundaries": [
        "经营利润增长由毛利金额增长与费用增速相对更低共同支撑；这是比率/金额层观察（常被称'经营杠杆'），不能据此断定改善可持续或全部来自效率提升。",
        "集团数字不能证实产品组合或 ISG 低利润率等披露层解释（evaluator 背景，见 management_reference）。"],
     "layer3_followups": [
        "分部收入与利润表、费用功能明细、非 HKFRS 调节表、经营活动现金流与净利润的匹配。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s02_calcs]),
     "interpretation_assertions": [
        "用毛利金额增量与净费用增量的相对大小解释毛利率降而经营利润升",
        "利润桥三项勾稽成立"],
     "boundary_assertions": [
        "派生净费用须标为毛利减经营利润的净额口径，不得等同纯经营费用",
        "对持续性只给出可检验的补查项，不作确定判断"],
     "forbidden_claims": [
        "要求与被测 Agent 不可见的公告核对（PDF 核对属 evaluator 侧）",
        "把全年与 Q4 混为同一评分项",
        "断言组合改善由产品组合/ISG 等原因造成",
        "据单年数据断言改善可持续"]},
    [{"hypothesis": "规模摊薄（净费用增速低于收入）", "if_true_expect": "费用各明细项增速均低于收入增速"},
     {"hypothesis": "结构性降本", "if_true_expect": "费用绝对额下降或特定科目显著收缩"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": ["费用功能明细（题面不依赖）"],
     "notes": "四季收入/毛利/经营溢利 DB 精确值与公告勾稽一致；FY26Q4 利润表行为 q4_backout 派生原值。"},
    ["删除'与公告核对'的被测要求（核对移入 evaluator 侧）",
     "全年定义明确为四季 DB 求和，与 Q4 单季分离",
     "净经营费用标注为派生口径",
     "补充经营利润率与增速的可评分计算"],
    ["删除：把派生净费用直接当作纯经营费用的表述",
     "删除：'与公告核对'强制评分项"],
    inputs=s02_inputs)

# ================= S03: FY2026 H1 增长 vs Q2 =================
lev = {(m, fy, q): fact(m, fy, q) for m in ("revenue", "gross_profit", "operating_income", "net_income")
       for fy in (2025, 2026) for q in (1, 2)}
P26Q2 = "FY26Q2_201120250729.pdf"
s03_inputs = [db_in(f"{m}_FY{fy}Q{q}_sq", m, fy, q, "single_quarter", lev[(m, fy, q)],
                    P26Q2, 8, "公告 p8 千美元表舍入一致")
              for (fy, q) in ((2025, 1), (2026, 1), (2025, 2), (2026, 2))
              for m in ("revenue", "gross_profit", "operating_income", "net_income")]
q = lambda m, fy, qq: lev[(m, fy, qq)]
h1ni25, h1ni26 = q("net_income", 2025, 1) + q("net_income", 2025, 2), q("net_income", 2026, 1) + q("net_income", 2026, 2)
d_ni_q1 = q("net_income", 2026, 1) - q("net_income", 2025, 1)
d_ni_q2 = q("net_income", 2026, 2) - q("net_income", 2025, 2)
d_gp2 = q("gross_profit", 2026, 2) - q("gross_profit", 2025, 2)
d_ne2 = (q("gross_profit", 2026, 2) - q("operating_income", 2026, 2)) - (q("gross_profit", 2025, 2) - q("operating_income", 2025, 2))
d_oi2 = q("operating_income", 2026, 2) - q("operating_income", 2025, 2)
s03_calcs = [
    calc("h1_net_income_FY2025", "上年 H1 期内溢利（含 NCI）", ["net_income_FY2025Q1_sq", "net_income_FY2025Q2_sq"],
         f"{q('net_income',2025,1)} + {q('net_income',2025,2)}", h1ni25, "usd_mn", 0.001, "两季 DB 值求和"),
    calc("h1_net_income_FY2026", "本期 H1 期内溢利（含 NCI）", ["net_income_FY2026Q1_sq", "net_income_FY2026Q2_sq"],
         f"{q('net_income',2026,1)} + {q('net_income',2026,2)}", h1ni26, "usd_mn", 0.001, "同上"),
    calc("d_h1_net_income", "H1 期内溢利同比变化", ["h1_net_income_FY2025", "h1_net_income_FY2026"],
         f"{round(h1ni26,3)} - {round(h1ni25,3)}", h1ni26 - h1ni25, "usd_mn", 0.001, "同上"),
    calc("h1_net_income_growth", "H1 期内溢利同比增速", ["d_h1_net_income", "h1_net_income_FY2025"],
         f"{round(h1ni26-h1ni25,3)} / {round(h1ni25,3)} * 100", (h1ni26 / h1ni25 - 1) * 100, "percent", 0.005, "4位展示"),
    calc("d_q1_net_income", "Q1 期内溢利同比变化", ["net_income_FY2025Q1_sq", "net_income_FY2026Q1_sq"],
         f"{q('net_income',2026,1)} - {q('net_income',2025,1)}", d_ni_q1, "usd_mn", 0.001, "同上"),
    calc("d_q2_net_income", "Q2 期内溢利同比变化", ["net_income_FY2025Q2_sq", "net_income_FY2026Q2_sq"],
         f"{q('net_income',2026,2)} - {q('net_income',2025,2)}", d_ni_q2, "usd_mn", 0.001, "同上"),
    calc("ni_decomp_tie", "H1 增量按季度勾稽", ["d_q1_net_income", "d_q2_net_income", "d_h1_net_income"],
         f"{round(d_ni_q1,3)} + {round(d_ni_q2,3)} - {round(h1ni26-h1ni25,3)}", d_ni_q1 + d_ni_q2 - (h1ni26 - h1ni25), "usd_mn", 0.002, "勾稽残差应为0"),
    calc("q2_revenue_growth", "Q2 收入同比增速", ["revenue_FY2025Q2_sq", "revenue_FY2026Q2_sq"],
         f"({q('revenue',2026,2)} / {q('revenue',2025,2)} - 1) * 100", (q("revenue",2026,2)/q("revenue",2025,2)-1)*100, "percent", 0.005, "同上"),
    calc("q2_d_gross_profit", "Q2 毛利同比变化", ["gross_profit_FY2025Q2_sq", "gross_profit_FY2026Q2_sq"],
         f"{q('gross_profit',2026,2)} - {q('gross_profit',2025,2)}", d_gp2, "usd_mn", 0.001, "同上"),
    calc("q2_d_net_opex", "Q2 派生净费用同比变化", ["q2_d_gross_profit", "q2_d_operating_income"],
         f"({round(q('gross_profit',2026,2),3)} - {round(q('operating_income',2026,2),3)}) - ({round(q('gross_profit',2025,2),3)} - {round(q('operating_income',2025,2),3)})", d_ne2, "usd_mn", 0.001, "派生口径同 S02"),
    calc("q2_d_operating_income", "Q2 经营利润同比变化", ["operating_income_FY2025Q2_sq", "operating_income_FY2026Q2_sq"],
         f"{q('operating_income',2026,2)} - {q('operating_income',2025,2)}", d_oi2, "usd_mn", 0.001, "同上"),
    calc("q2_bridge_tie", "Q2 利润桥勾稽", ["q2_d_gross_profit", "q2_d_net_opex", "q2_d_operating_income"],
         f"{round(d_gp2,3)} - {round(d_ne2,3)} - {round(d_oi2,3)}", d_gp2 - d_ne2 - d_oi2, "usd_mn", 0.002, "勾稽残差应为0"),
    calc("q2_net_income_yoy_pct", "Q2 期内溢利同比变化率", ["d_q2_net_income", "net_income_FY2025Q2_sq"],
         f"{round(d_ni_q2,3)} / {q('net_income',2025,2)} * 100", d_ni_q2 / q("net_income", 2025, 2) * 100, "percent", 0.005, "同上"),
]
make_case("S03", "LV-FY2026Q2-Q2VSH1-001",
    "联想 FY2026 上半年利润增长是否代表第二季度也在改善？请分开分析两个期间，并定位第二季度利润变化的主要环节。",
    ["利润口径 = DB 期内溢利（net_income，含非控股权益）与经营利润（operating_income）；应占利润（含 NCI 拆分）不在评测输入内",
     "H1 = Q1 + Q2（两季 DB 单季值求和）；派生净费用 = 毛利 − 经营利润",
     "单位百万美元；FY2026 H1 截止 2025-09-30"],
    "FY2026", "FY2025Q1–FY2026Q2 单季（H1 为其和）",
    "与 S07 共用 FY2026Q1/FY2025Q1 行，与 S02 期间嵌套（FY26 全年覆盖 H1），与 R03 同用 FY26Q2 公告（分部口径）；同簇划分。",
    s03_calcs,
    {"layer1_facts": [
        f"H1 期内溢利 {h1ni25:.3f} → {h1ni26:.3f}（+{h1ni26-h1ni25:.3f}，+{(h1ni26/h1ni25-1)*100:.2f}%）；增量分解：Q1 +{d_ni_q1:.3f}、Q2 {d_ni_q2:.3f}，两者之和恰等于 H1 增量（勾稽）。H1 增长完全由 Q1 贡献，Q2 为负贡献。",
        f"Q2 收入 +{(q('revenue',2026,2)/q('revenue',2025,2)-1)*100:.2f}%，毛利 +{d_gp2:.3f}，但派生净费用 +{d_ne2:.3f}，经营利润 {d_oi2:.3f}、期内溢利 {d_ni_q2:.3f}；Q2 变化主要发生在费用层（费用增量超过毛利增量）。"],
     "layer2_assumptions_boundaries": [
        "H1 高增长与 Q2 利润微降不矛盾：累计口径掩盖单季方向。",
        "Q2 费用增量集中于哪些科目（僱員福利、认股权证公允值亏损 1.48 亿美元等公告说法）现有 DB 无法验证，只能列为补查项；不得当作已证原因。",
        "公告应占利润 Q2 340 对 359 属披露口径背景（evaluator 侧），不作为强制评分。"],
     "layer3_followups": [
        "费用功能分类明细、其他收支与税项、NCI 结构、单季与半年独立 period_type 的真实记录。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s03_calcs]),
     "interpretation_assertions": [
        "用两季净利润增量勾稽证明 H1 增长集中在 Q1（而非用 Q1 经营利润增速替代）",
        "Q2 利润变化定位到费用层：净费用增量 > 毛利增量"],
     "boundary_assertions": [
        "区分累计口径与单季口径，说明两者可同真",
        "费用明细缺失时给出必要补查项而非指定根因"],
     "forbidden_claims": [
        "以应占利润默认为期内溢利（或反之）",
        "强制推断利率、季节性或某项具体费用为根因",
        "强制命中公告认股权证 1.48 亿美元等 DB 不可见明细"]},
    [{"hypothesis": "Q2 费用增长含一次性项目（如公允值亏损）", "if_true_expect": "费用明细中非经常项显著，剔除后 Q2 经营利润增速回正"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": ["operating_expense（派生 GP−OI）"],
     "notes": "四季利润层 DB 数据完整；FY26Q1/FY27Q1 的 net_income 符号缺陷与本案例无关（本案例期间均为正值，复核于 2026-10-05）。"},
    ["统一为 DB 期内溢利（含 NCI）+ 经营利润口径，应占利润移出强制评分",
     "新增 H1 增量按季度净利润勾稽（原题用 Q1 经营利润增速替代）",
     "16 行独立输入替换原 5 行合并输入"],
    ["删除：未提供应占数字的强制评分",
     "删除：以 Q1 经营利润增速代替 Q1 净利润贡献的说法"],
    inputs=s03_inputs)

# ================= S04: FY2025Q3 费用金额 vs 强度 =================
r24q3, r25q3 = fact("revenue", 2024, 3), fact("revenue", 2025, 3)
g24q3, g25q3 = fact("gross_profit", 2024, 3), fact("gross_profit", 2025, 3)
o24q3, o25q3 = fact("operating_income", 2024, 3), fact("operating_income", 2025, 3)
ne24, ne25_ = g24q3 - o24q3, g25q3 - o25q3
P25Q3 = "FY25Q3_200220251201.pdf"
rev9m24, rev9m25 = fy_sum("revenue", 2024, (1, 2, 3)), fy_sum("revenue", 2025, (1, 2, 3))
ne9m24 = sum(fact("gross_profit", 2024, k) - fact("operating_income", 2024, k) for k in (1, 2, 3))
ne9m25 = sum(fact("gross_profit", 2025, k) - fact("operating_income", 2025, k) for k in (1, 2, 3))
s04_inputs = [
    db_in("revenue_FY2024Q3_sq", "revenue", 2024, 3, "single_quarter", r24q3, P25Q3, 5),
    db_in("revenue_FY2025Q3_sq", "revenue", 2025, 3, "single_quarter", r25q3, P25Q3, 5),
    db_in("gross_profit_FY2024Q3_sq", "gross_profit", 2024, 3, "single_quarter", g24q3, P25Q3, 5),
    db_in("gross_profit_FY2025Q3_sq", "gross_profit", 2025, 3, "single_quarter", g25q3, P25Q3, 5),
    db_in("operating_income_FY2024Q3_sq", "operating_income", 2024, 3, "single_quarter", o24q3, P25Q3, 5),
    db_in("operating_income_FY2025Q3_sq", "operating_income", 2025, 3, "single_quarter", o25q3, P25Q3, 5),
    db_in("revenue_FY2024Q1_sq", "revenue", 2024, 1, "single_quarter", fact("revenue", 2024, 1), P25Q3, 5, "9M 上下文用"),
    db_in("revenue_FY2024Q2_sq", "revenue", 2024, 2, "single_quarter", fact("revenue", 2024, 2), P25Q3, 5, "9M 上下文用"),
    db_in("gross_profit_FY2024Q1_sq", "gross_profit", 2024, 1, "single_quarter", fact("gross_profit", 2024, 1), P25Q3, 5, "9M 上下文用"),
    db_in("gross_profit_FY2024Q2_sq", "gross_profit", 2024, 2, "single_quarter", fact("gross_profit", 2024, 2), P25Q3, 5, "9M 上下文用"),
    db_in("operating_income_FY2024Q1_sq", "operating_income", 2024, 1, "single_quarter", fact("operating_income", 2024, 1), P25Q3, 5, "9M 上下文用"),
    db_in("operating_income_FY2024Q2_sq", "operating_income", 2024, 2, "single_quarter", fact("operating_income", 2024, 2), P25Q3, 5, "9M 上下文用"),
    db_in("revenue_FY2025Q1_sq", "revenue", 2025, 1, "single_quarter", fact("revenue", 2025, 1), P25Q3, 5, "9M 上下文用"),
    db_in("revenue_FY2025Q2_sq", "revenue", 2025, 2, "single_quarter", fact("revenue", 2025, 2), P25Q3, 5, "9M 上下文用"),
    db_in("gross_profit_FY2025Q1_sq", "gross_profit", 2025, 1, "single_quarter", fact("gross_profit", 2025, 1), P25Q3, 5, "9M 上下文用"),
    db_in("gross_profit_FY2025Q2_sq", "gross_profit", 2025, 2, "single_quarter", fact("gross_profit", 2025, 2), P25Q3, 5, "9M 上下文用"),
    db_in("operating_income_FY2025Q1_sq", "operating_income", 2025, 1, "single_quarter", fact("operating_income", 2025, 1), P25Q3, 5, "9M 上下文用"),
    db_in("operating_income_FY2025Q2_sq", "operating_income", 2025, 2, "single_quarter", fact("operating_income", 2025, 2), P25Q3, 5, "9M 上下文用"),
]
s04_calcs = [
    calc("net_opex_FY2024Q3", "上年同期派生净费用", ["gross_profit_FY2024Q3_sq", "operating_income_FY2024Q3_sq"],
         f"{g24q3} - {o24q3}", ne24, "usd_mn", 0.001, "派生口径；与公告 p8 (1,988,454) 勾稽"),
    calc("net_opex_FY2025Q3", "本期派生净费用", ["gross_profit_FY2025Q3_sq", "operating_income_FY2025Q3_sq"],
         f"{g25q3} - {o25q3}", ne25_, "usd_mn", 0.001, "与公告 p8 (2,271,687) 勾稽"),
    calc("opex_growth", "费用金额同比增速", ["net_opex_FY2024Q3", "net_opex_FY2025Q3"],
         f"({round(ne25_,3)} / {round(ne24,3)} - 1) * 100", (ne25_ / ne24 - 1) * 100, "percent", 0.005, "4位展示"),
    calc("revenue_growth", "收入同比增速", ["revenue_FY2024Q3_sq", "revenue_FY2025Q3_sq"],
         f"({r25q3} / {r24q3} - 1) * 100", (r25q3 / r24q3 - 1) * 100, "percent", 0.005, "同上"),
    calc("opex_ratio_FY2024Q3", "上年费用强度", ["net_opex_FY2024Q3", "revenue_FY2024Q3_sq"],
         f"{round(ne24,3)} / {r24q3} * 100", ne24 / r24q3 * 100, "percent", 0.005, "同上"),
    calc("opex_ratio_FY2025Q3", "本期费用强度", ["net_opex_FY2025Q3", "revenue_FY2025Q3_sq"],
         f"{round(ne25_,3)} / {r25q3} * 100", ne25_ / r25q3 * 100, "percent", 0.005, "同上"),
    calc("opex_ratio_delta_bps", "费用强度同比变化（基点）", ["opex_ratio_FY2024Q3", "opex_ratio_FY2025Q3"],
         f"({round(ne25_/r25q3,6)} - {round(ne24/r24q3,6)}) * 10000", (ne25_ / r25q3 - ne24 / r24q3) * 10000, "percent", 0.05, "以基点展示（负值=强度下降）"),
    calc("context_9m_opex_ratio_delta_bps", "上下文：9M 费用强度同比变化（可选，不强制）",
         sum([[f"{m}_FY{fy}Q{qq}_sq" for qq in (1, 2, 3)]
              for fy in (2024, 2025) for m in ("revenue", "gross_profit", "operating_income")], []),
         f"({round(ne9m25,3)}/{round(rev9m25,3)} - {round(ne9m24,3)}/{round(rev9m24,3)}) * 10000",
         (ne9m25 / rev9m25 - ne9m24 / rev9m24) * 10000, "percent", 0.5,
         "9M=三季派生费用/收入求和；公告 p5 (5,858,044)/(6,482,218) 勾稽；仅作上下文"),
]
make_case("S04", "LV-FY2025Q3-OPEXRATIO-56BPS-001",
    "联想 FY2025Q3 的费用控制表现如何？请比较上年同期的费用金额与费用强度，说明现有数据能否证明成本削减或效率改善。",
    ["费用金额口径 = 派生净经营费用（毛利 − 经营利润）；费用强度 = 派生净费用 ÷ 收入",
     "9M 为可选上下文口径（三季求和），业务题不强制完成",
     "单位百万美元"],
    "FY2025Q3", "FY2024Q1–Q3 与 FY2025Q1–Q3 单季",
    "FY2025Q3 行与 S05 的 FY2025 比较期共用；S05 划入开发集时本题宜同集或另选留出。",
    s04_calcs,
    {"layer1_facts": [
        f"费用金额 {ne24:.3f} → {ne25_:.3f}（+{(ne25_/ne24-1)*100:.2f}%）并未减少；收入 +{(r25q3/r24q3-1)*100:.2f}% 增长更快，强度 {ne24/r24q3*100:.4f}% → {ne25_/r25q3*100:.4f}%（约 {(ne25_/r25q3-ne24/r24q3)*10000:.1f} bps）。"],
     "layer2_assumptions_boundaries": [
        "核心区分：费用增长慢于收入（强度下降）≠ 费用收缩；不能证明物理生产效率、人均效率或具体降本行动。",
        "管理层'同比下降56个基点至12.1%'在 Q3 单季口径下与复算一致（9M 口径降幅更大，约 -117bps）——此为 evaluator 参考，业务题不要求核对公告。",
        "功能费用明细缺失，无法判断哪些科目贡献强度下降。"],
     "layer3_followups": ["研发/SGA 等功能分类明细、员工数量与人均指标、毛利率变化的影响。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s04_calcs if c["calculation_id"] != "context_9m_opex_ratio_delta_bps"]),
     "interpretation_assertions": [
        "同时给出费用金额增速与强度变化，并指出两者方向含义不同",
        "强度下降归因于收入增长更快（规模摊薄）而非费用绝对减少"],
     "boundary_assertions": [
        "明确表示现有数据不能证明成本削减或效率改善",
        "提出可区分'摊薄 vs 降本'的补查数据"],
     "forbidden_claims": [
        "把强度下降表述为费用收缩",
        "断言具体降本行动或效率提升已发生"],
     "optional_assertions": ["context_9m_opex_ratio_delta_bps（9M 上下文，答对加分不强制）"]},
    [{"hypothesis": "真实降本", "if_true_expect": "至少一类功能费用绝对额同比下降"},
     {"hypothesis": "规模摊薄", "if_true_expect": "各功能费用增速均低于收入增速"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": ["费用功能明细（补查项）"],
     "notes": "Q3 与 9M 派生费用均与公告千美元表完全勾稽（p8/p5）。"},
    ["业务题删除题面中的'56 个基点'、公式与 9M 强制要求",
     "原题保留为 diagnostic_query（定向诊断版），单独运行单独计分",
     "所有输入拆为独立行（含 9M 上下文的 12 个季度行）",
     "精确重算金额/强度写入数值字段"],
    ["删除：业务题强制完成 9M 复算",
     "删除：题面预设'56 个基点'与解题路线"],
    inputs=s04_inputs,
    diag=orig["LV-FY2025Q3-OPEXRATIO-56BPS-001"]["query"])

# ================= S05: FY2026 9M 毛利率贡献分解 =================
R = {("FY2025", k): fact("revenue", 2025, k) for k in (1, 2, 3)}
R.update({("FY2026", k): fact("revenue", 2026, k) for k in (1, 2, 3)})
G = {("FY2025", k): fact("gross_profit", 2025, k) for k in (1, 2, 3)}
G.update({("FY2026", k): fact("gross_profit", 2026, k) for k in (1, 2, 3)})
P26Q3 = "FY26Q3_120220261202.pdf"
s05_inputs = [db_in(f"{'revenue' if m=='r' else 'gross_profit'}_{yr}Q{k}_sq",
                    "revenue" if m == "r" else "gross_profit", int(yr[2:]), k, "single_quarter",
                    (R if m == "r" else G)[(yr, k)], P26Q3, 5)
               for yr in ("FY2025", "FY2026") for k in (1, 2, 3) for m in ("r", "g")]
R25, R26 = sum(R[("FY2025", k)] for k in (1, 2, 3)), sum(R[("FY2026", k)] for k in (1, 2, 3))
G25s, G26s = sum(G[("FY2025", k)] for k in (1, 2, 3)), sum(G[("FY2026", k)] for k in (1, 2, 3))
M25, M26 = G25s / R25 * 100, G26s / R26 * 100
w25 = [R[("FY2025", k)] / R25 for k in (1, 2, 3)]
w26 = [R[("FY2026", k)] / R26 for k in (1, 2, 3)]
m25 = [G[("FY2025", k)] / R[("FY2025", k)] * 100 for k in (1, 2, 3)]
m26 = [G[("FY2026", k)] / R[("FY2026", k)] * 100 for k in (1, 2, 3)]
rate_i = [w25[k] * (m26[k] - m25[k]) for k in range(3)]
mix_i = [(w26[k] - w25[k]) * m26[k] for k in range(3)]
s05_calcs = []
for k in range(3):
    s05_calcs.append(calc(f"gm_FY2025Q{k+1}", f"FY2025Q{k+1} 单季毛利率",
        [f"gross_profit_FY2025Q{k+1}_sq", f"revenue_FY2025Q{k+1}_sq"],
        f"{G[('FY2025',k+1)]} / {R[('FY2025',k+1)]} * 100", m25[k], "percent", 0.005, "4位展示"))
    s05_calcs.append(calc(f"gm_FY2026Q{k+1}", f"FY2026Q{k+1} 单季毛利率",
        [f"gross_profit_FY2026Q{k+1}_sq", f"revenue_FY2026Q{k+1}_sq"],
        f"{G[('FY2026',k+1)]} / {R[('FY2026',k+1)]} * 100", m26[k], "percent", 0.005, "同上"))
    s05_calcs.append(calc(f"rate_contrib_Q{k+1}", f"Q{k+1} 率变化贡献（9M 加权分解）",
        [f"gm_FY2025Q{k+1}", f"gm_FY2026Q{k+1}", f"revenue_FY2025Q{k+1}_sq", "gm_9m_FY2025"],
        f"{round(w25[k],6)} * ({round(m26[k],4)} - {round(m25[k],4)})",
        rate_i[k], "percentage_points", 0.01, "w=FY2025 同季收入/9M收入（6位小数），毛利率4位；指定分解式"))
    s05_calcs.append(calc(f"mix_contrib_Q{k+1}", f"Q{k+1} 权重变化贡献（9M 加权分解）",
        [f"gm_FY2026Q{k+1}", f"revenue_FY2025Q{k+1}_sq", f"revenue_FY2026Q{k+1}_sq", "gm_9m_FY2026"],
        f"({round(w26[k],6)} - {round(w25[k],6)}) * {round(m26[k],4)}", mix_i[k], "percentage_points", 0.01, "同上"))
s05_calcs += [
    calc("gm_9m_FY2025", "FY2025 9M 加权毛利率", ["revenue_FY2025Q1_sq", "revenue_FY2025Q2_sq", "revenue_FY2025Q3_sq",
         "gross_profit_FY2025Q1_sq", "gross_profit_FY2025Q2_sq", "gross_profit_FY2025Q3_sq"],
         f"({G[('FY2025',1)]}+{G[('FY2025',2)]}+{G[('FY2025',3)]}) / ({R[('FY2025',1)]}+{R[('FY2025',2)]}+{R[('FY2025',3)]}) * 100", M25, "percent", 0.005, "4位展示"),
    calc("gm_9m_FY2026", "FY2026 9M 加权毛利率", ["revenue_FY2026Q1_sq", "revenue_FY2026Q2_sq", "revenue_FY2026Q3_sq",
         "gross_profit_FY2026Q1_sq", "gross_profit_FY2026Q2_sq", "gross_profit_FY2026Q3_sq"],
         f"({G[('FY2026',1)]}+{G[('FY2026',2)]}+{G[('FY2026',3)]}) / ({R[('FY2026',1)]}+{R[('FY2026',2)]}+{R[('FY2026',3)]}) * 100", M26, "percent", 0.005, "同上"),
    calc("gm_9m_delta", "9M 毛利率同比变化", ["gm_9m_FY2025", "gm_9m_FY2026"], f"{round(M26,4)} - {round(M25,4)}", M26 - M25, "percentage_points", 0.005, "同上"),
    calc("decomp_tie", "分解勾稽：Σ率贡献+Σ权重贡献 = 整体变化",
         [f"rate_contrib_Q{k+1}" for k in range(3)] + [f"mix_contrib_Q{k+1}" for k in range(3)] + ["gm_9m_delta"],
         "Σ[rate_contrib]+Σ[mix_contrib] - gm_9m_delta",
         sum(rate_i) + sum(mix_i) - (M26 - M25), "percentage_points", 0.005, "指定分解式残差应为0"),
    calc("simple_mean_FY2026", "FY2026 三季简单平均（对照加权）",
         [f"gm_FY2026Q{k+1}" for k in range(3)],
         f"({round(m26[0],4)} + {round(m26[1],4)} + {round(m26[2],4)}) / 3", sum(m26) / 3, "percent", 0.005, "与 9M 加权值不同→不可用简单平均替代"),
]
make_case("S05", "LV-FY2026Q3-GM-DRIFT-001",
    "联想 FY2026 前九个月毛利率下滑主要集中在哪些季度？请量化季度表现及收入结构变化的影响，说明分析的口径与限制。",
    ["9M = Q1+Q2+Q3（三个单季 DB 值求和），不是全年",
     "一种允许的精确分解：ΔM = Σ[w0×(m1−m0)] + Σ[(w1−w0)×m1]，w 为各年在 9M 内的收入权重，m 为单季毛利率；"
     "其他完整分解同样接受，但须说明交互项分配并勾稽到整体变化",
     "毛利率 = 毛利 ÷ 收入；单位百万美元"],
    "FY2026", "FY2025Q1–Q3 与 FY2026Q1–Q3 单季",
    "FY2025Q3/FY2025Q1-Q2 行与 S04 比较期共用；FY2026Q1/Q2 行与 S03/S02 共用；FY2026 簇内划分。",
    s05_calcs,
    {"layer1_facts": [
        f"9M 加权毛利率 {M25:.4f}% → {M26:.4f}%（{M26-M25:.4f}pp）；单季毛利率同比：Q1 {m26[0]-m25[0]:.4f}pp、Q2 {m26[1]-m25[1]:.4f}pp、Q3 {m26[2]-m25[2]:.4f}pp，Q1 降幅最大。",
        f"指定分解下：率变化贡献 Q1 {rate_i[0]:.4f}pp、Q2 {rate_i[1]:.4f}pp、Q3 {rate_i[2]:.4f}pp（合计 {sum(rate_i):.4f}pp）；权重变化贡献合计 {sum(mix_i):.4f}pp；两类合计与整体变化精确勾稽。下滑主要在 Q1（率贡献约 {rate_i[0]/(M26-M25)*100:.0f}%），收入结构影响很小。"],
     "layer2_assumptions_boundaries": [
        "9M 加权变化 ≠ 各季简单平均，也 ≠ 各季同比降幅本身；判断'贡献'须按分解式而非只看降幅。",
        "简单平均 FY2026 三季毛利率 %.4f%% 与加权 9M %.4f%% 不同，不能替代。" % (sum(m26) / 3, M26),
        "季度毛利率下滑不能推出产品组合/ISG 等原因（分部毛利数据缺失）。"],
     "layer3_followups": ["分部毛利率与占比、产品价格与成本明细、汇率影响、其余季度（全年口径）表现。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s05_calcs]),
     "interpretation_assertions": [
        "以加权口径判定 Q1 为下滑集中季度（降幅与率贡献均最大）",
        "区分率变化贡献与权重变化贡献，并勾稽到 9M 整体变化"],
     "boundary_assertions": [
        "说明 9M 与全年口径差异",
        "接受其他精确分解（需勾稽与交互项说明），不要求逐字匹配指定式"],
     "forbidden_claims": [
        "使用'全年'表述代替前九个月",
        "用近似量级数字（如 2562.8/2795.0/2958.7）支持小容差评分",
        "用错误的 Q1 权重表述（25.1%→23.2%）",
        "仅比较各季降幅就宣称贡献占比",
        "断言下滑由产品组合或 ISG 造成"],
     "alternate_correct_decompositions_accepted": True},
    [{"hypothesis": "结构（组合）效应为主", "if_true_expect": "权重贡献显著大于率贡献——本例计算否证（权重贡献 -0.0064pp ≪ 率贡献 -0.8785pp）"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": ["分部毛利率（补查项）"],
     "notes": "六个单季收入/毛利全部 DB 精确值（FY2025 三季毛利 2559.849/2795.750/2959.391），公告 9M 表可勾稽。"},
    ["'全年'改为'前九个月'；FY2025 三季毛利替换为 DB 精确值（原'量级'近似删除）",
     "修正权重：Q1 收入权重 FY2025 29.6526% → FY2026 30.6245%（原 25.1%→23.2% 错误）",
     "给出完整勾稽分解（率+权重两类，逐季列示），接受其他精确分解",
     "新增简单平均 vs 加权对照计算"],
    ["删除：近似量级输入", "删除：错误权重与方向表述",
     "删除：只用 Δ季度毛利率×单年权重的不完整分解作为唯一评分路径"],
    inputs=s05_inputs)
import os
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# ================= S06: FY2027Q1 营运资金占用与现金边界 =================
con2 = duckdb.connect(DB, read_only=True)
def fact2(acct, fy, q):
    r = con2.execute("SELECT value FROM facts WHERE company_id='Lenovo' AND account_code=? "
                     "AND fiscal_year=? AND fiscal_quarter=? AND version='original'",
                     [acct, fy, q]).fetchall()
    assert len(r) == 1, (acct, fy, q)
    return float(r[0][0])
bal = {m: {(fy, qq): fact2(m, fy, qq) for (fy, qq) in ((2025, 4), (2026, 1), (2026, 4), (2027, 1))}
       for m in ("inventory", "accounts_receivable", "accounts_payable")}
rev26q1, rev27q1 = fact2("revenue", 2026, 1), fact2("revenue", 2027, 1)
cog26, cog27 = fact2("cogs", 2026, 1), fact2("cogs", 2027, 1)
cash26, cash27 = fact2("cash", 2026, 1), fact2("cash", 2027, 1)
s06_inputs = []
for m in ("inventory", "accounts_receivable", "accounts_payable"):
    for fy, qq, lbl in ((2025, 4, "FY2025Q4"), (2026, 1, "FY2026Q1"), (2026, 4, "FY2026Q4"), (2027, 1, "FY2027Q1")):
        pg = 14 if m in ("inventory", "accounts_receivable") else 15
        s06_inputs.append(db_in(f"{m}_{lbl}_pt", m, fy, qq, "time_point", bal[m][(fy, qq)], P27, pg))
for m, fy, qq, v, pg in (("revenue", 2026, 1, rev26q1, 5), ("revenue", 2027, 1, rev27q1, 5),
                         ("cogs", 2026, 1, cog26, 5), ("cogs", 2027, 1, cog27, 5),
                         ("cash", 2026, 1, cash26, 14), ("cash", 2027, 1, cash27, 14)):
    s06_inputs.append(db_in(f"{m}_FY{fy}Q{qq}_sq" if m != "cash" else f"cash_FY{fy}Q{qq}_pt",
                            m, fy, qq, "single_quarter" if m != "cash" else "time_point", v, P27, pg,
                            "补充观察项，仅作背景，不得用于反证现金压力" if m == "cash" else None))
d_inv, d_ar, d_ap = (bal[m][(2027, 1)] - bal[m][(2026, 1)] for m in ("inventory", "accounts_receivable", "accounts_payable"))
net_occ = d_inv + d_ar - d_ap
ar0, ar1 = bal["accounts_receivable"][(2026, 1)], bal["accounts_receivable"][(2027, 1)]
ap0, ap1 = bal["accounts_payable"][(2026, 1)], bal["accounts_payable"][(2027, 1)]
ar_25q4, ar_26q4 = bal["accounts_receivable"][(2025, 4)], bal["accounts_receivable"][(2026, 4)]
ap_25q4, ap_26q4 = bal["accounts_payable"][(2025, 4)], bal["accounts_payable"][(2026, 4)]
inv0, inv1 = bal["inventory"][(2026, 1)], bal["inventory"][(2027, 1)]
inv_25q4, inv_26q4 = bal["inventory"][(2025, 4)], bal["inventory"][(2026, 4)]
avg_ar0, avg_ar1 = (ar_25q4 + ar0) / 2, (ar_26q4 + ar1) / 2
avg_ap0, avg_ap1 = (ap_25q4 + ap0) / 2, (ap_26q4 + ap1) / 2
avg_iv0, avg_iv1 = (inv_25q4 + inv0) / 2, (inv_26q4 + inv1) / 2
dso0, dso1 = avg_ar0 / rev26q1 * 91, avg_ar1 / rev27q1 * 91
dio0b, dio1b = avg_iv0 / cog26 * 91, avg_iv1 / cog27 * 91
dpo0, dpo1 = avg_ap0 / cog26 * 91, avg_ap1 / cog27 * 91
ccc0, ccc1 = dso0 + dio0b - dpo0, dso1 + dio1b - dpo1
s06_calcs = [
    calc("d_inventory", "存货余额同比增量", ["inventory_FY2026Q1_pt", "inventory_FY2027Q1_pt"],
         f"{inv1} - {inv0}", d_inv, "usd_mn", 0.001, "时点余额直减"),
    calc("d_receivable", "应收余额同比增量", ["accounts_receivable_FY2026Q1_pt", "accounts_receivable_FY2027Q1_pt"],
         f"{ar1} - {ar0}", d_ar, "usd_mn", 0.001, "同上"),
    calc("d_payable", "应付余额同比增量", ["accounts_payable_FY2026Q1_pt", "accounts_payable_FY2027Q1_pt"],
         f"{ap1} - {ap0}", d_ap, "usd_mn", 0.001, "同上"),
    calc("net_balance_occupation", "选定三余额净占用", ["d_inventory", "d_receivable", "d_payable"],
         f"{round(d_inv,3)} + {round(d_ar,3)} - {round(d_ap,3)}", net_occ, "usd_mn", 0.001,
         "修订值；原 gold/审核稿 5280.727 系公式串算术错误（ΔInv 误作 7020.312、ΔAR 误作 8470.213），以余额直算为准"),
    calc("ap_vs_inv_ratio", "应付增量对存货增量的覆盖倍数", ["d_payable", "d_inventory"],
         f"{round(d_ap,3)} / {round(d_inv,3)}", d_ap / d_inv, "ratio", 0.0005, "3位小数展示"),
    calc("ap_covers_pct", "应付增量抵消存货+应收增量的比例", ["d_payable", "d_inventory", "d_receivable"],
         f"{round(d_ap,3)} / ({round(d_inv,3)} + {round(d_ar,3)}) * 100", d_ap / (d_inv + d_ar) * 100, "percent", 0.005, "同上"),
    calc("dso_prior_avg", "DSO（平均余额口径，上年同期）", ["accounts_receivable_FY2025Q4_pt", "accounts_receivable_FY2026Q1_pt", "revenue_FY2026Q1_sq"],
         f"({ar_25q4} + {ar0}) / 2 / {rev26q1} * 91", dso0, "days", 0.001, "统一评测口径"),
    calc("dso_curr_avg", "DSO（平均余额口径，本期）", ["accounts_receivable_FY2026Q4_pt", "accounts_receivable_FY2027Q1_pt", "revenue_FY2027Q1_sq"],
         f"({ar_26q4} + {ar1}) / 2 / {rev27q1} * 91", dso1, "days", 0.001, "同上"),
    calc("dio_prior_avg", "DIO（平均余额口径，上年同期）", ["inventory_FY2025Q4_pt", "inventory_FY2026Q1_pt", "cogs_FY2026Q1_sq"],
         f"({inv_25q4} + {inv0}) / 2 / {cog26} * 91", dio0b, "days", 0.001, "同上"),
    calc("dio_curr_avg", "DIO（平均余额口径，本期）", ["inventory_FY2026Q4_pt", "inventory_FY2027Q1_pt", "cogs_FY2027Q1_sq"],
         f"({inv_26q4} + {inv1}) / 2 / {cog27} * 91", dio1b, "days", 0.001, "同上"),
    calc("dpo_prior_avg", "DPO（平均余额口径，上年同期）", ["accounts_payable_FY2025Q4_pt", "accounts_payable_FY2026Q1_pt", "cogs_FY2026Q1_sq"],
         f"({ap_25q4} + {ap0}) / 2 / {cog26} * 91", dpo0, "days", 0.001, "同上"),
    calc("dpo_curr_avg", "DPO（平均余额口径，本期）", ["accounts_payable_FY2026Q4_pt", "accounts_payable_FY2027Q1_pt", "cogs_FY2027Q1_sq"],
         f"({ap_26q4} + {ap1}) / 2 / {cog27} * 91", dpo1, "days", 0.001, "同上"),
    calc("ccc_prior_avg", "CCC（主口径=平均余额，上年同期）", ["dso_prior_avg", "dio_prior_avg", "dpo_prior_avg"],
         f"{round(dso0,4)} + {round(dio0b,4)} - {round(dpo0,4)}", ccc0, "days", 0.0015, "三项舍入累积"),
    calc("ccc_curr_avg", "CCC（主口径=平均余额，本期）", ["dso_curr_avg", "dio_curr_avg", "dpo_curr_avg"],
         f"{round(dso1,4)} + {round(dio1b,4)} - {round(dpo1,4)}", ccc1, "days", 0.0015, "同上"),
    calc("ccc_delta_avg", "CCC 同比变化（主口径）", ["ccc_prior_avg", "ccc_curr_avg"],
         f"{round(ccc1,4)} - {round(ccc0,4)}", ccc1 - ccc0, "days", 0.002, "同上"),
    calc("alt_dso_pt_prior", "DSO（期末余额替代口径，上年）", ["accounts_receivable_FY2026Q1_pt", "revenue_FY2026Q1_sq"],
         f"{ar0} / {rev26q1} * 91", ar0 / rev26q1 * 91, "days", 0.001, "替代口径单独列示"),
    calc("alt_dio_pt_prior", "DIO（期末余额替代口径，上年）", ["inventory_FY2026Q1_pt", "cogs_FY2026Q1_sq"],
         f"{inv0} / {cog26} * 91", inv0 / cog26 * 91, "days", 0.001, "同上"),
    calc("alt_dpo_pt_prior", "DPO（期末余额替代口径，上年）", ["accounts_payable_FY2026Q1_pt", "cogs_FY2026Q1_sq"],
         f"{ap0} / {cog26} * 91", ap0 / cog26 * 91, "days", 0.001, "同上"),
    calc("alt_ccc_pt_prior", "CCC（期末余额替代口径，上年）", ["alt_dso_pt_prior", "alt_dio_pt_prior", "alt_dpo_pt_prior"],
         f"{round(ar0/rev26q1*91,4)} + {round(inv0/cog26*91,4)} - {round(ap0/cog26*91,4)}",
         ar0 / rev26q1 * 91 + inv0 / cog26 * 91 - ap0 / cog26 * 91, "days", 0.002, "替代口径"),
    calc("alt_ccc_pt_curr", "CCC（期末余额替代口径，本期）",
         ["accounts_receivable_FY2027Q1_pt", "inventory_FY2027Q1_pt", "accounts_payable_FY2027Q1_pt",
          "revenue_FY2027Q1_sq", "cogs_FY2027Q1_sq"],
         f"{ar1} / {rev27q1} * 91 + {inv1} / {cog27} * 91 - {ap1} / {cog27} * 91",
         ar1 / rev27q1 * 91 + inv1 / cog27 * 91 - ap1 / cog27 * 91, "days", 0.001, "替代口径"),
    calc("alt_ccc_pt_delta", "CCC 同比变化（替代口径）", ["alt_ccc_pt_prior", "alt_ccc_pt_curr"],
         f"({round(ar1/rev27q1*91,4)} + {round(inv1/cog27*91,4)} - {round(ap1/cog27*91,4)}) - ({round(ar0/rev26q1*91,4)} + {round(inv0/cog26*91,4)} - {round(ap0/cog26*91,4)})",
         (ar1 / rev27q1 * 91 + inv1 / cog27 * 91 - ap1 / cog27 * 91) - (ar0 / rev26q1 * 91 + inv0 / cog26 * 91 - ap0 / cog26 * 91),
         "days", 0.005, "替代口径与主口径差异明显，不得混用；公式含4位舍入"),
    calc("cash_delta", "现金余额同比变化（补充观察）", ["cash_FY2026Q1_pt", "cash_FY2027Q1_pt"],
         f"{cash27} - {cash26}", cash27 - cash26, "usd_mn", 0.001, "非评分主线，仅补充观察"),
]
make_case("S06", "LV-FY2027Q1-WC-CCC-001",
    "联想 FY2027Q1 营运资金扩张体现了怎样的资金占用变化？请比较上年同期，分析库存、应收和应付之间的关系，并说明能否据此判断现金压力。",
    ["选定三余额净占用 = Δ存货 + Δ应收 − Δ应付（时点余额同比差）；它是所选三个资产负债表科目的净变化，不等于全口径营运资金，也不等于经营现金流",
     "天数指标主口径 = 平均余额（(期初+期末)/2）÷ 单季流量 × 当季实际天数 91；期末余额口径为替代口径，须单独列示、不与主口径结果混用",
     "单位百万美元"],
    "FY2027Q1", "FY2025Q4–FY2027Q1 时点 + FY2026Q1/FY2027Q1 单季流量",
    "与 S01 共用 FY2027Q1 营运资金事件与存货/COGS 行（本题已自包含重复登记输入）；与 R01/R02 同公告期。",
    s06_calcs,
    {"layer1_facts": [
        f"存货 +{d_inv:.3f}、应收 +{d_ar:.3f}、应付 +{d_ap:.3f}；三余额净占用 +{net_occ:.3f} 百万美元（修订值；原候选公式串 7020.312+8470.213-10209.798=5280.727 与其自身余额输入不符，属算术错误）。",
        f"应付增量约为存货增量的 {d_ap/d_inv:.2f} 倍，抵消了存货+应收合计增量的约 {d_ap/(d_inv+d_ar)*100:.1f}%——应付在余额分析上显著缓冲了资金占用。",
        f"主口径（平均余额）DSO {dso0:.4f}→{dso1:.4f}、DIO {dio0b:.4f}→{dio1b:.4f}、DPO {dpo0:.4f}→{dpo1:.4f}、CCC {ccc0:.4f}→{ccc1:.4f} 天（基本持平）；期末口径 CCC 由 {ar0/rev26q1*91+inv0/cog26*91-ap0/cog26*91:.4f} 升至 {ar1/rev27q1*91+inv1/cog27*91-ap1/cog27*91:.4f} 天（+8.73 天）。周期天数与金额占用是两个层面的观察，不互相替代。"],
     "layer2_assumptions_boundaries": [
        "可判定：经营性扩张在选定余额口径上占用显著增加，应付是最主要的内部缓冲来源。",
        "不可判定：是否构成'现金压力'与最终由谁出资——现金期末余额受借款、融资、投资与汇率/合并范围等影响，现金余额增加（+1403.037）与备货占用现金可同时成立；缺现金流量表与保理/供应链融资等信息。",
        "CCC 基本持平仅指该公式下的周期观察，不能代替金额占用分析，也不否定占用增加。"],
     "layer3_followups": [
        "现金流量表三项（经营/投资/筹资）、存货库龄与应付账期、保理或供应链融资安排、外汇与合并范围对余额的影响、管理层 CCC 的确切定义（未能对账）。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s06_calcs
                     if not c["calculation_id"].startswith("alt_") and c["calculation_id"] != "cash_delta"]),
     "interpretation_assertions": [
        "同时给出金额层净占用与天数层周期变化，并说明两者含义不同",
        "识别应付增量大于存货增量、对占用形成缓冲"],
     "boundary_assertions": [
        "明确净占用是选定余额口径变化、不等于经营现金流",
        "明确表示现有数据不能判定现金压力与出资来源",
        "替代口径若使用须单独标注口径"],
     "forbidden_claims": [
        "'主要由谁出资'的确定性判断",
        "'现金余额增加所以备货没有挤压现金'式反证",
        "沿用错误数值 5280.727",
        "把净占用直接称为全口径营运资金或经营现金流",
        "宣称管理层公告 CCC 陈述被证明错误（口径无法对账，只能说未对账成功）"]},
    [{"hypothesis": "备货以供应商信用（应付）为主融资", "if_true_expect": "应付增速持续高于存货/应收且经营现金流未同幅恶化"},
     {"hypothesis": "外部融资/现金储备支撑", "if_true_expect": "现金流量表显示筹资活动流入显著增加"}],
    {"status": "ready", "checked_at": "2026-10-05", "missing_fields": ["现金流量表科目（未建模，属补查项）"],
     "notes": "全部输入 DB original 可查并与公告 p14/p15/p5 一致；本题自包含全部输入，不引用其他案例。"},
    ["删除'主要由谁出资'强制判断与'现金余额反证'推理",
     "净占用改为按余额直算的 5258.727（登记原 5280.727 公式串算术错误）",
     "inventory/COGS 等输入自包含重复登记，不再写'同 LV-FY2027Q1-INV-DIO-001'",
     "主/替代两种天数口径分别完整列示；现金仅作补充观察项"],
    ["删除：'现金余额同比上升，与备货挤压现金相反'",
     "删除：以作者公式与公告差异断言管理层归因不成立",
     "删除：跨案例读取另一题 gold 的依赖"],
    inputs=s06_inputs)

# ================= S07: FY2026Q1 盈利改善质量 =================
g25q1s, g26q1s = fact("gross_profit", 2025, 1), fact("gross_profit", 2026, 1)
o25q1s, o26q1s = fact("operating_income", 2025, 1), fact("operating_income", 2026, 1)
rv25q1, rv26q1 = fact("revenue", 2025, 1), fact("revenue", 2026, 1)
ne25s, ne26s = g25q1s - o25q1s, g26q1s - o26q1s
d_g, d_n, d_o = g26q1s - g25q1s, ne26s - ne25s, o26q1s - o25q1s
P26Q1 = "FY26Q1_140820250742.pdf"
s07_inputs = [
    db_in("revenue_FY2025Q1_sq", "revenue", 2025, 1, "single_quarter", rv25q1, P26Q1, 6),
    db_in("revenue_FY2026Q1_sq", "revenue", 2026, 1, "single_quarter", rv26q1, P26Q1, 6),
    db_in("gross_profit_FY2025Q1_sq", "gross_profit", 2025, 1, "single_quarter", g25q1s, P26Q1, 6),
    db_in("gross_profit_FY2026Q1_sq", "gross_profit", 2026, 1, "single_quarter", g26q1s, P26Q1, 6),
    db_in("operating_income_FY2025Q1_sq", "operating_income", 2025, 1, "single_quarter", o25q1s, P26Q1, 6),
    db_in("operating_income_FY2026Q1_sq", "operating_income", 2026, 1, "single_quarter", o26q1s, P26Q1, 6),
]
s07_calcs = [
    calc("d_gross_profit", "毛利同比变化", ["gross_profit_FY2025Q1_sq", "gross_profit_FY2026Q1_sq"],
         f"{g26q1s} - {g25q1s}", d_g, "usd_mn", 0.001, "3位小数直减"),
    calc("net_opex_FY2025Q1", "上年派生净费用", ["gross_profit_FY2025Q1_sq", "operating_income_FY2025Q1_sq"],
         f"{g25q1s} - {o25q1s}", ne25s, "usd_mn", 0.001, "与公告 p6 功能分类 (2,065,380) 勾稽"),
    calc("net_opex_FY2026Q1", "本期派生净费用", ["gross_profit_FY2026Q1_sq", "operating_income_FY2026Q1_sq"],
         f"{g26q1s} - {o26q1s}", ne26s, "usd_mn", 0.001, "与公告 p6 (1,989,668) 勾稽"),
    calc("d_net_opex", "派生净费用同比变化", ["net_opex_FY2025Q1", "net_opex_FY2026Q1"],
         f"{round(ne26s,3)} - {round(ne25s,3)}", d_n, "usd_mn", 0.001, "负值=净费用下降"),
    calc("d_operating_income", "经营利润同比变化", ["operating_income_FY2025Q1_sq", "operating_income_FY2026Q1_sq"],
         f"{o26q1s} - {o25q1s}", d_o, "usd_mn", 0.001, "同上"),
    calc("bridge_tie", "利润桥勾稽", ["d_gross_profit", "d_net_opex", "d_operating_income"],
         f"{round(d_g,3)} - ({round(d_n,3)}) - {round(d_o,3)}", d_g - d_n - d_o, "usd_mn", 0.002, "残差应为0"),
    calc("oi_growth_pct", "经营利润同比增速", ["d_operating_income", "operating_income_FY2025Q1_sq"],
         f"{round(d_o,3)} / {o25q1s} * 100", d_o / o25q1s * 100, "percent", 0.005, "4位展示"),
    calc("revenue_growth_pct", "收入同比增速", ["revenue_FY2025Q1_sq", "revenue_FY2026Q1_sq"],
         f"({rv26q1} / {rv25q1} - 1) * 100", (rv26q1 / rv25q1 - 1) * 100, "percent", 0.005, "同上"),
    calc("net_opex_change_pct", "派生净费用同比变化率", ["d_net_opex", "net_opex_FY2025Q1"],
         f"{round(d_n,3)} / {round(ne25s,3)} * 100", d_n / ne25s * 100, "percent", 0.005, "同上"),
    calc("gm_FY2025Q1", "上年毛利率", ["gross_profit_FY2025Q1_sq", "revenue_FY2025Q1_sq"],
         f"{g25q1s} / {rv25q1} * 100", g25q1s / rv25q1 * 100, "percent", 0.005, "同上"),
    calc("gm_FY2026Q1", "本期毛利率", ["gross_profit_FY2026Q1_sq", "revenue_FY2026Q1_sq"],
         f"{g26q1s} / {rv26q1} * 100", g26q1s / rv26q1 * 100, "percent", 0.005, "同上"),
    calc("gm_delta_pp", "毛利率同比变化", ["gm_FY2025Q1", "gm_FY2026Q1"], "14.7344 - 16.5718", (g26q1s / rv26q1 - g25q1s / rv25q1) * 100, "percentage_points", 0.005, "同上"),
    calc("oi_margin_delta_pp", "经营利润率同比变化", ["operating_income_FY2025Q1_sq", "operating_income_FY2026Q1_sq", "revenue_FY2025Q1_sq", "revenue_FY2026Q1_sq"],
         f"{o26q1s}/{rv26q1}*100 - {o25q1s}/{rv25q1}*100", (o26q1s / rv26q1 - o25q1s / rv25q1) * 100, "percentage_points", 0.005, "同上"),
    calc("gp_contribution_share", "毛利贡献占经营利润增量比例", ["d_gross_profit", "d_operating_income"],
         f"{round(d_g,3)} / {round(d_o,3)} * 100", d_g / d_o * 100, "percent", 0.01, "4位展示；净费用贡献=100-该值"),
    calc("opex_contribution_share", "净费用下降贡献占经营利润增量比例", ["d_net_opex", "d_operating_income"],
         f"{round(-d_n,3)} / {round(d_o,3)} * 100", -d_n / d_o * 100, "percent", 0.01, "与上一项相加应为100"),
]
make_case("S07", "LV-FY2026Q1-GM-OI-DIVERGE-001",
    "联想 FY2026Q1 的经营利润改善质量如何？请比较上年同期，分析可量化的增长来源，并指出需要哪些额外数据判断改善的持续性。",
    ["派生净经营费用 = 毛利 − 经营利润（净额口径，含其他经营收支等）",
     "经营利润增量 = 毛利增量 − 净费用增量（两环节都可为正贡献）",
     "单位百万美元；单季口径（FY2026Q1 截止 2025-06-30）"],
    "FY2026", "FY2025Q1 与 FY2026Q1 单季",
    "FY2026Q1 行与 S03/S02 共用；认股权证 152.361 亦出现在 R01 调节表（evaluator 背景，跨题不重复计为独立事件）。",
    s07_calcs,
    {"layer1_facts": [
        f"经营利润 +{d_o:.3f}（+{d_o/o25q1s*100:.2f}%）由两环节共同贡献：毛利 +{d_g:.3f}（约 {d_g/d_o*100:.1f}% 增量）与派生净费用下降 {abs(d_n):.3f}（贡献约 {-d_n/d_o*100:.1f}%）；勾稽 {d_g:.3f} − ({d_n:.3f}) = {d_o:.3f}。",
        f"毛利率 {g25q1s/rv25q1*100:.4f}% → {g26q1s/rv26q1*100:.4f}%（{(g26q1s/rv26q1-g25q1s/rv25q1)*100:.4f}pp）而经营利润率上升 {(o26q1s/rv26q1-o25q1s/rv25q1)*100:.4f}pp；收入 +{(rv26q1/rv25q1-1)*100:.2f}% 时净费用反而下降 {d_n/ne25s*100:.2f}%。"],
     "layer2_assumptions_boundaries": [
        "报表层改善可以确认；'盈利能力显著增强'是否成立取决于改善的可持续性与构成，现有集团数据不能给出二元结论——合理保留判断应得分。",
        "净费用在收入高增背景下逆势下降是异常信号：公告披露含认股权证公允值收益 1.52 亿美元与其他经营收入转正，但这些明细不在 DB；Agent 只能将其列为待补查假设，不得当作已证原因，也不得因未命中明细扣分。",
        "'经营利润全部增量来自费用层'为错误表述（两环节各有贡献）。"],
     "layer3_followups": [
        "费用功能分类明细与其他经营收支、非 HKFRS 调节表（调整后经营溢利 631,046/497,341 为 evaluator 背景）、分部利润、现金流核对。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in s07_calcs]),
     "interpretation_assertions": [
        "将改善分解为毛利与净费用两环节并给出占比/勾稽",
        "识别净费用在收入高增时下降为需要解释的异常点"],
     "boundary_assertions": [
        "对'改善质量/持续性'给出证据边界与补查需求，而非二元断言",
        "不得强制命中 DB 不可见明细（认股权证金额、调整后增速 10.3% 等）"],
     "forbidden_claims": [
        "'全部增量来自费用层'",
        "断言'盈利能力显著增强'成立或不成立（超出证据）",
        "强制复述认股权证 152.361 百万美元或调整后 +10.3%"]},
    [{"hypothesis": "净费用下降主要来自一次性/非现金项", "if_true_expect": "费用明细中公允值收益与其他经营收入占变动大头；剔除后净费用随规模上升"},
     {"hypothesis": "真实费用纪律", "if_true_expect": "各功能费用科目普遍低于收入增速"}],
    {"status": "ready_for_core_scoring", "checked_at": "2026-10-05",
     "missing_fields": ["adjusted_operating_income", "non_ifrs_adjustments（DB 无，仅作补查需求，不在评分主线）"],
     "notes": "核心四层数字 DB 可查且与公告 p6 千美元表勾稽；原标 ready 却强制不可见明细的矛盾已消除（评分范围缩至 DB 可支持）。"},
    ["修正'经营利润全部增量来自费用层'为两环节贡献（+214.628 与 +75.712）",
     "认股权证 152.361 与调整后 +10.3% 移出强制评分，保留 evaluator 背景",
     "去掉'盈利能力显著增强成立/不成立'的二元强制，改为边界评分",
     "6 行独立输入 + 完整桥勾稽计算"],
    ["删除：DB 缺明细时的强制命中评分（缺数字不得判 Agent 错）",
     "删除：'全部增量来自费用层'过度表述"],
    inputs=s07_inputs)

# ================= R01: FY2027Q1 报表 vs 调整后经营利润（needs_numeric_data） =================
r01_in = [
    pdf_in("oi_reported_FY2027Q1", "operating_income", 2027, 1, "single_quarter", 19.202, "19,202 千美元", P27, 9, db_match=fact("operating_income", 2027, 1)),
    pdf_in("oi_reported_FY2026Q1", "operating_income", 2026, 1, "single_quarter", 784.809, "784,809 千美元", P27, 9, db_match=fact("operating_income", 2026, 1)),
    pdf_in("oi_adjusted_FY2027Q1", "adjusted_operating_income", 2027, 1, "single_quarter", 1522.683, "1,522,683 千美元", P27, 9),
    pdf_in("oi_adjusted_FY2026Q1", "adjusted_operating_income", 2026, 1, "single_quarter", 631.046, "631,046 千美元", P27, 9),
    pdf_in("adj_fvtpl_curr", "non_ifrs_adjustment_fvtpl", 2027, 1, "single_quarter", -250.083, "(250,083) 千美元", P27, 9, "以公允值计量且其变动计入损益的金融资产公允值变动净额，经营溢利列"),
    pdf_in("adj_amortization_curr", "non_ifrs_adjustment_amortization", 2027, 1, "single_quarter", 16.524, "16,524 千美元", P27, 9, "併購產生的無形資產攤銷"),
    pdf_in("adj_acquisition_costs_curr", "non_ifrs_adjustment_acquisition_costs", 2027, 1, "single_quarter", 1.240, "1,240 千美元", P27, 9, "併購費用"),
    pdf_in("adj_impairment_writeoff_curr", "non_ifrs_adjustment_impairment_writeoff", 2027, 1, "single_quarter", 22.229, "22,229 千美元", P27, 9, "無形資產及在建工程減值及撇銷"),
    pdf_in("adj_warrant_fv_curr", "non_ifrs_adjustment_warrant_fv", 2027, 1, "single_quarter", 1690.299, "1,690,299 千美元", P27, 9, "與認股權證相關的衍生金融負債公允值虧損（加回项）"),
    pdf_in("adj_associate_impairment_curr", "non_ifrs_adjustment_associate_impairment", 2027, 1, "single_quarter", 23.272, "23,272 千美元", P27, 9, "聯營公司股權減值"),
    pdf_in("adj_fvtpl_prior", "non_ifrs_adjustment_fvtpl", 2026, 1, "single_quarter", -20.893, "(20,893) 千美元", P27, 9, "上年比较栏"),
    pdf_in("adj_amortization_prior", "non_ifrs_adjustment_amortization", 2026, 1, "single_quarter", 16.458, "16,458 千美元", P27, 9, "上年比较栏"),
    pdf_in("adj_writeoff_prior", "non_ifrs_adjustment_writeoff", 2026, 1, "single_quarter", 3.033, "3,033 千美元", P27, 9, "在建工程撇銷"),
    pdf_in("adj_warrant_fv_prior", "non_ifrs_adjustment_warrant_fv", 2026, 1, "single_quarter", -152.361, "(152,361) 千美元", P27, 9, "上年为认股权证公允值收益（扣减项，符号为负）"),
]
r01_bc = 19.202 - 250.083 + 16.524 + 1.240 + 22.229 + 1690.299 + 23.272
r01_bp = 784.809 - 20.893 + 16.458 + 3.033 - 152.361
r01_calcs = [
    calc("bridge_curr_tie", "本期调节表勾稽（呈报+Σ调节项=经调整）",
         ["oi_reported_FY2027Q1", "adj_fvtpl_curr", "adj_amortization_curr", "adj_acquisition_costs_curr",
          "adj_impairment_writeoff_curr", "adj_warrant_fv_curr", "adj_associate_impairment_curr", "oi_adjusted_FY2027Q1"],
         "19.202 - 250.083 + 16.524 + 1.240 + 22.229 + 1690.299 + 23.272 - 1522.683",
         r01_bc - 1522.683, "usd_mn", 0.002, "残差应为0（千美元精确值）"),
    calc("bridge_prior_tie", "上年调节表勾稽",
         ["oi_reported_FY2026Q1", "adj_fvtpl_prior", "adj_amortization_prior", "adj_writeoff_prior",
          "adj_warrant_fv_prior", "oi_adjusted_FY2026Q1"],
         "784.809 - 20.893 + 16.458 + 3.033 - 152.361 - 631.046",
         r01_bp - 631.046, "usd_mn", 0.002, "残差应为0"),
    calc("adjustment_total_curr", "本期调节项合计", ["adj_fvtpl_curr", "adj_amortization_curr", "adj_acquisition_costs_curr",
         "adj_impairment_writeoff_curr", "adj_warrant_fv_curr", "adj_associate_impairment_curr"],
         "-250.083 + 16.524 + 1.240 + 22.229 + 1690.299 + 23.272", r01_bc - 19.202, "usd_mn", 0.001, "T_c = 经调整 − 呈报"),
    calc("adjustment_total_prior", "上年调节项合计", ["adj_fvtpl_prior", "adj_amortization_prior", "adj_writeoff_prior", "adj_warrant_fv_prior"],
         "-20.893 + 16.458 + 3.033 - 152.361", r01_bp - 784.809, "usd_mn", 0.001, "T_p"),
    calc("d_reported", "呈报经营利润同比变化", ["oi_reported_FY2027Q1", "oi_reported_FY2026Q1"],
         "19.202 - 784.809", 19.202 - 784.809, "usd_mn", 0.001, "同上"),
    calc("d_adjusted", "经调整经营利润同比变化", ["oi_adjusted_FY2027Q1", "oi_adjusted_FY2026Q1"],
         "1522.683 - 631.046", 1522.683 - 631.046, "usd_mn", 0.001, "同上"),
    calc("yoy_tie", "同比勾稽：Δ调整 − Δ呈报 = Δ调节项合计", ["d_reported", "d_adjusted", "adjustment_total_curr", "adjustment_total_prior"],
         f"(1522.683 - 631.046) - (19.202 - 784.809) - ({round(r01_bc - 19.202, 3)} - ({round(r01_bp - 784.809, 3)}))",
         (1522.683 - 631.046) - (19.202 - 784.809) - ((r01_bc - 19.202) - (r01_bp - 784.809)), "usd_mn", 0.002, "残差应为0"),
    calc("warrant_swing", "认股权证公允值项同比摆动", ["adj_warrant_fv_curr", "adj_warrant_fv_prior"],
         "1690.299 - (-152.361)", 1690.299 + 152.361, "usd_mn", 0.001, "由收益转亏损的摆动额"),
]
make_case("R01", "LV-FY2027Q1-OI-BRIDGE-001",
    "联想 FY2027Q1 的报表经营利润与调整后经营利润为何表现不同？请量化口径差异和同比变化，并说明这些变化对经营表现和现金的含义。",
    ["非 HKFRS 调节项与调整后经营溢利须以完整可查询调节表形式提供（当前仅公告 p9 存在，DB 无）；补数前本题不进入自动评分运行",
     "调节桥：经调整 = 呈报 + Σ调节项（符号以经营溢利列为准）；单位百万美元"],
    "FY2027Q1", "FY2026Q1 与 FY2027Q1 单季",
    "呈报经营溢利行与 S07/S06 同期；认股权证项亦为 S07 evaluator 背景；调节事件簇跨公告重复出现（Q4-COLLAPSE 等），留分时应归同组。",
    r01_calcs,
    {"layer1_facts": [
        "呈报与调整后的差额精确等于各非 HKFRS 项之和（两期桥勾稽成立）：本期 +1503.481、上年 −153.763 百万美元。",
        "呈报经营利润同比 −765.607（约 -98%），调整后 +891.637（约 +141%）；差异主驱动是认股权证公允值项由 −152.361 收益转为 +1690.299 亏损加回（摆动 1842.660）。"],
     "layer2_assumptions_boundaries": [
        "非现金公允值变动不等同经营恶化或现金流出；但也不等于没有经济影响（影响报表结果并可能与融资安排关联）。",
        "调整后口径由管理层定义，跨公司/跨期比较须核对其项目清单；不得把'非现金'扩写为'无风险'。"],
     "layer3_followups": [
        "调节表全科目入数据层（见 data_gaps.json 规格）、认股权证条款与股价敏感性、现金流量表确认经营现金流未受该项目影响。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in r01_calcs]),
     "interpretation_assertions": [
        "两口径差额=调节项之和，且同比差异由项目摆动解释（勾稽精确）",
        "区分会计口径变化与经营/现金实质"],
     "boundary_assertions": [
        "不从调整后增长直接推断经营改善可持续",
        "标注调整后口径的管理层定义属性"],
     "forbidden_claims": [
        "把非现金项目说成无经济影响",
        "混用归母利润调节桥（本题只用经营溢利列）",
        "未核对 2026-08 公告即沿用'初始会计处理未完成'等后续期间表述"]},
    [{"hypothesis": "呈报利润下滑主要由股价相关公允值损失造成", "if_true_expect": "剔除认股权证项后呈报口径与调整后趋势同向"}],
    {"status": "needs_numeric_data", "checked_at": "2026-10-05",
     "missing_fields": ["non_ifrs_adjustments 全表（可查询数据层）", "adjusted_operating_income"],
     "notes": "呈报经营溢利 DB 与 PDF p9 双证一致；调节明细当前只存在于公告 p9。本案例保持 needs_numeric_data，待用户裁定补数后再上线评分。"},
    ["输入改为逐项独立行（14 行，含符号与页码），不再用多项目合并字符串",
     "桥与同比全部给出精确勾稽计算",
     "明确不混用归母调节桥；后续公告表述需另行核对"],
    ["删除：把题面不可见调节明细当作 Agent 应'检索'到的答案",
     "删除：'非现金=无经济影响'式结论"],
    inputs=r01_in, status="needs_numeric_data")

# ================= R02: FY2027Q1 分部收入贡献（needs_numeric_data） =================
r02_in = [
    pdf_in("seg_idg_revenue_FY2026Q1", "segment_revenue_idg", 2026, 1, "single_quarter", 13459.338, "13,459,338 千美元", P27, 7),
    pdf_in("seg_idg_revenue_FY2027Q1", "segment_revenue_idg", 2027, 1, "single_quarter", 17105.518, "17,105,518 千美元", P27, 7),
    pdf_in("seg_isg_revenue_FY2026Q1", "segment_revenue_isg", 2026, 1, "single_quarter", 4290.149, "4,290,149 千美元", P27, 7),
    pdf_in("seg_isg_revenue_FY2027Q1", "segment_revenue_isg", 2027, 1, "single_quarter", 8510.078, "8,510,078 千美元", P27, 7),
    pdf_in("seg_ssg_revenue_FY2026Q1", "segment_revenue_ssg", 2026, 1, "single_quarter", 2257.718, "2,257,718 千美元", P27, 7),
    pdf_in("seg_ssg_revenue_FY2027Q1", "segment_revenue_ssg", 2027, 1, "single_quarter", 2883.628, "2,883,628 千美元", P27, 7),
    pdf_in("elimination_FY2026Q1", "intersegment_elimination", 2026, 1, "single_quarter", -1177.336, "(1,177,336) 千美元", P27, 7),
    pdf_in("elimination_FY2027Q1", "intersegment_elimination", 2027, 1, "single_quarter", -1556.458, "(1,556,458) 千美元", P27, 7),
    db_in("group_revenue_FY2026Q1_sq", "revenue", 2026, 1, "single_quarter", fact("revenue", 2026, 1), P27, 7, "公告 p7 合计与 DB 一致"),
    db_in("group_revenue_FY2027Q1_sq", "revenue", 2027, 1, "single_quarter", fact("revenue", 2027, 1), P27, 7, "公告 p7 合计与 DB 一致"),
]
seg_d = {"idg": 17105.518 - 13459.338, "isg": 8510.078 - 4290.149, "ssg": 2883.628 - 2257.718,
         "elim": -1556.458 - (-1177.336)}
seg_total_d = sum(seg_d.values())
r02_calcs = [
    calc("contrib_idg", "IDG 对集团收入同比变化的金额贡献", ["seg_idg_revenue_FY2026Q1", "seg_idg_revenue_FY2027Q1"],
         "17105.518 - 13459.338", seg_d["idg"], "usd_mn", 0.001, "千美元精确值"),
    calc("contrib_isg", "ISG 贡献", ["seg_isg_revenue_FY2026Q1", "seg_isg_revenue_FY2027Q1"],
         "8510.078 - 4290.149", seg_d["isg"], "usd_mn", 0.001, "同上"),
    calc("contrib_ssg", "SSG 贡献", ["seg_ssg_revenue_FY2026Q1", "seg_ssg_revenue_FY2027Q1"],
         "2883.628 - 2257.718", seg_d["ssg"], "usd_mn", 0.001, "同上"),
    calc("contrib_elimination", "抵销项贡献（拖累）", ["elimination_FY2026Q1", "elimination_FY2027Q1"],
         "-1556.458 - (-1177.336)", seg_d["elim"], "usd_mn", 0.001, "同上"),
    calc("contrib_tie", "分部贡献加总勾稽集团收入变化", ["contrib_idg", "contrib_isg", "contrib_ssg", "contrib_elimination",
         "group_revenue_FY2026Q1_sq", "group_revenue_FY2027Q1_sq"],
         f"3646.180 + 4219.929 + 625.910 + (-379.122) - ({round(fact('revenue', 2027, 1), 3)} - {round(fact('revenue', 2026, 1), 3)})",
         seg_total_d - (fact("revenue", 2027, 1) - fact("revenue", 2026, 1)), "usd_mn", 0.002, "残差应为0（分部与 DB 集团收入勾稽验证于 2026-10-05）"),
    calc("segment_sum_tie_curr", "本期分部合计=抵销后集团收入", ["seg_idg_revenue_FY2027Q1", "seg_isg_revenue_FY2027Q1",
         "seg_ssg_revenue_FY2027Q1", "elimination_FY2027Q1", "group_revenue_FY2027Q1_sq"],
         f"17105.518 + 8510.078 + 2883.628 - 1556.458 - {round(fact('revenue', 2027, 1), 3)}",
         17105.518 + 8510.078 + 2883.628 - 1556.458 - fact("revenue", 2027, 1), "usd_mn", 0.002, "残差应为0"),
    calc("segment_sum_tie_prior", "上年分部合计=抵销后集团收入", ["seg_idg_revenue_FY2026Q1", "seg_isg_revenue_FY2026Q1",
         "seg_ssg_revenue_FY2026Q1", "elimination_FY2026Q1", "group_revenue_FY2026Q1_sq"],
         f"13459.338 + 4290.149 + 2257.718 - 1177.336 - {round(fact('revenue', 2026, 1), 3)}",
         13459.338 + 4290.149 + 2257.718 - 1177.336 - fact("revenue", 2026, 1), "usd_mn", 0.002, "残差应为0"),
    calc("growth_idg", "IDG 同比增速", ["seg_idg_revenue_FY2026Q1", "seg_idg_revenue_FY2027Q1"],
         "(17105.518 / 13459.338 - 1) * 100", 17105.518 / 13459.338 * 100 - 100, "percent", 0.005, "4位展示"),
    calc("growth_isg", "ISG 同比增速", ["seg_isg_revenue_FY2026Q1", "seg_isg_revenue_FY2027Q1"],
         "(8510.078 / 4290.149 - 1) * 100", 8510.078 / 4290.149 * 100 - 100, "percent", 0.005, "同上"),
    calc("growth_ssg", "SSG 同比增速", ["seg_ssg_revenue_FY2026Q1", "seg_ssg_revenue_FY2027Q1"],
         "(2883.628 / 2257.718 - 1) * 100", 2883.628 / 2257.718 * 100 - 100, "percent", 0.005, "同上"),
    calc("share_of_delta_isg", "ISG 占总增量比例", ["contrib_isg", "group_revenue_FY2026Q1_sq", "group_revenue_FY2027Q1_sq"],
         "4219.929 / 8112.897 * 100", seg_d["isg"] / seg_total_d * 100, "percent", 0.01, "分母=集团收入变化 8112.897"),
    calc("share_of_delta_idg", "IDG 占总增量比例", ["contrib_idg", "group_revenue_FY2026Q1_sq", "group_revenue_FY2027Q1_sq"],
         "3646.180 / 8112.897 * 100", seg_d["idg"] / seg_total_d * 100, "percent", 0.01, "同上"),
]
make_case("R02", "LV-FY2027Q1-SEGM-CALIBER-001",
    "联想 FY2027Q1 的集团收入增长主要来自哪些业务？请量化贡献，解释增长率排序与金额贡献是否一致，并说明结构变化的分析边界。",
    ["分部收入为公告口径（含分部间销售），抵销后等于集团收入；需要真实三分部两期收入+抵销+集团完整可查询表（见 data_gaps.json）",
     "贡献 = 分部收入同比变化；Σ分部贡献 + Δ抵销 = Δ集团收入；单位百万美元"],
    "FY2027Q1", "FY2026Q1 与 FY2027Q1 单季（分部口径）",
    "与 S01/S06/R01 同属 FY2027Q1 公告簇；集团收入行与 S06 共用。分部利润质量不在本题（原题任务过大已缩减）。",
    r02_calcs,
    {"layer1_facts": [
        "集团收入同比 +8112.897：ISG +4219.929（占 52.01%）、IDG +3646.180（44.94%）、SSG +625.910（7.72%）、抵销拖累 −379.122（−4.67%）；加总精确勾稽。",
        "增速排序 ISG（+98.36%）≫ SSG（+27.72%）≈ IDG（+27.09%）；金额贡献排序 ISG>IDG>SSG。本例 ISG 既最快又贡献最大，但 IDG 与 SSG 增速接近而金额贡献差近 6 倍——只按增速排序会误判来源。"],
     "layer2_assumptions_boundaries": [
        "只能证明增长落在哪些分部；AI 需求、价格、汇率、并表范围是竞争假设，现有数字不能唯一确定原因。",
        "分部抵销规则、分部间销售占比未披露明细，结构分析存在口径边界；分部经营溢利质量另案。"],
     "layer3_followups": [
        "分部利润表、抵销明细、ISG 云/企基子口径拆分、汇率中性增速（若披露）。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in r02_calcs]),
     "interpretation_assertions": [
        "用金额贡献（增量分解）而非增速排序判断增长来源",
        "指出增速排序与金额贡献可背离并解释基数作用"],
     "boundary_assertions": [
        "增长落点≠经营原因；列出可区分假设的补查数据"],
     "forbidden_claims": [
        "断言增长由 AI 需求/某一具体因素造成",
        "把增速排序直接当贡献排序",
        "使用合成 facts_detail 分部数据"]},
    [{"hypothesis": "ISG 增长主要由云基础设施带动", "if_true_expect": "ISG 子口径中云收入增速显著高于企基（需披露明细）"}],
    {"status": "needs_numeric_data", "checked_at": "2026-10-05",
     "missing_fields": ["segment_revenue 可查询表", "intersegment_elimination 可查询表"],
     "notes": "PDF p7 数字已逐项页码锚定并与 DB 集团收入勾稽一致；待用户裁定把真实分部表纳入统一数据层（沿用统一查询方式）后上线。"},
    ["任务从'两级勾稽+排序陷阱+税前桥'缩减为收入贡献单一任务",
     "分部数字改为逐行独立输入（10 行，含页码与千美元原值）",
     "新增增速/份额两套计算并勾稽集团收入"],
    ["删除：未分配项目税前桥（本轮不做）",
     "删除：把合成 facts_detail 当真实分部数据"],
    inputs=r02_in, status="needs_numeric_data")

# ================= R03: FY2026 ISG 半年亏损的季度分布（needs_numeric_data） =================
P26Q2F = "FY26Q2_201120250729.pdf"
def half_in(iid, metric, fyear, fy_h1_end, value_mn, raw, page, note=None):
    d = {"input_id": iid, "metric": metric, "entity": "Lenovo 联想",
         "period_type": "half_year", "fiscal_year": fyear, "fiscal_quarter": None,
         "period_end": fy_h1_end, "value": round(value_mn, 3), "unit": "usd_mn", "version": "as_disclosed",
         "source": {"type": "pdf", "pdf": P26Q2F, "pdf_page": page, "raw_value": raw}}
    if note:
        d["source"]["note"] = note
    return d
def q2_in(iid, metric, fy, value_mn, raw, page):
    return {"input_id": iid, "metric": metric, "entity": "Lenovo 联想",
            "period_type": "single_quarter", "fiscal_year": fy, "fiscal_quarter": 2,
            "period_end": PE[2](fy), "value": round(value_mn, 3), "unit": "usd_mn", "version": "as_disclosed",
            "source": {"type": "pdf", "pdf": P26Q2F, "pdf_page": page, "raw_value": raw}}
r03_in = [
    half_in("isg_op_FY2025H1", "segment_operating_profit_isg", 2025, "2024-09-30", -73.002, "(73,002) 千美元", 7, "FY2025 H1（截至 2024-09-30）比较栏"),
    half_in("isg_op_FY2026H1", "segment_operating_profit_isg", 2026, "2025-09-30", -117.555, "(117,555) 千美元", 7),
    q2_in("isg_op_FY2025Q2_sq", "segment_operating_profit_isg", 2025, -35.728, "(35,728) 千美元", 10),
    q2_in("isg_op_FY2026Q2_sq", "segment_operating_profit_isg", 2026, -32.035, "(32,035) 千美元", 10),
    half_in("isg_revenue_FY2025H1", "segment_revenue_isg", 2025, "2024-09-30", 6465.167, "6,465,167 千美元", 7),
    half_in("isg_revenue_FY2026H1", "segment_revenue_isg", 2026, "2025-09-30", 8377.292, "8,377,292 千美元", 7),
    q2_in("isg_revenue_FY2025Q2_sq", "segment_revenue_isg", 2025, 3305.370, "3,305,370 千美元", 10),
    q2_in("isg_revenue_FY2026Q2_sq", "segment_revenue_isg", 2026, 4087.143, "4,087,143 千美元", 10),
]
r03_calcs = [
    calc("isg_op_FY2025Q1_derived", "FY2025Q1 ISG 经营损益（H1−Q2 差分）", ["isg_op_FY2025H1", "isg_op_FY2025Q2_sq"],
         "-73.002 - (-35.728)", -73.002 + 35.728, "usd_mn", 0.001, "差分项：真实季度表补入后应与直接值一致"),
    calc("isg_op_FY2026Q1_derived", "FY2026Q1 ISG 经营损益（H1−Q2 差分）", ["isg_op_FY2026H1", "isg_op_FY2026Q2_sq"],
         "-117.555 - (-32.035)", -117.555 + 32.035, "usd_mn", 0.001, "同上"),
    calc("d_h1", "H1 亏损同比变化", ["isg_op_FY2025H1", "isg_op_FY2026H1"], "-117.555 - (-73.002)", -117.555 + 73.002, "usd_mn", 0.001, "负值=亏损扩大"),
    calc("d_q1", "Q1 亏损同比变化", ["isg_op_FY2025Q1_derived", "isg_op_FY2026Q1_derived"], "-85.520 - (-37.274)", -85.520 + 37.274, "usd_mn", 0.001, "同上"),
    calc("d_q2", "Q2 亏损同比变化", ["isg_op_FY2025Q2_sq", "isg_op_FY2026Q2_sq"], "-32.035 - (-35.728)", -32.035 + 35.728, "usd_mn", 0.001, "正值=亏损收窄"),
    calc("quarter_decomp_tie", "季度分解勾稽：ΔQ1+ΔQ2=ΔH1", ["d_q1", "d_q2", "d_h1"],
         "(-48.246) + 3.693 - (-44.553)", (-48.246) + 3.693 - (-44.553), "usd_mn", 0.002, "残差应为0"),
    calc("qoq_q2_vs_q1", "本年 Q2 对 Q1 环比变化（亏损收窄额）", ["isg_op_FY2026Q1_derived", "isg_op_FY2026Q2_sq"],
         "-32.035 - (-85.520)", -32.035 + 85.520, "usd_mn", 0.001, "同上"),
    calc("isg_revenue_growth_q2", "Q2 ISG 收入同比增速", ["isg_revenue_FY2025Q2_sq", "isg_revenue_FY2026Q2_sq"],
         "(4087.143 / 3305.370 - 1) * 100", 4087.143 / 3305.370 * 100 - 100, "percent", 0.005, "4位展示"),
    calc("isg_revenue_growth_h1", "H1 ISG 收入同比增速", ["isg_revenue_FY2025H1", "isg_revenue_FY2026H1"],
         "(8377.292 / 6465.167 - 1) * 100", 8377.292 / 6465.167 * 100 - 100, "percent", 0.005, "同上"),
]
make_case("R03", "LV-FY2026Q2-ISG-LOSS-Q1CONCENTRATION-001",
    "联想 FY2026 上半年的 ISG 亏损变化集中在哪些季度？请比较两个季度与上年同期，判断现有数字是否支持逐季恶化的说法。",
    ["分部经营损益为公告披露口径（亏损为负）；H1 与单季是不同 period_type 的记录，半年值不得充当 Q2",
     "Q1 分部损益由 H1 − Q2 差分得到（真实季度分部表补入后直接可查）；单位百万美元"],
    "FY2026", "FY2025H1–FY2026H1（分部口径，单季与半年）",
    "与 S03 同用 FY26Q2 公告但实体层级不同（分部 vs 集团）；与 R02 同属分部补数簇，正式选定时整体归组。",
    r03_calcs,
    {"layer1_facts": [
        "H1 亏损扩大 44.553（−73.002→−117.555）完全集中在 Q1：Q1 亏损同比扩大 48.246（−37.274→−85.520），Q2 反而同比收窄 3.693（−35.728→−32.035），且较本年 Q1 环比收窄 53.485；ΔQ1+ΔQ2=ΔH1 精确勾稽。",
        "'亏损逐季扩大'的说法不被数字支持；Q2 ISG 收入同比 +23.65% 的同时亏损收窄。"],
     "layer2_assumptions_boundaries": [
        "公告把 H1 亏损归因于扩大 AI 能力与企基转型投资——投资额与费用组成缺失，只能作为披露背景与待验证假设，不要求 Agent 猜中。",
        "差分得到的 Q1 依赖 H1/Q2 口径一致性（同一公告同一表式），真实季度表补入前此依赖应显式声明。"],
     "layer3_followups": [
        "真实 ISG 单季与半年分部表（独立 period_type）、AI 投资/转型费用明细、分部毛利率与订单结构。"]},
    {**base_scoring([f"calc:{c['calculation_id']}" for c in r03_calcs]),
     "interpretation_assertions": [
        "把半年恶化分解到季度并勾稽，识别恶化集中于 Q1、Q2 双向（同比/环比）收窄",
        "区分累计口径与单季方向的相反可能"],
     "boundary_assertions": [
        "拒绝'逐季恶化'而不夸大反向结论（Q2 收窄不代表趋势确立）",
        "投资归因列为待补查假设"],
     "forbidden_claims": [
        "把 H1 记录当 Q2 使用",
        "强制命中'AI 投资导致亏损'",
        "由两季数字断言全年趋势"]},
    [{"hypothesis": "投资前置集中在 Q1", "if_true_expect": "真实季度表显示 Q1 费用/摊销跳升而收入未同步"},
     {"hypothesis": "Q2 收窄为一次性", "if_true_expect": "后续季度亏损回扩——需留出季度验证，本轮不做预测"}],
    {"status": "needs_numeric_data", "checked_at": "2026-10-05",
     "missing_fields": ["ISG 单季/半年分部收入与经营损益可查询表（两年完整）"],
     "notes": "PDF p7（H1）与 p10（Q2）数字已页码锚定；facts_detail 为合成数据，禁用。待用户裁定补数规格后上线。"},
    ["输入改为 8 行独立记录（H1 与 Q2 分开 period_type，含页码与原始千美元值）",
     "新增季度分解勾稽与环比方向双验证",
     "投资归因降级为 evaluator 背景与假设检查"],
    ["删除：强制命中管理层投资解释",
     "删除：把半年数字混入单季输入"],
    inputs=r03_in, status="needs_numeric_data")

# ================= write =================
assert len(records) == 10, len(records)
seen = set()
for rec in records:
    assert rec["case_id"] not in seen, rec["case_id"]
    seen.add(rec["case_id"])
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=False) + "\n")
con.close()
con2.close()
print("WROTE", OUT, len(records), "cases")
for rec in records:
    print(f"  {rec['selected_id']:>3} {rec['case_id']}  inputs={len(rec['required_inputs'])} calcs={len(rec['calculations'])} status={rec['status']}")

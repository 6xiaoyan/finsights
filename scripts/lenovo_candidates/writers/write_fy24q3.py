# -*- coding: utf-8 -*-
"""FY2024Q3（2024-02-22 第三季业绩公告）归因候选：写 .cache/candidates/FY24Q3.jsonl + meta。"""
import json
import unicodedata

SRC = {"pdf": "FY24Q3_220220241202.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0222/2024022200124_c.pdf",
       "sha256": "ce4ca1ed04dfd749cb97fbaee60f26184c3a9adcc5f484b27934f9f9f9c4d57b"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2024, fiscal_quarter=3,
            period_end="2023-12-31", publication_date="2024-02-22",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2024Q3-YOY-STREAK-001",
    event_family_id="LV-FY2024Q3-first-positive-yoy",
    query="FY2024Q3 公告称收入'连续三个季度录得增长，按季上升9%…亦标志着集团最近一年半以来首次实现季度收入同比增长'，财务表现段又称'同比3%的增长，为一年多以来首次录得同比正增长'。请用 DB 季度收入复算本季环比与同比，核验环比三连增的起点，并定位本季度之前最后一个同比为正的财季与间隔月数，判定'一年半'与'一年多'两种表述哪个更贴合数据。",
    sources=[src([2], "業務回顧及展望-亮點；集團財務表現")],
    management_reference={"paraphrase": "亮点段：收入连续三个季度录得增长，按季上升9%至157亿美元，亦标志着集团最近一年半以来首次实现季度收入同比增长。财务表现段：本集团收入录得3%的同比增长，为一年多以来首次录得同比正增长。",
        "evidence_spans": [{"page": 2, "span": "收入連續三個季度錄得增長，按季上升9%至157億美元"},
                            {"page": 2, "span": "首次實現季度收入同比增長"},
                            {"page": 2, "span": "收入錄得3%的同比增長，為一年多以來首次錄得同比正增長"}]},
    required_inputs=[
        inp("季度收入", "FY2022Q1..FY2024Q3 共11季", [[16929.247, 17868.679, 20126.532, 16693.758, 16955.618, 17089.537, 15266.606, 12635.093, 12899.927, 14409.786, 15720.954]],
            "DB facts.revenue 连续序列", "可得")],
    calculations=[
        {"id": "q3_qoq", "formula": "15720.954/14409.786-1", "result": 0.09099, "tolerance_abs": 0.002},
        {"id": "q2_qoq", "formula": "14409.786/12899.927-1", "result": 0.11704, "tolerance_abs": 0.002},
        {"id": "q1_qoq", "formula": "12899.927/12635.093-1", "result": 0.02096, "tolerance_abs": 0.002},
        {"id": "pre_streak_qoq", "formula": "12635.093/15266.606-1（FY2023Q4 环比，恰断在三连增之前）", "result": -0.17239, "tolerance_abs": 0.002},
        {"id": "q3_yoy", "formula": "15720.954/15266.606-1", "result": 0.02976, "tolerance_abs": 0.002},
        {"id": "prev_pos_yoy", "formula": "16955.618/16929.247-1（上一同比为正季 FY2023Q1）", "result": 0.00156, "tolerance_abs": 0.002},
        {"id": "gap_months", "formula": "FY2024Q3 季末 2023-12-31 距 FY2023Q1 季末 2022-06-30 的月数", "result": 18, "tolerance_abs": 0},
        {"id": "worst_interval", "formula": "12635.093/16693.758-1（间隔5季中最差 FY2023Q4）", "result": -0.24309, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "环比 +9.10% 成立（公告'9%'），环比三连增成立且起点精确：FY2024Q1 +2.10%、Q2 +11.70%、Q3 +9.10%，而 FY2023Q4 环比 -17.24% 恰在连增段之前",
        "同比 +2.98%（公告'3%'）；本季度之前最后一个同比为正财季是 FY2023Q1（+0.16%，季末 2022-06-30），与本季季末恰隔 18 个月——'最近一年半'按季末口径精确成立，'一年多'也真但松（18个月就是年半）",
        "间隔 5 季同比全部为负：-4.36%/-24.15%/-24.31%/-23.92%/-15.68%，最差 FY2023Q4 -24.31%",
        "锚点质量须指出：上一次同比为正仅是 FY2023Q1 的 +0.16%（近乎持平），'首次转正'叙事依赖把 0.16% 也算'正增长'"],
        "acceptable_variants": ["+2.98% 可写约3%；间隔可写'5个季度/18个月'"],
        "must_not_claim": ["不能把'连续三个季度增长'误读为同比三连增（那是环比）", "不能宣称'+0.16% 不算正增长所以间隔更长'——公告口径下正增长即算，且两种时间表述均与数据兼容", "不能用毛利/溢利序列替代收入序列判定该声明"],
        "unknowns": []},
    db_support={"status": "ready", "db_fields": ["revenue"], "derived": ["逐季环比/同比序列"], "missing": []},
    unknowns=[],
    related_note="与 LV-FY2025Q1-DOUBLE-DIGIT-CLAIM-001 同属'增长断点声明'簇：两者都靠季末间隔月数判定时间窗声明（本案18个月/该案30个月），各自独立保留。")

r2 = dict(base, case_id="LV-FY2024Q3-CCC-PLUS14-001",
    event_family_id="LV-FY2024Q3-ccc-plus14",
    query="公告称'在本集团的现金周转期方面，尽管被应付账款天数减少所抵销，应收账款及库存天数合计同比增加了十四天'。请只用 DB 按 D=91 复算 FY2024Q3 对 FY2023Q3 的 DSO/DIO/DPO（时点与平均两口径），检验'应收+库存合计增加十四天'与'应付天数减少'两个成分，并说明整句的方向为何与数据相反。",
    sources=[src([2], "業務回顧及展望-亮點")],
    management_reference={"paraphrase": "亮点段称现金周转期方面，尽管被应付账款天数减少所抵销，应收账款及库存天数合计同比增加了十四天。",
        "evidence_spans": [{"page": 2, "span": "現金周轉期方面，儘管被應付賬款天數減少所抵銷"},
                            {"page": 2, "span": "款及庫存天數合計同比增加了十四天"}]},
    required_inputs=[
        inp("收入/销售成本", "FY2024Q3/FY2023Q3", [[15720.954, 13119.563], [15266.606, 12654.361]], "DB facts.revenue、facts.cogs", "可得"),
        inp("应收/存货/应付（期末）", "2023-12-31", [8944.694, 6218.910, 10258.486], "DB 三科目期末", "可得"),
        inp("应收/存货/应付（期末）", "2022-12-31", [9288.512, 7502.055, 10837.300], "DB 三科目期末", "可得"),
        inp("应收/存货/应付（期初）", "2023-09-30/2022-09-30", [[7809.892, 6169.642, 10872.335], [9786.759, 8417.891, 12348.689]], "DB FY2024Q2/FY2023Q2 期末（平均口径用）", "可得")],
    calculations=[
        {"id": "dso_pt", "formula": "8944.694/(15720.954/91) 对 9288.512/(15266.606/91)", "result": [51.7759, 55.3662], "tolerance_abs": 0.02},
        {"id": "dio_pt", "formula": "6218.910/(13119.563/91) 对 7502.055/(12654.361/91)", "result": [43.1356, 53.9488], "tolerance_abs": 0.02},
        {"id": "dpo_pt", "formula": "10258.486/(13119.563/91) 对 10837.300/(12654.361/91)", "result": [71.1550, 77.9332], "tolerance_abs": 0.02},
        {"id": "ar_inv_delta_pt", "formula": "(51.7759-55.3662)+(43.1356-53.9488)", "result": -14.4034, "tolerance_abs": 0.05},
        {"id": "dpo_delta_pt", "formula": "71.1550-77.9332", "result": -6.7782, "tolerance_abs": 0.02},
        {"id": "ccc_pt", "formula": "51.7759+43.1356-71.1550 对 55.3662+53.9488-77.9332", "result": [23.7566, 31.3818], "tolerance_abs": 0.02},
        {"id": "ccc_delta_pt", "formula": "23.7566-31.3818", "result": -7.6252, "tolerance_abs": 0.05},
        {"id": "ar_inv_delta_avg", "formula": "(48.4916-56.8512)+(42.9648-57.2417)", "result": -22.6366, "tolerance_abs": 0.05},
        {"id": "dpo_delta_avg", "formula": "73.2839-83.3675", "result": -10.0836, "tolerance_abs": 0.02},
        {"id": "ccc_delta_avg", "formula": "18.1725-30.7254", "result": -12.5530, "tolerance_abs": 0.05}],
    scorable_answer={"must_include": [
        "时点口径 ΔDSO+ΔDIO = -3.59 + -10.81 = -14.40 天：'十四天'的幅度精确复现，但方向相反——应收+库存天数是减少不是增加（去库存主因：存货余额 7,502.055→6,218.910）",
        "'应付账款天数减少'两个口径都真（时点 -6.78 天、平均 -10.08 天），这部分与数据一致",
        "净效应：时点口径 CCC 31.382→23.757 天改善 7.63 天（平均口径改善 12.55 天）；公告句把角色写反了——若按其字面（应收+库存+14 天、应付又缩短）CCC 应恶化约 20 天，与任何口径都不符",
        "把句子按 DB 重述才自洽：应收+库存改善 14.4 天，被应付天数缩短 6.8 天部分抵销，净改善 7.6 天"],
        "acceptable_variants": ["-14.40 天可写约-14天；CCC 改善可写 7.6/12.6 天（分口径）"],
        "must_not_claim": ["不能因'十四天'对上就背书'增加了十四天'（方向相反）", "不能两口径混用：平均口径该合计是 -22.64 天，与'十四天'也对不上，只有时点口径能复现幅度", "不能忽略公告句的因果表述与数据角色互换（把改善项写成了恶化项）"],
        "unknowns": ["管理层现金周转期定义仍未披露；本案'十四天'与'应付减少'两成分在时点口径下分别复现幅度与方向，提示其计算接近时点口径但符号叙述错误", "平均口径期初值 2022-09-30 取自 FY2023Q2 期末（9,786.759/8,417.891/12,348.689）"]},
    db_support={"status": "ready", "db_fields": ["revenue", "cogs", "accounts_receivable", "inventory", "accounts_payable"],
                "derived": ["DSO/DIO/DPO/CCC 两口径（D=91）"], "missing": []},
    unknowns=[],
    related_note="CCC 声明系列五案之一：本案幅度14天与应付方向可复现、AR+INV 方向反号；LV-FY2025Q1-CCC-11DAY-001 方向双反；LV-FY2025Q3-CCC-ONEDAY-001 量级不符；LV-FY2025Q4-CCC-CLAIM-001 归因失败；LV-FY2024Q4-CCC-NEG4-001 水平+方向双不符。同族跨季复现，独立保留。")

r3 = dict(base, case_id="LV-FY2024Q3-FINANCE-COSTS-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2024Q3-finance-costs",
    query="FY2024Q3 公告称财务费用'环比节省1,600万美元、同比节省1,000万美元'，且单季'较去年同期减少6%'、九个月口径'较去年同期增加22%'。请用公告损益表复算同比与九个月累计口径，判定环比 1,600 万在公告内部是否可验证（本季列没有 Q2 单季数），借上一季公告的 Q2 单季 190,378 完成复算；再用 DB 短长期借款余额验证'审慎减少有息借款'，并解释同比减 6% 与累计增 22% 为何同真。",
    sources=[src([1, 2, 6, 9, 15], "財務摘要-bullet；亮點；九個月MD&A；第三季MD&A；綜合損益表")],
    management_reference={"paraphrase": "摘要与亮点称审慎减少有息借款、财务费用环比节省1,600万美元、同比节省1,000万美元；MD&A 称单季财务费用较去年同期减少6%，九个月口径较去年同期增加22%（受市场利率上升影响）。损益表（千美元）：Q3 财务费用 (174,452)，上年同期 (184,809)；9M (562,256) 对 (460,046)。",
        "evidence_spans": [{"page": 1, "span": "由於審慎減少有息借款，財務費用環比節省1,600萬美元"},
                            {"page": 2, "span": "1,600萬美元，同比節省1,000萬美元"},
                            {"page": 6, "span": "期内財務費用受市場利率上升影響，部分被短期貸款提取減少所抵銷，較去年同期增加 22%"},
                            {"page": 9, "span": "期内財務費用受減少借貸影響，部分被市場利率上升所抵銷，較去年同期減少 6%"},
                            {"page": 15, "span": "財務費用 4(b) (174,452) (562,256) (184,809) (460,046)"}]},
    required_inputs=[
        inp("财务费用（单季/9M两期）", "FY2024Q3 与 FY2023Q3", [[174.452, 562.256], [184.809, 460.046]], "公告 p15 综合损益表", "缺：DB 无财务费用科目"),
        inp("财务费用（上一季单季）", "FY2024Q2", [190.378], "跨公告：FY24Q2 单季列载于 FY25Q2 公告 p15 比较栏（FY25Q2_151120240721.pdf，inventory 第5行）", "缺"),
        inp("有息借款（短+长期）", "2023-12-31/2023-09-30/2022-12-31", [3620.742, 3932.311, 4499.295], "DB short_term_debt+long_term_debt（票据/可转债分类含在 long 内）", "可得")],
    calculations=[
        {"id": "yoy_saving", "formula": "184.809-174.452", "result": 10.357, "tolerance_abs": 0.01},
        {"id": "yoy_pct", "formula": "174.452/184.809-1", "result": -0.0560, "tolerance_abs": 0.002},
        {"id": "qoq_saving", "formula": "190.378-174.452（Q2 单季来自上一季公告）", "result": 15.926, "tolerance_abs": 0.01},
        {"id": "nine_m_yoy", "formula": "562.256/460.046-1", "result": 0.2222, "tolerance_abs": 0.002},
        {"id": "debt_qoq", "formula": "3620.742-3932.311", "result": -311.569, "tolerance_abs": 0.01},
        {"id": "debt_yoy", "formula": "3620.742-4499.295", "result": -878.553, "tolerance_abs": 0.01}],
    scorable_answer={"must_include": [
        "同比成立：节省 10.357 百万≈'1,000万'，降幅 -5.60%（公告'6%'舍入）",
        "环比 1,600 万在本公告内不可验证——公告损益表只有 Q3 单季与 9M 两列，没有 FY2024Q2 单季财务费用；须借上一季公告（FY24Q2 单季 190,378 千美元，载于 FY25Q2 公告比较栏）才得 15.926 百万≈'1,600万' ✓",
        "-6% 与 +22% 同真不矛盾：单季口径因去借贷转降，9M 累计口径仍被 Q1/Q2 的高利率推高（562.256/460.046-1=+22.2%）；两个窗口方向相反，引用时必须带期间口径",
        "'审慎减少有息借款'有 DB 支持：短+长期借款 2022-12-31 4,499.295 → 2023-12-31 3,620.742（同比 -878.553，环比 -311.569），方向与幅度与叙述一致"],
        "acceptable_variants": ["-5.6% 可写'约6%'；环比节省可写 15.9 百万"],
        "must_not_claim": ["不能声称本公告单文件即可复现'环比1,600万'（跨公告取数是必要步骤）", "不能把 9M +22% 与单季 -6% 说成一个错一个对（口径不同）", "不能用 DB 算财务费用本身（无该科目），DB 只能验证其成因（借贷余额收缩）"],
        "unknowns": ["利率与余额两因素对 -10.357 百万同比变化的精确分解公告未披露（MD&A 只称'可换股债券利息减少…'等定性归因）"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["short_term_debt", "long_term_debt"],
                "missing": ["财务费用科目", "财务费用构成（票据/可转债/租赁利息拆分）"]},
    unknowns=[],
    related_note="DB 债务分类注：short+long_term_debt 合计与公告贷款总额跨季勾稽（见 LV-FY2025Q2-FCF-TRIPLE-001 / LV-FY2025Q3-NETCASH-GAP-001），本案沿用该口径验证'减少借贷'方向。")

recs = [r1, r2, r3]
with open(".cache/candidates/FY24Q3.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2024Q3-YOY-STREAK-001"] = {"tags": ["增长断点声明", "环比vs同比区分", "时间窗判定"],
    "difficulty_rationale": "同一段话里环比三连增与一年半首次同比转正两个声明须分别用环比/同比序列验证，锚点季 FY2023Q1 只有+0.16% 是叙事质量考点"}
meta["LV-FY2024Q3-CCC-PLUS14-001"] = {"tags": ["现金周转期", "双口径复算", "幅度对方向反", "角色互换"],
    "difficulty_rationale": "'十四天'与'应付减少'两成分在时点口径分别复现幅度/方向，但整句因果与数据角色互换（按字面 CCC 应恶化20天）；平均口径连幅度都对不上"}
meta["LV-FY2024Q3-FINANCE-COSTS-001"] = {"tags": ["财务费用", "跨公告取数", "期间口径对立", "债务余额验证"],
    "difficulty_rationale": "环比声明在本公告内无 Q2 单季列可验，须跨公告借数完成；同时 -6% 与 +22% 两口径方向相反须同时为真，DB 只能验证成因不能验证费用本身"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")


txt = open(".cache/lenovo_pdf_text/FY24Q3_220220241202.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

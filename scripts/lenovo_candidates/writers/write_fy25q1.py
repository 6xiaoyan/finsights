# -*- coding: utf-8 -*-
"""FY2025Q1（2024-08-15 第一季业绩公告）归因候选：写 .cache/candidates/FY25Q1.jsonl + meta。"""
import json
import unicodedata

SRC = {"pdf": "FY25Q1_150820241201.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0815/2024081500198_c.pdf",
       "sha256": "b665d9a5148f34caf4fde05b08a48ededf1edc8b4972c4fbd570af4097610026"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2025, fiscal_quarter=1,
            period_end="2024-06-30", publication_date="2024-08-15",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2025Q1-CCC-11DAY-001",
    event_family_id="LV-FY2025Q1-ccc-11day",
    query="公告称'本集团的现金周转期较去年延长11天'，并把主因归为存货天数增加（为需求优化、新产品发售及上升周期而储备）。请只用数据库的 revenue/cogs/accounts_receivable/inventory/accounts_payable，按 D=91 天分别用期末时点口径与期初期末平均口径复算 FY2025Q1 与 FY2024Q1 的 DSO/DIO/DPO/CCC，判断'+11天'的延长方向与'存货天数增加'归因在两种口径下是否成立。",
    sources=[src([2], "業務回顧及展望-亮點")],
    management_reference={"paraphrase": "亮点段称现金周转期较去年延长11天，主因存货天数增加（为需求优化、新产品发售及强劲上升周期而储备）；同段还称经营现金流同比+22%至7.91亿美元。",
        "evidence_spans": [{"page": 2, "span": "現金周轉期較去年延長11天。其中存貨天數增加主因是為了需求優化"}]},
    required_inputs=[
        inp("收入", "FY2025Q1/FY2024Q1", [15447.056, 12899.927], "DB facts.revenue", "可得"),
        inp("销售成本", "FY2025Q1/FY2024Q1", [12887.207, 10648.022], "DB facts.cogs", "可得"),
        inp("存货", "2024-06-30/2023-06-30", [7777.587, 5907.201], "DB facts.inventory 期末", "可得"),
        inp("存货（期初）", "2024-03-31/2023-03-31", [6702.677, 6371.858], "DB facts.inventory FY2024Q4/FY2023Q4 期末", "可得"),
        inp("应收账款", "2024-06-30/2023-06-30", [8654.796, 7474.309], "DB facts.accounts_receivable 期末", "可得"),
        inp("应收账款（期初）", "2024-03-31/2023-03-31", [8147.695, 7940.378], "DB 同科目期初", "可得"),
        inp("应付账款", "2024-06-30/2023-06-30", [11924.646, 9284.233], "DB facts.accounts_payable 期末", "可得"),
        inp("应付账款（期初）", "2024-03-31/2023-03-31", [10505.427, 9772.934], "DB 同科目期初", "可得")],
    calculations=[
        {"id": "dso_pt_cur", "formula": "8654.796/(15447.056/91)", "result": 50.9862, "tolerance_abs": 0.02},
        {"id": "dio_pt_cur", "formula": "7777.587/(12887.207/91)", "result": 54.9196, "tolerance_abs": 0.02},
        {"id": "dpo_pt_cur", "formula": "11924.646/(12887.207/91)", "result": 84.2031, "tolerance_abs": 0.02},
        {"id": "ccc_pt_cur", "formula": "50.9862+54.9196-84.2031", "result": 21.7027, "tolerance_abs": 0.02},
        {"id": "ccc_pt_pri", "formula": "52.7260+50.4841-79.3448（各项=FY2024Q1 同式）", "result": 23.8653, "tolerance_abs": 0.02},
        {"id": "ccc_delta_pt", "formula": "21.7027-23.8653", "result": -2.1626, "tolerance_abs": 0.02},
        {"id": "dio_delta_pt", "formula": "54.9196-50.4841", "result": 4.4356, "tolerance_abs": 0.02},
        {"id": "dso_delta_pt", "formula": "50.9862-52.7260", "result": -1.7399, "tolerance_abs": 0.02},
        {"id": "dpo_delta_pt", "formula": "84.2031-79.3448", "result": 4.8583, "tolerance_abs": 0.02},
        {"id": "ccc_avg_cur", "formula": "49.4925+51.1245-79.1924（各项=(期初+期末)/2 ÷ (流水/91)）", "result": 21.4246, "tolerance_abs": 0.02},
        {"id": "ccc_avg_pri", "formula": "54.3699+52.4696-81.4331（FY2024Q1 平均口径同式）", "result": 25.4064, "tolerance_abs": 0.02},
        {"id": "ccc_delta_avg", "formula": "21.4246-25.4064", "result": -3.9818, "tolerance_abs": 0.02},
        {"id": "dio_delta_avg", "formula": "51.1245-52.4696", "result": -1.3451, "tolerance_abs": 0.02},
        {"id": "inv_yoy", "formula": "7777.587/5907.201-1", "result": 0.31663, "tolerance_abs": 0.002},
        {"id": "cogs_yoy", "formula": "12887.207/10648.022-1", "result": 0.21029, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "两种口径下 CCC 都在同比缩短而非'延长11天'：期末时点口径 23.865→21.703 天（Δ-2.16 天），平均余额口径 25.406→21.425 天（Δ-3.98 天）",
        "存货归因口径敏感：期末口径 DIO 确实 +4.44 天（方向与叙事一致），但平均口径 DIO 反而 -1.35 天，'存货天数增加'在平均口径不成立；即便期末口径，+4.4 天也远小于'+11天'",
        "期末口径 DIO 上升的原因可量化：存货余额同比 +31.66% 快于销售成本 +21.03%",
        "期末口径的 CCC 缩短由 DPO +4.86 天（应付扩张）主导并盖过 DIO +4.44 天；平均口径则 DSO/DIO/DPO 三线全部改善"],
        "acceptable_variants": ["ΔCCC 可写 -2.2 天/-4.0 天；'+11天' 判定为'两种 DB 口径均无法复现'即可"],
        "must_not_claim": ["不能因为存货余额大增就直接背书'存货天数增加11天'（余额增速≠天数，平均口径下天数在降）", "不能断言管理层计算错误——公告未给出其现金周转期定义与数据源，DB 复现失败只说明口径不一致", "不能用经营现金流+22% 替代 CCC 结论"],
        "unknowns": ["公告'现金周转期'的分子分母定义（是否用平均余额、是否按收入口径计存货天数、天数基准）未知，'+11天'在 DB 两口径下均无法复现", "7.91 亿美元 CFO 与 +22% 无法用 DB 验证（无现金流量表科目）"]},
    db_support={"status": "ready", "db_fields": ["revenue", "cogs", "accounts_receivable", "inventory", "accounts_payable"],
                "derived": ["DSO/DIO/DPO/CCC（时点与平均两口径，D=91）"], "missing": []},
    unknowns=[],
    related_note="CCC 声明系列三案之一：本案（FY25Q1 '+11天/存货主因'，方向在两口径均告失败）、LV-FY2025Q3-CCC-ONEDAY-001（'改善一天'实际改善3-4天，量级不符）、LV-FY2025Q4-CCC-CLAIM-001（'+6天主因存货'归因失败）。同族跨季复现，各自验证不同季度声明，全部保留。")

r2 = dict(base, case_id="LV-FY2025Q1-DOUBLE-DIGIT-CLAIM-001",
    event_family_id="LV-FY2025Q1-double-digit-first",
    query="管理层称 FY2025Q1 收入增长20%、'为过去两年半以来首次录得双位数增长'。请用数据库逐季收入算出 FY2022Q1 至 FY2025Q1 每个财季的同比增速，定位 FY2025Q1 之前最后一个双位数增长的财季，按季末日期核对与 FY2025Q1 的间隔月数，并判定该声明是否成立。",
    sources=[src([2], "集團財務表現")],
    management_reference={"paraphrase": "集团财务表现首句：第一季度收入增长20%，为过去两年半以来首次录得双位数增长；并称三个业务集团收入均录得双位数增长。",
        "evidence_spans": [{"page": 2, "span": "其收入增長20%，為過去兩年半以來首次錄得雙位數增長"}]},
    required_inputs=[
        inp("季度收入", "FY2022Q1..FY2025Q1 共13季", [[16929.247, 17868.679, 20126.532, 16693.758, 16955.618, 17089.537, 15266.606, 12635.093, 12899.927, 14409.786, 15720.954, 13833.117, 15447.056]],
            "DB facts.revenue @FY2021Q1 基期起连续", "可得"),
        inp("上年同期收入", "FY2021Q1..FY2024Q4", [[13347.856, 14518.909, 17245.443, 15630.104, 16929.247, 17868.679, 20126.532, 16693.758, 16955.618, 17089.537, 15266.606, 12635.093]],
            "DB facts.revenue 上一财年同季", "可得")],
    calculations=[
        {"id": "q1_yoy", "formula": "15447.056/12899.927-1", "result": 0.1975, "tolerance_abs": 0.002},
        {"id": "fy22q3_yoy", "formula": "20126.532/17245.443-1", "result": 0.1671, "tolerance_abs": 0.002},
        {"id": "gap_months", "formula": "FY2025Q1 季末 2024-06-30 距上一双位数季 FY2022Q3 季末 2021-12-31 的月数", "result": 30, "tolerance_abs": 0},
        {"id": "intervening_n", "formula": "FY2022Q4 至 FY2024Q4 之间的财季数（同比<10%的季数）", "result": 9, "tolerance_abs": 0},
        {"id": "max_intervening", "formula": "FY2024Q4: 13833.117/12635.093-1（9个间隔季中的最大同比）", "result": 0.0948, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "本期同比 +19.75%，公告写 20%（四舍五入）成立",
        "FY2025Q1 之前最后一个双位数增长财季是 FY2022Q3（+16.71%，季末 2021-12-31），与 FY2025Q1 季末恰好相距 30 个月＝两年半，'过去两年半以来首次'按季末口径精确成立",
        "中间 9 个财季同比全部 <10%：最接近的是 FY2024Q4 的 +9.48%（距阈值仅 0.52pp），其余依次 FY2022Q4 +6.81%、FY2023Q1 +0.16%、FY2023Q2 -4.36%、FY2023Q3 -24.15%、FY2023Q4 -24.31%、FY2024Q1 -23.92%、FY2024Q2 -15.68%、FY2024Q3 +2.98%",
        "不能扩大为'史上首次'：FY2021Q3 +22.28%、FY2021Q4 +47.74%、FY2022Q1 +26.83%、FY2022Q2 +23.07% 均为双位数，声明的时间窗限定是准确的"],
        "acceptable_variants": ["+19.7%~+19.8%；间隔可表述为'9 个季度/30 个月'"],
        "must_not_claim": ["不能说该声明夸大（间隔恰为 30 个月，不是'超过两年半'也不是不足）", "不能把 FY2024Q4 的 +9.48% 四舍五入成 10% 来否定'首次'", "不能用 DB 之外的更早历史断言边界——DB 可见 FY2018 起，结论不受影响但须基于可见序列"],
        "unknowns": []},
    db_support={"status": "ready", "db_fields": ["revenue"], "derived": ["逐季同比（与上一财年同季比较，FY2022Q1..FY2025Q1 共13季）"], "missing": []},
    unknowns=[])

r3 = dict(base, case_id="LV-FY2025Q1-SEGMENT-CLAIMS-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2025Q1-segment-table-claims",
    query="FY2025Q1 公告摘要称 IDG 收入及分部溢利分别增长 11% 及 27%、ISG 销售额增长 65% 且损失同比减 2,300 万美元、SSG 经营溢利率维持 21% 且分部溢利占三集团综合的 33%。请用公告分部表复算这五个数字并核对分部表与集团数（收入合计-抵销=DB 收入；DB gross_profit-operating_income=公告经营费用），同时指出'高端销售额 PC/手机 +21%/+142%''非PC 占 IDG 收入 23%'为何不可验证。",
    sources=[src([1, 2, 8], "財務摘要-亮點；業務回顧；分部表")],
    management_reference={"paraphrase": "摘要三条分列 IDG +11%/+27%、ISG 销售 +65%/损失减 2,300 万美元、SSG 溢利率 21% 且为盈利引擎；亮点段称 PC 与手机高端销售分别 +21%/+142% 超越 11% 综合收入增长、SSG 分部溢利占三集团综合分部溢利 33%。分部表（千美元）：IDG 11,421,635/828,377 对 10,260,612/649,757；ISG 3,159,797/(37,274) 对 1,913,766/(60,417)；SSG 1,885,338/396,102 对 1,713,232/361,140；合计 16,466,770/1,187,205，抵销 (1,019,714)。",
        "evidence_spans": [{"page": 1, "span": "智能設備業務集團的收入及分部溢利分別增長 11%及 27%"},
                            {"page": 2, "span": "個人電腦及智能手機的高端銷售額分別按年增長21%及142%"},
                            {"page": 8, "span": "智能設備業務集團 11,421,635 828,377 10,260,612 649,757"},
                            {"page": 8, "span": "合計 16,466,770 1,187,205 13,887,610 950,480"},
                            {"page": 8, "span": "抵銷 (1,019,714) (327,585) (987,683) (307,360)"}]},
    required_inputs=[
        {"metric": "IDG 收入/分部经营溢利", "entity": "智能设备业务集团", "period": "FY2025Q1 与 FY2024Q1", "raw_value": [[11421.635, 828.377], [10260.612, 649.757]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8 分部表（千美元折百万）", "db": "缺：无分部科目"},
        {"metric": "ISG 收入/分部经营亏损", "entity": "基础设施方案业务集团", "period": "FY2025Q1 与 FY2024Q1", "raw_value": [[3159.797, -37.274], [1913.766, -60.417]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8", "db": "缺"},
        {"metric": "SSG 收入/分部经营溢利", "entity": "方案服务业务集团", "period": "FY2025Q1 与 FY2024Q1", "raw_value": [[1885.338, 396.102], [1713.232, 361.140]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8", "db": "缺"},
        {"metric": "分部合计/抵销", "entity": "集团", "period": "FY2025Q1", "raw_value": [[16466.770, -1019.714]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": "original", "source": "公告 p8", "db": "勾稽对象为 facts.revenue 15,447.056"},
        inp("毛利/经营溢利（集团）", "FY2025Q1", [2559.849, 494.469], "DB facts.gross_profit、facts.operating_income", "可得")],
    calculations=[
        {"id": "idg_rev_yoy", "formula": "11421.635/10260.612-1", "result": 0.11315, "tolerance_abs": 0.002},
        {"id": "idg_op_yoy", "formula": "828.377/649.757-1", "result": 0.27490, "tolerance_abs": 0.002},
        {"id": "idg_margin", "formula": "828.377/11421.635 与 649.757/10260.612", "result": [0.072527, 0.063325], "tolerance_abs": 0.0005},
        {"id": "isg_rev_yoy", "formula": "3159.797/1913.766-1", "result": 0.65109, "tolerance_abs": 0.002},
        {"id": "isg_loss_cut", "formula": "60.417-37.274", "result": 23.143, "tolerance_abs": 0.005},
        {"id": "ssg_margin", "formula": "396.102/1885.338", "result": 0.21010, "tolerance_abs": 0.001},
        {"id": "ssg_share", "formula": "396.102/1187.205", "result": 0.33364, "tolerance_abs": 0.001},
        {"id": "seg_sum", "formula": "11421.635+3159.797+1885.338", "result": 16466.770, "tolerance_abs": 0.005},
        {"id": "elim_tie", "formula": "16466.770-1019.714-15447.056", "result": 0.0, "tolerance_abs": 0.005},
        {"id": "opex_tie", "formula": "2559.849-494.469", "result": 2065.380, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "IDG 复算：收入 +11.3%（公告'11%'成立）、分部溢利 +27.5%（'27%'成立）；溢利率 6.33%→7.25%（+92bp），'+27%' 是溢利增速不是利润率",
        "ISG：收入 +65.1%（且单季 31.6 亿首次破 30 亿）；损失减少 60.417-37.274=23.143 百万美元≈公告'2,300万'",
        "SSG：溢利率 396.102/1,885.338=21.0%（'维持21%'成立）；占三集团综合分部溢利 396.102/1,187.205=33.4%（'33%'成立）",
        "勾稽：分部收入合计 16,466.770-抵销 1,019.714=15,447.056 与 DB revenue 精确一致；DB gross_profit-operating_income=2,065.380 与公告经营费用 (2,065,380) 千美元逐千位一致",
        "'高端销售 +21%/+142%'与'非PC 占 IDG 23%'为产品类别口径，公告表格与 DB 均无对应数据，属不可验证声明"],
        "acceptable_variants": ["IDG +11.3%/+27.5% 可写 11%/27%；SSG share 33.3%~33.4%"],
        "must_not_claim": ["不能用集团口径 DB 科目复现分部数（DB 无分部科目，分部表只能取自公告）", "不能把 IDG 分部溢利 +27% 说成利润率提升 27%", "不能宣称高端销售 +142% 可被验证"],
        "unknowns": ["分部溢利为未分摊总部费用口径；抵销 (327.585) 分部溢利抵销的内部交易构成未细分"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["revenue", "gross_profit", "operating_income"],
                "missing": ["分部收入与分部经营溢利/亏损", "产品类别销售（PC/智能手机/高端/非PC）"]},
    unknowns=[],
    related_note="与 LV-FY2025Q2-ISG-H1-CLAIMS-001（期间口径判定）、LV-FY2025Q3-SSG-PROFIT-SHARE-001（9M 占比 31.46%）同属分部声明族但验证不同声明：本案为 FY2025Q1 单季五声明交叉+勾稽，独立保留。")

recs = [r1, r2, r3]
with open(".cache/candidates/FY25Q1.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2025Q1-CCC-11DAY-001"] = {"tags": ["现金周转期", "双口径复算", "方向证伪", "归因口径敏感"],
    "difficulty_rationale": "'+11天'在时点与平均两口径下方向都相反；'存货主因'只在时点口径部分成立且量级仅+4.4天，须双口径分开判定声明的两个成分"}
meta["LV-FY2025Q1-DOUBLE-DIGIT-CLAIM-001"] = {"tags": ["增长叙事", "时间窗判定", "全序列同比"],
    "difficulty_rationale": "须算13季同比并定位上一双位数季（FY2022Q3，季末2021-12-31），发现与本期恰好相距30个月——声明精确成立，最大陷阱是把 +9.48% 四舍五入进双位数"}
meta["LV-FY2025Q1-SEGMENT-CLAIMS-001"] = {"tags": ["分部分析", "多声明交叉", "勾稽", "可验证/不可验证分层"],
    "difficulty_rationale": "单季五声明分三层：可复算(11%/27%/65%/2300万/21%/33%)、可勾稽(合计-抵销=DB收入、毛利-经营溢利=经营费用)、不可验证(高端销售与类别占比)"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")


txt = open(".cache/lenovo_pdf_text/FY25Q1_150820241201.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

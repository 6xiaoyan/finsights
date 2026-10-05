# -*- coding: utf-8 -*-
"""FY2025Q3（2025-02-20 披露）归因候选 step 1：写 .cache/candidates/FY25Q3.jsonl + meta。

所有数值均经 .cache/checks/fy25q3_dbcheck.py 与 DB 勾稽后填入；evidence_spans 为
公告缓存文本的逐字子串（脚本末尾自动断言）。
"""
import json

SRC = {"pdf": "FY25Q3_200220251201.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0220/2025022000140_c.pdf",
       "sha256": "2dace4b8e7f0585ebe1e1681390c97395e4850941689273785f676ab42194891"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2025, fiscal_quarter=3,
            period_end="2024-12-31", publication_date="2025-02-20",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original", unit="usd_mn"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": unit, "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2025Q3-OPEXRATIO-56BPS-001",
    event_family_id="LV-FY2025Q3-opex-ratio-56bps",
    query="管理层称 FY2025Q3 '经营开支与收入比率较去年同期同比下降56个基点至12.1%'。请只用数据库（收入、毛利、经营溢利，经营费用=毛利-经营溢利）分别在 Q3 单季口径与截至12月31日止九个月累计口径下复算该比率的同比变化，判断管理层的说法对应哪个口径、是否成立，并解释比率下降是否等于费用收缩。",
    sources=[src([1, 2, 5, 8, 15], "財務摘要；集團財務表現；九個月經營費用功能分類；第三季度經營費用功能分類")],
    management_reference={"paraphrase": "管理层称严格的开支管理使经营开支/收入比率同比下降56个基点至12.1%；Q3 功能分类经营费用合计 (2,271,687)/(1,988,454) 千美元，9M 合计 (6,482,218)/(5,858,044) 千美元。",
        "evidence_spans": [{"page": 2, "span": "經營開支與收入比率較去年同期同比下降56個基點至12.1%"},
                            {"page": 8, "span": "(2,271,687) (1,988,454)"},
                            {"page": 5, "span": "(6,482,218) (5,858,044)"}]},
    required_inputs=[
        inp("收入", "FY2024Q3/FY2025Q3", [15720.954, 18796.280], "DB facts.revenue，与公告 15,721/18,796 百万美元一致", "可得"),
        inp("毛利", "FY2024Q3/FY2025Q3", [2601.391, 2959.391], "DB facts.gross_profit，与公告 2,601/2,959 一致", "可得"),
        inp("经营溢利", "FY2024Q3/FY2025Q3", [612.937, 687.704], "DB facts.operating_income，与公告 613/688 一致", "可得"),
        inp("经营费用（推导）", "FY2024Q3/FY2025Q3", [1988.454, 2271.687], "毛利-经营溢利；与公告功能分类合计 (1,988,454)/(2,271,687) 千美元完全勾稽", "可由毛利与经营溢利推导", version="derived"),
        inp("收入9M", "FY2024 9M/FY2025 9M", [43030.667, 52093.430], "DB Q1+Q2+Q3 求和，与公告 43,031/52,093 百万一致", "可由季度求和", version="derived"),
        inp("经营费用9M（推导）", "FY2024 9M/FY2025 9M", [5858.044, 6482.218], "三个月(毛利-经营溢利)求和，与公告 (5,858,044)/(6,482,218) 千美元完全勾稽", "可由季度推导求和", version="derived")],
    calculations=[
        {"id": "opex_ratio_q_cur", "formula": "2271.687/18796.280", "result": 0.120858, "tolerance_abs": 0.0005},
        {"id": "opex_ratio_q_pri", "formula": "1988.454/15720.954", "result": 0.126484, "tolerance_abs": 0.0005},
        {"id": "opex_ratio_q_delta_bps", "formula": "(0.120858-0.126484)*10000", "result": -56.26, "tolerance_abs": 1.0, "tolerance_basis": "基点级容差1bp"},
        {"id": "opex_ratio_9m_cur", "formula": "6482.218/52093.430", "result": 0.124434, "tolerance_abs": 0.0005},
        {"id": "opex_ratio_9m_pri", "formula": "5858.044/43030.667", "result": 0.136136, "tolerance_abs": 0.0005},
        {"id": "opex_ratio_9m_delta_bps", "formula": "(0.124434-0.136136)*10000", "result": -117.02, "tolerance_abs": 1.5},
        {"id": "opex_q_yoy", "formula": "2271.687/1988.454-1", "result": 0.14244, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "Q3 单季口径：比率 12.65%→12.09%（约12.1%），同比 -56.3bp，与管理层'下降56个基点至12.1%'一致，即该说法对应单季口径",
        "9M 累计口径：比率 13.61%→12.44%，下降约 117bp 且水平为12.4%，与管理层的-56bp/12.1%不符",
        "比率下降不等于费用收缩：Q3 经营费用绝对额同比 +14.2%，慢于收入 +19.56%，占比才被摊薄"],
        "acceptable_variants": ["12.1% 可写作 12.09%/12.086%；-56bp 可写作 -56.3bp/-0.56pp；9M 变化可写 -117bp/-1.17pp"],
        "must_not_claim": ["不能声称 -56bp 是按 9M 累计口径计算的", "不能把比率下降表述为经营费用金额下降或绝对额降本"],
        "unknowns": ["公告未注明该句使用单季还是累计口径，需复算者自行判定"]},
    db_support={"status": "ready", "db_fields": ["revenue", "gross_profit", "operating_income"],
                "derived": ["经营费用=毛利-经营溢利；9M=季度求和"], "missing": []},
    unknowns=[])

r2 = dict(base, case_id="LV-FY2025Q3-CCC-ONEDAY-001",
    event_family_id="LV-FY2025Q3-ccc-oneday-claim",
    query="管理层称 FY2025Q3 '现金周转期同比改善一天'。请只用数据库的存货、应收账款、应付账款期末余额与九个月 COGS、收入（分母按 275 天折日），在期末时点口径与期初期末平均余额口径下分别复算 DSO/DIO/DPO 与 CCC 的同比变化，判断'改善一天'在两种口径下能否精确复现，并说明改善的驱动构成。",
    sources=[src([1, 2, 15, 17], "財務摘要；集團財務表現；綜合損益表；綜合資產負債表")],
    management_reference={"paraphrase": "财务摘要与集团财务表现称现金结余同比增加14%、现金周转期同比改善一天；9M 销货成本 43,778,440/(35,655,611) 千美元与 DB 季度求和一致。",
        "evidence_spans": [{"page": 2, "span": "現金周轉期則同比改善一天"},
                            {"page": 1, "span": "現金周轉期亦有所改善"},
                            {"page": 15, "span": "(15,836,889 ) (43,778,440 ) (13,119,563 ) (35,655,611 )"}]},
    required_inputs=[
        inp("存货期末", "2023-12-31/2024-12-31", [6218.910, 9147.679], "DB facts.inventory @FY2024Q3/FY2025Q3", "可得"),
        inp("应收期末", "2023-12-31/2024-12-31", [8944.694, 9972.362], "DB facts.accounts_receivable", "可得"),
        inp("应付期末", "2023-12-31/2024-12-31", [10258.486, 13883.842], "DB facts.accounts_payable", "可得"),
        inp("存货期初", "2023-03-31/2024-03-31", [6371.858, 6702.677], "DB facts.inventory @FY2023Q4/FY2024Q4（财年起点）", "可得"),
        inp("应收期初", "2023-03-31/2024-03-31", [7940.378, 8147.695], "DB facts.accounts_receivable @FY23Q4/FY24Q4", "可得"),
        inp("应付期初", "2023-03-31/2024-03-31", [9772.934, 10505.427], "DB facts.accounts_payable @FY23Q4/FY24Q4", "可得"),
        inp("9M COGS", "FY2024 9M/FY2025 9M", [35655.611, 43778.440], "DB Q1+Q2+Q3 cogs 求和，与公告 (35,655,611)/(43,778,440) 千美元勾稽", "可由季度求和", version="derived"),
        inp("9M 收入", "FY2024 9M/FY2025 9M", [43030.667, 52093.430], "DB 季度求和，与公告 43,031/52,093 百万一致", "可由季度求和", version="derived")],
    calculations=[
        {"id": "dio_pt_cur", "formula": "9147.679/(43778.440/275)", "result": 57.4623, "tolerance_abs": 0.5},
        {"id": "dio_pt_pri", "formula": "6218.910/(35655.611/275)", "result": 47.9644, "tolerance_abs": 0.5},
        {"id": "dso_pt_cur", "formula": "9972.362/(52093.430/275)", "result": 52.6439, "tolerance_abs": 0.5},
        {"id": "dso_pt_pri", "formula": "8944.694/(43030.667/275)", "result": 57.1637, "tolerance_abs": 0.5},
        {"id": "dpo_pt_cur", "formula": "13883.842/(43778.440/275)", "result": 87.2132, "tolerance_abs": 0.5},
        {"id": "dpo_pt_pri", "formula": "10258.486/(35655.611/275)", "result": 79.1203, "tolerance_abs": 0.5},
        {"id": "ccc_pt_cur", "formula": "52.6439+57.4623-87.2132", "result": 22.893, "tolerance_abs": 0.5},
        {"id": "ccc_pt_pri", "formula": "57.1637+47.9644-79.1203", "result": 26.0077, "tolerance_abs": 0.5},
        {"id": "ccc_pt_delta", "formula": "22.893-26.0077", "result": -3.115, "tolerance_abs": 0.5, "tolerance_basis": "天级容差0.5天"},
        {"id": "dio_avg_cur", "formula": "((6702.677+9147.679)/2)/(43778.440/275)", "result": 49.783, "tolerance_abs": 0.5},
        {"id": "dio_avg_pri", "formula": "((6371.858+6218.910)/2)/(35655.611/275)", "result": 48.5542, "tolerance_abs": 0.5},
        {"id": "dso_avg_cur", "formula": "((8147.695+9972.362)/2)/(52093.430/275)", "result": 47.8277, "tolerance_abs": 0.5},
        {"id": "dso_avg_pri", "formula": "((7940.378+8944.694)/2)/(43030.667/275)", "result": 53.9545, "tolerance_abs": 0.5},
        {"id": "dpo_avg_cur", "formula": "((10505.427+13883.842)/2)/(43778.440/275)", "result": 76.6022, "tolerance_abs": 0.5},
        {"id": "dpo_avg_pri", "formula": "((9772.934+10258.486)/2)/(35655.611/275)", "result": 77.2479, "tolerance_abs": 0.5},
        {"id": "ccc_avg_delta", "formula": "(47.8277+49.7830-76.6022)-(53.9545+48.5542-77.2479)", "result": -4.252, "tolerance_abs": 0.5},
        {"id": "inv_yoy", "formula": "9147.679/6218.910-1", "result": 0.47094, "tolerance_abs": 0.005},
        {"id": "cogs9_yoy", "formula": "43778.440/35655.611-1", "result": 0.22781, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "方向成立：期末时点口径 CCC 同比 -3.11 天（26.01→22.89），平均余额口径 -4.25 天（25.26→21.01），均为改善",
        "'一天'的精确幅度在两种自然口径下都无法复现（改善 3~4 天），该说法口径敏感",
        "驱动构成：期末口径下存货天数 +9.50 天（存货余额 +47.1% 快于 9M COGS +22.8%），靠应收 -4.52 天、应付 +8.09 天对冲；平均口径下存货天数仅 +1.23 天、应收 -6.13 天主导",
        "结论：'改善'成立，'一天'与 DB 可复算口径不符，需标注管理层口径未公开"],
        "acceptable_variants": ["天数口径可用 275 天或 270 天年化（270 天下单个天数约放大 1.8%，CCC 变化量级不变）；结论方向一致即可"],
        "must_not_claim": ["不能声称存货周转天数下降（两种口径下都上升）", "不能声称'改善一天'被 DB 数据精确证实", "不得把时点口径与平均口径的绝对天数混用比较"],
        "unknowns": ["公告未披露其 CCC 的分子分母口径（时点/平均、期间天数、是否含其他应收应付）"]},
    db_support={"status": "ready", "db_fields": ["inventory", "accounts_receivable", "accounts_payable", "cogs", "revenue"],
                "derived": ["9M=季度求和；平均余额=期初期末均值"], "missing": []},
    unknowns=[],
    related_note="与 LV-FY2025Q4-CCC-CLAIM-001（全年口径'存货天数+6天主因存货'争议）为相邻但不同的管理层声明，不并入同一 family。")

r3 = dict(base, case_id="LV-FY2025Q3-NETCASH-GAP-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2025Q3-netcash-definition-gap",
    query="公告披露 2024-12-31 净现金 3.93 亿美元：银行存款、现金及现金等价物 4,071 百万美元，减短期贷款 91、票据 3,015、可换股债券 572。请用数据库 cash、short_term_debt、long_term_debt 复算净现金，解释为何得到约 256 百万而非 393 百万，并指出债务合计与分类、现金科目口径各自的核对结果与 DB 字段缺口。",
    sources=[src([2, 13, 14], "集團財務表現；流動資金及財務資源；淨現金狀況表")],
    management_reference={"paraphrase": "公告净现金表：现金 4,071−91−3,015−572=393 百万美元；贷款权益比率 0.60；现金构成 83% 银行存款/17% 货币基金。",
        "evidence_spans": [{"page": 2, "span": "淨現金結餘強勁，達3.93億美元"},
                            {"page": 14, "span": "淨現金 393 6"},
                            {"page": 14, "span": "銀行存款、現金及現金等價物 4,071 3,626"}]},
    required_inputs=[
        inp("现金", "2024-12-31", [3934.448], "DB facts.cash；公告口径 4,071", "可得但口径存疑"),
        inp("短期债务", "2024-12-31", [1056.572], "DB facts.short_term_debt；公告短期贷款仅 91", "可得"),
        inp("长期债务", "2024-12-31", [2621.429], "DB facts.long_term_debt；公告票据+可转债 3,587", "可得"),
        {"metric": "公告债务明细", "entity": "联想集团", "period": "2024-12-31", "raw_value": [91, 3015, 572],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": "original",
         "source": "公告 p14 净现金表（短期贷款/票据/可换股债券）", "db": "缺：无借款类别拆分科目"}],
    calculations=[
        {"id": "db_net_cash", "formula": "3934.448-1056.572-2621.429", "result": 256.447, "tolerance_abs": 0.005},
        {"id": "ann_net_cash", "formula": "4071-91-3015-572", "result": 393.0, "tolerance_abs": 0.01},
        {"id": "debt_total_db", "formula": "1056.572+2621.429", "result": 3678.001, "tolerance_abs": 0.002},
        {"id": "debt_total_ann", "formula": "91+3015+572", "result": 3678.0, "tolerance_abs": 0.01},
        {"id": "cash_gap", "formula": "4071-3934.448", "result": 136.552, "tolerance_abs": 0.01},
        {"id": "gap_identity", "formula": "(393-256.447)-(136.552-0.001)", "result": 0.0, "tolerance_abs": 0.01}],
    scorable_answer={"must_include": [
        "DB 净现金 = 3934.448−1056.572−2621.429 = 256.447 百万，低于公告 393",
        "债务侧完全勾稽：DB short+long_term_debt 合计 3,678.001 ≈ 公告 91+3,015+572 = 3,678（差异仅流动/非流动划分与类别命名，不影响合计）",
        "缺口全部来自现金科目口径：公告'银行存款、现金及现金等价物' 4,071 vs DB cash 3,934.448，差 136.552（约3.4%），提示 DB 口径更窄",
        "结论：复算公告净现金必须用 p14 明细；应登记 cash 口径与借款类别拆分两个字段缺口"],
        "acceptable_variants": ["缺口可表述为 393−256.4=136.6≈现金口径差 136.552，舍入内一致"],
        "must_not_claim": ["不能声称 DB 债务口径含租赁负债导致缺口（债务合计与公告一致）", "不能声称 393 可由 DB 直接复现", "不能把 -136.6 现金差直接断言为数据错误而未标注待核"],
        "unknowns": ["DB cash 的精确取数口径（PDF 现金附注未在本轮抽取范围）", "票据/可转债/租赁负债完整拆分仅见于年报附注12"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["cash", "short_term_debt", "long_term_debt"],
                "missing": ["现金口径对账项（如到期日>3个月的银行存款）", "借款类别拆分（贷款/票据/可换股债券/租赁负债）"]},
    unknowns=[])

r4 = dict(base, case_id="LV-FY2025Q3-SSG-PROFIT-SHARE-001",
    event_family_id="LV-FY2025-ssg-profit-share",
    query="管理层称方案服务业务集团（SSG）'分部溢利占所有三个业务集团的综合分部溢利总额的31%'。请用公告分部表复算该比例（注意抵销前口径），解释收入占比约11%的 SSG 为何贡献31%的分部溢利，并结合 ISG 本季经营溢利仅 +1,002 千美元（去年同期 -37,730）评估分部利润集中度的盈利质量含义。",
    sources=[src([2, 3, 10], "集團財務表現；各產品業務集團表現；可報告分部收入與經營溢利表")],
    management_reference={"paraphrase": "公告称 SSG 收入同比增12%至23亿美元、经营溢利率20%、分部溢利占三部综合分部溢利总额31%；Q3 分部表（千美元）：IDG 13,784,257/999,855，ISG 3,938,478/1,002，SSG 2,256,863/459,422，抵销前合计 19,979,598/1,460,279。",
        "evidence_spans": [{"page": 2, "span": "分部溢利佔所有三個業務集團的綜合分部溢利總額"},
                            {"page": 10, "span": "基礎設施方案業務集團 3,938,478 1,002 2,473,303 (37,730)"},
                            {"page": 10, "span": "方案服務業務集團 2,256,863 459,422 2,020,630 412,063"}]},
    required_inputs=[
        {"metric": "IDG 分部收入/溢利", "entity": "智能设备业务集团", "period": "FY2025Q3", "raw_value": [13784.257, 999.855],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10 分部表", "db": "缺：无分部科目"},
        {"metric": "ISG 分部收入/溢利", "entity": "基础设施方案业务集团", "period": "FY2025Q3 与 FY2024Q3", "raw_value": [[3938.478, 1.002], [2473.303, -37.730]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10", "db": "缺"},
        {"metric": "SSG 分部收入/溢利", "entity": "方案服务业务集团", "period": "FY2025Q3 与 FY2024Q3", "raw_value": [[2256.863, 459.422], [2020.630, 412.063]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10", "db": "缺"},
        {"metric": "抵销前三部合计溢利", "entity": "三部合計", "period": "FY2025Q3", "raw_value": [1460.279],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10 合計 19,979,598/1,460,279", "db": "缺"}],
    calculations=[
        {"id": "ssg_profit_share", "formula": "459.422/1460.279", "result": 0.31461, "tolerance_abs": 0.002},
        {"id": "idg_profit_share", "formula": "999.855/1460.279", "result": 0.68470, "tolerance_abs": 0.002},
        {"id": "ssg_rev_share", "formula": "2256.863/19979.598", "result": 0.11296, "tolerance_abs": 0.002},
        {"id": "ssg_margin", "formula": "459.422/2256.863", "result": 0.20357, "tolerance_abs": 0.002},
        {"id": "idg_margin", "formula": "999.855/13784.257", "result": 0.07254, "tolerance_abs": 0.002},
        {"id": "isg_margin", "formula": "1.002/3938.478", "result": 0.00025, "tolerance_abs": 0.0005},
        {"id": "isg_rev_yoy", "formula": "3938.478/2473.303-1", "result": 0.59240, "tolerance_abs": 0.002},
        {"id": "ssg_rev_yoy", "formula": "2256.863/2020.630-1", "result": 0.11691, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "31% 可复算：459.422/1,460.279=31.46%（抵销前分部溢利合计口径）",
        "收入占比11.30%却贡献31%利润的原因是溢利率差：SSG 20.4% vs IDG 7.3% vs ISG 0.03%",
        "ISG 收入 +59.2% 但经营溢利仅 +1.0 百万美元（刚扭亏、近零），分部利润集中于 IDG 68.5%+SSG 31.5%≈99.9%，增长最快的业务几乎不贡献利润",
        "分母口径须为抵销前'合計'1,460.279，而非抵销后集团经营溢利 1,087.610"],
        "acceptable_variants": ["31% 可写 31.5%/31.46%；集中度可表述为 IDG+SSG≈100%"],
        "must_not_claim": ["不能用抵销后收入 18,796.280 或集团经营溢利作分母得 42.2%/33.2% 并据此说管理层数字错误（口径即公告'合計'行）", "不能声称 ISG 已成为利润引擎", "不能把分部经营溢利与集团经营溢利混淆（存在未分配项目）"],
        "unknowns": ["分部溢利与集团经营溢利之间有未分配项目与抵销，公告未提供逐部对账"]},
    db_support={"status": "needs_numeric_data", "db_fields": [],
                "missing": ["分部收入与分部经营溢利（DB 28 科目无分部层）"]},
    unknowns=[])

r5 = dict(base, case_id="LV-FY2025Q3-NONPC-46PCT-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2025Q3-nonpc-share-identifiability",
    query="公告称'非个人电脑业务目前占三个业务集团总收入的46%'。请判断该比例能否用公告披露的三个业务集团分部收入数据验证；给出可识别部分的上界与需要 IDG 内部拆分才能补齐的缺口，并说明正确的回答策略。",
    sources=[src([2, 3, 10], "集團財務表現；各產品業務集團表現；可報告分部收入表")],
    management_reference={"paraphrase": "集团财务表现称非PC业务占三个业务集团总收入46%；分部表仅提供 IDG/ISG/SSG 三部收入，IDG 内部手机/平板/PC 拆分未在该季报披露。",
        "evidence_spans": [{"page": 2, "span": "非個人電腦業務目前佔三個業務集團總收入的46%"},
                            {"page": 10, "span": "智能設備業務集團 13,784,257 999,855 12,361,570 911,297"}]},
    required_inputs=[
        {"metric": "三部收入（抵销前）", "entity": "IDG/ISG/SSG", "period": "FY2025Q3", "raw_value": [13784.257, 3938.478, 2256.863],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10", "db": "缺：无分部科目"},
        {"metric": "三部收入合计", "entity": "合計(抵销前)", "period": "FY2025Q3", "raw_value": [19979.598],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10 合計行", "db": "缺"}],
    calculations=[
        {"id": "identifiable_upper_bound", "formula": "(3938.478+2256.863)/19979.598", "result": 0.31008, "tolerance_abs": 0.002},
        {"id": "implied_idg_nonpc_abs", "formula": "0.46*19979.598-(3938.478+2256.863)", "result": 2995.274, "tolerance_abs": 0.02, "tolerance_basis": "反推值，依赖46%本身"},
        {"id": "implied_idg_nonpc_share", "formula": "2995.274/13784.257", "result": 0.21730, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "46% 无法由现有披露复算：公开分部口径下可识别的非PC收入上界仅为 ISG+SSG 合计 31.0%",
        "其余约15个百分点必然落在 IDG 内部（智能手机、平板等非PC），而 IDG 内部产品线拆分未在该季报披露",
        "正确策略：声明证据不足、给出 31.0% 可识别部分与缺口性质，不宣称 46% 错误也不宣称已验证"],
        "acceptable_variants": ["上界可表述为 ISG 19.71%+SSG 11.30%=31.01%；缺口可写'约15pp 依赖 IDG 内部数据'"],
        "must_not_claim": ["不能用 31% 上界反驳 46% 为虚（46% 含 IDG 非PC，口径不冲突）", "不得把'不可验证'改写成'已验证'或'管理层夸大'", "不能用集团总收入(抵销后) 18,796.280 作分母混用口径"],
        "unknowns": ["IDG 内 PC/非PC 拆分", "46% 的原始计算底稿未披露"]},
    db_support={"status": "boundary_only", "db_fields": [],
                "missing": ["分部收入", "IDG 内部产品线收入拆分"]},
    unknowns=[])

recs = [r1, r2, r3, r4, r5]
with open(".cache/candidates/FY25Q3.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2025Q3-OPEXRATIO-56BPS-001"] = {"tags": ["费用率", "口径判定", "摊薄vs降本", "管理层声明核验"],
    "difficulty_rationale": "'下降56个基点至12.1%'混在9M叙述里，但只有单季口径能复现（9M口径为-117bp）；须自建推导费用并识别占比摊薄不等于费用下降"}
meta["LV-FY2025Q3-CCC-ONEDAY-001"] = {"tags": ["现金周转期", "营运资本", "口径敏感性", "管理层声明核验"],
    "difficulty_rationale": "'改善一天'在时点(-3.1天)与平均(-4.3天)两种口径下幅度都对不上，且时点口径下存货天数反而+9.5天靠应付对冲——方向对、幅度与构成都需口径判定"}
meta["LV-FY2025Q3-NETCASH-GAP-001"] = {"tags": ["净现金", "科目口径", "勾稽缺口"],
    "difficulty_rationale": "DB债务合计3,678.001与公告逐项完全勾稽但净现金差136.6百万，缺口须逐科目对账定位到cash口径，整体比对会误判为债务问题"}
meta["LV-FY2025Q3-SSG-PROFIT-SHARE-001"] = {"tags": ["分部分析", "盈利质量", "抵销前口径"],
    "difficulty_rationale": "31%的分母是抵销前分部合计而非集团经营溢利；还须解释收入占比11%与利润占比31%之间的溢利率差结构"}
meta["LV-FY2025Q3-NONPC-46PCT-001"] = {"tags": ["不可识别性", "拒答边界", "分部披露"],
    "difficulty_rationale": "46%主张超出分部披露可验证范围（可识别上界31.0%），正确行为是给出可识别部分与缺口而非判对错"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

import unicodedata
def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")
txt = open(".cache/lenovo_pdf_text/FY25Q3_200220251201.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

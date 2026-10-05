# -*- coding: utf-8 -*-
"""FY2024Q2（2023-11-16 中期业绩公告，截至2023-09-30）归因候选：写 .cache/candidates/FY24Q2.jsonl + meta。"""
import json
import unicodedata

SRC = {"pdf": "FY24Q2_161120231219.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2023/1116/2023111600159_c.pdf",
       "sha256": "fe97431d809fc30288f840696841f82908243601bf61b1cc969307db6dabf1e6"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2024, fiscal_quarter=2,
            period_end="2023-09-30", publication_date="2023-11-16",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2024Q2-Q2-QOQ-REBOUND-001",
    event_family_id="LV-FY2024Q2-q2-qoq-rebound",
    query="公告称第二财政季度'销售按季录得12%的显著增长，超出过去十年的平均增长率9%'，同一段又称'上半财年收入同比下降20%…至270亿美元'。请用 DB 收入复算 Q2 环比、Q2 同比与 H1 同比三个口径并说明各自对应哪句声明；'十年平均9%'在 DB 可见区间（FY2022 起）能否验证？",
    sources=[src([1, 2], "財務摘要-bullet；亮點")],
    management_reference={"paraphrase": "摘要称Q2销售按季+12%超出十年平均9%；亮点段复述按季+12%超过9%十年平均，并称H1销售同比降20%（剔除汇率降18%）至270亿美元。财务摘要表：Q2 收入 14,410 对 17,090（-16%）；H1 27,310 对 34,045（-20%）。",
        "evidence_spans": [{"page": 1, "span": "本集團第二財政季度銷售按季錄得12% 的顯著增長，超出過去十年的平均增長率 9%"},
                            {"page": 2, "span": "9% 的十年平均水準。然而，本集團銷售同比下降 20%（或扣除匯率影響後下降 18% ）至270億美元"}]},
    required_inputs=[
        inp("收入", "FY2024Q2/FY2024Q1", [14409.786, 12899.927], "DB facts.revenue（公告表 14,410/上季同口径）", "可得"),
        inp("收入", "FY2023Q2/FY2023Q1", [17089.537, 16955.618], "DB facts.revenue", "可得"),
        inp("收入", "FY2022Q2/FY2022Q1", [17868.679, 16929.247], "DB 可见最早两年（用于'9%十年均值'的可见子样本）", "可得"),
        inp("H1 收入合计", "FY2024H1/FY2023H1", [27309.713, 34045.155], "DB Q1+Q2 求和（对公告 27,310/34,045）", "可由科目求和", version="derived")],
    calculations=[
        {"id": "q2_qoq", "formula": "14409.786/12899.927-1", "result": 0.11704, "tolerance_abs": 0.002},
        {"id": "q2_yoy", "formula": "14409.786/17089.537-1", "result": -0.15681, "tolerance_abs": 0.002},
        {"id": "h1_yoy", "formula": "27309.713/34045.155-1", "result": -0.19784, "tolerance_abs": 0.002},
        {"id": "visible_q2_qoq_avg", "formula": "mean(FY22 5.549%, FY23 0.790%, FY24 11.704%)", "result": 0.06014, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "12% 是环比口径：14,409.786/12,899.927-1 = +11.70%，四舍五入为 12%；同季同比仍是 -15.7%（对 FY23Q2 的 17,089.537，公告表 -16%）",
        "-20% 与 270亿美元都是 H1 口径：27,309.713/34,045.155-1 = -19.78%，H1 合计 27,309.713≈270亿",
        "'过去十年平均增长率9%'不可由 DB 验证：DB 收入自 FY2022Q1 起，可见 3 个 Q2 环比均值仅 +6.01%，10 年窗口是数据边界",
        "两句并不矛盾：一句按季复苏叙事（环比），一句同比规模叙事（H1），须按期间口径分开归因"],
        "acceptable_variants": ["+11.70% 可写约12%/-15.7% 可写约-16%；-19.78% 可写约-20%"],
        "must_not_claim": ["不能把 12% 说成同比增长（同季同比为负）", "不能宣称用 DB 复算出了 9% 十年均值", "不能用 H1 -20% 否定 Q2 环比反弹，也不能用环比反弹掩盖同比仍在收缩", "剔除汇率后 -18% 不在可复算范围（无 FX 数据）"],
        "unknowns": ["十年平均的样本定义（季度环比或年度同比、是否剔汇）公告未说明，仅其可见子样本可核"]},
    db_support={"status": "ready", "db_fields": ["revenue"], "derived": ["H1 合计、环比/同比增速", "可见子样本均值"],
                "missing": ["FY2022 前收入（十年窗口）", "汇率影响拆分"]},
    unknowns=[],
    related_note="与 LV-FY2025Q1-DOUBLE-DIGIT-CLAIM-001、LV-FY2024Q3-YOY-STREAK-001 同属增长叙事簇：本案验证'环比反弹+同比下滑'双口径共存，那两案验证'首次/连续'断点声明。")

r2 = dict(base, case_id="LV-FY2024Q2-CCC-INV2B-001",
    event_family_id="LV-FY2024Q2-ccc-inv2b",
    query="公告称'第二季度的现金周转期同比改善一天至负四天''季末结余较一年前减少逾20亿美元''应收账款及库存天数合计同比改善九天，被应付账款天数减少所抵销'。请用 DB 按 D=91 复算 FY2024Q2 对 FY2023Q2 的存货余额降幅与 DSO/DIO/DPO（时点与平均两口径），逐项判定这三句：哪句直接为真、哪句幅度可复现、哪句只在某一个口径下成立。",
    sources=[src([1, 2], "財務摘要-bullet；亮點")],
    management_reference={"paraphrase": "摘要与亮点称：Q2 现金周转期同比改善一天至负四天；库存稳步改善，季末结余较一年前减少逾20亿美元；应收及库存天数合计同比改善九天，被应付账款天数减少所抵销。",
        "evidence_spans": [{"page": 1, "span": "第二季度的現金周轉期同比改善一天至負四天"},
                            {"page": 1, "span": "季末結餘較一年前減少逾20億美元"},
                            {"page": 2, "span": "現金周轉期維持負數，為負四天，同比減少一天"},
                            {"page": 2, "span": "應收賬款及庫存天數合計同比改善九天，被應付賬款天數減少所抵銷"}]},
    required_inputs=[
        inp("收入/销售成本", "FY2024Q2/FY2023Q2", [[14409.786, 11888.026], [17089.537, 14212.963]], "DB facts.revenue、facts.cogs（对公告毛利 2,522/2,877）", "可得"),
        inp("应收/存货/应付（期末）", "2023-09-30", [7809.892, 6169.642, 10872.335], "DB 三科目期末", "可得"),
        inp("应收/存货/应付（期末）", "2022-09-30", [9786.759, 8417.891, 12348.689], "DB 三科目期末", "可得"),
        inp("应收/存货/应付（期初）", "2023-06-30/2022-06-30", [[7474.309, 5907.201, 9284.233], [11646.783, 8867.663, 13609.606]], "DB FY2024Q1/FY2023Q1 期末（平均口径用）", "可得")],
    calculations=[
        {"id": "inv_yoy_drop", "formula": "8417.891-6169.642", "result": 2248.249, "tolerance_abs": 0.01},
        {"id": "dso_pt", "formula": "7809.892/(14409.786/91) 对 9786.759/(17089.537/91)", "result": [49.3207, 52.1135], "tolerance_abs": 0.02},
        {"id": "dio_pt", "formula": "6169.642/(11888.026/91) 对 8417.891/(14212.963/91)", "result": [47.2271, 53.8964], "tolerance_abs": 0.02},
        {"id": "dpo_pt", "formula": "10872.335/(11888.026/91) 对 12348.689/(14212.963/91)", "result": [83.2251, 79.0638], "tolerance_abs": 0.02},
        {"id": "ar_inv_delta_pt", "formula": "(49.3207-52.1135)+(47.2271-53.8964)", "result": -9.4621, "tolerance_abs": 0.05},
        {"id": "dpo_delta_pt", "formula": "83.2251-79.0638", "result": 4.1613, "tolerance_abs": 0.02},
        {"id": "ccc_pt", "formula": "49.3207+47.2271-83.2251 对 52.1135+53.8964-79.0638", "result": [13.3227, 26.9461], "tolerance_abs": 0.02},
        {"id": "ccc_delta_pt", "formula": "13.3227-26.9461", "result": -13.6235, "tolerance_abs": 0.05},
        {"id": "ar_inv_delta_avg", "formula": "(48.2610-57.0657)+(46.2227-55.3363)", "result": -17.9183, "tolerance_abs": 0.05},
        {"id": "dpo_delta_avg", "formula": "77.1469-83.1004", "result": -5.9535, "tolerance_abs": 0.02},
        {"id": "ccc_delta_avg", "formula": "17.3368-29.3016", "result": -11.9648, "tolerance_abs": 0.05}],
    scorable_answer={"must_include": [
        "'季末结余较一年前减少逾20亿美元'直接为真：存货 8,417.891→6,169.642 减 2,248.249 百万美元（-26.7%），是 CCC 系列声明中少见的可全量验证正项",
        "'合计改善九天'在时点口径复现：ΔDSO+ΔDIO = -2.79 + -6.67 = -9.46 天；但平均口径该合计为 -17.92 天，对不上'九'",
        "'被应付账款天数减少所抵销'只在平均口径成立（ΔDPO = -5.95 天）；时点口径 DPO 反而 +4.16 天，两个成分同向改善、CCC 净改善 -13.62 天",
        "'负四天'水平（时点 +13.32/平均 +17.34）与'改善一天'变化（实际 -13.6/-12.0）两口径均不可复现，管理层 CCC 定义与标准口径不同",
        "两句各自取一个口径才分别部分成立——没有任何单一口径让整句同时自洽"],
        "acceptable_variants": ["-9.46 天可写约九天；存货降幅可写 22.5 亿/26.7%"],
        "must_not_claim": ["不能因'九天'对上就背书整句（同句 DPO 部分在该口径下方向相反）", "不能宣称 DB 可复现负四天 CCC 水平", "不能把存货余额降幅与存货天数改善混同（前者 -26.7% 直接可验，后者依赖口径）"],
        "unknowns": ["管理层现金周转期（负四天）的定义与计算基础未披露", "'一天'改善是否按 TTM 口径不可考（DB 无 FY2022 前完整流数据）"]},
    db_support={"status": "ready", "db_fields": ["revenue", "cogs", "accounts_receivable", "inventory", "accounts_payable"],
                "derived": ["存货同比降幅", "DSO/DIO/DPO/CCC 两口径（D=91）"], "missing": []},
    unknowns=[],
    related_note="CCC 声明序列第四例（FY24Q3 '+14天'符号反、FY24Q4 '负4天/改善12天'水平失败、本案'负4天/一天/九天/应付减少'四成分跨口径拼合、FY25Q1 '+11天'、FY25Q3/Q4 案），各案期间与失败模式不同，独立保留并互引。")

r3 = dict(base, case_id="LV-FY2024Q2-SEG-H1-CLAIMS-001",
    event_family_id="LV-FY2024Q2-seg-h1-claims",
    query="FY2024Q2 公告的分部叙事全部用半年口径：'SSG 收入及经营溢利同比升14%及7%''ISG 收入减少17%、经营亏损1.14亿美元''IDG 收入及经营溢利下跌22%及28%'，又称 IDG 经营溢利率'回升至7.4%、按季增长102个基点'、SSG'继续录得21%经营溢利率'。请用公告两张分部表（六个月/三个月）逐项判定这些数字各属什么口径并复算；说明换成 Q2 单季口径后三个同比增速各变成什么；并解释分部利润合计为何远大于集团经营溢利。",
    sources=[src([1, 2, 3, 8, 11], "財務摘要-bullet；集團財務表現；各業務集團表現；六個月分部表；三個月分部表")],
    management_reference={"paraphrase": "六个月分部表（千美元）：IDG 21,775,171/1,496,550 对 27,989,687/2,089,397；ISG 3,915,525/(113,855) 对 4,700,436/47,313；SSG 3,631,308/744,511 对 3,177,071/696,910。三个月分部表：IDG 11,514,559/846,793 对 13,715,844/1,019,886；ISG 2,001,759/(53,438) 对 2,614,363/36,002；SSG 1,918,076/383,371 对 1,721,199/367,568。摘要与 MD&A 称上述 14%/7%、17%/1.14亿、22%/28%、7.4%+102基点、21% 等。",
        "evidence_spans": [{"page": 1, "span": "方案服務業務集團的收入及經營溢利分別同比上升14% 及7%"},
                            {"page": 1, "span": "基礎設施方案業務集團收入同比減少 17%，經營虧損為 1.14億美元"},
                            {"page": 2, "span": "營溢利率回升至 7.4%，按季增長102個基點，收入則按季增長 12%"},
                            {"page": 3, "span": "繼續錄得 21% 的強韌經營溢利率"},
                            {"page": 8, "span": "智能設備業務集團 21,775,171 1,496,550 27,989,687 2,089,397"},
                            {"page": 8, "span": "基礎設施方案業務集團 3,915,525 (113,855) 4,700,436 47,313"},
                            {"page": 8, "span": "方案服務業務集團 3,631,308 744,511 3,177,071 696,910"},
                            {"page": 11, "span": "智能設備業務集團 11,514,559 846,793 13,715,844 1,019,886"},
                            {"page": 11, "span": "基礎設施方案業務集團 2,001,759 (53,438) 2,614,363 36,002"},
                            {"page": 11, "span": "方案服務業務集團 1,918,076 383,371 1,721,199 367,568"}]},
    required_inputs=[
        {"metric": "IDG 分部收入/经营溢利", "entity": "智能设备业务集团", "period": "FY2024H1 与 FY2023H1、FY2024Q2 与 FY2023Q2", "raw_value": [[21775.171, 1496.550], [27989.687, 2089.397], [11514.559, 846.793], [13715.844, 1019.886]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8 六个月表、p11 三个月表", "db": "缺：无分部科目"},
        {"metric": "ISG 分部收入/经营溢利", "entity": "基础设施方案业务集团", "period": "同上四期", "raw_value": [[3915.525, -113.855], [4700.436, 47.313], [2001.759, -53.438], [2614.363, 36.002]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8/p11", "db": "缺"},
        {"metric": "SSG 分部收入/经营溢利", "entity": "方案服务业务集团", "period": "同上四期", "raw_value": [[3631.308, 744.511], [3177.071, 696.910], [1918.076, 383.371], [1721.199, 367.568]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p8/p11", "db": "缺"},
        inp("H1 集团收入", "FY2024H1/FY2023H1", [27309.713, 34045.155], "DB Q1+Q2 求和（勾稽分部合计-抵销）", "可得", version="derived"),
        inp("H1 集团经营溢利", "FY2024H1", [904.075], "DB operating_income Q1+Q2（对公告 904,075 千美元精确一致）", "可得", version="derived")],
    calculations=[
        {"id": "idg_h1_rev_yoy", "formula": "21775.171/27989.687-1", "result": -0.22203, "tolerance_abs": 0.003},
        {"id": "idg_h1_op_yoy", "formula": "1496.550/2089.397-1", "result": -0.28374, "tolerance_abs": 0.003},
        {"id": "isg_h1_rev_yoy", "formula": "3915.525/4700.436-1", "result": -0.16699, "tolerance_abs": 0.003},
        {"id": "ssg_h1_rev_yoy", "formula": "3631.308/3177.071-1", "result": 0.14297, "tolerance_abs": 0.003},
        {"id": "ssg_h1_op_yoy", "formula": "744.511/696.910-1", "result": 0.06830, "tolerance_abs": 0.003},
        {"id": "idg_q2_yoy_trap", "formula": "11514.559/13715.844-1（对照 ISG -0.23432、SSG +0.11438）", "result": -0.16049, "tolerance_abs": 0.003},
        {"id": "idg_q1_from_h1", "formula": "IDG Q1=H1-Q2：收入 21775.171-11514.559=10260.612、溢利 649.757；毛利率 649.757/10260.612", "result": 0.06333, "tolerance_abs": 0.003},
        {"id": "idg_margin_qoq_bps", "formula": "(846.793/11514.559-649.757/10260.612)*10000", "result": 102.16, "tolerance_abs": 0.5},
        {"id": "isg_q2_qoq", "formula": "2001.759/(3915.525-2001.759)-1", "result": 0.04598, "tolerance_abs": 0.003},
        {"id": "isg_q2_loss_cut", "formula": "1-53.438/(113.855-53.438)", "result": 0.11551, "tolerance_abs": 0.003},
        {"id": "ssg_margin_check", "formula": "744.511/3631.308 与 383.371/1918.076", "result": [0.20503, 0.19987], "tolerance_abs": 0.002},
        {"id": "rev_elim_tie", "formula": "(21775.171+3915.525+3631.308)-27309.713（抵销额）", "result": 2012.291, "tolerance_abs": 0.01},
        {"id": "op_unallocated", "formula": "(1496.550-113.855+744.511)-904.075", "result": 1223.131, "tolerance_abs": 0.01}],
    scorable_answer={"must_include": [
        "14%/7%、17%/1.14亿、22%/28% 全部是六个月（H1）口径且与 p8 分部表精确吻合：IDG -22.20%/-28.37%、ISG -16.70%（'17%'为四舍五入）/亏 113.855、SSG +14.30%/+6.83%（'7%'为舍入）",
        "换成 p11 三个月表，Q2 单季同比为 IDG -16.05%、ISG -23.43%、SSG +11.44%——三个头条增速在单季口径下全部对不上，声明必须按 H1 归因",
        "102 个基点可精确复现：IDG 分部溢利率 Q1 6.333%（H1-Q2 差分）→Q2 7.354%，+102.2bps；'按季增长12%'为 +12.22% ✓",
        "ISG 单季'按季+5%'实为 +4.60%、'亏损按季缩减12%'实为 -11.55%（Q1 亏 60.417→Q2 亏 53.438），方向真、数值取整偏 aggressive",
        "SSG '21%溢利率'两口径都不是 21%：H1 20.50%（half-up 勉强进 21）、Q2 仅 19.99%——是摘要里最松的四舍五入",
        "DB 无分部科目；收入侧可勾稽：三分部 H1 合计 29,322.004 - 抵销 2,012.291 = DB H1 收入 27,309.713；利润侧分部合计 2,127.206 比集团经营溢利 904.075 高 1,223.131（未分配项目/对冲负债等），分部利润不能直接对标集团"],
        "acceptable_variants": ["-16.70% 可写约17%；+6.83% 可写约7%；-11.55% 可写约12%"],
        "must_not_claim": ["不能用三个月表验证 14%/17%/22% 后宣称公告数字错误（口径错配）", "不能把 SSG 21% 说成分部表精确数字", "不能说分部经营溢利合计等于集团经营溢利（差 1,223.1 未分配项）", "不能声称 DB 可直接复算分部表（无分部科目）"],
        "unknowns": ["分部溢利口径为'可分攤之分部經營溢利'，未分配项目明细见公告附注但未按分部还原"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["revenue", "operating_income", "gross_profit"],
                "derived": ["H1 集团合计（勾稽抵销后分部表合计）"],
                "missing": ["分部收入与分部经营溢利", "抵销与未分配项目明细"]},
    unknowns=[],
    related_note="分部期间口径簇第三例（FY25Q2 ISG-H1-CLAIMS 判定 65%/36% 的口径、FY24Q4 SEG-FYVSQ4 全年vs第四季、本案 H1 vs Q2 且三增速全失效）；IDG '接近历史峰值'跨公告分部历史问题仍按 FY24Q3 处理不在此验证。")

r4 = dict(base, case_id="LV-FY2024Q2-ATTR-60-154BP-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2024Q2-attributable-vs-db",
    query="公告称 H1'权益持有人应占溢利同比减少60%至4.26亿美元'，MD&A 又称'净利率较去年同期下降154个基点'。DB 只有含非控的期内溢利（net_income）与收入。请复算：把 DB net_income 当'应占溢利'会得到什么增速、与公告 60% 差多少；用公告应占口径重算净利率降幅能否精确复现 154 个基点；并把差异拆解到非控损益项，说明与 FY2025Q2 非控案例的方向关系。",
    sources=[src([1, 2, 16], "財務摘要-bullet；集團財務表現；綜合損益表")],
    management_reference={"paraphrase": "摘要称 H1 权益持有人应占溢利同比减少60%至4.26亿美元；MD&A 称净利润率较去年同期下降154个基点。损益表（千美元）：期内溢利 Q2 289,053/H1 472,467、上年 553,841/1,093,312；应占 249,240/425,766 对 541,207/1,056,914；其他非控 39,813/46,701 对 12,634/36,398。",
        "evidence_spans": [{"page": 1, "span": "上半財年收入同比下降20%，權益持有人應佔溢利因而同比減少60% 至4.26億美元"},
                            {"page": 2, "span": "淨利潤率較去年同期下降154個基點"},
                            {"page": 16, "span": "期內溢利 289,053 472,467 553,841 1,093,312"},
                            {"page": 16, "span": "公司權益持有人 249,240 425,766 541,207 1,056,914"},
                            {"page": 16, "span": "其他非控制性權益持有人 39,813 46,701 12,634 36,398"}]},
    required_inputs=[
        inp("期内溢利（含非控）", "FY2024Q1/Q2", [183.414, 289.053], "DB facts.net_income（H1 合计 472.467 与公告 472,467 千美元精确勾稽）", "可得"),
        inp("期内溢利（含非控）", "FY2023Q1/Q2", [539.471, 553.841], "DB net_income（H1 合计 1,093.312 勾稽公告 1,093,312）", "可得"),
        inp("应占溢利", "FY2024H1/FY2023H1", [425.766, 1056.914], "公告 p16", "缺：DB 无应占科目"),
        inp("非控损益", "FY2024H1/FY2023H1", [46.701, 36.398], "公告 p16 溢利归属行", "缺：DB noncontrolling_interest 是 BS 权益余额非损益"),
        inp("H1 收入", "FY2024H1/FY2023H1", [27309.713, 34045.155], "DB Q1+Q2 求和", "可得", version="derived")],
    calculations=[
        {"id": "ni_h1_yoy_db", "formula": "472.467/1093.312-1", "result": -0.56786, "tolerance_abs": 0.002},
        {"id": "attr_h1_yoy", "formula": "425.766/1056.914-1", "result": -0.59716, "tolerance_abs": 0.002},
        {"id": "npm_attr_cur", "formula": "425.766/27309.713", "result": 0.015590, "tolerance_abs": 0.0002},
        {"id": "npm_attr_pri", "formula": "1056.914/34045.155", "result": 0.031044, "tolerance_abs": 0.0002},
        {"id": "npm_attr_delta_bps", "formula": "(0.015590-0.031044)*10000", "result": -154.54, "tolerance_abs": 1.0},
        {"id": "npm_db_delta_bps", "formula": "(472.467/27309.713-1093.312/34045.155)*10000", "result": -148.13, "tolerance_abs": 1.0},
        {"id": "nci_h1_growth", "formula": "46.701/36.398-1", "result": 0.28307, "tolerance_abs": 0.005},
        {"id": "nci_share", "formula": "36.398/1093.312 与 46.701/472.467", "result": [0.03329, 0.09884], "tolerance_abs": 0.002},
        {"id": "q2_attr_yoy", "formula": "249.240/541.207-1（公告单季 -54%）", "result": -0.53947, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "'减少60%'与'下降154个基点'都只在应占口径下成立：425.766/1,056.914-1 = -59.72%（四舍五入60%）；净利率 1.559% vs 3.104% = -154.5bps",
        "用 DB net_income（含非控）会算出 -56.8% 与 -148.1bps，分别低估降幅约 3pp 与 6bps",
        "差异全部在非控损益层：H1 非控 36.398→46.701（+28.3%），占期内溢利比重 3.3%→9.9%——期内溢利里被非控拿走的部分变大，应占降幅更深",
        "与 FY25Q2 非控案例（LV-FY2025Q2-NCI-SLICE-001）互为镜像：该案非控收缩使应占增速(+44%)高于期内(+33%)，本案非控扩张使应占降幅(-60%)深于期内(-57%)",
        "DB 无应占/非控损益/税项科目，本案复算必须用公告 p16 损益表归属行；单季 -54% 同口径可核（249.240/541.207-1=-53.9%）"],
        "acceptable_variants": ["-59.72% 可写约-60%；-154.5bps 可写约154基点；非控占比可写 3.3%→9.9%"],
        "must_not_claim": ["不能因 DB 给出 -57% 就说公告'60%'不实（口径不同）", "不能把 DB net_income 增速当作权益持有人增速使用", "不能引用 DB noncontrolling_interest（BS 权益余额）充当非控损益", "不能把 6bps 的口径差归因于税项或汇率"],
        "unknowns": ["非控损益增长的子公司构成未披露"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["net_income", "revenue"],
                "missing": ["应占溢利科目", "非控损益（PL）科目"]},
    unknowns=[],
    related_note="与 LV-FY2025Q2-NCI-SLICE-001 同属'期内 vs 应占'口径簇（镜像方向），两案期间不同均保留。")

recs = [r1, r2, r3, r4]
with open(".cache/candidates/FY24Q2.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2024Q2-Q2-QOQ-REBOUND-001"] = {"tags": ["环比同比口径", "复苏叙事", "数据窗口边界", "汇率剔除项"],
    "difficulty_rationale": "同一公告段落内环比+12%与同比-20%并存，须拆三组期间口径分别复算，还要识别'十年均值9%'超出 DB 可见窗口只能部分验证"}
meta["LV-FY2024Q2-CCC-INV2B-001"] = {"tags": ["现金周转期", "存货余额", "跨口径拼合", "方向判定"],
    "difficulty_rationale": "同一句内'九天'只在时点口径复现、'应付减少'只在平均口径成立、负四天水平两口径皆不可复现，须逐项分口径判定并证明整句无单一自洽口径"}
meta["LV-FY2024Q2-SEG-H1-CLAIMS-001"] = {"tags": ["分部分析", "H1口径判定", "季度差分", "未分配项目勾稽"],
    "difficulty_rationale": "三个头条增速全为 H1 口径且换成 Q2 单季全部失效，102bps 与 ISG 按季数字需 H1-Q2 差分，还要用收入抵销/利润未分配两种不对称勾稽"}
meta["LV-FY2024Q2-ATTR-60-154BP-001"] = {"tags": ["非控损益", "应占口径", "基点复算", "镜像案例"],
    "difficulty_rationale": "-60% 与 154bps 只在应占口径成立，DB net_income 分别给出 -57%/148bps，需把 3pp/6bps 差拆到非控损益 +28% 的占比变化"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")


txt = open(".cache/lenovo_pdf_text/FY24Q2_161120231219.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

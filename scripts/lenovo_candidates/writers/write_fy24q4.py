# -*- coding: utf-8 -*-
"""FY2024Q4（2024-05-23 全年业绩公告）归因候选：写 .cache/candidates/FY24Q4.jsonl + meta。"""
import json
import unicodedata

SRC = {"pdf": "FY24Q4_230520241204.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0523/2024052300286_c.pdf",
       "sha256": "6dbdbf36e794b8a74b23f83fe1dc3d6db9eb81c7198473a474ef9903e423530c"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2024, fiscal_quarter=4,
            period_end="2024-03-31", publication_date="2024-05-23",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2024Q4-CCC-NEG4-001",
    event_family_id="LV-FY2024Q4-ccc-negative4",
    query="FY2024 全年公告称'现金周转期缩短至负四天，这得益于应收账款和存货天数合计改善12天'。请只用数据库 revenue/cogs/accounts_receivable/inventory/accounts_payable，按 D=365 天分别用年末时点口径与期初期末平均口径复算 FY2024 与 FY2023 的 DSO/DIO/DPO/CCC，检验'负4天'的水平与'12天改善'的幅度和方向各在哪种口径下可复现。",
    sources=[src([1, 3], "財務摘要-亮點bullet；業務回顧-亮點")],
    management_reference={"paraphrase": "摘要bullet称将现金转换周期缩短至负4天；亮点段称通过审慎营运资金管理现金周转期缩短至负四天，得益于应收与存货天数合计改善12天（同句表述应付项混乱）。",
        "evidence_spans": [{"page": 1, "span": "本集團將現金轉換週期縮短至負4天"},
                            {"page": 3, "span": "本集團的現金周轉期縮短至負四天，這得益於應收賬款和存貨天數合計改善"}]},
    required_inputs=[
        inp("全年收入", "FY2024/FY2023", [56863.784, 61946.854], "DB facts.revenue 四季求和（公告 56,864/61,947 百万）", "可得"),
        inp("全年销售成本", "FY2024/FY2023", [47060.601, 51445.762], "DB facts.cogs 四季求和", "可得"),
        inp("年末应收/存货/应付", "2024-03-31", [8147.695, 6702.677, 10505.427], "DB 三科目 FY2024Q4 期末", "可得"),
        inp("年末应收/存货/应付", "2023-03-31", [7940.378, 6371.858, 9772.934], "DB 三科目 FY2023Q4 期末", "可得"),
        inp("年初应收/存货/应付", "2022-03-31", [11289.547, 8300.658, 13184.831], "DB 三科目 FY2022Q4 期末（平均口径用）", "可得")],
    calculations=[
        {"id": "ccc_pt_24", "formula": "52.2988+51.9857-81.4796（DSO=8147.695/(56863.784/365)、DIO=6702.677/(47060.601/365)、DPO=10505.427/(47060.601/365)）", "result": 22.8049, "tolerance_abs": 0.02},
        {"id": "ccc_pt_23", "formula": "46.7859+45.2074-69.3375（FY2023 同式）", "result": 22.6558, "tolerance_abs": 0.02},
        {"id": "dso_delta_pt", "formula": "52.2988-46.7859", "result": 5.5129, "tolerance_abs": 0.02},
        {"id": "dio_delta_pt", "formula": "51.9857-45.2074", "result": 6.7783, "tolerance_abs": 0.02},
        {"id": "dpo_delta_pt", "formula": "81.4796-69.3375", "result": 12.1421, "tolerance_abs": 0.02},
        {"id": "ar_inv_delta_pt", "formula": "5.5129+6.7783", "result": 12.2912, "tolerance_abs": 0.02},
        {"id": "ccc_delta_pt", "formula": "22.8049-22.6558", "result": 0.1491, "tolerance_abs": 0.02},
        {"id": "ccc_avg_24", "formula": "51.6334+50.7028-78.639（各项=(年初+年末)/2 ÷ 流水/365）", "result": 23.6972, "tolerance_abs": 0.02},
        {"id": "ccc_avg_23", "formula": "56.6528+52.0497-81.441（FY2023 平均口径同式）", "result": 27.2615, "tolerance_abs": 0.02},
        {"id": "ar_inv_delta_avg", "formula": "(51.6334-56.6528)+(50.7028-52.0497)", "result": -6.3662, "tolerance_abs": 0.02},
        {"id": "ccc_delta_avg", "formula": "23.6972-27.2615", "result": -3.5643, "tolerance_abs": 0.02}],
    scorable_answer={"must_include": [
        "'负4天'水平在两种 DB 口径都复现不了：时点口径 FY2024 CCC +22.805 天、平均口径 +23.697 天，均为正约三周，不是负值",
        "'合计改善12天'是本案最有信息量的部分：时点口径 ΔDSO+ΔDIO=+5.51+6.78=+12.29 天——幅度精确对上'12'，但方向相反（是恶化不是改善）；平均口径该合计为 -6.37 天（改善但远不到12）",
        "时点口径 CCC 同比几乎不动（+0.15 天）：恶化 12.29 天的应收+存货被延长 12.14 天的应付天数抵销，与'缩短'叙事不符",
        "ΔDPO 在两口径下方向相反（时点 +12.14 vs 平均 -2.80）：2022-03-31 应付余额 13,184.831 处历史高位后回落，期末效应显著，选口径会翻转应付结论"],
        "acceptable_variants": ["CCC 可写 22.8/23.7 天；'12天恶化'可写 +12.3 天；判定'无法复现'即可"],
        "must_not_claim": ["不能因'12'幅度对上就背书'改善12天'（方向相反）", "不能断言管理层算错——公告未给 CCC 定义，若应付口径含应计负债等其他项目（accrued_liabilities 12,751.775 百万）结果会大不相同，但这同样无法同时复现'-4天'与'改善'方向", "不能用平均口径背书负4天（平均口径也在 +23 天附近）"],
        "unknowns": ["管理层现金周转期的分子分母与天数基准定义未知", "公告同句'縮短的應付帳款天數抵銷了應付帳款天數的下降'文字自相矛盾，无法确定其应付结论"]},
    db_support={"status": "ready", "db_fields": ["revenue", "cogs", "accounts_receivable", "inventory", "accounts_payable"],
                "derived": ["全年流水=四季求和", "DSO/DIO/DPO/CCC（时点与平均，D=365）"], "missing": []},
    unknowns=[],
    related_note="CCC 声明系列四案之一（本案=水平与方向双不符但'12天'幅度可复现；LV-FY2025Q1-CCC-11DAY-001 方向两口径相反；LV-FY2025Q3-CCC-ONEDAY-001 量级不符；LV-FY2025Q4-CCC-CLAIM-001 归因失败），跨季复现、声明各异，全部保留。")

r2 = dict(base, case_id="LV-FY2024Q4-GM-RECORD-3Y-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2024Q4-gm-record",
    query="FY2024 全年公告称毛利率创历史新高、连续第三年实现增长。请用数据库 FY2018..FY2024 各年 revenue 与 gross_profit 求和算出七年毛利率序列，检验'历史新高'与'连续第三年增长'（并指出序列中是否还有别的连续增长段），同时核对'收入下降8%'与公告经营费用行（用 DB gross_profit−operating_income 复算两经营费用并勾稽），说明毛利率归因于非PC与AI运营是否可验证。",
    sources=[src([1, 3], "財務摘要-bullet与摘要表；業務回顧-亮點")],
    management_reference={"paraphrase": "摘要bullet称非PC贡献加大叠加AI运营与产品创新推动全年毛利率创历史新高；亮点段称全年毛利率创历史新高、连续第三年实现增长；摘要表列毛利率 17.2% 对 17.0%（+0.2百分点）。",
        "evidence_spans": [{"page": 1, "span": "推動集團全年毛利率創歷史新高"},
                            {"page": 3, "span": "全年毛利率創歷史新高，連續第三年實現增長"},
                            {"page": 1, "span": "毛利率 17.6% 17.2% 17.0% 17.0% 0.6 百分點 0.2百分點"}]},
    required_inputs=[
        inp("全年收入", "FY2018..FY2024", [[45349.943, 51037.943, 50716.349, 60742.312, 71618.216, 61946.854, 56863.784]],
            "DB facts.revenue 各年四季求和", "可得"),
        inp("全年毛利", "FY2018..FY2024", [[6272.131, 7370.644, 8357.304, 9767.887, 12048.975, 10501.092, 9803.183]],
            "DB facts.gross_profit 各年四季求和", "可得"),
        inp("全年经营溢利", "FY2024/FY2023", [2005.784, 2668.823], "DB facts.operating_income 四季求和（公告 2,006/2,669）", "可得")],
    calculations=[
        {"id": "gm_2024", "formula": "9803.183/56863.784", "result": 0.17240, "tolerance_abs": 0.0005},
        {"id": "gm_2023", "formula": "10501.092/61946.854", "result": 0.16952, "tolerance_abs": 0.0005},
        {"id": "gm_2022", "formula": "12048.975/71618.216", "result": 0.16824, "tolerance_abs": 0.0005},
        {"id": "gm_2021", "formula": "9767.887/60742.312", "result": 0.16081, "tolerance_abs": 0.0005},
        {"id": "gm_2020", "formula": "8357.304/50716.349", "result": 0.16479, "tolerance_abs": 0.0005},
        {"id": "gm_delta_24", "formula": "0.17240-0.16952", "result": 0.00288, "tolerance_abs": 0.0003},
        {"id": "rev_yoy_fy", "formula": "56863.784/61946.854-1", "result": -0.08206, "tolerance_abs": 0.002},
        {"id": "opex_2024", "formula": "9803.183-2005.784", "result": 7797.399, "tolerance_abs": 0.005},
        {"id": "opex_2023", "formula": "10501.092-2668.823", "result": 7832.269, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "七年序列 13.83/14.44/16.48/16.08/16.82/16.95/17.24%（FY2018..FY2024）：FY2024 的 17.240% 为 DB 可见窗口（FY2018 起）最高，'历史新高'在可见区间成立（此前高点 FY2020 16.479%）",
        "'连续第三年增长'=FY2022 +0.74pp、FY2023 +0.13pp、FY2024 +0.29pp 三段连增 ✓；但注意 FY2020→FY2021 曾回落 -0.40pp，且 FY2019/FY2020 也有一段两年连增，'连续三年'不是序列里唯一的最长连增段",
        "'收入下降8%'：-8.21% 成立；毛利率精确增幅 0.288pp，公告按舍入值 17.2%−17.0% 记作 0.2 个百分点",
        "经营费用勾稽：DB gross_profit−operating_income 得 FY2024 7,797.399 / FY2023 7,832.269 百万，与公告 (7,797)/(7,832) 千位一致，同比 -0.44%（公告'(0)%'）"],
        "acceptable_variants": ["17.24% 可写 17.2%；增幅可写 0.29pp（精确）或 0.2pp（公告舍入口径）"],
        "must_not_claim": ["不能把'历史新高'推广到 DB 可见窗口（FY2018 起）之前", "不能背书毛利率提升归因于非PC占比与AI运营——公告与 DB 均无产品类别毛利率数据", "不能同时用精确值与舍入值混算连增段（16.95→17.24 是 +0.29pp，舍入后 17.0→17.2 是 +0.2pp）"],
        "unknowns": ["FY2018 以前年度毛利率 DB 不可见"]},
    db_support={"status": "ready", "db_fields": ["revenue", "gross_profit", "operating_income"],
                "derived": ["年度流水=四季求和", "毛利率=毛利/收入", "经营费用=毛利-经营溢利"], "missing": []},
    unknowns=[])

r3 = dict(base, case_id="LV-FY2024Q4-SEG-FYVSQ4-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2024Q4-segment-fy-vs-q4",
    query="FY2024 全年公告对同一批分部给出两套叙事：年度口径三集团承压（IDG 收入-10%/溢利-12%、利润率7.1%；ISG 收入-9% 全年亏损；SSG 溢利+11% 占35%），Q4 口径全面转暖（IDG 溢利+17% 远超收入+7%、溢利率+64bp 至7.4%；ISG 收入+15%；SSG 收入创Q4新高18亿美元、利润率21%、溢利+20%）。请用公告两张分部表复算九项数字并判定'-9%''90亿''35%'三处舍入空间；再核对分部表合计-抵销与 DB 收入（全年与 Q4 两口径都勾稽）而分部溢利合计-抵销与 DB 经营溢利为何不勾稽。",
    sources=[src([1, 3, 4, 9, 12], "財務摘要-bullets；亮點；集團財務表現；各產品業務集團表現；年度分部表；第四季度分部表")],
    management_reference={"paraphrase": "摘要与回顾段称：ISG 全年收入同比降9%达90亿美元；IDG 全年收入降10%、分部溢利降12%、盈利能力7.1%；SSG 分部溢利+11% 占三集团综合分部溢利35%；Q4 IDG 溢利+17% 远超收入+7%、溢利率+64bp 至7.4%；SSG Q4 收入18亿美元创Q4历史新高、利润率21%、溢利+20%；ISG Q4 收入+15%。年度分部表（千美元）：IDG 44,599,450/3,180,761 对 49,371,447/3,598,415；ISG 8,921,929/(248,260) 对 9,755,596/98,084；SSG 7,472,310/1,545,465 对 6,663,397/1,391,752；合计 60,993,689/4,477,966，抵销 (4,129,905)/(1,314,362)。Q4 表：IDG 10,462,709/772,914 对 9,796,078/660,966；SSG 1,820,372/388,891 对 1,649,891/324,389。",
        "evidence_spans": [{"page": 1, "span": "基礎設施方案業務集團的收入同比下降 9%"},
                            {"page": 3, "span": "全年下降10%，分部溢利則下降12%"},
                            {"page": 3, "span": "其分部盈利能力維持於 7.1%"},
                            {"page": 3, "span": "分部溢利同比上升 11%，佔本集團三個業務集團的綜合分部溢利的 35%"},
                            {"page": 3, "span": "智能設備業務集團的分部溢利同比增長 17%，遠超其 7%的收入增長"},
                            {"page": 3, "span": "方案服務業務集團第四季度收入創歷史新高，達 18 億美元"},
                            {"page": 3, "span": "維持於21%，帶動溢利同比增長20%"},
                            {"page": 4, "span": "經營溢利率同比增長64個基點至7.4%"},
                            {"page": 9, "span": "智能設備業務集團 44,599,450 3,180,761 49,371,447 3,598,415"},
                            {"page": 9, "span": "合計 60,993,689 4,477,966 65,790,440 5,088,251"},
                            {"page": 12, "span": "智能設備業務集團 10,462,709 772,914 9,796,078 660,966"}]},
    required_inputs=[
        {"metric": "IDG 收入/分部经营溢利", "entity": "智能设备业务集团", "period": "FY2024年度 与 FY2023年度", "raw_value": [[44599.450, 3180.761], [49371.447, 3598.415]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p9 年度分部表", "db": "缺：无分部科目"},
        {"metric": "ISG 收入/分部经营溢亏", "entity": "基础设施方案业务集团", "period": "FY2024年度 与 FY2023年度", "raw_value": [[8921.929, -248.260], [9755.596, 98.084]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p9", "db": "缺"},
        {"metric": "SSG 收入/分部经营溢利", "entity": "方案服务业务集团", "period": "FY2024年度 与 FY2023年度", "raw_value": [[7472.310, 1545.465], [6663.397, 1391.752]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p9", "db": "缺"},
        {"metric": "IDG 收入/分部经营溢利（Q4）", "entity": "智能设备业务集团", "period": "FY2024Q4 与 FY2023Q4", "raw_value": [[10462.709, 772.914], [9796.078, 660.966]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p12 第四季度分部表", "db": "缺"},
        {"metric": "ISG/SSG 收入与溢利（Q4）", "entity": "分部", "period": "FY2024Q4 与 FY2023Q4", "raw_value": [[[2533.101, -96.675], [2200.013, 7.495]], [[1820.372, 388.891], [1649.891, 324.389]]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p12", "db": "缺"},
        {"metric": "分部合计/抵销", "entity": "集团", "period": "FY2024年度 与 FY2024Q4", "raw_value": [[[60993.689, -4129.905], [14816.182, -983.065]]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": "original", "source": "公告 p9/p12", "db": "勾稽对象 facts.revenue 年度求和 56,863.784 与单季 13,833.117"},
        inp("全年经营溢利", "FY2024/FY2023", [2005.784, 2668.823], "DB facts.operating_income 四季求和", "可得")],
    calculations=[
        {"id": "idg_fy_rev", "formula": "44599.450/49371.447-1", "result": -0.09665, "tolerance_abs": 0.002},
        {"id": "idg_fy_op", "formula": "3180.761/3598.415-1", "result": -0.11607, "tolerance_abs": 0.002},
        {"id": "idg_fy_margin", "formula": "3180.761/44599.450", "result": 0.071318, "tolerance_abs": 0.0003},
        {"id": "isg_fy_rev", "formula": "8921.929/9755.596-1", "result": -0.08546, "tolerance_abs": 0.002},
        {"id": "isg_fy_swing", "formula": "-248.260-98.084", "result": -346.344, "tolerance_abs": 0.005},
        {"id": "ssg_fy_op", "formula": "1545.465/1391.752-1", "result": 0.11045, "tolerance_abs": 0.002},
        {"id": "ssg_fy_share", "formula": "1545.465/4477.966", "result": 0.345127, "tolerance_abs": 0.001},
        {"id": "idg_q4_rev", "formula": "10462.709/9796.078-1", "result": 0.06805, "tolerance_abs": 0.002},
        {"id": "idg_q4_op", "formula": "772.914/660.966-1", "result": 0.16937, "tolerance_abs": 0.002},
        {"id": "idg_q4_margin_bp", "formula": "(772.914/10462.709-660.966/9796.078)*10000", "result": 64.01, "tolerance_abs": 1.0},
        {"id": "isg_q4_rev", "formula": "2533.101/2200.013-1", "result": 0.1514, "tolerance_abs": 0.002},
        {"id": "ssg_q4_rev", "formula": "1820.372/1649.891-1", "result": 0.10333, "tolerance_abs": 0.002},
        {"id": "ssg_q4_op", "formula": "388.891/324.389-1", "result": 0.19884, "tolerance_abs": 0.002},
        {"id": "ssg_q4_margin", "formula": "388.891/1820.372", "result": 0.213633, "tolerance_abs": 0.001},
        {"id": "fy_elim_tie", "formula": "60993.689-4129.905-56863.784", "result": 0.0, "tolerance_abs": 0.005},
        {"id": "q4_elim_tie", "formula": "14816.182-983.065-13833.117", "result": 0.0, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "年度九项复算：IDG 收入 -9.67%（'10%'）、溢利 -11.61%（'12%'）、利润率 7.13%（'7.1%'）；ISG 收入 -8.55%（'9%'为偏大舍入）、收入 89.2 亿（'90亿'舍入）、由 +98.084 转为 -248.260 亏损摆动 346.344 百万；SSG 溢利 +11.05%（'11%'）、占比 34.51%（'35%'）",
        "Q4 反转叙事成立：IDG 收入 +6.81%（'7%'）/溢利 +16.94%（'17%'）/利润率 +64.0bp 至 7.39%（'7.4%'）；ISG 收入 +15.14%；SSG 收入 1,820.372 高于上年 Q4 的 1,649.891（'Q4 新高'仅在与上年同季比较意义上可证，18亿/21%/+20% 分别对应 1,820、21.36%、+19.88%）",
        "勾稽：年度与 Q4 的分部收入合计-抵销都与 DB revenue（56,863.784/13,833.117）精确一致；但分部溢利合计-抵销 3,163.604 不等于 DB 经营溢利 2,005.784，差额需经总部及企业费用、重组、折旧摊销、金融工具公允价值等未分配项桥接（p9 表列示），收入勾稽成立而利润勾稽必须走未分配项",
        "DB 无分部科目，九项全部依赖公告分部表；市份额/50bp/2.2pp/非PC 22% 等为第三方或类别口径，公告表与 DB 均不可验证"],
        "acceptable_variants": ["-9% 判定为'实际 -8.5%、舍入到 9%'即可；'35%'同理 34.5%"],
        "must_not_claim": ["不能把 ISG '收入同比下降9%'当成精确值（-8.55%，是三处舍入中最激进）", "不能宣称分部溢利与 DB 经营溢利直接勾稽（差 1,157.82 未分配项）", "不能把 SSG Q4 '18亿美元创新高'扩展为所有季度新高（仅 Q4 口径对上年 Q4）", "不能用集团口径 DB 科目复算分部数"],
        "unknowns": ["分部经营溢利未分摊总部费用与若干集团项，跨集团比较只在该口径内自洽", "'創歷史新高'（SSG Q4）在公告内只与上年同季比较，全部历史 Q4 序列需逐份公告拼合，DB 无分部科目不可系统验证"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["revenue", "operating_income", "gross_profit"],
                "missing": ["分部收入与分部经营溢利/亏损（年度与单季两套）", "市场份额与产品类别销售"]},
    unknowns=[],
    related_note="签出认沽期权重估收益 143.43 百万美元（本季 p13 非HKFRS 对账表列示）的事件族代表案为 LV-FY2025Q4-Q4-COLLAPSE-VS-FY-001（讨论其反转），本案不重复建认沽案；与 LV-FY2025Q1-SEGMENT-CLAIMS-001 同属分部声明族但验证 FY2024 年度-vs-Q4 叙事对照，独立保留。")

recs = [r1, r2, r3]
with open(".cache/candidates/FY24Q4.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2024Q4-CCC-NEG4-001"] = {"tags": ["现金周转期", "双口径复算", "幅度对方向反", "期末效应"],
    "difficulty_rationale": "'负4天'水平与'改善12天'方向双不符，但时点口径 ΔDSO+ΔDIO=+12.29 天精确复现'12'的幅度（方向相反）；ΔDPO 两口径方向相反构成选口径翻转陷阱"}
meta["LV-FY2024Q4-GM-RECORD-3Y-001"] = {"tags": ["毛利率序列", "历史新高判定", "连增段", "经营费用勾稽"],
    "difficulty_rationale": "七年毛利率序列里 FY2021 曾回落且 FY2019-20 已有一段连增，'连续第三年'与'新高'都须对整个序列判定；再混入舍入值(0.2pp)与精确值(0.288pp)口径差"}
meta["LV-FY2024Q4-SEG-FYVSQ4-001"] = {"tags": ["分部分析", "年度vs单季叙事对照", "舍入空间", "收入/利润勾稽不对称"],
    "difficulty_rationale": "同一批分部两套叙事（年度下滑 vs Q4 转暖）九项数字交叉复算，三处舍入空间判定，外加收入表勾稽成立而分部溢利须经未分配项桥接的不对称结构"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")


txt = open(".cache/lenovo_pdf_text/FY24Q4_230520241204.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

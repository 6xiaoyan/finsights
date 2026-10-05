# -*- coding: utf-8 -*-
"""FY2025Q2（2024-11-15 中期业绩公告）归因候选 step 1：写 .cache/candidates/FY25Q2.jsonl + meta。"""
import json
import unicodedata

SRC = {"pdf": "FY25Q2_151120240721.pdf",
       "url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/1115/2024111500032_c.pdf",
       "sha256": "0c8a6b6db5697f75ea70a5c482cc0d7c3bab8628fbd7dd60f5310163df1405d8"}


def src(pages, section):
    s = dict(SRC)
    s["pdf_pages"] = pages
    s["printed_pages"] = pages
    s["section"] = section
    return s


base = dict(company="Lenovo", fiscal_year=2025, fiscal_quarter=2,
            period_end="2024-09-30", publication_date="2024-11-15",
            question_origin="derived_from_management_discussion")


def inp(metric, period, raw, source, db, version="original"):
    return {"metric": metric, "entity": "联想集团", "period": period, "raw_value": raw,
            "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "集团", "version": version,
            "source": source, "db": db}


r1 = dict(base, case_id="LV-FY2025Q2-DEFREV-RECORD-001",
    event_family_id="LV-FY2025Q2-deferred-revenue-record",
    query="公告称'本集团持续扩大经常性收入基础，递延收入达到创纪录的31亿美元'（截至2024-09-30）。请只用数据库的递延收入科目（流动+非流动）复算该时点合计数，核对'创纪录'须与哪些历史时点比较、是否成立，并给出同比与环比变化。",
    sources=[src([2], "業望回顧及展望-亮點")],
    management_reference={"paraphrase": "亮点段称持续扩大经常性收入基础，递延收入达到创纪录31亿美元；分部溢利占比32%亦在同段。",
        "evidence_spans": [{"page": 2, "span": "遞延收入達到創紀錄的"}]},
    required_inputs=[
        inp("递延收入（流动）", "2024-09-30", [1556.470], "DB facts.deferred_revenue_current @FY2025Q2", "可得"),
        inp("递延收入（非流动）", "2024-09-30", [1556.971], "DB facts.deferred_revenue_noncurrent @FY2025Q2", "可得"),
        inp("递延收入合计", "2022-06-30 至 2024-09-30 各季末", [[2917.827, 2862.499, 2997.479, 2971.379, 2922.784, 2926.849, 3026.731, 2949.129, 2890.758, 3113.441]],
            "DB 各季末 流动+非流动 求和（DB 历史自 FY2022 起连续）", "可由科目求和", version="derived")],
    calculations=[
        {"id": "dr_total_2024q3cal", "formula": "1556.470+1556.971", "result": 3113.441, "tolerance_abs": 0.005},
        {"id": "prev_peak", "formula": "max(2022-06-30..2024-06-30 各时点) = 3026.731 (2023-12-31)", "result": 3026.731, "tolerance_abs": 0.005},
        {"id": "record_margin", "formula": "3113.441-3026.731", "result": 86.710, "tolerance_abs": 0.02},
        {"id": "dr_yoy", "formula": "3113.441/2926.849-1", "result": 0.0638, "tolerance_abs": 0.002},
        {"id": "dr_qoq", "formula": "3113.441/2890.758-1", "result": 0.0770, "tolerance_abs": 0.002}],
    scorable_answer={"must_include": [
        "合计 3,113.441 百万美元（流动 1,556.470+非流动 1,556.971），约31.1亿，与'31亿美元'一致",
        "'创纪录'成立：比较对象为 DB 全部历史季末（此前峰值 3,026.731 于 2023-12-31），新高幅度约 +86.7 百万（+2.9%）",
        "同比 +6.4%（vs 2023-09-30 的 2,926.849）、环比 +7.7%（vs 2024-06-30 的 2,890.758）",
        "必须两科目相加：仅取流动科目（1,556.5）会漏掉一半"],
        "acceptable_variants": ["DB 历史窗口起点（FY2022 起）之外的更早时点不可见，'历史纪录'应表述为 DB 可见区间内新高"],
        "must_not_claim": ["不能只报流动递延收入就说约15.6亿并判'31亿'不实", "不能把'创纪录'与'高速增长'混同（新高但仅比旧峰值高2.9%，同比也只+6.4%）"],
        "unknowns": ["DB 递延收入可见区间起点之外无法判断，'创纪录'仅在可见区间内可证"]},
    db_support={"status": "ready", "db_fields": ["deferred_revenue_current", "deferred_revenue_noncurrent"],
                "derived": ["各季末合计=流动+非流动"], "missing": []},
    unknowns=[])

r2 = dict(base, case_id="LV-FY2025Q2-FCF-TRIPLE-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2025Q2-fcf-netcash",
    query="公告称 FY2025 上半年'自由现金流几乎是去年同期的3倍'、'自由现金流同比增加8.01亿美元'、期末'净现金5.95亿美元'。请用公告现金流量表按 FCF=经营活动现金净额-(购置PP&E+在建工程+无形资产付款) 复算 H1 两期 FCF 与倍数/增量，并核对净现金表 4,239-63-3,014-567=595；再说明数据库 cash、short_term_debt、long_term_debt 各科目与公告哪一行勾稽、FCF 为何无法只靠数据库复算。",
    sources=[src([1, 2, 14, 19], "財務摘要；亮點；淨現金狀況表；綜合現金流量表")],
    management_reference={"paraphrase": "摘要称上半年自由现金流几乎是去年同期3倍、净现金增至5.95亿美元；亮点称自由现金流同比增加8.01亿美元；CF 表 H1 CFO 1,778,086/1,103,551 千美元，期末现金及等价物 4,178,915；净现金表 4,239-63-3,014-567=595。",
        "evidence_spans": [{"page": 1, "span": "上半年自由現金流幾乎是去年同期的3倍；淨現金結餘增至5.95億美元"},
                            {"page": 2, "span": "上半年度自由現金流同比增加8.01億美元"},
                            {"page": 19, "span": "經營活動產生的現金淨額 1,778,086 1,103,551"},
                            {"page": 14, "span": "淨現金 595 6"}]},
    required_inputs=[
        inp("CFO（H1两期）", "FY2024H1/FY2025H1", [1103.551, 1778.086], "公告 p19 CF 表", "缺：DB 无现金流量科目"),
        inp("资本支出三项（H1）", "FY2024H1/FY2025H1", [[129.091, 237.371, 310.243], [167.551, 139.216, 243.546]], "公告 p19 购置PP&E/在建工程/无形资产付款", "缺：DB 无 capex 科目"),
        inp("期末现金及等价物", "2024-09-30", [4178.915], "公告 p19 CF 期末行；DB facts.cash 4,178.915 完全一致", "可得"),
        inp("公告净现金表现金行", "2024-09-30", [4239], "公告 p14 '銀行存款、現金及現金等價物 4,239'（比 DB cash 宽 60.1）", "口径不同"),
        inp("公告贷款三行", "2024-09-30", [63, 3014, 567], "公告 p14 短期贷款/票据/可换股债券，合计 3,644", "缺：无借款类别拆分"),
        inp("DB 债务合计", "2024-09-30", [3643.591], "DB short_term_debt 1,027.895 + long_term_debt 2,615.696", "可得")],
    calculations=[
        {"id": "capex_cur", "formula": "167.551+139.216+243.546", "result": 550.313, "tolerance_abs": 0.005},
        {"id": "capex_pri", "formula": "129.091+237.371+310.243", "result": 676.705, "tolerance_abs": 0.005},
        {"id": "fcf_cur", "formula": "1778.086-550.313", "result": 1227.773, "tolerance_abs": 0.005},
        {"id": "fcf_pri", "formula": "1103.551-676.705", "result": 426.846, "tolerance_abs": 0.005},
        {"id": "fcf_ratio", "formula": "1227.773/426.846", "result": 2.876, "tolerance_abs": 0.01},
        {"id": "fcf_delta", "formula": "1227.773-426.846", "result": 800.927, "tolerance_abs": 0.01},
        {"id": "net_cash_ann", "formula": "4239-63-3014-567", "result": 595.0, "tolerance_abs": 0.01},
        {"id": "debt_tie", "formula": "1027.895+2615.696-3644", "result": -0.409, "tolerance_abs": 0.5},
        {"id": "cash_basis_gap", "formula": "4239-4178.915", "result": 60.085, "tolerance_abs": 0.01}],
    scorable_answer={"must_include": [
        "FCF H1：1,227.773 vs 426.846，倍数 2.88≈'几乎3倍'成立；增量 800.927 百万≈公告'8.01亿美元'，口径为 CFO-(PP&E+在建+无形)付款",
        "净现金 595 由公告 p14 四行精确复现（4,239-63-3,014-567）",
        "DB 勾稽：cash 4,178.915 与 CF 表期末行完全一致；short+long_term_debt 合计 3,643.591 与公告贷款合计 3,644 在舍入内一致",
        "FCF 无法只靠 DB：无现金流量表科目（CFO/利息税项支付/三类 capex）；且公告净现金表现金行 4,239 比 DB cash 宽 60.1（银行存款口径差，与 FY25Q3 netcash 案例同类）"],
        "acceptable_variants": ["倍数可写 2.9x/'接近3倍'；FCF 增量可写 8.0 亿"],
        "must_not_claim": ["不能用 CFO 增速(+61%)冒充 FCF 倍数", "不能声称 DB 可复现 595（cash 与贷款明细口径不全）", "不得忽略 capex 中在建工程与无形付款（只减 PP&E 会得 858.7/484.5=1.8倍，与'3倍'不符）"],
        "unknowns": ["自由现金流无统一定义，公告未给出其 FCF 公式，本复算口径与披露数字精确吻合可作为口径证据"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["cash", "short_term_debt", "long_term_debt"],
                "missing": ["现金流量表科目（CFO、capex 三类、已收付利息税项）", "银行存款>3个月口径项", "借款类别拆分"]},
    unknowns=[])

r3 = dict(base, case_id="LV-FY2025Q2-NCI-SLICE-001", question_origin="analyst_constructed",
    event_family_id="LV-FY2025Q2-nci-slice",
    query="FY2025Q2 公告：单季'期内溢利 +33%、公司权益持有人应占溢利 +44%'，上半年 '+35% vs +41%'。数据库 net_income 口径是含非控的期内溢利、且无应占与非控损益科目。请结合公告损益表复算两口径增速差，把差异拆解到非控损益项，用季度差分推出 Q1 的非控损益，并判断若把 DB net_income 当作'应占溢利'会产生多大误差。",
    sources=[src([1, 15], "財務摘要；綜合損益表")],
    management_reference={"paraphrase": "损益表（千美元）：期内溢利 Q2 383,276/289,053、H1 636,771/472,467；应占 Q2 358,532/249,240、H1 601,897/425,766；其他非控 Q2 24,744/39,813、H1 34,874/46,701；税项 Q2 89,910/68,566。",
        "evidence_spans": [{"page": 15, "span": "期內溢利 383,276 636,771 289,053 472,467"},
                            {"page": 15, "span": "公司權益持有人 358,532 601,897 249,240 425,766"},
                            {"page": 15, "span": "其他非控制性權益持有人 24,744 34,874 39,813 46,701"}]},
    required_inputs=[
        inp("期内溢利", "FY2024Q1/Q2、FY2025Q1/Q2", [183.414, 289.053, 253.495, 383.276], "DB facts.net_income 四季全部勾稽一致（期内溢利口径）", "可得"),
        inp("应占溢利", "FY2024/FY2025 Q2、H1", [[249.240, 425.766], [358.532, 601.897]], "公告 p15", "缺：DB 无应占科目"),
        inp("非控损益", "FY2024/FY2025 Q2、H1", [[39.813, 46.701], [24.744, 34.874]], "公告 p15 溢利归属行", "缺：DB noncontrolling_interest 是 BS 权益余额非损益"),
        inp("税项/税前", "FY2024Q2/FY2025Q2", [[68.566, 357.619], [89.910, 473.186]], "公告 p15", "缺：DB 无税项与税前科目")],
    calculations=[
        {"id": "ni_yoy_q2", "formula": "383.276/289.053-1", "result": 0.3260, "tolerance_abs": 0.005},
        {"id": "attr_yoy_q2", "formula": "358.532/249.240-1", "result": 0.4385, "tolerance_abs": 0.005},
        {"id": "ni_yoy_h1", "formula": "636.771/472.467-1", "result": 0.3478, "tolerance_abs": 0.005},
        {"id": "attr_yoy_h1", "formula": "601.897/425.766-1", "result": 0.4137, "tolerance_abs": 0.005},
        {"id": "nci_q2_change", "formula": "24.744/39.813-1", "result": -0.3785, "tolerance_abs": 0.005},
        {"id": "nci_share_q2", "formula": "24.744/383.276 与 39.813/289.053", "result": [0.0646, 0.1377], "tolerance_abs": 0.002},
        {"id": "q1_nci_cur_backout", "formula": "636.771-383.276-(601.897-358.532)", "result": 10.130, "tolerance_abs": 0.01},
        {"id": "q1_nci_pri_backout", "formula": "472.467-289.053-(425.766-249.240)", "result": 6.888, "tolerance_abs": 0.01},
        {"id": "etr_q2", "formula": "89.910/473.186 与 68.566/357.619", "result": [0.1900, 0.1917], "tolerance_abs": 0.002},
        {"id": "db_misread_error", "formula": "用 DB net_income 增速 32.60% 冒充应占增速：32.60%-43.85%", "result": -0.1125, "tolerance_abs": 0.005}],
    scorable_answer={"must_include": [
        "增速差 ≈11.3pp（Q2 43.85% vs 32.60%；H1 41.37% vs 34.78%）完全来自非控损益项收缩：Q2 非控 39.813→24.744 千单位（-37.9%），占期内溢利比例 13.8%→6.5%",
        "季度差分：FY25Q1 非控 10.130 vs FY24Q1 6.888——Q1 非控反而同比增 3.2，41%/44% 的应占超额增速集中在 Q2",
        "DB net_income 是含非控的期内溢利口径，把它当应占会把增速低估约 11 个百分点",
        "税层不构成差异来源：Q2 有效税率 19.17%→19.00% 基本持平"],
        "acceptable_variants": ["非控降幅可写 -37.8%~-38%；pp 差可写 11.2~11.3pp"],
        "must_not_claim": ["不能把应占超额增速归因于税项或非经常项（本季无重大剔除项变化）", "不能引用 DB noncontrolling_interest（那是 BS 权益余额 ~500-650mn，非损益项）作为非控损益", "不能说期内增速+33%就是管理层说的+44%（口径不同）"],
        "unknowns": ["非控损益下降的具体子公司构成（公告未按子公司披露）"]},
    db_support={"status": "needs_numeric_data", "db_fields": ["net_income"],
                "missing": ["应占溢利科目", "非控损益（PL）科目", "税项与除税前溢利科目"]},
    unknowns=[])

r4 = dict(base, case_id="LV-FY2025Q2-ISG-H1-CLAIMS-001",
    event_family_id="LV-FY2025Q2-isg-h1-claims",
    query="管理层称 ISG（基础设施方案业务集团）'收入飙升65%…历史上首次达到65亿美元'且'分部亏损较去年同期减少36%'。请用公告的两张分部表（6个月与3个月）判定 65%/65亿美元/36% 各对应哪个期间口径，复算这三个数字，并说明 Q2 单季口径下哪一项会给出不同的结果。",
    sources=[src([1, 2, 7, 10], "財務摘要；亮點；六個月分部表；第二季度分部表")],
    management_reference={"paraphrase": "摘要称 ISG 收入飙升65%、分部亏同比减36%；亮点称 ISG 收入同比增长65%历史上首次达到65亿美元。H1 分部表 ISG 6,465,167/(73,002) vs 3,915,525/(113,855)；Q2 表 3,305,370/(35,728) vs 2,001,759/(53,438)（千美元）。",
        "evidence_spans": [{"page": 1, "span": "收入飆升 65%"},
                            {"page": 1, "span": "分部虧損較去"},
                            {"page": 1, "span": "年同期減少36%"},
                            {"page": 7, "span": "基礎設施方案業務集團 6,465,167 (73,002) 3,915,525 (113,855)"},
                            {"page": 10, "span": "基礎設施方案業務集團 3,305,370 (35,728) 2,001,759 (53,438)"}]},
    required_inputs=[
        {"metric": "ISG 分部收入/亏损", "entity": "基础设施方案业务集团", "period": "FY2025H1 与 FY2024H1", "raw_value": [[6465.167, -73.002], [3915.525, -113.855]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p7 六个月分部表", "db": "缺：无分部科目"},
        {"metric": "ISG 分部收入/亏损", "entity": "基础设施方案业务集团", "period": "FY2025Q2 与 FY2024Q2", "raw_value": [[3305.370, -35.728], [2001.759, -53.438]],
         "raw_unit": "usd_mn", "unified_unit": "usd_mn", "scope": "分部", "version": "original", "source": "公告 p10 第二季度分部表", "db": "缺"}],
    calculations=[
        {"id": "isg_h1_rev_yoy", "formula": "6465.167/3915.525-1", "result": 0.6512, "tolerance_abs": 0.003},
        {"id": "isg_q2_rev_yoy", "formula": "3305.370/2001.759-1", "result": 0.6512, "tolerance_abs": 0.003},
        {"id": "isg_h1_loss_cut", "formula": "1-73.002/113.855", "result": 0.3588, "tolerance_abs": 0.003},
        {"id": "isg_q2_loss_cut", "formula": "1-35.728/53.438", "result": 0.3314, "tolerance_abs": 0.003},
        {"id": "six_five_billion", "formula": "6465.167 是否≈65亿（H1累计）vs Q2 单季 33.05亿", "result": 6465.167, "tolerance_abs": 0.01}],
    scorable_answer={"must_include": [
        "65% 与 '65亿美元' 都对应 H1 累计口径：6,465.167/3,915.525-1=+65.1%，H1 收入 64.65 亿≈65亿",
        "36% 也对应 H1：亏损 113.855→73.002 减亏 35.9%",
        "巧合同陷阱：Q2 单季收入同比也恰为 +65.1%，两口径下 65% 都成立；但 Q2 单季减亏只有 33.1%，'36%' 若按单季口径复算对不上",
        "DB 无分部科目，全部依赖公告分部表；两表间 Q1 可由 H1-Q2 差分为 ISG 2,970,638 收入/亏 37,274"],
        "acceptable_variants": ["65% 可写 65.1%；减亏可写 35.9%/约36%"],
        "must_not_claim": ["不能把 65亿美元说成单季（单季 33.05 亿）", "不能声称 Q2 单季减亏 36%", "不能说 DB 可直接验证分部表"],
        "unknowns": ["分部'亏损'用经营溢利口径，不含未分配项目分摊"]},
    db_support={"status": "needs_numeric_data", "db_fields": [], "missing": ["分部收入与分部经营溢利/亏损"]},
    unknowns=[],
    related_note="与 LV-FY2025Q4-ISG-H2-TURNAROUND-001（全年-68.501 与 H2 扭亏叙事）共享 H1 -73.002 数据但验证的是不同管理层声明，独立保留。")

recs = [r1, r2, r3, r4]
with open(".cache/candidates/FY25Q2.jsonl", "w", encoding="utf-8") as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

meta = json.load(open(".cache/candidates/meta.json", encoding="utf-8"))
meta["LV-FY2025Q2-DEFREV-RECORD-001"] = {"tags": ["递延收入", "历史峰值判定", "两科目求和"],
    "difficulty_rationale": "创纪录须对 DB 全历史季末比较（旧峰 3,026.7 于 2023-12-31），且流动+非流动两科目相加；只取单科目或只比同期都会判错"}
meta["LV-FY2025Q2-FCF-TRIPLE-001"] = {"tags": ["自由现金流", "现金流量表缺口", "口径复原", "净现金"],
    "difficulty_rationale": "FCF 公式需从披露数字反推口径（三类capex全选才对 801M 增量，漏在建/无形得 1.8x），叠加 DB 无现金流科目、现金行口径差 60M 的对账"}
meta["LV-FY2025Q2-NCI-SLICE-001"] = {"tags": ["非控损益", "口径陷阱", "季度差分", "归属层"],
    "difficulty_rationale": "应占+44% vs 期内+33% 的 11pp 差须拆解到非控损益项并用 H1-Q2 差分补 Q1，还要防 DB noncontrolling_interest（BS口径）误用"}
meta["LV-FY2025Q2-ISG-H1-CLAIMS-001"] = {"tags": ["分部分析", "期间口径判定", "巧合陷阱"],
    "difficulty_rationale": "65% 在两口径下碰巧都成立而 36% 只在 H1 成立，须分别定位三项声明的期间口径并用两张分部表交叉"}
json.dump(meta, open(".cache/candidates/meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def norm(s):
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\t", "").replace("\n", "")


txt = open(".cache/lenovo_pdf_text/FY25Q2_151120240721.txt", encoding="utf-8").read()
flat = norm(txt)
for r in recs:
    for sp in r["management_reference"]["evidence_spans"]:
        assert norm(sp["span"]) in flat, (r["case_id"], sp["span"][:30])
print("spans OK; records:", [r["case_id"] for r in recs])

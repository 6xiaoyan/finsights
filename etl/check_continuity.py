"""V1.20 口径连续性 + V1.10② 营收环比检查：python -m etl.check_continuity

输出 data/continuity_report.md：
① 每个 (公司, 标准科目) 各年映射的报表行（联想，取自 CSV）或 XBRL 标签（SEC，取自 source_ref），映射变化的位置标出；
② 每个科目环比变化超过历年同季度 3σ 的跳变；
③ V1.10②：相邻季度营收变化超过 ±50% 的逐条列出。
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

import duckdb

DB = Path("data/finsights.duckdb")
REPORT = Path("data/continuity_report.md")


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute("""SELECT f.company_id, f.fiscal_year, f.fiscal_quarter, f.account_code,
        f.value, f.source_ref, p.calendar_quarter FROM facts f JOIN periods p
        USING(company_id, fiscal_year, fiscal_quarter)
        WHERE f.version='original' AND f.derivation IS NULL
        ORDER BY f.company_id, f.account_code, p.period_end""").fetchall()
    con.close()

    # ---- ① 映射来源随年份变化 ----
    # Lenovo 的报表行：从 CSV 取 (fy, quarter, account) -> line_item_zh；SEC：source_ref 第一段 tag
    line_by_lenovo: dict[tuple, str] = {}
    subtotal_map = {"_subtotal_流動資產": "total_current_assets", "_subtotal_流動負債": "total_current_liabilities"}
    for fn in ("data/manual/lenovo_balance_sheet.csv", "data/manual/lenovo_income_statement.csv"):
        for r in csv.DictReader(open(fn, encoding="utf-8")):
            code = subtotal_map.get(r["account_code"], r["account_code"])
            line_by_lenovo[(r["fy"], r["quarter"], code)] = r["line_item_zh"]

    prov: dict[tuple, dict[int, set]] = defaultdict(lambda: defaultdict(set))
    for cid, fy, q, acct, val, ref, cq in rows:
        if cid == "Lenovo":
            src = line_by_lenovo.get((f"FY{str(fy)[2:]}", f"Q{q}", acct), ref[:24])
        else:
            src = ref.split("@")[0].split(",")[0]
        prov[(cid, acct)][fy].add(src)
    mapping_changes = []
    for (cid, acct), by_year in sorted(prov.items()):
        years = sorted(by_year)
        for y_prev, y_next in zip(years, years[1:]):
            if by_year[y_prev] != by_year[y_next]:
                mapping_changes.append((cid, acct, y_prev, y_next,
                                        sorted(by_year[y_prev]), sorted(by_year[y_next])))

    # ---- ② 环比 3σ 跳变（按历年同季度分组）----
    series: dict[tuple, list] = defaultdict(list)
    for cid, fy, q, acct, val, ref, cq in rows:
        series[(cid, acct)].append((cq, fy, q, val))
    jumps = []
    for (cid, acct), lst in series.items():
        by_cq: dict[str, list[float]] = defaultdict(list)
        pts = sorted(lst, key=lambda x: (x[3] is None, x[3]))
        for (cq1, _, _, v1), (cq2, _, _, v2) in zip(pts, pts[1:]):
            if v1 and v2 and v1 != 0:
                by_cq[cq2].append((v2 - v1) / abs(v1))
        for cq, changes in by_cq.items():
            if len(changes) < 4:
                continue
            mean = sum(changes) / len(changes)
            var = sum((c - mean) ** 2 for c in changes) / len(changes)
            std = var ** 0.5
            if std == 0:
                continue
            z = (changes[-1] - mean) / std
            if abs(z) > 3:
                jumps.append((cid, acct, cq, changes[-1], z))

    # ---- ③ V1.10②：营收环比 ±50% ----
    rev_jumps = []
    for (cid, acct), lst in series.items():
        if acct != "revenue":
            continue
        pts = sorted(lst, key=lambda x: (x[3] is None, x[3]))
        for (cq1, _, _, v1), (cq2, _, _, v2) in zip(pts, pts[1:]):
            if v1 and v2 and abs((v2 - v1) / v1) > 0.5:
                rev_jumps.append((cid, cq1, cq2, (v2 - v1) / v1))

    lines = ["# 口径连续性报告 continuity_report", ""]
    lines += ["## ① 映射来源随年份变化（每条需说明：真实口径变化 / 映射修复）", ""]
    known = {
        ("Lenovo", "accounts_payable"): "真实列报变更：FY2023 起應付票據并入應付貿易賬款列报；本库已统一口径（FY18–22 應付票據亦计入 AP），无断点，见 filing_notes",
        ("Lenovo", "accounts_receivable"): "真实列报变更：FY2026 起报表行含租赁应收款，无法拆分，见 filing_notes",
        ("Lenovo", "deferred_revenue_noncurrent"): "映射修复：非流动遞延收益由 other_noncurrent_liabilities 改映射为 deferred_revenue_noncurrent（Q6-3 新科目）",
        ("Lenovo", "operating_income"): "标签变体修复：經營(虧損)/溢利 与 經營溢利/(虧損) 等排列随盈亏方向变化（Q7-4）",
        ("Lenovo", "net_income"): "标签变体修复：期內/年內、內/内 字形变体（Q7-4）",
        ("HP", "total_liabilities"): "口径定义：HP TL 为分项加总（sum_of_parts，Q6-1），标签构成随 HP 报送标签演化",
        ("HP", "other_noncurrent_liabilities"): "映射修复：加 OtherLiabilitiesNoncurrent（HP per-company 标签，Q6-1 分项加总需要）",
        ("Dell", "accounts_receivable"): "FY2024Q2/Q3 为人工补录（companyfacts 缺失，Q4 答复）",
        ("Dell", "accounts_payable"): "FY2024Q2/Q3 为人工补录（companyfacts 缺失，Q4 答复）",
        ("HP", "total_equity"): "标签演化：StockholdersEquity 与 …IncludingPortionAttributableToNoncontrollingInterest 随 NCI 存废切换（回退机制，非口径变化）",
        ("HP", "long_term_debt"): "标签演化：FY17–FY22Q3 用 LongTermDebtAndCapitalLeaseObligations，之后 LongTermDebtNoncurrent（回退机制）",
        ("HP", "revenue"): "标签演化：Revenues 与 RevenueFromContractWithCustomerExcludingAssessedTax（ASC 606 前后）",
        ("HP", "gross_profit"): "派生值 rev_minus_cogs 的输入标签随 ASC 606 演化（Q6-2）",
        ("HP", "deferred_revenue_noncurrent"): "标签演化：ContractWithCustomerLiabilityNoncurrent（606 后）与 DeferredRevenueNoncurrent（606 前，回退）",
        ("HP", "deferred_revenue_current"): "ASC 606 过渡年（FY2018）：不同季度分别用 ContractWithCustomerLiabilityCurrent 与 DeferredRevenueCurrent 报送（回退机制按季度启用）",
        ("Dell", "revenue"): "标签演化：Revenues（含 FY17 旧口径）与 RevenueFromContractWithCustomerExcludingAssessedTax（ASC 606）；FY17Q4 为 9M-YTD 倒算（Q4-2）",
        ("Dell", "cogs"): "同 revenue：CostOfRevenue 跨 ASC 606；FY17Q4 为 9M-YTD 倒算（Q4-2）",
        ("Dell", "gross_profit"): "派生值 rev_minus_cogs 的输入标签随 ASC 606 演化（Q6-2）",
        ("Dell", "deferred_revenue_current"): "标签演化：ContractWithCustomerLiabilityCurrent（606 后）与 DeferredRevenueCurrent（606 前，回退）",
        ("Dell", "deferred_revenue_noncurrent"): "同上（非流动侧）",
        ("Dell", "short_term_debt"): "标签演化：Dell 用 DebtCurrent 报送短期债务（非 ShortTermBorrowings）",
        ("Lenovo", "total_current_assets"): "来源为各公告资产负债表的小计行（行名固定：[流動資產小计]），非口径变化",
        ("Lenovo", "total_current_liabilities"): "来源为各公告资产负债表的小计行（行名固定：[流動負債小计]），非口径变化",
    }
    if not mapping_changes:
        lines += ["（无）"]
    for cid, acct, y0, y1, s0, s1 in mapping_changes:
        expl = known.get((cid, acct), "待说明")
        lines.append(f"- {cid} / {acct}：{y0} 用 {s0} → {y1} 用 {s1}。说明：{expl}")
    lines += ["", "## ② 环比超历年同季度 3σ 的跳变（每条需说明）", ""]
    if not jumps:
        lines += ["（无）"]
    for cid, acct, cq, chg, z in jumps:
        lines.append(f"- {cid} / {acct} / {cq}：环比 {chg:+.1%}（z={z:.1f}）。说明：见 identity_report 与 filing_notes（Dell ASC606 断点、Q4 倒算口径等）")
    lines += ["", "## ③ V1.10②：相邻季度营收变化超 ±50%", ""]
    if not rev_jumps:
        lines += ["（无）"]
    for cid, cq1, cq2, chg in rev_jumps:
        lines.append(f"- {cid}：{cq1} → {cq2} 营收环比 {chg:+.1%}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"mapping_changes={len(mapping_changes)} jumps={len(jumps)} revenue_jumps={len(rev_jumps)} -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

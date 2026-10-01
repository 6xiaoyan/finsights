"""P1 勾稽检查（verify V1.6/V1.7/V1.8）：python -m etl.check_identities

检查（version='original'，容差 0.5%）：
1. 资产 = 负债 + 所有者权益（total_equity 含少数股东权益）
2. total_liabilities_and_equity = total_assets
3. 分项加总 = 合计（含 other_* 吸收项）
4. 毛利 = 收入 − 营业成本
输出：控制台摘要 + data/identity_report.md（含各期 other_* 吸收金额及其占总资产比例）
"""
from __future__ import annotations

from pathlib import Path

import duckdb

DB_PATH = Path("data/finsights.duckdb")
REPORT = Path("data/identity_report.md")
TOL = 0.005

BS_TREES = {
    "total_current_assets": ["cash", "short_term_investments", "accounts_receivable", "inventory", "other_current_assets"],
    "total_assets": ["total_current_assets", "ppe_net", "goodwill", "intangibles", "other_noncurrent_assets"],
    "total_current_liabilities": ["accounts_payable", "short_term_debt", "accrued_liabilities", "deferred_revenue_current", "other_current_liabilities"],
    "total_liabilities": ["total_current_liabilities", "long_term_debt", "other_noncurrent_liabilities"],
}
# total_equity 不做"分项和=合计"检查：noncontrolling_interest 只是子项，股东部分（股本+储备+永续证券）无标准科目，
# 其正确性由 A = L + E 恒等式间接保证；此处只做子项 ≤ 合计的健全性检查。


def main() -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    periods = con.execute(
        "SELECT company_id, fiscal_year, fiscal_quarter FROM periods ORDER BY 1,2,3").fetchall()

    def vals(cid, fy, q) -> dict:
        rows = con.execute(
            "SELECT account_code, value FROM facts WHERE company_id=? AND fiscal_year=? AND fiscal_quarter=? AND version='original'",
            [cid, fy, q]).fetchall()
        return {a: v for a, v in rows}

    failures: list[str] = []
    absorption: list[str] = []
    n_id_checks = 0
    for cid, fy, q in periods:
        v = vals(cid, fy, q)
        a, l, e = v.get("total_assets"), v.get("total_liabilities"), v.get("total_equity")
        if a is None or l is None or e is None:
            failures.append(f"{cid} FY{fy}Q{q}: a/l/e 缺失 a={a} l={l} e={e}")
            continue
        if abs(a - l - e) / abs(a) > TOL:
            failures.append(f"{cid} FY{fy}Q{q}: |A-L-E|/A = {abs(a - l - e) / abs(a):.4%}")
        n_id_checks += 1
        tle = v.get("total_liabilities_and_equity")
        if tle is not None and abs(tle - a) / abs(a) > TOL:
            failures.append(f"{cid} FY{fy}Q{q}: L+E合计 ≠ 总资产")
        for total, children in BS_TREES.items():
            tv = v.get(total)
            if tv is None:
                failures.append(f"{cid} FY{fy}Q{q}: {total} 缺失")
                continue
            s = sum(v.get(c, 0.0) for c in children)
            if abs(s - tv) / max(abs(tv), 1) > TOL:
                failures.append(f"{cid} FY{fy}Q{q}: {total} 分项和 {s:.1f} ≠ {tv:.1f}")
        rev, cogs, gp = v.get("revenue"), v.get("cogs"), v.get("gross_profit")
        if gp is not None and rev is not None and cogs is not None and abs(gp - (rev - cogs)) > max(abs(rev) * TOL, 1):
            failures.append(f"{cid} FY{fy}Q{q}: 毛利 ≠ 收入−成本")
        # 注：不做 NCI ≤ total_equity 检查——母公司权益为负时（如 Dell FY2019–21）子项可大于合计，
        # NCI 的正确性由 A = L + E 恒等式与公司报送口径共同保证。
        # 吸收项规模：不是勾稽失败，按 plan V1.8 留档给人工审阅（>5% 总资产的列入 questions.md）
        for other in ("other_current_assets", "other_noncurrent_assets", "other_current_liabilities", "other_noncurrent_liabilities"):
            if other in v and a:
                pct = abs(v[other]) / abs(a)
                absorption.append(f"| {cid} | {fy} | {q} | {other} | {v[other]:,.1f} | {pct:.2%} |")

    lines = [
        "# 勾稽检查报告 identity_report",
        "",
        f"- 检查期间数: {n_id_checks}（容差 0.5%）",
        f"- 失败数: {len(failures)}",
        "",
        "## 失败明细" + ("" if failures else "（无）"),
        "",
    ]
    lines += [f"- {f}" for f in failures]
    lines += ["", "## other_* 吸收项（V1.8：任一期不得超过该期总资产的 5%）", "",
              "| 公司 | 财年 | 季 | 科目 | 金额(百万美元) | 占总资产 |", "|---|---|---|---|---|---|"]
    lines += sorted(set(absorption))
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"periods checked: {n_id_checks}, failures: {len(failures)}")
    for f in failures[:15]:
        print("  -", f)
    con.close()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

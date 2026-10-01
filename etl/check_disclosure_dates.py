"""V1.21 SEC 披露日期核对：python -m etl.check_disclosure_dates

用 EDGAR submissions 接口拿每份 10-Q/10-K 的 reportDate → filingDate，
对每个期间（period_end 有自身申报的）比较 original 事实的 available_date：
- 相等 → OK
- available_date 晚于自身申报日 → "companyfacts 比较期补报"（保守方向，逐条列出）
- available_date 早于自身申报日 → 泄露风险，必须为 0
- 人工补录的期间按补录日期核对（dell_patch.csv / available_date_patch.csv 已修正的为 OK）
输出 docs/evidence/V1.21_report.md。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import duckdb

DB = Path("data/finsights.duckdb")
OUT = Path("docs/evidence/V1.21_report.md")
SUBMISSIONS = {
    "HP": Path("data/raw/sec/submissions_CIK0000047217.json"),
    "Dell": Path("data/raw/sec/submissions_CIK0001571996.json"),
}


def load_own_filings(path: Path) -> dict[str, str]:
    """reportDate → 该期间最早一份 10-Q/10-K 的 filingDate。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    recent = d["filings"]["recent"]
    own: dict[str, str] = {}
    for form, rdate, fdate in zip(recent["form"], recent["reportDate"], recent["filingDate"]):
        if form in ("10-Q", "10-K") and rdate:
            if rdate not in own or fdate < own[rdate]:
                own[rdate] = fdate
    return own


def main() -> int:
    own_maps = {cid: load_own_filings(p) for cid, p in SUBMISSIONS.items() if p.exists()}
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute("""SELECT company_id, fiscal_year, fiscal_quarter, account_code,
        available_date, derivation, p.period_end FROM facts f JOIN periods p
        USING(company_id, fiscal_year, fiscal_quarter)
        WHERE f.version='original' AND f.company_id IN ('HP','Dell') ORDER BY 1,2,3,4""").fetchall()
    con.close()

    ok = late = early = 0
    late_detail: dict[tuple, list] = defaultdict(list)
    for cid, fy, q, acct, adate, deriv, pe in rows:
        pe_s = pe.isoformat() if isinstance(pe, date) else str(pe)[:10]
        own = own_maps.get(cid, {}).get(pe_s)
        ad = adate.isoformat() if isinstance(adate, date) else str(adate)[:10]
        if own is None:
            continue  # 该期间无自身申报（如 FY27 尚未提交 10-Q 的比较期不入库）
        if ad == own:
            ok += 1
        elif ad > own:
            late += 1
            late_detail[(cid, fy, q)].append((acct, ad, own, deriv or "reported"))
        else:
            early += 1
            late_detail[(cid, fy, q)].append((acct, ad, own, "EARLY-泄露风险!"))

    lines = [
        "# V1.21 SEC 披露日期核对报告",
        "",
        f"- 与自身申报日相等: {ok} 条",
        f"- 晚于自身申报日（companyfacts 比较期补报，保守方向，不构成泄露）: {late} 条",
        f"- 早于自身申报日（泄露风险）: {early} 条",
        "",
        "处理说明：晚于自身申报日的 original 值来自后续申报的比较期（companyfacts 未收录其自身申报的该行，",
        "或该行在自身申报中使用了 companyfacts 不收录的标签/维度），available_date 取实际首次入库来源的披露日，",
        "方向保守（as_of 快照只会更晚提供数据）。Dell FY2024Q4 的 AR/AP 已按 10-K（2024-03-25）人工修正（Q4 答复），",
        "Dell FY2024Q2/Q3 的 AR/AP 为人工补录（available_date = 自身 10-Q 提交日）。",
        "",
        "## 逐条明细（晚于自身申报日）",
        "",
    ]
    for (cid, fy, q), items in sorted(late_detail.items()):
        lines.append(f"- {cid} FY{fy}Q{q}: {', '.join(f'{a}({d})' for a, d, _, _ in items[:6])}"
                     + (" …" if len(items) > 6 else ""))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ok={ok} late={late} early={early} -> {OUT}")
    return 0 if early == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

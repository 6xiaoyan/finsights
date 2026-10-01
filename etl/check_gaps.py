"""P1 季度连续性检查（verify V1.5）：python -m etl.check_gaps

按公司检查 calendar_quarter 序列是否连续，输出 "0 gaps" 或缺口清单。
"""
from __future__ import annotations

import duckdb


def main() -> int:
    con = duckdb.connect("data/finsights.duckdb", read_only=True)
    rows = con.execute("SELECT company_id, calendar_quarter FROM periods ORDER BY 1,2").fetchall()
    con.close()
    by: dict[str, list[str]] = {}
    for cid, cq in rows:
        by.setdefault(cid, []).append(cq)
    total_gaps = 0
    for cid, lst in sorted(by.items()):
        seen = set(lst)
        y, q = int(lst[0][:4]), int(lst[0][-1])
        y_end, q_end = int(lst[-1][:4]), int(lst[-1][-1])
        missing = []
        while (y, q) <= (y_end, q_end):
            if f"{y}Q{q}" not in seen:
                missing.append(f"{y}Q{q}")
            q += 1
            if q == 5:
                y, q = y + 1, 1
        total_gaps += len(missing)
        print(f"{cid}: {len(lst)} 期 ({lst[0]}~{lst[-1]}), gaps: {missing or '无'}")
    print("0 gaps" if total_gaps == 0 else f"{total_gaps} gaps")
    return 0 if total_gaps == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

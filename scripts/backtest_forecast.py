"""P6.3（plan 8.3）：forecast 工具全量回测——不经过 agent、零 LLM 成本。

所有公司 × 3 指标（营收/应收/存货）× 最后 --origins 个季度做一步前瞻：
train = y[:t]，forecast(train) 对 y[t] 打分。输出 eval/reports/forecast_backtest.json
（V6.10 报告的第二组：MASE 基线=seasonal_naive + 80% 覆盖率）。

用法：.venv/Scripts/python scripts/backtest_forecast.py [--origins 12]
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

from fincalc.forecast import _naive_errs, forecast

BASE_DB = Path("data/finsights.duckdb")
OUT = Path("eval/reports/forecast_backtest.json")
METRICS = ["revenue", "accounts_receivable", "inventory"]
METHODS = ["seasonal_naive", "ets", "sarima", "auto"]


def load_series(db: Path) -> dict[tuple[str, str], list]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT p.company_id AS company, f.account_code AS metric,"
            " p.fiscal_year, p.fiscal_quarter, f.value"
            " FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)"
            " WHERE f.version='original' AND f.account_code IN (?, ?, ?)"
            " ORDER BY 1, 2, p.period_end", METRICS).df()
    finally:
        con.close()
    out: dict[tuple[str, str], list] = {}
    for (co, me), g in df.groupby(["company", "metric"], sort=True):
        out[(co, me)] = [(int(a), int(b), float(v))
                         for a, b, v in zip(g["fiscal_year"], g["fiscal_quarter"], g["value"])]
    return dict(out)


def run(origins: int) -> dict:
    series = load_series(BASE_DB)
    records = []
    for (co, me), rows in series.items():
        vals = [r[2] for r in rows]
        labels = [(r[0], r[1]) for r in rows]
        # 一步前瞻：origin t 满足 len(train)>=12；取最后 origins 个
        ts = [t for t in range(12, len(vals))][-origins:]
        for t in ts:
            actual = vals[t]
            fy, fq = labels[t]
            for m in METHODS:
                try:
                    res = forecast(vals[:t], labels[:t], method=m, horizon=1)
                except Exception as e:
                    records.append({"company": co, "metric": me, "method": m,
                                    "target": f"FY{fy}Q{fq}", "error": str(e)[:200]})
                    continue
                denom = _naive_errs(np.asarray(vals[:t], dtype=float))
                err = abs(res["point"][0] - actual)
                records.append({
                    "company": co, "metric": me, "method": m,
                    "target": f"FY{fy}Q{fq}", "as_of_index": t - 1,
                    "as_of_period": f"FY{labels[t - 1][0]}Q{labels[t - 1][1]}",
                    "actual": actual, "pred": res["point"][0],
                    "lower": res["lower"][0], "upper": res["upper"][0],
                    "mase": round(err / denom, 4) if denom else None,
                    "covered": bool(res["lower"][0] <= actual <= res["upper"][0]),
                    "train_len": t})

    def agg(rows):
        ok = [r for r in rows if "error" not in r]
        if not ok:
            return {"n": 0, "mase": None, "coverage": None, "mae": None}
        return {"n": len(ok),
                "mase": round(statistics.fmean(r["mase"] for r in ok), 4),
                "coverage": round(sum(r["covered"] for r in ok) / len(ok), 4),
                "mae": round(statistics.fmean(abs(r["pred"] - r["actual"]) for r in ok), 2)}

    overall = {m: agg([r for r in records if r["method"] == m]) for m in METHODS}
    by_series = {f"{co}|{me}": agg([r for r in records
                                    if r["company"] == co and r["metric"] == me])
                 for (co, me) in series}
    return {"created": datetime.now().isoformat(timespec="seconds"),
            "spec": {"origins_per_series": origins, "step": 1, "level": 0.8,
                     "mase_baseline": "seasonal_naive", "metrics": METRICS,
                     "db": str(BASE_DB)},
            "overall": overall, "by_series": by_series, "records": records}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origins", type=int, default=12)
    args = ap.parse_args(argv[1:])
    data = run(args.origins)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"written {OUT}  records={len(data['records'])}")
    for m, s in data["overall"].items():
        print(f"{m:16s} n={s['n']:3d} MASE={s['mase']} coverage={s['coverage']} MAE={s['mae']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))

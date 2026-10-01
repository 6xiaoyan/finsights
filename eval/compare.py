"""P3.4：两次运行对比（plan 5.4）。

用法：`python -m eval.compare <run_a> <run_b>`
差值小于两次运行的方差之和（var_a + var_b）时标注"不显著"；差值为 0（含自比）恒不显著。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from eval.runner import RUNS, REPORTS


def load(run_id: str, runs_root: Path = RUNS) -> dict:
    return json.loads((runs_root / run_id / "summary.json").read_text(encoding="utf-8"))


def _per_trial_acc(summary: dict, cat: str) -> list[float]:
    recs = [r for r in summary["records"] if r["category"] == cat]
    by_trial: dict[int, list[bool]] = {}
    for r in recs:
        by_trial.setdefault(r["trial"], []).append(r["correct"])
    return [sum(v) / len(v) for v in by_trial.values()]


def compare(run_a: str, run_b: str, runs_root: Path = RUNS, reports_root: Path = REPORTS) -> str:
    sa, sb = load(run_a, runs_root), load(run_b, runs_root)
    cats = sorted({r["category"] for r in sa["records"] + sb["records"]})
    lines = [f"# 对比 {run_a}（{sa['agent']}） vs {run_b}（{sb['agent']}）", "",
             "| 指标 | a | b | 差值(a−b) | 判定 |", "|---|---|---|---|---|"]
    for cat in cats:
        aa, bb = _per_trial_acc(sa, cat), _per_trial_acc(sb, cat)
        if not aa or not bb:
            continue
        ma, mb = statistics.mean(aa), statistics.mean(bb)
        va = statistics.pvariance(aa) if len(aa) > 1 else 0.0
        vb = statistics.pvariance(bb) if len(bb) > 1 else 0.0
        diff = ma - mb
        verdict = "不显著" if (diff == 0 or abs(diff) < va + vb) else "显著"
        lines.append(f"| {cat} 准确率 | {ma:.2f} | {mb:.2f} | {diff:+.2f} | {verdict}（方差和 {va + vb:.3f}） |")
    for key, label in (("steps", "平均步数"), ("tool_calls", "工具调用"),
                       ("sql_errors", "SQL 报错"), ("latency_s", "延迟(s)"),
                       ("cost_usd", "成本($)")):
        va_ = [r[key] for r in sa["records"]]
        vb_ = [r[key] for r in sb["records"]]
        if not va_ or not vb_:
            continue
        ma, mb = statistics.mean(va_), statistics.mean(vb_)
        va, vb = statistics.pvariance(va_), statistics.pvariance(vb_)
        diff = ma - mb
        verdict = "不显著" if (diff == 0 or abs(diff) < va + vb) else "显著"
        lines.append(f"| {label} | {ma:.2f} | {mb:.2f} | {diff:+.2f} | {verdict} |")
    out = "\n".join(lines) + "\n"
    reports_root.mkdir(parents=True, exist_ok=True)
    (reports_root / f"compare_{run_a}_vs_{run_b}.md").write_text(out, encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    args = ap.parse_args(argv[1:])
    print(compare(args.run_a, args.run_b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

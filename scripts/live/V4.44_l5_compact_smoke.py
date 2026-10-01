"""V4.44 live check：真实 LLM + 16K 窗口压缩冒烟（不进入 pytest，见 verify.md 0.5）。

运行：.venv/Scripts/python scripts/live/V4.44_l5_compact_smoke.py > docs/evidence/V4.44_output.txt 2>&1
通过标准：trace 中至少 1 次 L3 事件；最终答案通过 verifier；没有 API 报错。
实现方式：复制 config.yaml 并把 context 窗口调小（window=16000 等），
其余（模型、限速、护栏）与正式运行一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, ".")

from agent.llm import LLMClient  # noqa: E402
from agent.loop import make_run_ctx, run  # noqa: E402

DB = "data/finsights.duckdb"
QUESTION = ("联想 FY24Q2 存货环比明显上升，请做一次完整归因："
            "先量化变化并给出分部贡献，检查这种变化是否属于季节性正常，"
            "再与 HP、Dell 的同自然季度表现对比，查看该期附注是否有口径变化，"
            "最后给出根因标签和依据。")


def make_smoke_config(out_path: Path) -> Path:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    c = cfg["context"]
    c.update(window=16000, reserve_output=3000, compact_threshold=2000,
             clear_at_least=200, l3_ratio=0.05, stub_keep_last=3,
             ledger_keep_recent_turns=2, l5_enabled=True, l5_keep_last_turns=1)
    out_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return out_path


def main() -> int:
    evidence = Path("docs/evidence")
    evidence.mkdir(parents=True, exist_ok=True)
    cfg_path = make_smoke_config(evidence / "V4.44_config_used.yaml")
    trace_path = evidence / "V4.44_trace.jsonl"
    if trace_path.exists():
        trace_path.unlink()

    client = LLMClient(config_path=cfg_path)
    ctx = make_run_ctx(DB, client, run_id="V4.44-smoke", config_path=cfg_path,
                       trace_path=str(trace_path))
    out = run(QUESTION, ctx)

    compact = [e for e in ctx.trace.events if e.get("type") == "compact"]
    by_level: dict[str, int] = {}
    for e in compact:
        by_level[e["level"]] = by_level.get(e["level"], 0) + 1
    fails = [e for e in ctx.trace.events if e.get("type") in ("compact_fail", "refuse")]
    print("== V4.44 冒烟结果 ==")
    print(f"status={out.answer.status} verified={out.verified}")
    print(f"stats={out.stats}")
    print(f"compact 事件按层级: {by_level}（要求 L3 ≥ 1）")
    print(f"compact_fail/refuse 事件: {fails}")
    print(f"结果 rid 数: {len(ctx.store.all())}")
    print("---- 答案 ----")
    print(out.answer.answer_md)
    ok = (out.answer.status == "answered" and out.verified and by_level.get("L3", 0) >= 1
          and not fails)
    print(f"\nV4.44: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""V4.44 live check：真实 LLM + 16K 窗口压缩冒烟（不进入 pytest，见 verify.md 0.5）。

运行（系统代理失效时需绕过，NO_PROXY=api.agnes-ai.cn）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn .venv/Scripts/python \
    scripts/live/V4.44_l5_compact_smoke.py > docs/evidence/V4.44_output.txt 2>&1
通过标准：trace 中至少 1 次 L3 事件；最终答案通过 verifier；没有 API 报错。
实现方式：复制 config.yaml 并把 context 窗口调小（window=16000 等），走 agent/v1 适配层
（带令牌桶限速——免费档 RPM 配额很紧），其余与正式运行一致。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml
from openai import RateLimitError

sys.path.insert(0, ".")

from agent.llm import LLMClient  # noqa: E402
from agent.v1 import run as run_v1  # noqa: E402
from eval.runner import RateLimiter  # noqa: E402

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
             ledger_keep_recent_turns=3, l5_enabled=True, l5_keep_last_turns=1)
    # 长任务归因题实测：20–30 步在投影过狠（keep=2）时模型会反复复核 r2/r3 烧完预算；
    # 冒烟放宽到 40 步、keep=3，压缩层级仍必然触发（阈值 2000 est-tokens）
    cfg["agent"]["max_steps"] = 40
    cfg["agent"]["max_wall_s"] = 900
    # 实测 C：模型每次打回都在定向改进（打回 1→2 问题集合收敛），但 2 次打回上限
    # 用尽时还没修完（尾数舍入如 z=0.09 vs 存储 0.0913、正文 ≈0% 需精确 0 的 claim）。
    # 打回次数是设计内的调参项（V4.11e），冒烟给到 4 次，让模型跑完修正闭环。
    cfg["agent"]["final_answer_max_retries"] = 4
    out_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return out_path


def main() -> int:
    evidence = Path("docs/evidence")
    evidence.mkdir(parents=True, exist_ok=True)
    cfg_path = make_smoke_config(evidence / "V4.44_config_used.yaml")

    client = LLMClient(config_path=cfg_path)
    last_err: Exception | None = None
    for attempt in range(3):          # 429 按分钟窗口恢复：整题重做，最多 3 次
        try:
            answer, info = run_v1(QUESTION, llm=client, db_path=Path(DB),
                                  as_of=date(2026, 10, 1), limiter=RateLimiter(6),
                                  config_path=cfg_path)
            break
        except RateLimitError as e:
            last_err = e
            print(f"[trial {attempt + 1}] 429 配额墙，等待 180s 后整题重做", file=sys.stderr)
            time.sleep(180 * (attempt + 1))
    else:
        print(f"V4.44: FAIL（限速重试耗尽: {last_err}）", file=sys.stderr)
        return 1

    compact = [e for e in info["trace"] if e.get("type") == "compact"]
    (evidence / "V4.44_trace.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in info["trace"]) + "\n",
        encoding="utf-8")
    by_level: dict[str, int] = {}
    for e in compact:
        by_level[e["level"]] = by_level.get(e["level"], 0) + 1
    fails = [e for e in info["trace"] if e.get("type") in ("compact_fail", "refuse")]
    print("== V4.44 冒烟结果 ==")
    print(f"status={answer.status} verified={info['verified']}")
    print("stats = " + str({k: v for k, v in info.items() if k != "trace"}))
    print(f"compact 事件按层级: {by_level}（要求 L3 ≥ 1）")
    print(f"compact_fail/refuse 事件: {fails}")
    print("---- 答案 ----")
    print(answer.answer_md)
    ok = (answer.status == "answered" and info["verified"] and by_level.get("L3", 0) >= 1
          and not fails)
    print(f"\nV4.44: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

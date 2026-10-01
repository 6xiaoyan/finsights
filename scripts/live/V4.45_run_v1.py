"""V4.45 live check：v1（P4 完整版 agent）真实运行 L1+L2（不进入 pytest，见 verify.md 0.5）。

运行（系统代理失效时需绕过）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn .venv/Scripts/python \
    scripts/live/V4.45_run_v1.py > docs/evidence/V4.45_output.txt 2>&1
通过标准：runs/<run_id>/ 下 60 个 trace（10 题 × 3 trials）；报告与 leaderboard 有 v1 行；
`python -m eval.compare <v0_run> <v1_run>` 中 v1 在 L1+L2 的总正确率不低于 v0。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

if __name__ == "__main__":
    # 与 v0 基线完全相同的采样参数（V3.7），保证两行可比
    sys.exit(main(["V4.45_run_v1", "--agent", "v1", "--datasets", "l1,l2",
                   "--trials", "3", "--concurrency", "2", "--max-rpm", "8"]))

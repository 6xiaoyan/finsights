"""V3.7 live check：v0 基线真实运行（不进入 pytest，见 verify.md 0.5）。

运行：.venv/Scripts/python scripts/live/V3.7_run_v0.py > docs/evidence/V3.7_output.txt 2>&1
通过标准：runs/<run_id>/ 下 30 个 trace 文件（10 题 × 3 trials）；
eval/reports/<run_id>.md 含每类均值±标准差与过程指标（步数/工具调用/SQL 报错/token/延迟/成本）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

if __name__ == "__main__":
    # 免费档实测：>10 RPM 持续请求会触发按分钟窗口的 429 配额墙，
    # 故压到 8 RPM、并发 2；runner 内另有 429 trial 级重试兜底。
    sys.exit(main(["V3.7_run_v0", "--agent", "v0", "--datasets", "l1,l2",
                   "--trials", "3", "--concurrency", "2", "--max-rpm", "8"]))

"""V5.10 live check：L3 归因评测 3 次 × 8 题口径（不进入 pytest，见 verify.md 0.5）。

题集（Q12 登记，事前规则"每注入类型取第一题 + 阴性取前三题"，非挑题）：
  l3_001 company_specific / l3_002 industry_wide / l3_003 mix_shift /
  l3_004 reclassification / l3_021 l3_022 l3_023 阴性对照 = 7 题可判。
第 8 题（真实事件）依赖 V5.11/V5.12 人工标注，标注完成后追加重跑；本次如实按 7 题运行。

通过标准（verify V5.10）：报告含 top-1、误归因、阴性题误报、证据完整性
（seasonal_check / peer_compare 调用比例）；失败题写入 eval/badcases.md。

运行（系统代理失效时需绕过）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= \
    .venv/Scripts/python scripts/live/V5.10_run_l3.py > docs/evidence/V5.10_output.txt 2>&1
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

IDS = "l3_001,l3_002,l3_003,l3_004,l3_021,l3_022,l3_023"

if __name__ == "__main__":
    sys.exit(main(["V5.10_run_l3", "--agent", "v1", "--datasets", "l3", "--ids", IDS,
                   "--trials", "3", "--concurrency", "2", "--max-rpm", "6"]))

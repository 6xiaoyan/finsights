"""V6.10 重跑前冒烟（不进入 pytest，见 verify.md 0.5）：L4 全部 4 题各 1 trial。

验证点：as_of 快照上 agent 能调用 forecast 工具、契约行（预测值:/预测区间:/方法:）
可被 eval/graders/l4.py 解析、gold 比对与 V6.11 provenance（claim 引用 forecast rid）正常。

运行（系统代理失效时需绕过）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= \
    .venv/Scripts/python scripts/live/V6.10_smoke_l4.py > docs/evidence/V6.10_smoke_output.txt 2>&1
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["V6.10_smoke_l4", "--agent", "v1", "--datasets", "l4",
                   "--trials", "1", "--concurrency", "2", "--max-rpm", "6"]))

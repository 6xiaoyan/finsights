"""V5.10 重跑前冒烟（不进入 pytest，见 verify.md 0.5）：bank 重建后每题 1 trial。

l3_002 industry_wide / l3_004 reclassification——两类旧 bank 缺陷（错期落点 / 零注入）的
代表题，验证：新题面与场景数据一致、agent 输出契约行可解析、打分链路（场景路由+gold）正常。

运行（系统代理失效时需绕过）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= \
    .venv/Scripts/python scripts/live/V5.10_smoke_regen.py > docs/evidence/V5.10_smoke_regen_output.txt 2>&1
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["V5.10_smoke_regen", "--agent", "v1", "--datasets", "l3",
                   "--ids", "l3_002,l3_004", "--trials", "1",
                   "--concurrency", "2", "--max-rpm", "6"]))

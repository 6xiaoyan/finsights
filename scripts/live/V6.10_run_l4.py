"""V6.10/V6.11 live check：L4 预测评测 3 次 × 4 题（不进入 pytest，见 verify.md 0.5）。

题库 eval/datasets/l4.jsonl（plan 8.3：4 题、不同公司 × 营收/应收/存货）；
每题 by construction 走 as_of 快照库（runner make_agent → etl.snapshot.make_snapshot），
agent 无途径看到目标期间实际值（V6.9 已由离线测试把关）。

通过标准：
- V6.10：报告同时含"agent"与"forecast 工具直接输出"两组 MASE + 80% 覆盖率
  （第二组需先跑 scripts/backtest_forecast.py 生成 eval/reports/forecast_backtest.json）；
- V6.11：答案中的预测数字都引用 forecast 结果的 rid（打分器 provenance_ok，
  verifier 的 stop 钩子另行把关）。

运行（系统代理失效时需绕过）：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= \
    .venv/Scripts/python scripts/live/V6.10_run_l4.py > docs/evidence/V6.10_output.txt 2>&1
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from eval.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["V6.10_run_l4", "--agent", "v1", "--datasets", "l4",
                   "--trials", "3", "--concurrency", "2", "--max-rpm", "6"]))

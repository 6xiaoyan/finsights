"""V7.3 最小鲁棒检查 + MVP CLI 端到端真实链路（不进入 pytest，见 verify.md 0.5）。

六问顺序跑 `agent.cli.main`（与用户入口同路径，每题一份 runs/cli-* 运行包）：
  1 简单取数、2 需计算分析、3 需披露归因（联想 FY2023Q3 真实事件）、
  4 错误前提（数据实为上升 22%）、5 不支持的公司、6 财年/自然年歧义。
判定口径（人工读证据）：4/5/6 期望 refuse/clarify 或明确纠正/声明口径；
1–3 期望 answered 且引用可回查。失败区分阻断缺陷与待优化 bad case，不扩展新评测。

运行：
  PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn HTTPS_PROXY= HTTP_PROXY= ALL_PROXY= \
    .venv/Scripts/python scripts/live/V7.3_min_robust.py > docs/evidence/V7.3_min_output.txt 2>&1
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from agent.cli import main as cli_main  # noqa: E402

QUESTIONS = [
    ("e2e_fetch", "联想 FY2025Q2 期末的营业收入（当季）和存货余额分别是多少？"),
    ("e2e_calc", "联想 FY2025Q2 的营运资本相比上一季是改善还是恶化？给出计算依据。"),
    ("e2e_attrib", "联想 FY2023Q3 存货出现下降，原因是什么？请结合公司披露解释并标注出处。"),
    ("robust_false_premise", "联想 FY2025Q1 营收出现明显下滑，主要原因是什么？"),
    ("robust_unsupported", "滴滴全球 2024 年的毛利率和存货周转是多少？"),
    ("robust_fy_ambiguity", "戴尔 2024 年第三季度的收入是多少？"),
]


def run_one(tag: str, question: str) -> None:
    t0 = time.monotonic()
    print(f"\n{'=' * 20} [{tag}] {question}\n", flush=True)
    try:
        rc = cli_main(["--question", question, "--max-rpm", "6"])
    except Exception as e:  # noqa: BLE001 取证脚本：记录后继续
        rc = f"exception:{type(e).__name__}:{e}"
    print(f"[{tag}] exit={rc} wall={time.monotonic() - t0:.0f}s "
          f"ts={datetime.now().isoformat(timespec='seconds')}", flush=True)


if __name__ == "__main__":
    for _tag, _q in QUESTIONS:
        run_one(_tag, _q)
    print("\nV7.3 最小检查完毕：逐题读 runs/cli-*/ 包判定 阻断缺陷 vs 待优化 bad case。")

"""Agent A：E2E 评测与数据集工具（docs/prd-agent-evaluation.md §5–6）。

模块边界（PRD §4 建议独占目录）：
- eval/financial_e2e/           本包
- eval/datasets/financial_real_v1/   30 题真实财报数据集（含参考答案）
- docs/eval_e2e_delivery.md     交付说明

不修改 Agent 行为、不修改 B 的目录（eval/agent_process/）、不自行合并 main。
"""
from eval.financial_e2e.schema import CaseRecord, load_dataset, save_dataset

__all__ = ["CaseRecord", "load_dataset", "save_dataset"]

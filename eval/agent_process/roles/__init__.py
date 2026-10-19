"""角色独立评测：Planner / Worker / Reviewer。

三种角色都支持两类输入：固定样例（`samples/`，B 自撰，验证评测流程）与
真实运行包轨迹（从运行包提取的实际过程）。评分调用在评测侧执行，
gold 与评测反馈都不回灌给被测角色。
"""
from eval.agent_process.roles.common import (EVIDENCE_KINDS, SAMPLES_DIR,
                                             evidence_basis, load_samples, make_judge)
from eval.agent_process.roles.planner import (PLANNER_DIMENSIONS, evaluate_package as
                                              evaluate_planner_package,
                                              evaluate_plan_measured,
                                              evaluate_sample as evaluate_planner_sample)

__all__ = [
    "SAMPLES_DIR", "EVIDENCE_KINDS", "evidence_basis", "load_samples", "make_judge",
    "PLANNER_DIMENSIONS", "evaluate_planner_package", "evaluate_plan_measured",
    "evaluate_planner_sample",
]

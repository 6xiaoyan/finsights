"""打分器注册表：类别 → grade 函数（每类一个打分器，plan 1.2）。"""
from __future__ import annotations

from importlib import import_module
from typing import Callable

_REGISTRY: dict[str, str] = {"L1": "eval.graders.l1", "L2": "eval.graders.l2",
                             "L3": "eval.graders.l3", "L4": "eval.graders.l4"}
# L5/L6 在 P7 加入。


def get_grader(category: str) -> Callable[[object, object], dict]:
    if category not in _REGISTRY:
        raise KeyError(f"未注册打分器类别: {category}（可用: {sorted(_REGISTRY)}）")
    return import_module(_REGISTRY[category]).grade

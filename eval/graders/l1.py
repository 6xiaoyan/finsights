"""P3 L1 打分器：claims 数值容差比较（plan 5.3——只看 claims 里的 value，不从自由文本解析数字）。"""
from __future__ import annotations

from eval.schema import Answer, Question


def grade(question: Question, answer: Answer) -> dict:
    gold = question.gold
    if answer.status != "answered":
        return {"correct": False, "detail": f"status={answer.status}"}
    tol = gold.tolerance * abs(gold.value)
    for c in answer.claims:
        if abs(c.value - gold.value) <= (tol if tol > 0 else gold.tolerance):
            return {"correct": True, "detail": f"claim {c.value} ≈ gold {gold.value}"}
    return {"correct": False,
            "detail": f"claims {[c.value for c in answer.claims]} 无一匹配 gold {gold.value}±{tol:.4g}"}

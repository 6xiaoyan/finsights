"""P2.2：指标目录（给 agent 看的：名称、中文名、类型、单位、可用维度）。"""
from __future__ import annotations

from semantic.compiler import load_metrics


def list_catalog() -> list[dict]:
    """全部指标的目录（name, label_zh, type, unit, dims）。"""
    metrics = load_metrics()
    return [
        {"name": name, "label_zh": m["label_zh"], "type": m["type"],
         "unit": m["unit"], "dims": m["dims"]}
        for name, m in sorted(metrics.items())
    ]


def describe(name: str) -> dict:
    metrics = load_metrics()
    if name not in metrics:
        raise KeyError(f"指标不存在: {name}")
    return metrics[name]

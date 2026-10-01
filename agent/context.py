"""P4.2：上下文视图占位（plan 6.6.5）。

P4.5 在这里实现 L1–L5 压缩与 on_context_overflow；当前 build_view 为恒等投影，
保证主循环从一开始就只把"视图"发给模型、原始历史永不改动（不变量 2）。
阈值等参数一律从 config 读取，代码中不写死（V4.43 将扫描本文件）。
"""
from __future__ import annotations

from typing import Any


class ContextTooLong(Exception):
    """API 报上下文超长时由 llm 层抛出（P4.5 接入）。"""


def build_view(history: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    return list(history)


def on_context_overflow(view: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    raise ContextTooLong("上下文压缩在 P4.5 实现，当前无法处理超长视图")

"""P4.2/P4.4：hooks（plan 6.8）。

pre_tool：final_answer 之前没调用过任何数据工具而答案里有数字 → 直接打回（V4.11 f）。
  run_sql 的 SQL 护栏在 tools/guard 内强制执行（execute 路径）；db/as_of 不是工具参数，模型不可改。
stop：护栏核验，默认走 agent/verifier.verify_answer（P4.4）；测试可从 RunContext.verifier 注入替换。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# 数字扫描时排除的"非数值"标签：财年季度（FY24Q2 / FY2024Q2）、自然季度（2024Q3）、
# 年份（2024 年 / 2024）、序数（第 3）、result_id 引用（r7）。P4.4 verifier 复用同一规则。
_EXCLUDE_RE = re.compile(
    r"FY\s?\d{2,4}\s*Q[1-4]|(?:19|20)\d{2}\s*Q[1-4]|(?:19|20)\d{2}\s*年|第\s*\d+(?:[、，.．]?|"
    r"\s*(?:点|条|步|章|节|部分))|\[\s*r\d+\s*\]|(?<![A-Za-z0-9.])r\d+(?![0-9A-Za-z])")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:[.,]\d+)*%?")  # 千分位+小数一体；跳过标识符内数字（p50、P95）


def bare_numbers(text: str) -> list[str]:
    """提取文本中的"真数值"（排除年份/季度标签/序号/rid 引用后）。

    Markdown 行首列表序号（如 `2. `、`3） `）仅移除序号本身，其后的财务数字仍参与扫描（交接 §4.2）。
    """
    text = re.sub(r"(?m)^\s*\d{1,2}[.、)]\s+", " ", text)
    return _NUMBER_RE.findall(_EXCLUDE_RE.sub(" ", text))


@dataclass
class Verdict:
    ok: bool
    feedback: str = ""


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""


def pre_tool(call: Any, ctx: Any) -> HookResult:
    """call 为 ToolCallOut；拒绝时把 reason 作为工具结果返回给模型。"""
    if call.name == "final_answer":
        try:
            args = json.loads(call.arguments)
        except Exception:
            return HookResult()  # 参数解析错误由 execute/loop 以工具错误文本处理
        if args.get("status", "answered") == "answered" and not ctx.tools_run:
            found = bare_numbers(args.get("answer_md", ""))
            if found:
                return HookResult(
                    blocked=True,
                    reason=f"已拒绝：答案中的数字 {found[:5]} 没有任何出处——此前没有调用过任何"
                           "数据工具。请先用 query_metric/variance/calc 等工具取数，"
                           "再在 claims 中引用对应 result_id。")
    return HookResult()


def stop(args: Any, ctx: Any) -> Verdict:
    """final_answer 护栏：默认 agent/verifier 的完整四项核验；ctx.verifier 可注入覆盖。"""
    if ctx.verifier is not None:
        return ctx.verifier(args, ctx)
    from agent.verifier import verify_answer   # 延迟导入避免环
    return verify_answer(args, ctx)

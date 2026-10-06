"""P4.6：v1 agent（plan 6.x 完整版）——把 agent/loop.run 接入评测 runner。

v0 是 PRD 6.8 对比表的基线；v1 是本轮交付：14 工具 + hooks/verifier 护栏 +
证据账本 + L1–L5 上下文压缩。本文件只做适配：注入真实 LLM、限速、整理 runner 需要的 info。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.llm import LLMClient
from agent.loop import make_run_ctx, run as loop_run
from eval.schema import Answer

DB_PATH = Path("data/finsights.duckdb")


class Paced:
    """限速包装：免费档按分钟窗口的 RPM 配额，每次 API 调用先过令牌桶。"""

    def __init__(self, client: Any, limiter: Any):
        self._client, self._limiter = client, limiter

    def chat(self, messages, tools=None, **kw):
        if self._limiter is not None:
            self._limiter.wait()
        return self._client.chat(messages, tools=tools, **kw)

    def __getattr__(self, name: str) -> Any:
        # 其余属性透传给真实客户端：last_call_events（重试留痕）、nudged、model 等。
        # 没有这层代理，ctx.llm 是 Paced 时 getattr(last_call_events) 恒空，重试不可追查。
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._client, name)


def run(question: str, llm: LLMClient | None = None, db_path: Path = DB_PATH,
        as_of=None, limiter: Any = None,
        config_path: Path | str | None = None) -> tuple[Answer, dict]:
    """跑一道题。返回 (Answer, runner 需要的 info：trace/steps/tokens/latency/sql_errors…)。"""
    client = llm if llm is not None else LLMClient()
    ctx = make_run_ctx(str(db_path), Paced(client, limiter) if limiter else client,
                       run_id="v1", as_of=as_of,
                       config_path=config_path or "config.yaml")
    out = loop_run(question, ctx)
    info = dict(out.stats)
    info["trace"] = list(ctx.trace.events)
    info["sql_errors"] = sum(
        1 for e in ctx.trace.events
        if e.get("type") == "tool_result" and e.get("tool") == "run_sql" and not e.get("ok"))
    info["verified"] = out.verified
    return out.answer, info

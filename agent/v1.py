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
    """限速包装：免费档按分钟窗口的 RPM 配额。

    限速点只有一处——`LLMClient.chat` 在每次真正发出的 API 请求前（含其内部 429 重试）
    调用共享限速器。因此当被包装的客户端自带限速能力（`paces_own_requests`）时，本包装
    只做"同一个限速器实例"的转发，不再自己 wait()；否则一次请求会被限速两遍，实际速率
    被腰斩。对没有限速能力的裸 mock 客户端（离线测试），退回由本包装 wait() 的旧语义。
    """

    def __init__(self, client: Any, limiter: Any):
        self._client = client
        if getattr(client, "paces_own_requests", False):
            # 显式传入的限速器优先（CLI --max-rpm / runner --max-rpm 覆盖 config 默认），
            # 并写回客户端，使主 Agent、multi 子 Agent、CLI 共用这一个实例。
            if limiter is not None:
                client.limiter = limiter
            self._limiter = None
        else:
            self._limiter = limiter

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

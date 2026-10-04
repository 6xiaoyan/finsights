"""LLM 客户端封装。

plan P0：封装 chat 调用（支持 tools），带重试（指数退避，最多重试 3 次）和超时，
返回 token 用量。模型名、base_url、温度等全部来自 config.yaml 与 .env，
代码中不写死任何模型名（verify V0.4）。
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from agent.context import ContextTooLong, calibrate, est_tokens

# 可重试：限流 / 超时 / 连接问题 / 服务端 5xx。
# 认证、参数、权限错误不重试，立即抛出（重试也不会成功）。
RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

# 上下文超长类报错：不重试，转成 ContextTooLong 交给压缩层做 reactive/PTL（plan 6.6.3）
_CONTEXT_LENGTH_RE = re.compile(
    r"maximum context length|context (?:length|window)|too many tokens|exceed\w*.{0,40}token"
    r"|超过.{0,16}(?:上下文|窗口|长度)", re.I)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


@dataclass
class ToolCallOut:
    id: str
    name: str
    arguments: str  # JSON 字符串；由调用方用 pydantic 校验，不直接信任


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCallOut] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    message: dict[str, Any] = field(default_factory=dict)  # 可直接 append 回 messages


class LLMClient:
    def __init__(
        self,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        api_key: str | None = None,
        client: Any | None = None,  # 测试时注入 mock
    ):
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        llm = cfg["llm"]
        self.base_url: str = llm["base_url"]
        self.model: str = llm["model"]
        self.temperatures: dict[str, float] = {
            "agent": llm.get("temperature_agent", 0.2),
            "judge": llm.get("temperature_judge", 0.0),
        }
        # thinking 显式配置（verify V0.9）：不依赖 API 默认值，每次请求都传
        thinking_cfg = llm.get("enable_thinking") or {}
        self.enable_thinking: dict[str, bool] = {
            "agent": bool(thinking_cfg.get("agent", False)),
            "judge": bool(thinking_cfg.get("judge", False)),
            "summary": bool(thinking_cfg.get("summary", thinking_cfg.get("judge", False))),
        }
        self.timeout_s: float = llm.get("timeout_s", 120)
        self.max_retries: int = llm.get("max_retries", 3)  # 重试次数；总尝试 = max_retries + 1
        self.max_output_tokens_main: int = llm.get("max_output_tokens_main", 8000)   # A1 输出上限
        self.max_output_tokens_summary: int = llm.get("max_output_tokens_summary", 4000)

        if api_key is None:
            load_dotenv()
            api_key = os.environ.get("LLM_API_KEY")
        if not api_key:
            raise RuntimeError(".env 中缺少 LLM_API_KEY（参考 .env.example）")

        self._client = client or OpenAI(
            base_url=self.base_url,
            api_key=api_key,
            timeout=self.timeout_s,
            max_retries=0,  # 重试由本类控制，避免与 SDK 内置重试叠加
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        role: str = "agent",
        temperature: float | None = None,
    ) -> ChatResult:
        temp = temperature if temperature is not None else self.temperatures[role]
        # A1：本次调用的估算输入 token（含工具定义），返回后用 usage.prompt_tokens 校准比率
        est = est_tokens(messages, tool_def_chars=len(repr(tools)) if tools else 0)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": self.max_output_tokens_summary if role == "summary" else self.max_output_tokens_main,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": self.enable_thinking[role]}},
        }
        if tools:
            kwargs["tools"] = tools

        attempts = self.max_retries + 1
        last_err: Exception | None = None
        self.last_call_events: list[dict] = []  # 第一阶段：内部重试可追查（含等待与错误）
        for attempt in range(attempts):
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                if _CONTEXT_LENGTH_RE.search(str(e)):
                    raise ContextTooLong(f"API 报上下文超长: {e}") from e
                raise   # 其他参数错误不重试，立即抛出
            except RETRYABLE_ERRORS as e:
                last_err = e
                wait = 2**attempt if attempt < attempts - 1 else 0
                self.last_call_events.append({"type": "llm_retry", "attempt": attempt + 1,
                                              "wait_s": wait, "error": str(e)[:200]})
                if attempt < attempts - 1:
                    time.sleep(wait)  # 指数退避：1s, 2s, 4s
                continue
            result = self._parse(resp)
            actual = int(result.usage.get("prompt_tokens") or 0)
            if actual:  # 偏差随 usage["est_prompt_tokens"] 进 trace（loop 的 assistant 事件）
                calibrate(est, actual)
                result.usage["est_prompt_tokens"] = est
            return result
        assert last_err is not None
        raise last_err

    @staticmethod
    def _parse(resp: Any) -> ChatResult:
        msg = resp.choices[0].message
        # reasoning 内容只写日志，不写回对话（questions.md Q2 答复）
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        if reasoning:
            print(f"[reasoning] {reasoning}", file=sys.stderr)
        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        tool_calls = [
            ToolCallOut(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments,
            )
            for tc in (msg.tool_calls or [])
        ]
        message: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in tool_calls
            ]
        return ChatResult(content=msg.content, tool_calls=tool_calls, usage=usage, message=message)


def main(argv: list[str]) -> int:
    prompt = argv[1] if len(argv) > 1 else "ping"
    client = LLMClient()
    result = client.chat([{"role": "user", "content": prompt}])
    text = result.content or ""
    print(text)
    if text:
        print(f"[usage] {result.usage}")
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

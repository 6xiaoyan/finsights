"""LLM 客户端封装。

plan P0：封装 chat 调用（支持 tools），带重试（指数退避，最多重试 3 次）和超时，
返回 token 用量。模型名、base_url、温度等全部来自 config.yaml 与 .env，
代码中不写死任何模型名（verify V0.4）。
"""
from __future__ import annotations

import os
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
    InternalServerError,
    OpenAI,
    RateLimitError,
)

# 可重试：限流 / 超时 / 连接问题 / 服务端 5xx。
# 认证、参数、权限错误不重试，立即抛出（重试也不会成功）。
RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

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
        self.timeout_s: float = llm.get("timeout_s", 120)
        self.max_retries: int = llm.get("max_retries", 3)  # 重试次数；总尝试 = max_retries + 1

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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
        }
        if tools:
            kwargs["tools"] = tools

        attempts = self.max_retries + 1
        last_err: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except RETRYABLE_ERRORS as e:
                last_err = e
                if attempt < attempts - 1:
                    time.sleep(2**attempt)  # 指数退避：1s, 2s, 4s
                continue
            return self._parse(resp)
        assert last_err is not None
        raise last_err

    @staticmethod
    def _parse(resp: Any) -> ChatResult:
        msg = resp.choices[0].message
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

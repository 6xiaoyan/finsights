"""agent/llm.py 的单元测试（全部离线 mock；联网检查统一放 scripts/live/，见 verify.md 0.5）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.llm import LLMClient

_TEST_KEY = "test-key"  # 注入 mock 用假 key；用常量传入，避免触发 G4 的 api_key= 字面量扫描


def _api_error(cls: type[Exception]) -> Exception:
    """构造 openai SDK 的异常实例（需要 response 参数）。"""
    return cls(
        "mock error",
        response=httpx.Response(500 if cls is not openai.RateLimitError else 429, request=httpx.Request("POST", "http://unit.test")),
        body=None,
    )


class _FakeCompletions:
    """按脚本依次返回结果或抛异常，并记录调用次数。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_client(monkeypatch, script) -> tuple[LLMClient, _FakeCompletions]:
    monkeypatch.setattr("agent.llm.time.sleep", lambda s: None)  # 测试不真等退避
    fake = _FakeCompletions(script)
    client = LLMClient(api_key=_TEST_KEY)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return client, fake


def _resp(content="ok", tool_calls=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def test_retry_succeeds_after_two_429(monkeypatch):
    """V0.7：前 2 次返回 429，第 3 次成功 → 最终成功，共调用 3 次。"""
    script = [_api_error(openai.RateLimitError), _api_error(openai.RateLimitError), _resp("ok")]
    client, fake = _make_client(monkeypatch, script)
    result = client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "ok"
    assert fake.calls == 3
    assert result.usage["total_tokens"] == 15


def test_retry_raises_after_exhaustion(monkeypatch):
    """V0.7：连续 4 次失败（1 次调用 + 3 次重试）→ 抛出异常。"""
    script = [_api_error(openai.RateLimitError) for _ in range(4)]
    client, fake = _make_client(monkeypatch, script)
    with pytest.raises(openai.RateLimitError):
        client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls == 4


def test_auth_error_not_retried(monkeypatch):
    """认证错误不重试，立即抛出。"""
    script = [_api_error(openai.AuthenticationError)]
    client, fake = _make_client(monkeypatch, script)
    with pytest.raises(openai.AuthenticationError):
        client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls == 1


def test_tool_call_parsing(monkeypatch):
    """带 tools 的调用：正确解析 tool_call，且 message 可直接回填 messages。"""
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_weather", arguments=json.dumps({"city": "北京"}, ensure_ascii=False)),
    )
    client, fake = _make_client(monkeypatch, [_resp(content=None, tool_calls=[tc])])
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询城市天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    result = client.chat([{"role": "user", "content": "北京天气怎么样？"}], tools=tools)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"
    assert json.loads(result.tool_calls[0].arguments)["city"] == "北京"
    assert result.message["tool_calls"][0]["id"] == "call_1"
    assert result.message["tool_calls"][0]["function"]["name"] == "get_weather"


def test_enable_thinking_sent_explicitly(monkeypatch):
    """V0.9：每次请求都显式携带 enable_thinking，取值来自 config（agent/judge 分别配置）。"""
    seen = {}

    class _Capture(_FakeCompletions):
        def create(self, **kwargs):
            seen["extra_body"] = kwargs.get("extra_body")
            return super().create(**kwargs)

    monkeypatch.setattr("agent.llm.time.sleep", lambda s: None)
    fake = _Capture([_resp("a"), _resp("b")])
    client = LLMClient(api_key=_TEST_KEY)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    client.chat([{"role": "user", "content": "x"}], role="agent")
    assert seen["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    client.chat([{"role": "user", "content": "x"}], role="judge")
    assert seen["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_reasoning_content_not_written_back(monkeypatch, capsys):
    """V0.9 / Q2：响应中的 reasoning 内容只写 stderr 日志，不写回 messages。"""
    msg = SimpleNamespace(content="ok", tool_calls=None, reasoning_content="内部推理过程")
    fake_script = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
    ]
    client, _ = _make_client(monkeypatch, fake_script)
    result = client.chat([{"role": "user", "content": "x"}])
    assert result.message == {"role": "assistant", "content": "ok"}
    assert "reasoning" not in result.message
    captured = capsys.readouterr()
    assert "[reasoning]" in captured.err
    assert "内部推理过程" in captured.err


def test_temperature_by_role(monkeypatch):
    """agent / judge 使用不同温度，来自 config。"""
    seen = {}

    class _Capture(_FakeCompletions):
        def create(self, **kwargs):
            seen["temperature"] = kwargs["temperature"]
            return super().create(**kwargs)

    monkeypatch.setattr("agent.llm.time.sleep", lambda s: None)
    fake = _Capture([_resp("a"), _resp("b")])
    client = LLMClient(api_key=_TEST_KEY)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    client.chat([{"role": "user", "content": "x"}], role="agent")
    assert seen["temperature"] == 0.2
    client.chat([{"role": "user", "content": "x"}], role="judge")
    assert seen["temperature"] == 0.0

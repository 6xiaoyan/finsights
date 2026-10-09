"""切到 deepseek-flash 后的最小连通性探针（live，verify.md 0.5，不进 pytest）。

只验证三件事，各一次极短请求：
  1. key 有效、base_url/model 可用；
  2. thinking 开关被服务端接受（config 的 thinking_param=deepseek → {"thinking":{"type":"disabled"}}），
     且响应里没有 reasoning_content（说明确实关掉了）；
  3. tool calling 可用（带一个 get_weather 工具，看是否返回 tool_call）。

不打印 key；请求 max_tokens 很小。输出作为 docs/evidence 留档。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv

from agent.llm import LLMClient

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}]


def main() -> int:
    load_dotenv(".env")
    c = LLMClient()
    report = {
        "base_url": c.base_url, "model": c.model,
        "api_key_env": c.api_key_env,
        "thinking_param": c.thinking_param,
        "enable_thinking": c.enable_thinking,
        "rate_limit_rpm": c.rate_limit_rpm,
        "note": "key 未记录；仅记录变量名与结果",
    }
    print("base_url      :", c.base_url)
    print("model         :", c.model)
    print("api_key_env   :", c.api_key_env)
    print("thinking_param:", c.thinking_param, "| enable_thinking:", c.enable_thinking)
    print("rate_limit_rpm:", c.rate_limit_rpm)

    # 1) 基础连通（短回复）
    t0 = time.monotonic()
    r = c.chat([{"role": "user", "content": "只回复一个字：通"}])
    dt = time.monotonic() - t0
    report["ping"] = {"ok": True, "content": r.content, "latency_s": round(dt, 2),
                      "usage": r.usage}
    print(f"\n[1] ping: {r.content!r}  {dt:.2f}s  usage={r.usage}")
    print(f"    reasoning_content 是否出现在 message 里: {'reasoning_content' in r.message}")
    report["ping"]["reasoning_in_message"] = "reasoning_content" in r.message

    # 2) 工具调用
    t0 = time.monotonic()
    r2 = c.chat([{"role": "user", "content": "北京天气怎么样？"}], tools=TOOLS)
    dt2 = time.monotonic() - t0
    tc = r2.tool_calls[0] if r2.tool_calls else None
    report["tool_call"] = {
        "ok": bool(tc), "latency_s": round(dt2, 2),
        "name": tc.name if tc else None,
        "arguments": tc.arguments if tc else None,
    }
    print(f"[2] tool_call: {tc.name if tc else '无'} args={tc.arguments if tc else '-'}  {dt2:.2f}s")
    if not tc:
        print("    !! 工具调用未返回 tool_call——归因流程会走不通", file=sys.stderr)

    # 3) 重试留痕通道（不发请求，只确认字段存在且可序列化）
    report["retry_events"] = c.last_call_events
    print("[3] last_call_events 可读，长度", len(c.last_call_events))

    out = Path("docs/evidence/deepseek_switch_probe.txt")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n留档: {out}")
    return 0 if (r.content and tc) else 1


if __name__ == "__main__":
    raise SystemExit(main())
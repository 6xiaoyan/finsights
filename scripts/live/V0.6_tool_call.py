"""V0.6 live check：真实 LLM 的工具调用测试（不进入 pytest，见 verify.md 0.5）。

运行：.venv/Scripts/python scripts/live/V0.6_tool_call.py > docs/evidence/V0.6_output.txt 2>&1
通过标准：返回的 tool_call 名为 get_weather，参数中包含"北京"。
"""
from __future__ import annotations

import datetime as _dt
import json
import sys

sys.path.insert(0, ".")

from agent.llm import LLMClient  # noqa: E402

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市当前天气。当用户询问天气时使用。",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "城市名"}},
            "required": ["city"],
        },
    },
}


def main() -> int:
    client = LLMClient()
    print(f"time: {_dt.datetime.now().isoformat()}")
    print(f"model: {client.model}")
    r = client.chat([{"role": "user", "content": "北京天气怎么样？"}], tools=[WEATHER_TOOL])
    calls = [{"name": t.name, "arguments": t.arguments} for t in r.tool_calls]
    print(f"tool_calls: {json.dumps(calls, ensure_ascii=False)}")
    ok = len(r.tool_calls) == 1 and r.tool_calls[0].name == "get_weather" and "北京" in r.tool_calls[0].arguments
    print("V0.6:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""P0 模型能力探针（真实调用，自由运行，不进入 pytest 收集）。

目的：评估 agnes-3.0-flash 是否胜任本项目对 LLM 的角色要求（编排、叙述、
在给定证据上的归因推理、JSON 遵从、错误前提识别）。算术与取数按设计由代码完成，
因此探针不测算术，测的是上述五点。

运行：.venv/Scripts/python docs/evidence/P0_capability_probe.py > docs/evidence/P0_capability_probe_output.txt 2>&1
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from agent.llm import LLMClient  # noqa: E402

ROOT_CAUSE_LABELS = ["no_anomaly", "seasonal", "industry_wide", "company_specific", "mix_shift", "reclassification"]

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


def attribution_prompt(rows: str) -> str:
    return f"""你是财务分析助手。以下是三家公司 2024Q3 存货的环比变化，以及各自过去 8 年同季度（Q3）环比变化的分布（均值和标准差）。z-score 已由系统算好，你不需要做任何计算。附注检查：三家公司当期均未披露会计口径变更。

{rows}

问题：联想存货上升最可能属于哪类原因？只能从这些标签中选一个：{ROOT_CAUSE_LABELS}。

输出要求：只输出一个 JSON 对象，不要输出任何其他文字：
{{"label": "<标签>", "account": "inventory", "direction": "up", "confidence": <0到1的小数>, "evidence": "<一句话依据>"}}"""


INDUSTRY_WIDE_ROWS = """- Lenovo：本季 +18.2%；历年 Q3 环比：均值 +4.1%，标准差 3.0%（z≈+4.7）
- HP：本季 +17.5%；历年 Q3 环比：均值 +4.5%，标准差 3.2%（z≈+4.1）
- Dell：本季 +15.8%；历年 Q3 环比：均值 +3.8%，标准差 2.9%（z≈+4.1）"""

COMPANY_SPECIFIC_ROWS = """- Lenovo：本季 +18.2%；历年 Q3 环比：均值 +4.1%，标准差 3.0%（z≈+4.7）
- HP：本季 +2.1%；历年 Q3 环比：均值 +4.5%，标准差 3.2%（z≈-0.8）
- Dell：本季 -0.5%；历年 Q3 环比：均值 +3.8%，标准差 2.9%（z≈-1.5）"""


def grade_json(text: str, expected: str) -> tuple[bool, str]:
    """从回复中提取 JSON 并核对标签。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        obj = json.loads(s.strip())
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败: {e}"
    label = obj.get("label")
    ok = label == expected and obj.get("account") == "inventory" and obj.get("direction") == "up"
    detail = f"label={label} (期望 {expected}), confidence={obj.get('confidence')}, evidence={obj.get('evidence', '')[:80]}"
    return ok, detail


def premise_check(text: str) -> tuple[bool, str]:
    """错误前提题：回复应指出存货实际上是上升的，而不是顺着"下降"去解释。"""
    indicators = ["上升", "增长", "并未下降", "没有下降", "不是下降", "增加"]
    ok = any(k in text for k in indicators)
    return ok, "指出了前提错误" if ok else "未纠正前提"


def main() -> int:
    client = LLMClient()
    results: list[tuple[str, bool, str]] = []

    # --- 探针 1：基础连通 + 中文（V0.5）---
    r = client.chat([{"role": "user", "content": "你好，请用一句话自我介绍，并说明你是哪个模型。"}], temperature=0.0)
    print("=" * 70)
    print("[探针1 ping] 回复：")
    print(r.content)
    print(f"[usage] {r.usage}")
    results.append(("ping_连通与中文", bool(r.content and r.content.strip()), r.usage.get("total_tokens", 0).__str__()))

    # --- 探针 2：工具调用（V0.6）---
    r = client.chat([{"role": "user", "content": "北京天气怎么样？"}], tools=[WEATHER_TOOL], temperature=0.0)
    print("=" * 70)
    print("[探针2 工具调用] tool_calls：")
    print(json.dumps([{"name": t.name, "arguments": t.arguments} for t in r.tool_calls], ensure_ascii=False))
    ok = len(r.tool_calls) == 1 and r.tool_calls[0].name == "get_weather" and "北京" in r.tool_calls[0].arguments
    results.append(("工具调用_get_weather_北京", ok, ""))

    # --- 探针 3：归因推理 A（期望 industry_wide：三家同季共振）---
    r = client.chat([{"role": "user", "content": attribution_prompt(INDUSTRY_WIDE_ROWS)}], temperature=0.0)
    print("=" * 70)
    print("[探针3 归因A 三家共振 → 期望 industry_wide] 回复：")
    print(r.content)
    ok, detail = grade_json(r.content or "", "industry_wide")
    results.append(("归因A_industry_wide", ok, detail))

    # --- 探针 4：归因推理 B（期望 company_specific：仅联想异常）---
    r = client.chat([{"role": "user", "content": attribution_prompt(COMPANY_SPECIFIC_ROWS)}], temperature=0.0)
    print("=" * 70)
    print("[探针4 归因B 仅联想异常 → 期望 company_specific] 回复：")
    print(r.content)
    ok, detail = grade_json(r.content or "", "company_specific")
    results.append(("归因B_company_specific", ok, detail))

    # --- 探针 5：错误前提（L6 型）---
    bad = COMPANY_SPECIFIC_ROWS.replace("本季 +18.2%", "本季 +18.2%")
    question = "联想 FY24Q2 存货为什么明显下降了？请给出原因。\n\n数据：" + bad
    r = client.chat([{"role": "user", "content": question}], temperature=0.0)
    print("=" * 70)
    print("[探针5 错误前提：数据是上升，问为何下降] 回复：")
    print(r.content)
    ok, detail = premise_check(r.content or "")
    results.append(("错误前提_纠正", ok, detail))

    print("=" * 70)
    print("[汇总]")
    all_ok = True
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        line = f"{mark}  {name}"
        if detail:
            line += f"  ({detail})"
        print(line)
    print("总体：", "ALL PASS" if all_ok else "有失败项")
    return 0


if __name__ == "__main__":
    sys.exit(main())

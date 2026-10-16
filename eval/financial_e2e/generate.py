"""得分点候选生成（PRD §5.4）。

便宜模型根据已核验来源和参考答案生成得分点候选；生成结果强制 review_status=draft，
不得由生成模型自行设 approved（PRD §5.4）。先用于 3–5 题样本校准，再扩至 30 题。

生成器的输入不含"禁止断言"的最终裁决，只用于产出候选；人工/独立审核者完成来源、
可回答性与替代答案公平性检查后才能启用正式评分。
"""
from __future__ import annotations

import json
from typing import Any

from eval.contracts import ScoringPoint
from eval.financial_e2e.schema import CaseRecord

GENERATOR_PROMPT_VERSION = "e2e-point-gen-v1"

POINT_GEN_SYSTEM = (
    "你是财务评测的得分点设计助手。根据题目、参考答案、来源与数值断言，"
    "把参考答案拆成原子得分点。要求：\n"
    "1) 每个 point_id 稳定且唯一，形如 p1/p2/...；\n"
    "2) atomic_judgment 是最小语义单元，不与其它点重叠；\n"
    "3) required 取 required/optional，默认 required；只把题目核心结论设为 required；\n"
    "4) acceptable_equivalents 列可接受的等价表达；contradiction_examples 列典型矛盾反例；\n"
    "5) 不要要求答案罗列全部中间计算或零残差；\n"
    "6) 不要引入来源与数值断言之外的数字。\n"
    "输出单个 JSON 数组：[{\"point_id\": \"p1\", \"atomic_judgment\": ..., "
    "\"condition\": ..., \"required\": \"required\", \"weight\": 1.0, "
    "\"evidence_source\": [...], \"acceptable_equivalents\": [...], "
    "\"contradiction_examples\": [...], \"verification\": ...}]。"
)


def _extract_json_array(text: str) -> list:
    text = text.strip()
    import re
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            raise ValueError(f"生成器输出不是合法 JSON 数组: {text[:200]!r}")
        data = json.loads(m.group(0))
    if not isinstance(data, list):
        raise ValueError("生成器输出应为数组")
    return data


def build_prompt(case: CaseRecord) -> str:
    sources = "\n".join(f"- {s.title}（{s.publish_date}，{s.period}，p{s.page}）"
                        for s in case.sources)
    nums = "\n".join(f"- {a.metric} {a.period}: {a.value} {a.unit}"
                     + (f"（基期 {a.baseline_period}）" if a.baseline_period else "")
                     for a in case.numeric_assertions)
    return (f"【题目】\n{case.question}\n\n【范围】\n{case.scope}\n\n"
            f"【参考答案】\n{case.reference_answer}\n\n【来源】\n{sources}\n\n"
            f"【数值断言】\n{nums}\n")


def generate_points(case: CaseRecord, client: Any) -> tuple[list[ScoringPoint], dict]:
    """调用生成模型产出候选点。返回 (points, generator_provenance)。"""
    res = client.chat([{"role": "system", "content": POINT_GEN_SYSTEM},
                       {"role": "user", "content": build_prompt(case)}], role="judge")
    raw = _extract_json_array(res.content or "")
    points = [ScoringPoint(**p) for p in raw]
    prov = {"model": getattr(client, "model", None),
            "prompt_version": GENERATOR_PROMPT_VERSION,
            "usage": dict(res.usage or {})}
    return points, prov


def apply_candidate(case: CaseRecord, points: list[ScoringPoint], prov: dict) -> CaseRecord:
    """写入候选点，强制 draft（不信任生成模型自报状态）。"""
    return case.model_copy(update={"scoring_points": points, "generator": prov,
                                   "review_status": "draft"})

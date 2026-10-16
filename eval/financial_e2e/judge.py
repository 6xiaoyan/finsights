"""LLM-as-judge 封装（PRD §6.1）。

- 逐点评判：输入问题、参考点、可用事实和最终答案；输出 met/partial/missing/contradicted/unknown。
- 整篇评判：扣题、推理连贯、结论与证据一致、条件/不确定性、表达清晰度，各 1–5 分。
- 生成参考答案的模型与评分模型即便同服务也分开调用、提示与记录（PRD §9.2）。

Judge 通过 `Judge` 协议注入：live 用 LLMJudge（LLMClient），离线测试注入 CallableJudge。
第一版不接 embedding；语义等价在逐点评判中检查。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

POINT_PROMPT_VERSION = "e2e-point-judge-v1"
WHOLE_PROMPT_VERSION = "e2e-whole-judge-v1"

VERDICTS = ("met", "partial", "missing", "contradicted", "unknown")
WHOLE_DIMENSIONS = ("relevance", "coherence", "evidence_consistency", "uncertainty", "clarity")

POINT_SYSTEM = (
    "你是严格的财务答案评分员。只依据给定【参考点】【可用事实】【最终答案】判断该参考点是否被满足。"
    "不得引入外部知识，不得因文风或长度加减分。"
    "verdict 取值：met=完整满足；partial=仅部分满足（只在参考点声明了部分得分规则时用）；"
    "missing=未提及；contradicted=与参考点矛盾；unknown=证据不足或答案缺失无法判断。"
    "输出单个 JSON 对象：{\"verdict\": ..., \"evidence\": 答案中的原句片段或空串, \"reason\": 简要理由}。"
)

WHOLE_SYSTEM = (
    "你是整篇答案评分员。仅依据问题与最终答案，按 5 个维度各给 1–5 整数分："
    "relevance=扣题、coherence=推理连贯、evidence_consistency=结论与证据一致、"
    "uncertainty=条件与不确定性表述、clarity=表达清晰度。"
    "不得读取或参考任何内部审阅意见。输出单个 JSON 对象："
    "{\"dimensions\": {\"relevance\": n, ...}, \"reason\": 简要理由}。"
)


class Judge(Protocol):
    def score_point(self, question: str, point: dict, facts: str, answer: str) -> dict: ...
    def score_whole(self, question: str, answer: str) -> dict: ...


def _extract_json(text: str) -> dict:
    """从可能带 ```json 包裹或前后缀的模型输出里取第一个 JSON 对象。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError(f"Judge 输出不是合法 JSON: {text[:200]!r}")


class _UsageTracker:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def add(self, usage: dict) -> None:
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

    def snapshot(self) -> dict:
        return {"prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens}


class LLMJudge:
    """live judge：复用 agent.llm.LLMClient，role=judge，温度取 config 的 judge 温度。"""

    name = "llm"
    version = POINT_PROMPT_VERSION

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            from agent.llm import LLMClient
            client = LLMClient()
        self.client = client
        self.model = model or getattr(client, "model", None)
        self.usage = _UsageTracker()

    def _chat_json(self, system: str, user: str) -> dict:
        res = self.client.chat([{"role": "system", "content": system},
                                {"role": "user", "content": user}], role="judge")
        self.usage.add(res.usage)
        return _extract_json(res.content or "")

    def score_point(self, question: str, point: dict, facts: str, answer: str) -> dict:
        user = (f"【问题】\n{question}\n\n【参考点】\n{json.dumps(point, ensure_ascii=False)}"
                f"\n\n【可用事实】\n{facts or '（无额外事实，仅依据参考点与答案）'}"
                f"\n\n【最终答案】\n{answer or '（空：未交付答案）'}")
        out = self._chat_json(POINT_SYSTEM, user)
        verdict = str(out.get("verdict", "unknown")).lower()
        if verdict not in VERDICTS:
            verdict = "unknown"
        return {"verdict": verdict, "evidence": out.get("evidence", ""),
                "reason": out.get("reason", ""), "model": self.model,
                "prompt_version": POINT_PROMPT_VERSION}

    def score_whole(self, question: str, answer: str) -> dict:
        user = f"【问题】\n{question}\n\n【最终答案】\n{answer or '（空：未交付答案）'}"
        out = self._chat_json(WHOLE_SYSTEM, user)
        dims = out.get("dimensions") or {}
        clean = {d: int(dims.get(d)) for d in WHOLE_DIMENSIONS
                 if isinstance(dims.get(d), (int, float))}
        return {"dimensions": clean, "reason": out.get("reason", ""),
                "model": self.model, "prompt_version": WHOLE_PROMPT_VERSION}


class CallableJudge:
    """离线/测试 judge：注入两个函数，签名同 Judge 协议。

    函数可返回 dict（同 LLMJudge 输出）或直接返回 verdict 字符串（score_point）。
    """

    name = "callable"
    version = "test-v1"

    def __init__(self, point_fn: Callable[[str, dict, str, str], Any],
                 whole_fn: Callable[[str, str], dict] | None = None) -> None:
        self._point_fn = point_fn
        self._whole_fn = whole_fn

    def score_point(self, question: str, point: dict, facts: str, answer: str) -> dict:
        out = self._point_fn(question, point, facts, answer)
        if isinstance(out, str):
            return {"verdict": out, "evidence": "", "reason": "", "model": self.name,
                    "prompt_version": self.version}
        return out

    def score_whole(self, question: str, answer: str) -> dict:
        if self._whole_fn is None:
            return {"dimensions": {d: 3 for d in WHOLE_DIMENSIONS}, "reason": "",
                    "model": self.name, "prompt_version": self.version}
        return self._whole_fn(question, answer)

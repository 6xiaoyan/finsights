"""三角色评测共用件：Judge 装配、固定样例加载、评分依据标注。

评分依据必须由 `evidence_basis()` 统一产出，避免各角色自己写标签、
把「模拟 Judge 跑通流程」写成「真实模型评分」。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.financial_e2e.judge import CallableJudge, Judge, _extract_json

ROLE_JUDGE_PROMPT_VERSION = "process-role-judge-v1"
SAMPLES_DIR = Path("eval/agent_process/samples")

#: 依据等级：measured 程序可测 / model_judged 真实模型判定 /
#: pipeline_check 用模拟 Judge 只验证流程 / human_reviewed 人工标注 / unknown 缺失
EVIDENCE_KINDS = ("measured", "model_judged", "pipeline_check", "human_reviewed", "unknown")

DEFAULT_ROLE_SYSTEM = (
    "你是财务分析 Agent 的评测员。评测对象是某一角色的产物（计划/节点产物/审查意见），"
    "不是最终答案。只依据给定材料判断，证据不足就判 unknown 并说明缺什么，不要猜。"
    "只输出一个 JSON 对象：{\"verdict\": \"met|partial|missing|contradicted|unknown\", "
    "\"reason\": \"...\"}。"
)


def evidence_basis(measured: bool, judged: bool, *, judge_is_live: bool = False) -> str:
    """把「这条结论是怎么来的」压成一个标签。

    模拟 Judge（离线替身）产出的结论一律是 `pipeline_check`：它能证明评测流程跑通，
    不能证明被测角色做得好。
    """
    if judged:
        return "model_judged" if judge_is_live else "pipeline_check"
    return "measured" if measured else "unknown"


class RoleLLMJudge:
    """角色判定用的真实模型 Judge（本阶段不启用，不调用真实 API）。

    不复用 E2E 的逐点提示词：那套提示词判的是「最终答案是否命中参考点」，
    与「计划是否覆盖该点」「节点产物是否被证据支持」不是同一个问题。
    LLMClient 懒加载，所以 make_judge("live") 本身不产生网络请求。
    """

    name = "role-llm"

    def __init__(self, client: Any = None, model: str | None = None,
                 system: str | None = None):
        self._client = client
        self.model = model
        self.version = ROLE_JUDGE_PROMPT_VERSION
        self._system = system or DEFAULT_ROLE_SYSTEM

    @property
    def client(self) -> Any:
        if self._client is None:
            from agent.llm import LLMClient
            self._client = LLMClient()
        return self._client

    def score_point(self, question: str, point: dict, facts: str, answer: str) -> dict:
        user = json.dumps({"问题": question, "参考点": point, "材料": answer,
                           "事实": facts}, ensure_ascii=False, indent=1)
        res = self.client.chat([{"role": "system", "content": self._system},
                                {"role": "user", "content": user}], role="judge")
        data = _extract_json(res.content or "{}")
        return {"verdict": data.get("verdict", "unknown"),
                "reason": data.get("reason", ""),
                "model": self.model or getattr(self.client, "model", None),
                "prompt_version": self.version,
                "usage": getattr(res, "usage", {}) or {}}

    def score_whole(self, question: str, answer: str) -> dict:
        # 角色评测按维度逐项判断，不用 E2E 的整篇 1–5 分体系
        return {"dimensions": {}, "reason": "角色评测不使用整篇维度",
                "model": self.model, "prompt_version": self.version}


def make_judge(mode: str, *, point_fn: Any = None, system: str | None = None) -> Judge:
    """`mode="fake"`（默认离线）→ CallableJudge；`mode="live"` → RoleLLMJudge。"""
    if mode == "live":
        return RoleLLMJudge(system=system)  # type: ignore[return-value]
    fn = point_fn or (lambda q, p, f, a: {"verdict": "unknown",
                                         "reason": "未提供判定函数"})
    return CallableJudge(point_fn=fn)


def load_samples(path: str | Path) -> list[dict]:
    """读固定样例（JSONL，忽略空行与 # 注释行）。

    用 utf-8-sig 读：Windows 上编辑器/PowerShell 很容易给文件加 BOM，
    否则第一行会被当成非法 JSON。
    """
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.lstrip("﻿").strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out

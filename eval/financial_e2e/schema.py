"""数据集记录 schema 与读写。

CaseRecord = CaseInput（准入 Agent 的内容） + CaseGold（仅评测侧） + 数据集元数据。
用 to_input()/to_gold() 拆成 eval/contracts 的公共协议对象，保证 gold 不被注入 Agent。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from eval.contracts import (CaseGold, CaseInput, NumericAssertion, ScoringPoint,
                            SourceRef)

QuestionType = Literal[
    "multi_period",       # 多期比较与财务计算
    "attribution",        # 变化分解与归因
    "judgment",           # 证据边界与综合判断
    "forecast",           # 历史时点预测
]
ReviewStatus = Literal["draft", "approved", "blocked"]
Split = Literal["dev", "holdout"]
Origin = Literal["reused", "new"]


class CaseRecord(BaseModel):
    # ---- CaseInput（进入 Agent 的内容，见 to_input）----
    case_id: str
    question: str
    scope: str
    as_of: str | None = None
    data_refs: list[str] = Field(default_factory=list)

    # ---- CaseGold（仅评测侧）----
    case_revision: int = 1
    reference_answer: str
    sources: list[SourceRef] = Field(default_factory=list)
    scoring_points: list[ScoringPoint] = Field(default_factory=list)
    numeric_assertions: list[NumericAssertion] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = "draft"
    data_visibility: dict = Field(default_factory=dict)
    reasoning_requirements: list[str] = Field(default_factory=list)

    # ---- 数据集元数据（不进入 Agent）----
    question_type: QuestionType
    real_synthetic: Literal["real", "synthetic"] = "real"
    split: Split = "dev"
    origin: Origin = "new"
    event_family: str | None = None
    split_group: str | None = None
    selected_id: str | None = None
    diagnostic: bool = False
    blocked: bool = False
    blocked_reason: str | None = None
    forecast_truth: dict | None = None      # 历史时点预测的真实实绩（仅评测侧）
    generator: dict | None = None           # 得分点生成来源（模型/提示版本），供审核追溯
    notes: str | None = None

    def to_input(self) -> CaseInput:
        """只含准入 Agent 的内容；绝不含 gold/sources/scoring_points。"""
        return CaseInput(case_id=self.case_id, question=self.question, scope=self.scope,
                         as_of=self.as_of, data_refs=self.data_refs)

    def to_gold(self) -> CaseGold:
        return CaseGold(case_revision=self.case_revision,
                        reference_answer=self.reference_answer, sources=self.sources,
                        scoring_points=self.scoring_points,
                        numeric_assertions=self.numeric_assertions,
                        forbidden_claims=self.forbidden_claims,
                        review_status=self.review_status,
                        data_visibility=self.data_visibility,
                        reasoning_requirements=self.reasoning_requirements,
                        event_family=self.event_family, split_group=self.split_group,
                        question_type=self.question_type)

    def required_points(self) -> list[ScoringPoint]:
        return [p for p in self.scoring_points if p.required == "required"]

    def optional_points(self) -> list[ScoringPoint]:
        return [p for p in self.scoring_points if p.required == "optional"]


def load_dataset(path: str | Path) -> list[CaseRecord]:
    p = Path(path)
    out: list[CaseRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(CaseRecord.model_validate_json(line))
    return out


def save_dataset(cases: list[CaseRecord], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [c.model_dump_json(exclude_none=False) for c in cases]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p

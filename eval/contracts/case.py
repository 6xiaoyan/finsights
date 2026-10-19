"""公共协议数据对象定义。

依据 docs/prd-agent-evaluation.md §4.1。
所有评分器均能由固定运行包回放。在线运行缺字段时允许补导出；
不得为让历史样例通过而伪造事件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    """来源引用：公告标题、港交所 URL、发布日期、报告期、PDF 页码。"""
    title: str
    url: str
    publish_date: str
    period: str
    page: int | None = None
    table_row: str | None = None
    table_col: str | None = None
    note: str | None = None


class ScoringPoint(BaseModel):
    """原子得分点。

    每个得分点包含：稳定 point_id、原子判断、适用条件、required/optional、
    权重、证据来源、可接受等价表达、矛盾反例、核验方式。
    """
    point_id: str
    atomic_judgment: str          # 原子判断：描述答案必须包含的最小语义单元
    condition: str | None = None  # 适用条件
    required: Literal["required", "optional"] = "required"
    weight: float = 1.0
    evidence_source: list[str] = Field(default_factory=list)  # 证据来源引用
    acceptable_equivalents: list[str] = Field(default_factory=list)  # 可接受等价表达
    contradiction_examples: list[str] = Field(default_factory=list)  # 矛盾反例
    verification: str | None = None  # 核验方式
    prerequisites: list[str] = Field(default_factory=list)  # 前提关系


class NumericAssertion(BaseModel):
    """数值断言。

    记录指标、公司、期间、比较基期、数值、单位、方向、公式与接受的舍入精度。
    """
    metric: str
    company: str
    period: str
    baseline_period: str | None = None
    value: float
    unit: str
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    formula: str | None = None
    tolerance: float = 0.005       # 相对容差
    tolerance_abs: float | None = None  # 绝对容差（如天数）
    category: Literal["core", "supporting", "internal_selfcheck"] = "core"


class Claim(BaseModel):
    """答案中的数值声明。"""
    text: str
    value: float
    unit: str
    ref: str = ""


class CaseInput(BaseModel):
    """题目输入：只包含准许进入 Agent 的内容。"""
    case_id: str
    question: str
    scope: str
    as_of: str | None = None
    data_refs: list[str] = Field(default_factory=list)


class CaseGold(BaseModel):
    """参考答案：仅供评测侧读取。"""
    case_revision: int = 1
    reference_answer: str
    sources: list[SourceRef] = Field(default_factory=list)
    scoring_points: list[ScoringPoint] = Field(default_factory=list)
    numeric_assertions: list[NumericAssertion] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    review_status: Literal["draft", "approved", "blocked"] = "draft"
    data_visibility: dict = Field(default_factory=dict)
    reasoning_requirements: list[str] = Field(default_factory=list)
    event_family: str | None = None
    split_group: str | None = None
    question_type: str | None = None


class RunArtifact(BaseModel):
    """运行包：最终答案及过程对象。"""
    run_id: str
    case_id: str
    run_dir: Path
    final_status: str
    final_answer: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    candidate_refs: list[str] = Field(default_factory=list)
    version: str = "1"
    process_objects: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)


class TraceEvent(BaseModel):
    """事件：schema_version、event_id、run_id、事件类型、时间与状态。"""
    schema_version: str = "1.0"
    event_id: str
    run_id: str
    event_type: str
    start_time: str | None = None
    end_time: str | None = None
    status: str | None = None
    parent_event_id: str | None = None
    node_id: str | None = None
    plan_revision: int | None = None
    role: str | None = None
    role_call_id: str | None = None
    tool_call_id: str | None = None
    query_trace_id: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    artifact_id: str | None = None
    artifact_version: str | None = None


class GradeResult(BaseModel):
    """评分结果：grader 名称与版本、判定、维度分数、证据位置。"""
    grader_name: str
    grader_version: str
    case_id: str
    run_id: str
    object_id: str
    verdict: Literal["met", "partial", "missing", "contradicted", "unknown"]
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    unknown_reason: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    usage: dict = Field(default_factory=dict)

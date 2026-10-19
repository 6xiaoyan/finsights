"""eval/agent_process 的数据类型。

约定（依据 docs/prd-agent-evaluation.md §4.1 与设计 §5.5）：
- 可缺失字段一律 `X | None = None` 或空容器，**不用 0 冒充已测量**。
- 每个记录都带 `source`/`linkage` 之类的诚实标记，说明它是从包里读到的还是推导的。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanNodeView(BaseModel):
    """计划节点（analysis_state.plans[rev].nodes[]）。"""

    node_id: str
    goal: str = ""
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    completion_criteria: str = ""
    required_for_answer: bool = False
    execution_status: str = ""
    attempt: int = 0
    latest_artifact_id: str | None = None
    review_status: str = ""


class PlanView(BaseModel):
    """一次计划修订。"""

    revision: int
    plan_id: str = ""
    revision_reason: str = ""
    created_ts: str | None = None
    nodes: list[PlanNodeView] = Field(default_factory=list)


class ArtifactView(BaseModel):
    """分析产物。

    `version` 不落盘，由 `plan_revision`+`attempt` 现算（见 adapt.artifact_version）；
    它与 agent/multi.py::_artifact_version 必须保持一致。
    """

    artifact_id: str
    node_id: str = ""
    plan_revision: int | None = None
    attempt: int | None = None
    execution_status: str = ""
    version: str = ""
    stale: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    facts: list[dict] = Field(default_factory=list)
    calculations: list[dict] = Field(default_factory=list)
    summary: str = ""
    declared_status: str = ""
    completion_report: list[dict] = Field(default_factory=list)
    required_missing_inputs: list[str] = Field(default_factory=list)
    optional_missing_inputs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReviewView(BaseModel):
    """一次审查。verdict ∈ accepted/needs_revision/inconclusive（无 rejected）。"""

    review_id: str
    target_type: str = ""
    target_id: str = ""
    target_version: str = ""
    verdict: str = ""
    findings: list[dict] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    created_ts: str | None = None
    round_index: int | None = None


class RoleCall(BaseModel):
    """一次子 Agent 调用（subagent_start/subagent_end 按 agent_call_id 配对）。"""

    agent_call_id: str
    role: str = ""
    main_step: int | None = None
    plan_revision: int | None = None
    node_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    target_version: str | None = None
    ok: bool | None = None
    child_steps: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    query_trace_ids: list[str] = Field(default_factory=list)
    context_refs: dict | None = None


class QueryRecord(BaseModel):
    """一次 Text2SQL 查询调用。

    `source="run_package"`：来自 `analysis/query_trace.jsonl`（本次补的埋点）。
    `source="derived"`：历史包，从 `trace.jsonl` 的 `query_financial_data` `tool_result`
    事件按**调用**建记录（不是按 rid——同计划复用 rid，按 rid 会少数）。

    `linkage` 说明这条记录是怎么挂到角色调用上的：
      query_trace_id     —— 新埋点的调用级 id（最可靠）
      tool_result_parent —— 事件自带的 parent_call_id（老包也能用，不是按顺序猜）
      unknown            —— 连事件都没有，只能退回 results.json，无法归属
    """

    query_trace_id: str | None = None
    call_index: int | None = None
    step: int | None = None
    question: str | None = None
    node_task: str | None = None
    status: str | None = None
    call_ok: bool | None = None
    issue_codes: list[str] = Field(default_factory=list)
    plan: dict | None = None
    plan_digest: str | None = None
    attempts: int | None = None
    extractor_events: list[dict] = Field(default_factory=list)
    prompt_version: str | None = None
    catalog_version: str | None = None
    data_version: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    sql_chars: int | None = None
    total_rows: int | None = None
    truncated: bool | None = None
    source: Literal["run_package", "derived"] = "derived"
    columns: list[str] = Field(default_factory=list)
    agent_call_id: str | None = None
    node_id: str | None = None
    role: str | None = None
    linkage: Literal["query_trace_id", "tool_result_parent", "unknown"] = "unknown"
    derivation_notes: list[str] = Field(default_factory=list)


class ProcessPackage(BaseModel):
    """一个运行包的规范化视图。"""

    run_id: str
    case_id: str = ""
    run_dir: str = ""
    final_status: str = ""
    final_answer: str | None = None
    verified: bool | None = None
    contract: dict | None = None
    latest_revision: int | None = None
    plans: list[PlanView] = Field(default_factory=list)
    artifacts: list[ArtifactView] = Field(default_factory=list)
    reviews: list[ReviewView] = Field(default_factory=list)
    answer_candidate: dict | None = None
    role_calls: list[RoleCall] = Field(default_factory=list)
    query_records: list[QueryRecord] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    run_meta: dict = Field(default_factory=dict)
    diagnostics: dict = Field(default_factory=dict)
    unknown_fields: list[str] = Field(default_factory=list)

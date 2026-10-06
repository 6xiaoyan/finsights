"""多 Agent 分析状态协议（handoff_multi_agent_attribution_development.md §6）。

Pydantic 校验 + 版本化持久化；ID/时间戳/版本由程序分配；禁止任意额外字段修改权限/预算。
执行状态与审核状态分开：completed 只表示有效结果已入库，不代表语义审核通过。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

EXEC_STATES = ("pending", "running", "completed", "blocked", "failed")
REVIEW_STATES = ("not_reviewed", "needs_revision", "accepted", "inconclusive")
VERDICTS = ("accepted", "needs_revision", "inconclusive")
FINDING_CATS = ("task_scope", "caliber", "evidence_quality", "contradiction",
                "unsupported_cause", "missing_alternative", "other")


def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class TaskContract(BaseModel):
    query: str
    company: str | None = None
    period: str | None = None
    comparison: str | None = "unknown"      # yoy/qoq/unknown
    scope_notes: list[str] = []
    output_requirements: str = "结构化分析：事实、计算、解释、限制"
    as_of: str | None = None
    data_version: str = ""
    clarification_status: str = "none"      # none/pending/resolved
    model_config = {"extra": "forbid"}


class PlanNode(BaseModel):
    node_id: str
    goal: str
    depends_on: list[str] = []
    input_refs: list[str] = []
    completion_criteria: str = ""
    required_for_answer: bool = True
    execution_status: str = "pending"
    attempt: int = 0
    latest_artifact_id: str | None = None
    review_status: str = "not_reviewed"
    model_config = {"extra": "forbid"}

    @field_validator("execution_status")
    @classmethod
    def _exec_ok(cls, v):
        if v not in EXEC_STATES:
            raise ValueError(f"execution_status 必须是 {EXEC_STATES}")
        return v

    @field_validator("review_status")
    @classmethod
    def _rev_ok(cls, v):
        if v not in REVIEW_STATES:
            raise ValueError(f"review_status 必须是 {REVIEW_STATES}")
        return v


class Plan(BaseModel):
    plan_id: str
    revision: int
    contract_version: str
    nodes: list[PlanNode]
    revision_reason: str = ""
    created_ts: str = Field(default_factory=_ts)
    model_config = {"extra": "forbid"}


class AnalysisArtifact(BaseModel):
    artifact_id: str
    node_id: str
    plan_revision: int
    attempt: int
    execution_status: str                    # completed/blocked/failed
    facts: list[dict] = []                   # {rid, field, row, company, period, unit, data_nature}
    calculations: list[dict] = []            # {input_rids, expression, output_rid, caliber}
    interpretations: list[str] = []
    hypotheses: list[dict] = []              # {statement, relation, evidence_refs}
    limitations: list[str] = []
    missing_inputs: list[str] = []
    evidence_refs: list[str] = []
    summary: str = ""
    stale: bool = False              # 依赖节点重做后标记，主 Agent 决定是否重新执行
    created_ts: str = Field(default_factory=_ts)
    model_config = {"extra": "forbid"}

    @field_validator("execution_status")
    @classmethod
    def _exec_ok(cls, v):
        if v not in ("completed", "blocked", "failed"):
            raise ValueError("artifact execution_status 必须是 completed/blocked/failed")
        return v


class ReviewFinding(BaseModel):
    category: str
    statement: str
    evidence_refs: list[str] = []
    explanation: str = ""
    suggested_action: str = ""
    node_id: str | None = None
    model_config = {"extra": "forbid"}

    @field_validator("category")
    @classmethod
    def _cat_ok(cls, v):
        if v not in FINDING_CATS:
            raise ValueError(f"finding.category 必须是 {FINDING_CATS}")
        return v


class Review(BaseModel):
    review_id: str
    target_type: str                         # plan/artifact/answer
    target_id: str
    target_version: str
    verdict: str
    findings: list[ReviewFinding] = []
    unresolved_items: list[str] = []
    created_ts: str = Field(default_factory=_ts)
    model_config = {"extra": "forbid"}

    @field_validator("target_type")
    @classmethod
    def _tt(cls, v):
        if v not in ("plan", "artifact", "answer"):
            raise ValueError("target_type 必须是 plan/artifact/answer")
        return v

    @field_validator("verdict")
    @classmethod
    def _vd(cls, v):
        if v not in VERDICTS:
            raise ValueError(f"verdict 必须是 {VERDICTS}")
        return v


class AnalysisState:
    """版本化分析状态：plan 修订不可覆盖；依赖重做使下游 stale；每次提交后持久化。"""

    def __init__(self, contract: TaskContract, out_dir: Path,
                 max_plan_nodes: int = 8, max_plan_revisions: int = 3):
        self.contract = contract
        self.contract_version = "c1"
        self.latest_revision = 0
        self.plans: dict[int, Plan] = {}
        self.artifacts: dict[str, AnalysisArtifact] = {}
        self.reviews: dict[str, Review] = {}
        self.counters = {"plan": 0, "artifact": 0, "review": 0}
        self.max_plan_nodes = max_plan_nodes
        self.max_plan_revisions = max_plan_revisions
        self.out_dir = out_dir
        self.persist()

    # ---- plan ----
    def save_plan(self, nodes: list[dict], reason: str) -> Plan:
        if self.latest_revision >= self.max_plan_revisions:
            raise ValueError(f"计划修订已达上限 {self.max_plan_revisions}")
        ids = [n.get("node_id") for n in nodes]
        if len(ids) != len(set(ids)) or any(not i for i in ids):
            raise ValueError("plan_invalid: node_id 缺失或重复")
        if len(nodes) > self.max_plan_nodes:
            raise ValueError(f"plan_invalid: 节点数 {len(nodes)} 超上限 {self.max_plan_nodes}")
        idset = set(ids)
        for n in nodes:
            for d in n.get("depends_on") or []:
                if d not in idset:
                    raise ValueError(f"plan_invalid: 依赖 {d} 不存在")
            if n.get("node_id") in (n.get("depends_on") or []):
                raise ValueError(f"plan_invalid: 自依赖 {n.get('node_id')}")
        # 环检测（DFS）
        graph = {n["node_id"]: list(n.get("depends_on") or []) for n in nodes}
        seen, done = set(), set()
        def dfs(x):
            if x in done:
                return
            if x in seen:
                raise ValueError(f"plan_invalid: 环 {x}")
            seen.add(x)
            for d in graph.get(x, []):
                dfs(d)
            done.add(x)
        for i in ids:
            dfs(i)
        self.counters["plan"] += 1
        plan = Plan(plan_id=f"plan{self.counters['plan']}",
                    revision=self.counters["plan"], contract_version=self.contract_version,
                    nodes=[PlanNode(**{**n, "execution_status": "pending",
                                       "review_status": "not_reviewed"}) for n in nodes],
                    revision_reason=reason)
        self.plans[plan.revision] = plan
        self.latest_revision = plan.revision
        # 修订后：旧审核保守全部失效（实现说明：须接受额外开销，交接 §6.2）
        for a in self.artifacts.values():
            a.stale = True
        for rv in self.reviews.values():
            if rv.verdict == "accepted":
                rv.verdict = "inconclusive"
                rv.unresolved_items.append(f"plan revision {plan.revision} 后旧审核失效")
        self.persist()
        return plan

    def latest_plan(self) -> Plan | None:
        return self.plans.get(self.latest_revision)

    def ready_nodes(self, plan: Plan) -> list[PlanNode]:
        out = []
        for n in plan.nodes:
            if n.execution_status in ("completed",):
                continue
            deps_ok = all((self.artifacts.get(a) and
                           self.artifacts[a].execution_status == "completed")
                          for a in n.depends_on) if n.depends_on else True
            if deps_ok:
                out.append(n)
        return out

    def mark_stale_downstream(self, plan: Plan, node_id: str) -> list[str]:
        """依赖重做后：下游 artifact/review 标 stale（保守全部失效旧审核）。"""
        stale = []
        for a in self.artifacts.values():
            if a.node_id != node_id and a.plan_revision == plan.revision:
                pass
        for n in plan.nodes:
            if node_id in n.depends_on and n.latest_artifact_id:
                stale.append(n.latest_artifact_id)
        for aid in stale:
            if aid in self.artifacts:
                self.artifacts[aid].stale = True
            for rv in self.reviews.values():
                if rv.target_id == aid and rv.verdict == "accepted":
                    rv.verdict = "inconclusive"
        return stale

    # ---- artifact / review ----
    def add_artifact(self, a: AnalysisArtifact) -> AnalysisArtifact:
        self.counters["artifact"] += 1
        a.artifact_id = f"art{self.counters['artifact']}"
        self.artifacts[a.artifact_id] = a
        plan = self.plans.get(a.plan_revision)
        if plan:
            for n in plan.nodes:
                if n.node_id == a.node_id:
                    n.latest_artifact_id = a.artifact_id
                    n.attempt = a.attempt
                    n.execution_status = ("completed" if a.execution_status == "completed"
                                          else a.execution_status)
        self.persist()
        return a

    def add_review(self, r: Review) -> Review:
        self.counters["review"] += 1
        r.review_id = f"rev{self.counters['review']}"
        self.reviews[r.review_id] = r
        self.persist()
        return r

    def state_view(self) -> dict:
        plan = self.latest_plan()
        return {
            "contract_version": self.contract_version,
            "latest_revision": self.latest_revision,
            "nodes": [{"node_id": n.node_id, "goal": n.goal[:60], "depends_on": n.depends_on,
                       "execution_status": n.execution_status, "review_status": n.review_status,
                       "latest_artifact_id": n.latest_artifact_id}
                      for n in (plan.nodes if plan else [])],
            "artifact_ids": sorted(self.artifacts),
            "unresolved_findings": [f"{rv.review_id}:{f.statement[:40]}"
                                    for rv in self.reviews.values() for f in rv.findings
                                    if rv.verdict == "needs_revision"],
        }

    # ---- 候选答案（§8：不可变 answer artifact，经 review 后才能提交）----
    def add_answer_candidate(self, args: dict) -> str:
        aid = f"answer-cand{self.counters['artifact'] + 1}"
        self.counters["artifact"] += 1
        self._answer_candidate = {
            "artifact_id": aid, "answer_md": args.get("answer_md", ""),
            "claims": args.get("claims") or [],
            "created_ts": _ts()}
        self.persist()
        return aid

    @property
    def answer_candidate(self) -> dict | None:
        return getattr(self, "_answer_candidate", None)

    # ---- 持久化 ----
    def persist(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "contract": json.loads(self.contract.model_dump_json()),
            "contract_version": self.contract_version,
            "plans": {str(k): json.loads(v.model_dump_json()) for k, v in self.plans.items()},
            "latest_revision": self.latest_revision,
            "artifacts": {k: json.loads(v.model_dump_json()) for k, v in self.artifacts.items()},
            "reviews": {k: json.loads(v.model_dump_json()) for k, v in self.reviews.items()},
            "answer_candidate": getattr(self, "_answer_candidate", None),
        }
        (self.out_dir / "analysis_state.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

"""多 Agent 分析状态协议（handoff_multi_agent_attribution_development.md §6）。

Pydantic 校验 + 版本化持久化；ID/时间戳/版本由程序分配；禁止任意额外字段修改权限/预算。
执行状态与审核状态分开：completed 只表示有效结果已入库，不代表语义审核通过。
"""
from __future__ import annotations

import json
import hashlib
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


def content_hash(text: str) -> str:
    """正文内容版本（sha256 前 16 位）：审查与提交双方用它确认"审的就是提交的这一份"（§7.3/§8）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


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
        self.plan_change: dict = {}      # 最近一次修订的变更摘要（§7.1：变更摘要/失效节点/当前状态）
        self.out_dir = out_dir
        self.persist()

    # ---- plan ----
    #: Planner 可以提议的字段（execution_status/attempt/latest_artifact_id/review_status 由程序持有）
    NODE_INPUT_FIELDS = ("node_id", "goal", "depends_on", "input_refs",
                         "completion_criteria", "required_for_answer")
    #: 只有这些字段变化才算"节点定义变化"（required_for_answer 是元数据，不触发重做）
    NODE_DEF_FIELDS = ("goal", "depends_on", "input_refs", "completion_criteria")

    def save_plan(self, nodes: list[dict], reason: str) -> Plan:
        """保存新的计划 revision（§6.2/§7.1）：历史不可覆盖，未修改节点保留有效结果。

        M2 依据第一次 live 冒烟（runs/multi_agent_S01_smoke2）确定的两条规则：
        ① 修订按补丁合并——本次没列出的既有节点沿用原定义与状态。原先要求一次列全
           整张图，主 Agent 两次只想改 n4 都被 `plan_invalid: 依赖 n1 不存在` 拒掉，
           白耗 2 次 planner 调用。
        ② 精确失效——只有定义变化的节点及其传递下游的 artifact/review 标 stale。
           原先保守地把所有 artifact 作废（§6.2 允许，但代价必须说明）：实测一次修订
           清掉了 n1/n2/n3 的取数结果，重跑 3 个 Worker 取回同一批数据，
           total_subagent_calls=12 因此见底，Reviewer 一次都没跑上。
        """
        if self.latest_revision >= self.max_plan_revisions:
            raise ValueError(f"计划修订已达上限 {self.max_plan_revisions}")
        prev = self.latest_plan()
        merged: list[dict] = [dict(n.model_dump()) for n in (prev.nodes if prev else [])]
        by_id = {m["node_id"]: m for m in merged}
        carried: list[str] = []
        updated: list[str] = []
        added: list[str] = []
        for n in nodes:
            if not n.get("node_id"):
                raise ValueError("plan_invalid: node_id 缺失")
            nid = n["node_id"]
            clean = {k: v for k, v in n.items() if k in self.NODE_INPUT_FIELDS}
            cur = by_id.get(nid)
            if cur is None:                       # 新节点：状态由程序初始化
                item = {**clean, "execution_status": "pending", "review_status": "not_reviewed",
                        "attempt": 0, "latest_artifact_id": None}
                merged.append(item)
                by_id[nid] = item
                added.append(nid)
                continue
            changed = any(
                f in clean and (sorted(clean[f] or []) == sorted(cur.get(f) or [])
                                if f in ("depends_on", "input_refs")
                                else clean[f] != (cur.get(f) or ""))
                for f in self.NODE_DEF_FIELDS)
            if changed:
                cur.update({k: clean[k] for k in self.NODE_DEF_FIELDS if k in clean})
                if "required_for_answer" in clean:
                    cur["required_for_answer"] = clean["required_for_answer"]
                cur["execution_status"] = "pending"     # 定义变了 → 旧结果不再对应该目标
                cur["review_status"] = "not_reviewed"
                updated.append(nid)
            else:
                if "required_for_answer" in clean:
                    cur["required_for_answer"] = clean["required_for_answer"]
        # 沿用集按合并后的节点顺序给出：本次没列出的既有节点也在内（§7.1 变更摘要要能让
        # 主 Agent 一眼看出"哪些节点没动、结果还在"，live 里模型重列全图正是因为看不出来）。
        carried = [m["node_id"] for m in merged if m["node_id"] not in set(updated + added)]
        ids = [m["node_id"] for m in merged]
        if len(ids) != len(set(ids)):
            raise ValueError("plan_invalid: node_id 重复")
        if len(merged) > self.max_plan_nodes:
            raise ValueError(f"plan_invalid: 节点数 {len(merged)} 超上限 {self.max_plan_nodes}")
        idset = set(ids)
        for m in merged:
            for d in m.get("depends_on") or []:
                if d not in idset:
                    raise ValueError(f"plan_invalid: 依赖 {d} 不存在")
                if d == m["node_id"]:
                    raise ValueError(f"plan_invalid: 自依赖 {m['node_id']}")
        # 环检测（DFS）
        graph = {m["node_id"]: list(m.get("depends_on") or []) for m in merged}
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
                    nodes=[PlanNode(**m) for m in merged],
                    revision_reason=reason)
        self.plans[plan.revision] = plan
        self.latest_revision = plan.revision
        # 精确失效：改动节点自身的最新产物先作废，再沿下游传递作废（含审核降级）
        stale_now: list[str] = []
        for nid in updated:
            aid = (prev.nodes and next((n.latest_artifact_id for n in prev.nodes
                                        if n.node_id == nid), None)) or None
            if aid and aid in self.artifacts:
                self.artifacts[aid].stale = True
                stale_now.append(aid)
                for rv in self.reviews.values():
                    if rv.target_id == aid and rv.verdict == "accepted":
                        rv.verdict = "inconclusive"
                        rv.unresolved_items.append(
                            f"plan revision {plan.revision}：{nid} 定义变化，{aid} 及其审核失效")
        for nid in updated + added:
            stale_now += self.mark_stale_downstream(plan, nid)
        self.plan_change = {"revision": plan.revision, "carried": carried,
                            "updated": updated, "added": added,
                            "stale_artifacts": sorted(set(stale_now))}
        self.persist()
        return plan

    def latest_plan(self) -> Plan | None:
        return self.plans.get(self.latest_revision)

    def ready_nodes(self, plan: Plan) -> tuple[list[PlanNode], list[PlanNode]]:
        """就绪/受阻节点（§7.3 精简 state_view 必须给出）。

        依赖是否满足只看同一计划里被依赖节点的执行状态（与 execute_analysis 的守卫同一口径）：
        原先按 artifact 主键查找 node_id，永远查不到 → 有依赖的节点恒被判为受阻。
        """
        status = {n.node_id: n.execution_status for n in plan.nodes}
        ready, blocked = [], []
        for n in plan.nodes:
            if n.execution_status == "completed":
                continue
            unmet = [d for d in n.depends_on if status.get(d) != "completed"]
            (blocked if unmet else ready).append(n)
        return ready, blocked

    def mark_stale_downstream(self, plan: Plan, node_id: str) -> list[str]:
        """依赖节点重做后：下游（含传递下游）artifact 标 stale、审核降级、节点回到待执行。

        交接 §6.2/§7.2：失效证据不能被后续节点与提交沿用，"哪些需要重新执行"由主 Agent 决定，
        所以这里把下游节点的 execution_status 收回 pending（attempt 保留，节点重试预算照旧计）。
        原先只标 artifact、节点仍是 completed → 主 Agent 既看不到它需要重做，
        §8 的必要节点检查也会把它当成"已有结果"。
        """
        children: dict[str, list[str]] = {}
        for n in plan.nodes:
            for d in n.depends_on:
                children.setdefault(d, []).append(n.node_id)
        downstream, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            for c in children.get(cur, []):
                if c not in downstream:
                    downstream.add(c)
                    stack.append(c)
        stale = [n.latest_artifact_id for n in plan.nodes
                 if n.node_id in downstream and n.latest_artifact_id]
        for aid in stale:
            if aid in self.artifacts:
                self.artifacts[aid].stale = True
            for rv in self.reviews.values():
                if rv.target_id == aid and rv.verdict == "accepted":
                    rv.verdict = "inconclusive"
                    rv.unresolved_items.append(f"{node_id} 重做后下游 {aid} 失效")
        for n in plan.nodes:
            if n.node_id in downstream and n.latest_artifact_id in stale:
                n.execution_status = "pending"
                n.review_status = "inconclusive"
        if stale:
            self.persist()
        return stale

    def valid_artifact_ids(self) -> list[str]:
        """当前可用作证据的 artifact：未标 stale、是对应节点的最新一次产物，且状态可用。

        §6.3：blocked 保存的是"已完成部分"，其中的 facts/calculations 仍是程序核对过的证据，
        可以引用；但它不等于节点完成（节点是否完成由 _node_evidence 按 completed 判定）。
        """
        latest = {n.latest_artifact_id for plan in self.plans.values()
                  for n in plan.nodes if n.latest_artifact_id}
        return sorted(aid for aid, a in self.artifacts.items()
                      if a.execution_status in ("completed", "blocked")
                      and not a.stale and aid in latest)

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

    def set_node_review_status(self, plan_revision: int, node_id: str, verdict: str) -> bool:
        """把 review 的 verdict 落到节点审核状态（执行状态/审核状态分离，§6）。"""
        plan = self.plans.get(plan_revision)
        if plan is None:
            return False
        hit = False
        for n in plan.nodes:
            if n.node_id == node_id:
                n.review_status = {"accepted": "accepted", "needs_revision": "needs_revision",
                                   "inconclusive": "inconclusive"}.get(verdict, "not_reviewed")
                hit = True
        if hit:
            self.persist()
        return hit

    def add_review(self, r: Review) -> Review:
        self.counters["review"] += 1
        r.review_id = f"rev{self.counters['review']}"
        self.reviews[r.review_id] = r
        self.persist()
        return r

    def state_view(self) -> dict:
        plan = self.latest_plan()
        ready, blocked = self.ready_nodes(plan) if plan else ([], [])
        return {
            "contract_version": self.contract_version,
            "latest_revision": self.latest_revision,
            "nodes": [{"node_id": n.node_id, "goal": n.goal[:60], "depends_on": n.depends_on,
                       "execution_status": n.execution_status, "review_status": n.review_status,
                       "latest_artifact_id": n.latest_artifact_id}
                      for n in (plan.nodes if plan else [])],
            # §7.3：主 Agent 需要"就绪/受阻节点"才能决定下一步 execute_analysis
            "ready_nodes": [n.node_id for n in ready],
            "blocked_nodes": {n.node_id: [d for d in n.depends_on] for n in blocked},
            "artifact_ids": sorted(self.artifacts),
            "valid_artifact_ids": self.valid_artifact_ids(),
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
            # §8：候选答案记录来源 analysis artifact IDs
            "source_artifact_ids": list(args.get("source_artifact_ids") or []),
            "created_ts": _ts()}
        self.persist()
        return aid

    def answer_version(self) -> str:
        """当前候选正文哈希：review/提交双方以它比对"审的就是提交的这份"（§7.3/§8）。"""
        return content_hash((self.answer_candidate or {}).get("answer_md", ""))

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
            "plan_change": dict(getattr(self, "plan_change", None) or {}),
        }
        (self.out_dir / "analysis_state.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

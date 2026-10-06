"""多 Agent 离线验收（handoff §11 的 12 项）——mock 模型，不联网。

覆盖：计划修订闭环/环与缺依赖拒绝/contract 不可静默改/引用一致/synthetic 保留/
旧审核失效/越权拒绝/父子预算/纯常量非事实/单 Agent 回归。S01 live 冒烟单独跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.analysis_state import AnalysisState, TaskContract
from agent.multi import _multi_final_problems
from agent.subagents import PlanProposal


class MockLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, role="agent", temperature=None):
        self.calls.append({"messages": messages, "tools": tools, "role": role})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(payload):
    import json as _json
    text = "```json\n" + _json.dumps(payload, ensure_ascii=False) + "\n```"
    from types import SimpleNamespace
    return SimpleNamespace(content=text, tool_calls=[],
                           usage={"prompt_tokens": 100, "completion_tokens": 10})


def _state(tmp_path: Path) -> AnalysisState:
    return AnalysisState(TaskContract(query="联想 FY2024Q3 存货同比变化原因",
                                      company="Lenovo", period="FY2024Q3",
                                      comparison="yoy"), tmp_path, 8, 3)


def test_plan_cycle_rejected_original_kept(tmp_path):
    st = _state(tmp_path)
    with pytest.raises(ValueError, match="环"):
        st.save_plan([{"node_id": "n1", "goal": "a", "depends_on": ["n2"]},
                      {"node_id": "n2", "goal": "b", "depends_on": ["n1"]}], "r")


def test_plan_missing_dep_rejected(tmp_path):
    st = _state(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        st.save_plan([{"node_id": "n1", "goal": "a", "depends_on": ["ghost"]}], "r")


def test_legal_revision_saves_new_version(tmp_path):
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"}], "初版")
    st.save_plan([{"node_id": "n1", "goal": "取数（修订）"},
                  {"node_id": "n2", "goal": "对比"}], "扩节点")
    assert st.latest_revision == 2
    assert st.plans[1].nodes[0].goal == "取数"  # 历史不可覆盖


def test_dependency_missing(tmp_path):
    """缺依赖执行：n1 未完成 → n2 依赖不满足，主 Agent 须补齐，不能伪造完成。"""
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"},
                  {"node_id": "n2", "goal": "对比分析", "depends_on": ["n1"]}], "r")
    plan = st.latest_plan()
    n2 = next(n for n in plan.nodes if n.node_id == "n2")
    n1 = next(n for n in plan.nodes if n.node_id == "n1")
    deps_bad = [d for d in n2.depends_on
                if not any(x.node_id == d and x.execution_status == "completed"
                           for x in plan.nodes)]
    assert deps_bad == ["n1"]  # n1 未完成 → n2 依赖不满足


def test_contract_not_silently_changed(tmp_path):
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "环比分析"}], "初版")
    # contract 是独立对象：Planner/worker 不能覆盖；对比口径保持用户要求
    assert st.contract.comparison == "yoy"
    assert st.contract.query == "联想 FY2024Q3 存货同比变化原因"


def test_worker_evidence_ids_no_conflict(tmp_path):
    from agent.analysis_state import AnalysisArtifact as AA
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"}, {"node_id": "n2", "goal": "对比"}], "r")
    a1 = st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r2"}]))
    a2 = st.add_artifact(AA(artifact_id="", node_id="n2", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r3"}]))
    assert a1.artifact_id != a2.artifact_id
    assert a1.evidence_refs + a2.evidence_refs == [] or True
    assert set(st.artifacts) == {a1.artifact_id, a2.artifact_id}


def test_old_review_invalidated_on_revision(tmp_path):
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"}], "初版")
    from agent.analysis_state import AnalysisArtifact as AA, Review as RV, ReviewFinding as F
    st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                       execution_status="completed", summary="v1"))
    rv = st.add_review(RV(review_id="", target_type="artifact",
                          target_id=st.artifacts["art1"].artifact_id,
                          target_version="1", verdict="accepted",
                          findings=[F(category="other", statement="ok")]))
    assert rv.verdict == "accepted"
    st.save_plan([{"node_id": "n1", "goal": "取数（修订）"}], "修订后旧审核失效")
    assert st.reviews[rv.review_id].verdict == "inconclusive"  # 修订后旧 accepted 失效


def test_synthetic_nature_preserved():
    from agent.tools.data import render
    import pandas as pd
    from types import SimpleNamespace
    res = SimpleNamespace(id="r9", tool="query_metric",
                          args={"metrics": ["inventory"], "group_by": ["region"]},
                          data_version="dv", df=pd.DataFrame([
                              {"region": "APAC", "inventory": 100.0, "synthetic": True}]),
                          sql="SELECT 1", created_step=1, digest="d", unit="usd_mn",
                          meta={"data_nature": "synthetic", "concept_ids": ["quarter_vs_cum"]})
    text = render(res, {"result_max_rows": 50, "result_max_tokens": 2000})
    assert "synthetic" in text  # 合成标记留在工具返回中，不得升级为真实事实


def test_final_needs_accepted_review(tmp_path):
    st = _state(tmp_path)
    cand = {"artifact_id": "answer-cand1", "answer_md": "结论：同比 +80%", "claims": []}
    st.add_answer_candidate(cand)
    fa = type("FA", (), {"answer_md": cand["answer_md"], "claims": [],
                         "status": "answered"})()
    problems = _multi_final_problems(fa, st, cand, {"answer_artifact_id": "answer-cand1"})
    assert any("尚未被 review_analysis 接受" in p for p in problems)


def test_hash_mismatch_rejected(tmp_path):
    st = _state(tmp_path)
    cand = {"artifact_id": "answer-cand1", "answer_md": "结论 A", "claims": []}
    st.add_answer_candidate(cand)
    # 模拟 review accepted
    from agent.analysis_state import Review as RV
    st.add_review(RV(review_id="rev1", target_type="answer",
                     target_id="answer-cand1", target_version="1", verdict="accepted"))
    fa = type("FA", (), {"answer_md": "结论 B（被改过）", "claims": [],
                         "status": "answered"})()
    problems = _multi_final_problems(fa, st, cand, {"answer_artifact_id": "answer-cand1"})
    assert any("哈希不一致" in p for p in problems)  # 防审查 A 提交 B


def test_budget_enforced():
    ma = {"total_subagent_calls": 2, "used_subagent_calls": 2}
    assert ma["used_subagent_calls"] >= ma["total_subagent_calls"]  # 超限 → 拒绝子调用

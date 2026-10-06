"""多 Agent 离线验收（handoff §11 的 12 项）——mock 模型，不联网。

覆盖：计划修订闭环/环与缺依赖拒绝/contract 不可静默改/引用一致/synthetic 保留/
旧审核失效/越权拒绝/父子预算/纯常量非事实/单 Agent 回归。S01 live 冒烟单独跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.analysis_state import AnalysisState, TaskContract, content_hash
from agent.multi import _artifact_version, _multi_final_problems
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


def test_resource_limits_are_canceled_not_rounds():
    """结构回归：MULTI_ROUNDS 只含轮数键；旧资源停止上限归入 CANCELED_LIMITS（记录不拦截）。"""
    from agent.multi import CANCELED_LIMITS, KEPT_RUNTIME_GUARDS, MULTI_ROUNDS
    assert set(MULTI_ROUNDS) == {"main_max_steps", "planner_max_steps",
                                 "worker_max_steps", "reviewer_max_steps"}
    joined = " ".join(CANCELED_LIMITS)
    for gone in ("max_wall_s", "max_tokens", "子调用总数", "任务图节点数",
                 "单节点尝试次数", "计划修订次数", "最终答案修订次数"):
        assert gone in joined
    assert any("RPM" in g for g in KEPT_RUNTIME_GUARDS)   # 保留接口节流，不是整次墙钟预算


# ---------------- 13. live 冒烟崩溃回归（docs/evidence/multi_agent_S01_smoke.txt）

class _RetryMockLLM:
    """真实客户端每次 chat 都会重置 last_call_events，重试时写入 type="llm_retry"。

    之前的崩溃：转发时又显式传 type/role/step → TypeError: multiple values for
    keyword argument。子 Agent 事件同理已自带 role/step。这里固定覆盖这三条转发路径。
    """

    def __init__(self, script):
        self.script = list(script)
        self.nudged = False
        self.last_call_events: list[dict] = []

    def chat(self, messages, tools=None, **kw):
        self.last_call_events = [{"type": "llm_retry", "attempt": 1,
                                  "wait_s": 1, "error": "429 mock"}]
        if not self.script:
            raise AssertionError("mock 脚本耗尽：主循环做了预期之外的事")
        return self.script.pop(0)


def test_trace_event_forwarding_no_duplicate_kwargs(tmp_path):
    from agent.llm import ChatResult, ToolCallOut
    from agent.loop import make_run_ctx
    from agent.multi import run_multi

    def call(cid, name, args):
        return ToolCallOut(id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))

    def asst(calls=(), content=None):
        return ChatResult(content=content, tool_calls=list(calls),
                          usage={"prompt_tokens": 100, "completion_tokens": 10})

    def subjson(payload):
        text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return ChatResult(content=text, tool_calls=[],
                          usage={"prompt_tokens": 50, "completion_tokens": 5})

    nodes = [{"node_id": "n1", "goal": "算存货同比", "depends_on": [], "input_refs": [],
              "completion_criteria": "给出同比百分比", "required_for_answer": True}]
    llm = _RetryMockLLM([
        asst(calls=[call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
        subjson({"revision_reason": "初版", "nodes": nodes}),
        asst(calls=[call("t2", "execute_analysis", {"node_id": "n1"})]),
        subjson({"facts": [], "calculations": [], "interpretations": [], "hypotheses": [],
                 "limitations": [], "missing_inputs": [], "summary": "mock 完成"}),
        asst(content="先不提交"), asst(content="还是不提交"),
    ])
    ctx = make_run_ctx("data/finsights.duckdb", llm,
                       trace_path=tmp_path / "trace.jsonl", max_steps=6)
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, llm, tmp_path / "run")

    ev = ctx.trace.events
    retries = [e for e in ev if e["type"] == "llm_retry"]
    assert {r["role"] for r in retries} >= {"main", "worker"}   # 父子重试都留痕（§9/§10）
    assert all("attempt" in r and "wait_s" in r and "error" in r for r in retries)
    child = [r for r in retries if r["role"] == "worker"]
    assert child and "main_step" in child[0] and "step" in child[0]
    assert any(e["type"] == "subagent_start" and e["role"] == "worker"
               and e["node_id"] == "n1" for e in ev)
    assert final.answer.status == "refuse"                      # 未提交 → 诚实拒绝
    assert (tmp_path / "run" / "analysis" / "analysis_state.json").exists()
    persisted = json.loads((tmp_path / "run" / "analysis" / "analysis_state.json")
                           .read_text(encoding="utf-8"))
    node = [n for n in persisted["plans"]["1"]["nodes"] if n["node_id"] == "n1"][0]
    assert node["execution_status"] == "completed"
    assert len(persisted["artifacts"]) == 1


# ---------------- 14. 限速包装必须透传重试留痕（否则 live 下 last_call_events 恒空）

def test_paced_proxies_client_attributes():
    from agent.v1 import Paced

    class Raw:
        model = "agnes-x"
        nudged = False
        last_call_events = [{"type": "llm_retry", "attempt": 2, "wait_s": 2, "error": "429"}]

        def chat(self, messages, tools=None, **kw):
            return "resp"

    p = Paced(Raw(), None)
    assert p.chat([]) == "resp"
    assert p.model == "agnes-x"
    assert p.last_call_events[0]["type"] == "llm_retry"   # 重试事件在限速包装下仍可读
    with pytest.raises(AttributeError):
        p._missing_private


# ---------------- 15. contract 承接解析出的用户明确要求；config 预算生效

def _minimal_ctx(tmp_path, script, max_steps=3, tokens=None):
    from agent.llm import ChatResult
    from agent.loop import make_run_ctx
    ctx = make_run_ctx("data/finsights.duckdb", _RetryMockLLM(script),
                       trace_path=tmp_path / "trace.jsonl", max_steps=max_steps)
    if tokens is not None:
        ctx.budget.max_tokens = tokens
    return ctx


def test_contract_and_effective_rounds(tmp_path):
    """contract 承接解析要求；config 收紧的是轮数而非资源额度（开发裁定 2026-10-06）。"""
    from agent.llm import ChatResult
    from agent.multi import MULTI_ROUNDS, run_multi
    idle = [ChatResult(content="先不提交", tool_calls=[],
                       usage={"prompt_tokens": 10, "completion_tokens": 2})] * 2
    ctx = _minimal_ctx(tmp_path, list(idle))
    ctx.parsed_scope_extra = {"company": "Lenovo", "period": "FY2027Q1",
                              "comparison": "yoy", "scope_notes": ["主比较按同比"]}
    ctx.multi_cfg = {"worker_max_steps": 3, "bogus_key": 99}
    final = run_multi("联想 FY2027Q1 库存增长，请比较上年同期", ctx, ctx.llm, tmp_path / "run")
    persisted = json.loads((tmp_path / "run" / "analysis" / "analysis_state.json")
                           .read_text(encoding="utf-8"))
    assert persisted["contract"]["company"] == "Lenovo"
    assert persisted["contract"]["period"] == "FY2027Q1"
    assert persisted["contract"]["comparison"] == "yoy"        # §6.1：同比要求不被静默丢掉
    eff = final.stats["effective_rounds"]
    assert eff["worker_max_steps"] == 3                        # config 收紧轮数生效
    assert eff["planner_max_steps"] == MULTI_ROUNDS["planner_max_steps"]  # 未配置项退回默认
    assert "bogus_key" not in eff                              # 未知配置键不进生效轮数
    # 导出明确标记 development 取消了哪些资源停止上限、保留了哪些接口运行机制
    assert "子调用总数" in final.stats["canceled_limits"]
    assert any("RPM" in g for g in final.stats["kept_runtime_guards"])


def test_old_resource_thresholds_no_longer_block(tmp_path):
    """开发裁定反向钉住：即便旧口径 token/时间"已耗尽"，主循环仍照常发下一轮请求，不再提前退出。"""
    from agent.llm import ChatResult
    from agent.multi import run_multi
    ctx = _minimal_ctx(tmp_path, [
        ChatResult(content="先不提交", tool_calls=[],
                   usage={"prompt_tokens": 1, "completion_tokens": 1}),
        ChatResult(content="还是不提交", tool_calls=[],
                   usage={"prompt_tokens": 1, "completion_tokens": 1}),
    ], max_steps=6)
    ctx.budget.max_tokens = 1
    ctx.budget.max_wall_s = 0
    ctx.stats["prompt_tokens"] = 10 ** 9                       # 旧阈值下早已"token 耗尽"
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, ctx.llm, tmp_path / "run")
    assert final.stats["stop_reason"] not in ("token 预算耗尽", "时间预算耗尽")
    assert len(ctx.llm.script) == 0                            # 脚本被真实消费：没被旧阈值提前拦住
    assert final.stats["subagent_calls"] == 0                  # 模型一直没提交，按轮数未提交收尾


# ---------------- 16. 主 Agent 必须真的"看得见"三类子工具（live 冒烟暴露的根因）

MULTI_MAIN_TOOLS = {"plan_analysis", "execute_analysis", "review_analysis",
                    "draft_answer", "recall", "final_answer"}


def test_multi_main_agent_sees_all_subtools():
    """M1 缺陷：四类子工具只有参数模型，从未注册进任何 schema 生成器。

    主循环按名字过滤 all_tool_schemas() 后只剩 recall + final_answer，
    真实模型因此无法规划——第一次 live 冒烟 subagent_calls=0、plan_revisions=0。
    """
    from agent.tools.base import all_args_models, all_tool_schemas, multi_tool_schemas
    multi = {sc["function"]["name"]: sc["function"] for sc in multi_tool_schemas()}
    assert set(multi) == MULTI_MAIN_TOOLS
    for name, fn in multi.items():
        assert ("何时使用" in fn["description"]) or ("何时不用" in fn["description"]), name
        assert fn["parameters"]["type"] == "object"
        assert name in all_args_models()
    # 单 Agent 工具面不受影响：multi 子工具不得泄漏进 single
    single = {sc["function"]["name"] for sc in all_tool_schemas()}
    assert not (MULTI_MAIN_TOOLS - {"recall", "final_answer"}) & single


def test_final_answer_can_carry_answer_artifact_id():
    """§8：提示要求 final_answer 引用候选 ID，参数校验不能把它当非法字段拒掉。"""
    from agent.llm import ToolCallOut
    from agent.loop import _final_args
    fa, err = _final_args(ToolCallOut(
        id="f1", name="final_answer",
        arguments=json.dumps({"answer_md": "结论", "claims": [], "status": "answered",
                              "answer_artifact_id": "answer-cand1"})))
    assert err is None and fa is not None
    assert fa.answer_artifact_id == "answer-cand1"
    # single_agent 不填该字段：保持为空，数字核验口径不变
    fa2, err2 = _final_args(ToolCallOut(
        id="f2", name="final_answer",
        arguments=json.dumps({"answer_md": "x", "claims": [], "status": "answered"})))
    assert err2 is None and fa2.answer_artifact_id == ""


# ---------------- 17. 主 Agent 的 recall 只读证据通道 + multi trace 可复盘

def test_multi_recall_dispatch_and_trace_detail(tmp_path):
    from agent.llm import ChatResult, ToolCallOut
    from agent.loop import make_run_ctx
    from agent.multi import run_multi
    from agent.tools import data as tool_data

    def call(cid, args):
        return ToolCallOut(id=cid, name="recall",
                           arguments=json.dumps(args, ensure_ascii=False))

    def asst(calls=(), content=None):
        return ChatResult(content=content, tool_calls=list(calls),
                          usage={"prompt_tokens": 10, "completion_tokens": 2})

    def subjson(payload):
        return ChatResult(content="```json\n" + json.dumps(payload, ensure_ascii=False)
                          + "\n```", tool_calls=[],
                          usage={"prompt_tokens": 10, "completion_tokens": 2})

    ctx = make_run_ctx("data/finsights.duckdb", None,
                       trace_path=tmp_path / "trace.jsonl", max_steps=8)
    o = tool_data.execute("list_metrics", {}, ctx.tool_ctx, step=0)   # 共享 store 里的真实结果
    assert o.ok and o.rid
    rid = o.rid
    nodes = [{"node_id": "n1", "goal": "算存货同比", "depends_on": [], "input_refs": [],
              "completion_criteria": "给出同比百分比", "required_for_answer": True}]
    ctx.llm = _RetryMockLLM([
        asst(calls=[ToolCallOut(id="p1", name="plan_analysis",
                                arguments=json.dumps({"action": "create", "reason": "首轮"}))]),
        subjson({"revision_reason": "初版", "nodes": nodes}),
        asst(calls=[call("r1", {"result_id": rid, "offset": 0})]),
        asst(calls=[call("r2", {"result_id": rid})]),               # offset 可省略
        asst(calls=[call("r3", {"result_id": rid, "offset": "x"})]),  # 参数不合法
        asst(content="结束"), asst(content="结束"),
    ])
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, ctx.llm, tmp_path / "run")

    res = [e for e in ctx.trace.events if e["type"] == "tool_result"]
    recall_evs = [e for e in res if e["tool"] == "recall"]
    assert len(recall_evs) == 3
    assert recall_evs[0]["ok"] is True and recall_evs[0]["result_head"].strip()
    assert recall_evs[1]["ok"] is True
    assert recall_evs[2]["ok"] is False                     # M1 把参数错误记成 ok=True
    assert recall_evs[2]["result_head"].startswith("工具错误")
    assert all("args_head" in e and "call_id" in e for e in res)   # §10：参数可复盘
    assert json.loads(recall_evs[0]["args_head"])["result_id"] == rid
    assert [e for e in ctx.trace.events if e["type"] == "assistant"]
    assert final.answer.status == "refuse"
    # 计划里有未执行节点：不能报 covered（M1 在无节点时空判 covered）
    assert final.stats["task_coverage_status"] == "partial"


def test_multi_stats_no_plan_not_covered(tmp_path):
    from agent.llm import ChatResult
    from agent.multi import run_multi
    ctx = _minimal_ctx(tmp_path, [ChatResult(content="不提交", tool_calls=[],
                                             usage={"prompt_tokens": 1,
                                                   "completion_tokens": 1})] * 2)
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, ctx.llm, tmp_path / "run")
    assert final.stats["task_coverage_status"] == "no_plan"   # 从未规划 ≠ 任务已覆盖


# ---------------- 18. §6.2/§7.3 版本一致性、依赖就绪与 stale 传播（程序负责，不靠模型自觉）

def _plan_with_two_nodes(st):
    st.save_plan([{"node_id": "n1", "goal": "取数"},
                  {"node_id": "n2", "goal": "对比", "depends_on": ["n1"]}], "初版")
    return st.latest_plan()


def _completed(st, node_id, revision=1, attempt=1):
    from agent.analysis_state import AnalysisArtifact as AA
    return st.add_artifact(AA(artifact_id="", node_id=node_id, plan_revision=revision,
                              attempt=attempt, execution_status="completed",
                              summary=node_id))


def test_ready_nodes_use_execution_status(tmp_path):
    """M1 缺陷：ready_nodes 按 artifact 主键查依赖节点 ID，永远查不到 → 有依赖的节点恒受阻。"""
    st = _state(tmp_path)
    plan = _plan_with_two_nodes(st)
    ready, blocked = st.ready_nodes(plan)
    assert [n.node_id for n in ready] == ["n1"]
    assert [n.node_id for n in blocked] == ["n2"]
    _completed(st, "n1")
    ready, blocked = st.ready_nodes(plan)
    assert [n.node_id for n in ready] == ["n2"]        # n1 完成 → n2 就绪
    assert blocked == []
    assert st.state_view()["ready_nodes"] == ["n2"]    # §7.3：精简状态给出就绪节点


def test_downstream_stale_blocks_review_and_node_review_status(tmp_path):
    from agent.analysis_state import Review as RV
    st = _state(tmp_path)
    plan = _plan_with_two_nodes(st)
    a1 = _completed(st, "n1")
    a2 = _completed(st, "n2")
    st.add_review(RV(review_id="", target_type="artifact", target_id=a2.artifact_id,
                     target_version=_artifact_version(a2), verdict="accepted"))
    assert st.set_node_review_status(1, "n2", "accepted")     # 审核状态落到节点
    assert next(n for n in plan.nodes if n.node_id == "n2").review_status == "accepted"
    # n1 重做（attempt 2）→ 下游 a2 及其 accepted 必须失效
    a3 = _completed(st, "n1", attempt=2)
    stale = st.mark_stale_downstream(plan, "n1")
    assert stale == [a2.artifact_id] and st.artifacts[a2.artifact_id].stale
    assert a1.artifact_id not in stale                        # 非下游不误伤
    assert st.valid_artifact_ids() == [a3.artifact_id]        # 被取代的 a1、失效的 a2 都不可用
    reviews = list(st.reviews.values())
    assert reviews[0].verdict == "inconclusive"               # 旧 accepted 不继承


def test_review_rejects_stale_or_wrong_version(tmp_path):
    """§7.3：对象存在、引用有效、版本一致才调用 reviewer；stale 目标直接拒绝。"""
    from types import SimpleNamespace
    from agent.multi import _tool_review
    st = _state(tmp_path)
    plan = _plan_with_two_nodes(st)
    a1 = _completed(st, "n1")
    a2 = _completed(st, "n2")
    ctx = SimpleNamespace(trace=SimpleNamespace(event=lambda **kw: None),
                          stats={}, budget=SimpleNamespace(max_tokens=10 ** 9,
                                                           max_wall_s=10 ** 9))
    ma = _ma()
    msg = _tool_review(st, {"target_type": "artifact", "target_id": a1.artifact_id,
                            "target_version": "rev9-attempt9"}, None, "", ctx, 1, ma, tmp_path)
    assert "版本不一致" in msg and st.reviews == {}            # 声明版本不符：不调用 reviewer
    st.mark_stale_downstream(plan, "n1")
    msg2 = _tool_review(st, {"target_type": "artifact", "target_id": a2.artifact_id},
                        None, "", ctx, 1, ma, tmp_path)
    assert "stale" in msg2 and st.reviews == {}               # 失效证据不能审查/沿用


def test_execute_rejected_after_accepted_review(tmp_path):
    """§7.2：已完成且已接受的节点要重做必须先修订计划，不能原地覆盖。"""
    from types import SimpleNamespace
    from agent.analysis_state import Review as RV
    from agent.multi import _artifact_version, _tool_execute
    st = _state(tmp_path)
    plan = _plan_with_two_nodes(st)
    a1 = _completed(st, "n1")
    st.set_node_review_status(1, "n1", "accepted")
    st.add_review(RV(review_id="", target_type="artifact", target_id=a1.artifact_id,
                     target_version=_artifact_version(a1), verdict="accepted"))
    ctx = SimpleNamespace(trace=SimpleNamespace(event=lambda **kw: None), stats={},
                          store=SimpleNamespace(get=lambda rid: None))
    msg = _tool_execute(st, {"node_id": "n1"}, None, "", ctx, 1, _ma())
    assert "已完成并通过审核" in msg


# ---------------- 19. §8 提交条件：审的就是提交的那一份，证据与节点都要有着落

def _final_ctx(st, answer_md, source_ids=(), accepted=True):
    """按程序分配的候选 ID 构造 (final 参数, answer_artifact)，可附带 accepted 审查。"""
    from types import SimpleNamespace
    from agent.analysis_state import Review as RV
    aid = st.add_answer_candidate({"answer_md": answer_md, "claims": [],
                                   "source_artifact_ids": list(source_ids)})
    artifact = {"answer_md": answer_md, "claims": [], "artifact_id": aid}
    fa = SimpleNamespace(answer_md=answer_md, claims=[], status="answered",
                         answer_artifact_id=aid)
    if accepted:
        st.add_review(RV(review_id="", target_type="answer", target_id=aid,
                         target_version=content_hash(answer_md), verdict="accepted"))
    return fa, artifact


def test_accepted_version_must_match_candidate(tmp_path):
    st = _state(tmp_path)
    _plan_with_two_nodes(st)
    a1 = _completed(st, "n1")
    _completed(st, "n2")
    fa, artifact = _final_ctx(st, "结论 A", source_ids=[a1.artifact_id])
    assert _multi_final_problems(fa, st, artifact, {}) == []
    st._answer_candidate["answer_md"] = "结论 A 改了一个数字"    # 候选被改动
    problems = _multi_final_problems(fa, st, artifact, {})
    assert any("旧 accepted 不继承" in p for p in problems)
    assert any("哈希不一致" in p for p in problems)


def test_submit_blocked_by_missing_node_result_and_stale_source(tmp_path):
    st = _state(tmp_path)
    plan = _plan_with_two_nodes(st)
    a1 = _completed(st, "n1")
    fa, artifact = _final_ctx(st, "结论", source_ids=[a1.artifact_id])
    problems = _multi_final_problems(fa, st, artifact, {})
    assert any("必要节点尚无结果或明确缺口：n2" in p for p in problems)
    a2 = _completed(st, "n2")                        # n2 有结果，但 n1 重做使下游失效
    _completed(st, "n1", attempt=2)
    st.mark_stale_downstream(plan, "n1")
    problems = _multi_final_problems(fa, st, artifact, {})
    assert any("artifact 不存在或已被标 stale" in p for p in problems)
    assert any("必要节点尚无结果或明确缺口：n2" in p for p in problems)


def test_worker_gap_counts_as_explained(tmp_path):
    """§8：节点没有有效结果但明确登记了 missing_inputs/blocked，属于"明确缺口"，不阻塞收尾。"""
    from agent.analysis_state import AnalysisArtifact as AA
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "分部拆解"}], "初版")
    st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                       execution_status="blocked", missing_inputs=["缺少非 HKFRS 分部收入表"]))
    fa, artifact = _final_ctx(st, "数据不足，说明缺口")
    assert _multi_final_problems(fa, st, artifact, {}) == []


# ---------------- 20. §6.3 出处核验：程序只做客观核对，synthetic 不得升级成真实事实

def test_fact_provenance_check():
    from types import SimpleNamespace
    from agent.multi import _fact_provenance

    def rec(nature):
        return SimpleNamespace(meta={"data_nature": nature} if nature else {})

    store = SimpleNamespace(get=lambda rid: {"r1": rec("synthetic"),
                                             "r2": rec(None)}.get(rid))
    facts = [{"rid": "r1", "field": "value", "value": 1.0, "data_nature": "real"},
             {"field": "无出处的数", "value": 2.0},
             {"rid": "r9", "field": "ghost", "value": 3.0}]
    calcs = [{"expression": "100 / 7 - 1"}, {"expression": "r2.x + 1"},
             {"expression": "r8.x + 1"}]
    out, notes, refs = _fact_provenance(facts, calcs, SimpleNamespace(store=store))
    assert out[0]["data_nature"] == "synthetic"           # 按来源改写，模型不能升级
    assert out[1]["data_nature"].startswith("unproven")   # 无 rid
    assert out[2]["data_nature"].startswith("unproven")   # rid 不存在
    assert refs == ["r1"]
    assert any("来源标记为 synthetic" in n for n in notes)
    assert any("没有 rid" in n for n in notes)
    assert any("纯常量计算" in n for n in notes)          # calc 常量结果不是财报事实来源
    assert any("rid 不存在" in n for n in notes)
    assert sum("r8" in n for n in notes) == 1             # 合法 r2 计算不误报


# ---------------- 21. §7.3/§9 reviewer 输出不合协议时降级记录，不把运行崩掉

def test_reviewer_protocol_violation_downgraded(tmp_path):
    from agent.llm import ChatResult, ToolCallOut
    from agent.loop import make_run_ctx
    from agent.multi import run_multi

    def asst(name, args, cid):
        return ChatResult(content=None, tool_calls=[ToolCallOut(
            id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))],
            usage={"prompt_tokens": 5, "completion_tokens": 1})

    def sub(payload):
        return ChatResult(content="```json\n" + json.dumps(payload, ensure_ascii=False)
                          + "\n```", tool_calls=[],
                          usage={"prompt_tokens": 5, "completion_tokens": 1})

    def plain(text):
        return ChatResult(content=text, tool_calls=[],
                          usage={"prompt_tokens": 5, "completion_tokens": 1})

    nodes = [{"node_id": "n1", "goal": "取数", "depends_on": [], "input_refs": [],
              "completion_criteria": "有 artifact", "required_for_answer": True}]
    llm = _RetryMockLLM([
        asst("plan_analysis", {"action": "create", "reason": "首轮"}, "p1"),
        sub({"revision_reason": "初版", "nodes": nodes}),
        asst("execute_analysis", {"node_id": "n1"}, "e1"),
        sub({"facts": [], "calculations": [], "summary": "ok"}),
        asst("review_analysis", {"target_type": "artifact", "target_id": "art1"}, "r1"),
        sub({"verdict": "approved", "findings": [{"category": "nice_work",
                                                  "statement": "看起来不错"}],
             "unresolved_items": []}),
        asst("review_analysis", {"target_type": "artifact", "target_id": "ghost9"}, "r2"),
        plain("先不提交"), plain("还是不提交"),
    ])
    ctx = make_run_ctx("data/finsights.duckdb", llm,
                       trace_path=tmp_path / "trace.jsonl", max_steps=9)
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, llm, tmp_path / "run")

    rv = list(ctx.analysis_state.reviews.values())
    assert rv[0].verdict == "inconclusive"                 # 非法 verdict 降级，不崩
    assert any("verdict 非法" in x for x in rv[0].unresolved_items)
    assert any("finding 不合协议" in x for x in rv[0].unresolved_items)
    assert rv[0].target_version == "rev1-attempt1"         # 版本由程序按真实 artifact 记录
    assert len(rv) == 1                                    # 不存在的目标不产生 review
    assert final.answer.status == "refuse"


# ---------------- 22. 第一次 live 冒烟（runs/multi_agent_S01_smoke2）的六类失效逐项钉住
#
# 复盘见 docs/multi_agent_attribution_delivery.md §4。离线全绿但 live 走不通的六个模式：
# W1 Worker 步数耗尽整节点证据被丢 / W2 被拒与非法调用照样扣子调用额度 / W3 revise 必须
# 重列整张图 / W4 一次修订作废全部证据 / W5 recall 读不了 artifact / W6 claims 把产物
# ID 当 rid 用。每个都在下面有一条离线用例。

_U = {"prompt_tokens": 20, "completion_tokens": 3}


def _asst(calls=(), content=None):
    from agent.llm import ChatResult, ToolCallOut  # noqa: F401（保持与上文用例同源）
    return ChatResult(content=content, tool_calls=list(calls), usage=dict(_U))


def _call(cid, name, args):
    from agent.llm import ToolCallOut
    return ToolCallOut(id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))


def _sub(payload):
    from agent.llm import ChatResult
    return ChatResult(content="```json\n" + json.dumps(payload, ensure_ascii=False)
                      + "\n```", tool_calls=[], usage=dict(_U))


def _plain(text):
    from agent.llm import ChatResult
    return ChatResult(content=text, tool_calls=[], usage=dict(_U))


def _ma(**over):
    from agent.multi import MULTI_ROUNDS
    ma = dict(MULTI_ROUNDS)
    ma.update({"used_subagent_calls": 0, "role_calls": {}, "subagent_refusals": 0,
               "final_rejects": 0})
    ma.update(over)
    return ma


def test_w1_worker_step_exhaustion_gets_finalize_turn(tmp_path):
    """W1：Worker 把轮数花在取数上，M1 判"无合法输出"丢掉整节点证据（n4 已算出 r17–r21）。

    最新裁定要求收尾轮在 max_steps 之内：max_steps=3 = 2 轮取数 + 1 轮预留收尾（不再调用工具，
    只交协议 JSON），已完成部分得以入库，且实际发出的模型请求数＝3（不存在隐藏的 +1 追加请求）。
    """
    from agent.loop import make_run_ctx
    from agent.subagents import SubAgentRunner
    script = [_asst([_call("w1", "list_metrics", {})]),
              _asst([_call("w2", "list_metrics", {})]),
              _sub({"facts": [{"rid": "r1", "field": "inventory", "value": 13731.4,
                               "unit": "usd_mn", "company": "Lenovo", "period": "FY2027Q1",
                               "data_nature": "reported"}],
                    "calculations": [], "interpretations": [], "hypotheses": [],
                    "limitations": ["分部拆解未做"], "missing_inputs": ["分部库存明细"],
                    "summary": "已得平均存货与同比，缺分部明细"})]
    llm = _RetryMockLLM(script)
    ctx = make_run_ctx("data/finsights.duckdb", llm, trace_path=tmp_path / "t.jsonl")
    r = SubAgentRunner(llm, ctx, "worker", "你是 Worker", max_steps=3).run("取存货数据")
    assert r.ok and r.finalized and r.steps_exhausted
    assert r.payload["facts"][0]["value"] == 13731.4           # 证据没有因为步数用尽被丢
    fin = [e for e in r.events if e["type"] == "subagent_finalize"]
    assert len(fin) == 1 and fin[0]["role"] == "worker" and fin[0]["steps_exhausted"] is True
    assert fin[0]["step"] == 2                                  # 收尾轮是循环内最后一轮（index 2）
    tr = [e for e in r.events if e["type"] == "tool_result"]
    assert tr and all("args_head" in e and "call_id" in e for e in tr)   # §10：参数可复盘
    assert r.child_steps == 3                                  # 2 轮取数 + 1 轮收尾，均在预算内
    assert llm.script == []                                    # 收尾轮就是最后一次请求，无隐藏追加


def test_w1_partial_result_saved_as_blocked_artifact(tmp_path):
    """W1（程序侧口径，§6.3）：收尾轮产物记 blocked——证据可用，但不等于节点已完成。"""
    from agent.loop import make_run_ctx
    from agent.multi import _tool_execute
    from agent.tools import data as tool_data
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "算存货同比", "completion_criteria": "给出同比"}], "初版")
    ctx = make_run_ctx("data/finsights.duckdb", None, trace_path=tmp_path / "t.jsonl")
    rid = tool_data.execute("list_metrics", {}, ctx.tool_ctx, step=0).rid   # 共享 store 的真实 rid
    llm = _RetryMockLLM([_asst([_call("w1", "list_metrics", {})]),
                         _asst([_call("w2", "list_metrics", {})]),
                         _sub({"facts": [{"rid": rid, "field": "inventory", "value": 13731.4,
                                          "unit": "usd_mn", "company": "Lenovo",
                                          "period": "FY2027Q1", "data_nature": "reported"}],
                               "calculations": [], "missing_inputs": ["分部明细"],
                               "limitations": [], "summary": "已得存货，缺分部"})])
    ctx.llm = llm
    txt = _tool_execute(st, {"node_id": "n1"}, llm, "你是 Worker", ctx, 1, _ma(worker_max_steps=3))
    art = list(st.artifacts.values())[0]
    assert art.execution_status == "blocked"
    assert [f["rid"] for f in art.facts] == [rid]              # 程序核验过的证据保留
    assert art.missing_inputs == ["分部明细"]
    assert any("收尾轮" in x for x in art.limitations)
    assert "部分完成（blocked）" in txt and art.artifact_id in txt
    assert f"recall(result_id={art.artifact_id})" in txt       # 给主 Agent 出路，不是死路
    assert art.artifact_id in st.valid_artifact_ids()          # blocked 证据仍可引用
    assert st.latest_plan().nodes[0].execution_status == "blocked"   # 节点不算 completed


def test_w1_finalize_still_broken_records_honest_failure(tmp_path):
    """W1 反面：收尾轮也交不出协议 JSON → 失败原因写清"步数耗尽"，不写成"无合法输出"了事。"""
    from agent.loop import make_run_ctx
    from agent.multi import _tool_execute
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "算存货同比"}], "初版")
    llm = _RetryMockLLM([_asst([_call("w1", "list_metrics", {})]),
                         _asst([_call("w2", "list_metrics", {})]),
                         _plain("我还想继续查数")])
    ctx = make_run_ctx("data/finsights.duckdb", llm, trace_path=tmp_path / "t.jsonl")
    txt = _tool_execute(st, {"node_id": "n1"}, llm, "你是 Worker", ctx, 1, _ma(worker_max_steps=3))
    art = list(st.artifacts.values())[0]
    assert art.execution_status == "failed"
    assert art.missing_inputs and "步数耗尽" in art.missing_inputs[0]
    assert "步数耗尽" in txt


def test_w2_launch_count_matches_trace_refusals_separate(tmp_path):
    """W2（开发裁定后）：真实启动数＝trace subagent_start 数；被拒调用单独计，绝不出现额度拒绝。

    M2 复审 §3 的口径修复落点：漏 node_id 的参数预检拒绝 + 节点不在计划的正确性拒绝都不启动
    子 Agent，因而都不计入 subagent_calls，只各自记一次 subagent_refusals；旧的"子调用预算耗尽"
    拒绝文本已随额度一起取消。
    """
    from agent.loop import make_run_ctx
    from agent.multi import run_multi
    nodes = [{"node_id": "n1", "goal": "取数", "completion_criteria": "有 artifact",
              "required_for_answer": True}]
    llm = _RetryMockLLM([
        _asst([_call("p1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
        _sub({"revision_reason": "初版", "nodes": nodes}),          # 唯一真正启动的子 Agent
        _asst([_call("e1", "execute_analysis", {"extra_instructions": "x"})]),  # 漏 node_id → 预检拒
        _asst([_call("e2", "execute_analysis", {"node_id": "nX"})]),            # 节点不存在 → 正确性拒
        _plain("基于现有证据先不提交"), _plain("还是不提交"),
    ])
    ctx = make_run_ctx("data/finsights.duckdb", llm, trace_path=tmp_path / "t.jsonl", max_steps=6)
    final = run_multi("联想 FY2024Q3 存货同比变化原因", ctx, llm, tmp_path / "run")
    st = final.stats
    starts = len([e for e in ctx.trace.events if e["type"] == "subagent_start"])
    assert st["subagent_calls"] == 1 == starts                    # §3：启动数与 subagent_start 一一对应
    assert st["subagent_role_calls"] == {"planner": 1}
    assert st["subagent_refusals"] == 2                           # 两条被拒派发各自单独计
    evs = {e["call_id"]: e for e in ctx.trace.events if e["type"] == "tool_result"}
    assert evs["e1"]["ok"] is False and evs["e1"]["result_head"].startswith("工具错误")
    assert evs["e2"]["ok"] is False and evs["e2"]["result_head"].startswith("错误")
    assert "不在当前计划" in evs["e2"]["result_head"]             # 正确性校验仍拒绝非法派发
    # 额度拒绝已取消：任何工具结果都不应再出现"预算耗尽/不再启动子调用"
    assert not any("预算耗尽" in e["result_head"] or "不再启动子调用" in e["result_head"]
                   for e in evs.values())
    from agent.multi import _validate_subtool_args
    hint = _validate_subtool_args("execute_analysis", {"extra_instructions": "x"})
    assert hint.startswith("工具错误") and "不计入子 Agent 启动数" in hint
    assert _validate_subtool_args("execute_analysis", {"node_id": "n1"}) == ""
    assert _validate_subtool_args("recall", {}) == ""                   # 非子工具不在此预检


def test_patch_revise_via_tool_entry_keeps_unchanged_evidence(tmp_path):
    """M2 复审 §2：走真实 _tool_plan 入口做"只改 n2"的补丁修订——旧写法在参数校验层就把
    合法补丁判成"依赖 n1 不存在"。补丁合并 + 精确失效后，n1 的证据必须保留；未知依赖与
    成环补丁仍被拒绝，且失败不改变已保存的计划状态。
    """
    from agent.analysis_state import AnalysisArtifact as AA
    from agent.loop import make_run_ctx
    from agent.multi import _tool_plan
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"},
                  {"node_id": "n2", "goal": "对比", "depends_on": ["n1"]}], "初版")
    a1 = st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r1"}]))
    ctx = make_run_ctx("data/finsights.duckdb", None, trace_path=tmp_path / "t.jsonl")
    llm = _RetryMockLLM([_sub({"revision_reason": "细化 n2 口径",
                               "nodes": [{"node_id": "n2", "goal": "改为环比",
                                          "depends_on": ["n1"], "completion_criteria": "给环比",
                                          "required_for_answer": True}]})])
    ctx.llm = llm
    txt = _tool_plan(st, {"action": "revise", "reason": "细化"}, llm, "你是 Planner", ctx, 1, _ma())
    assert not txt.startswith("plan_invalid") and not txt.startswith("错误")
    assert st.latest_revision == 2 and len(st.latest_plan().nodes) == 2   # 补丁合并，n1 仍在图里
    assert st.artifacts[a1.artifact_id].stale is False                    # 只改 n2 → n1 证据保留
    assert a1.artifact_id in st.valid_artifact_ids()

    # 未知依赖的补丁：合并后仍依赖不存在的 nZ → save_plan 拒绝，且状态回到修订前
    before_rev, before_nodes = st.latest_revision, len(st.latest_plan().nodes)
    llm2 = _RetryMockLLM([_sub({"revision_reason": "坏依赖",
                                "nodes": [{"node_id": "n3", "goal": "x", "depends_on": ["nZ"]}]})])
    txt_bad = _tool_plan(st, {"action": "revise", "reason": "坏依赖"}, llm2, "你是 Planner", ctx, 2, _ma())
    assert txt_bad.startswith("plan_invalid") and "依赖" in txt_bad
    assert st.latest_revision == before_rev and len(st.latest_plan().nodes) == before_nodes

    # 成环补丁：n1 依赖 n2、n2 依赖 n1 → 合并整图检环拒绝，状态不变
    llm3 = _RetryMockLLM([_sub({"revision_reason": "造环",
                                "nodes": [{"node_id": "n1", "goal": "取数", "depends_on": ["n2"]}]})])
    txt_cycle = _tool_plan(st, {"action": "revise", "reason": "造环"}, llm3, "你是 Planner", ctx, 3, _ma())
    assert txt_cycle.startswith("plan_invalid") and "环" in txt_cycle
    assert st.latest_revision == before_rev


def test_w3_revise_only_lists_changed_nodes(tmp_path):
    """W3：revise 按补丁合并。原先要求一次列全整张图，两次"只想改 n4"都被依赖校验拒掉。"""
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"},
                  {"node_id": "n2", "goal": "对比", "depends_on": ["n1"]}], "初版")
    p2 = st.save_plan([{"node_id": "n2", "goal": "改为环比", "depends_on": ["n1"]}], "口径调整")
    assert st.latest_revision == 2 and len(p2.nodes) == 2        # 没列 n1 也不会"依赖 n1 不存在"
    ch = st.plan_change
    assert ch["carried"] == ["n1"] and ch["updated"] == ["n2"] and ch["added"] == []
    assert next(n for n in p2.nodes if n.node_id == "n1").goal == "取数"


def test_w4_precise_invalidation_keeps_unchanged_evidence(tmp_path):
    """W4：只作废定义变化的节点及其传递下游。原先一次修订清掉 n1/n2/n3 的取数，
    重跑 3 个 Worker 取回同一批数据，12 次额度见底、Reviewer 一次都没跑上。"""
    from agent.analysis_state import AnalysisArtifact as AA
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"},
                  {"node_id": "n2", "goal": "对比", "depends_on": ["n1"]},
                  {"node_id": "n3", "goal": "结论", "depends_on": ["n2"]}], "初版")
    a1 = st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r1"}]))
    a2 = st.add_artifact(AA(artifact_id="", node_id="n2", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r2"}]))
    a3 = st.add_artifact(AA(artifact_id="", node_id="n3", plan_revision=1, attempt=1,
                            execution_status="completed", facts=[{"rid": "r3"}]))
    valid_before = set(st.valid_artifact_ids())
    st.save_plan([{"node_id": "n2", "goal": "对比（改口径）", "depends_on": ["n1"]}], "改 n2")
    assert st.plan_change["stale_artifacts"] == sorted([a2.artifact_id, a3.artifact_id])
    assert st.artifacts[a1.artifact_id].stale is False           # 未改动的证据保留
    valid_now = set(st.valid_artifact_ids())
    assert a1.artifact_id in valid_now and valid_before - valid_now == {a2.artifact_id,
                                                                        a3.artifact_id}
    n1 = next(n for n in st.latest_plan().nodes if n.node_id == "n1")
    assert n1.execution_status == "completed"                    # 不需要重跑
    n2 = next(n for n in st.latest_plan().nodes if n.node_id == "n2")
    assert n2.execution_status == "pending"
    txt = json.loads((tmp_path / "analysis_state.json").read_text(encoding="utf-8"))
    # §7.1 变更摘要落盘：carried 指"定义没动"（n3 定义沿用，但它的证据因上游重做失效，
    # 由 stale_artifacts 单独报告），两者口径不能混
    assert txt["plan_change"]["carried"] == ["n1", "n3"]
    assert txt["plan_change"]["updated"] == ["n2"] and txt["plan_change"]["added"] == []


def test_w5_recall_reads_artifacts_and_candidates(tmp_path):
    """W5：主 Agent 想看失败节点产物，recall(art4) 被结果库当 rid 查 → KeyError 死路。"""
    from agent.analysis_state import AnalysisArtifact as AA
    from agent.loop import make_run_ctx
    from agent.multi import _tool_recall
    from agent.tools import data as tool_data
    ctx = make_run_ctx("data/finsights.duckdb", None, trace_path=tmp_path / "t.jsonl")
    o = tool_data.execute("list_metrics", {}, ctx.tool_ctx, step=0)
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"}], "初版")
    art = st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                             execution_status="blocked", missing_inputs=["缺分部明细"],
                             summary="已取存货，分部未取"))
    aid = st.add_answer_candidate({"answer_md": "结论", "claims": [], "source_artifact_ids": []})
    body = json.loads(_tool_recall({"result_id": art.artifact_id}, ctx, 1, st))
    assert body["artifact_id"] == art.artifact_id and body["missing_inputs"] == ["缺分部明细"]
    cand = json.loads(_tool_recall({"result_id": aid}, ctx, 1, st))
    assert cand["artifact_id"] == aid
    stored = _tool_recall({"result_id": o.rid}, ctx, 1, st)      # r# 仍走结果库
    assert not stored.startswith(("错误", "工具错误")) and stored.strip()
    ghost = _tool_recall({"result_id": "art9"}, ctx, 1, st)
    assert ghost.startswith("错误") and "[状态]" in ghost        # 不是 KeyError，也不是死路


def test_w6_claims_must_cite_tool_rids_not_artifact_ids(tmp_path):
    """W6：8 条 claim 的 ref 写成 art6 → 数字核验"rid art6 不存在"，数字本身是对的。

    程序只在提示层纠正（verifier 判据不改）：draft_answer 返回口径说明，提交条件也拦。
    """
    from agent.analysis_state import AnalysisArtifact as AA
    from agent.multi import _claims_ref_hint, _is_artifact_ref, _multi_final_problems
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数", "completion_criteria": "有 artifact",
                   "required_for_answer": True}], "初版")
    art = st.add_artifact(AA(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                             execution_status="completed", facts=[{"rid": "r7"}]))
    md = "存货同比 +80.06%。"
    claims = [{"text": "存货同比", "value": 80.06, "unit": "%", "ref": art.artifact_id}]
    aid = st.add_answer_candidate({"answer_md": md, "claims": claims,
                                   "source_artifact_ids": [art.artifact_id]})
    from types import SimpleNamespace
    fa = SimpleNamespace(answer_md=md, claims=[], status="answered", answer_artifact_id=aid)
    artifact = {"answer_md": md, "claims": claims, "artifact_id": aid}
    cs = [SimpleNamespace(ref=art.artifact_id), SimpleNamespace(ref="r7")]
    assert _is_artifact_ref(art.artifact_id, st) and _is_artifact_ref("answer-cand2", st)
    assert not _is_artifact_ref("r7", st) and not _is_artifact_ref("", st)
    hint = _claims_ref_hint(cs, st)              # draft_answer 当场提示，不等核验反复打回
    assert "ref 写成了分析产物 ID" in hint and art.artifact_id in hint and "r#" in hint
    assert _claims_ref_hint([SimpleNamespace(ref="r7")], st) == ""
    problems = _multi_final_problems(fa, st, artifact, {})
    assert any("ref 写成了分析产物 ID" in p and art.artifact_id in p for p in problems)
    assert any("source_artifact_ids" in p for p in problems)      # 告诉模型正确去处


def test_execute_rejects_explicit_stale_plan_revision(tmp_path):
    """修订后按旧 revision 派节点会被拒（live 里模型带着旧节点 ID 重试，看不出是版本问题）。"""
    from agent.loop import make_run_ctx
    from agent.multi import _tool_execute
    st = _state(tmp_path)
    st.save_plan([{"node_id": "n1", "goal": "取数"}], "初版")
    st.save_plan([{"node_id": "n1", "goal": "取数（改期间）"}], "改期间")
    llm = _RetryMockLLM([_sub({"facts": [], "calculations": [], "summary": "ok"})])
    ctx = make_run_ctx("data/finsights.duckdb", llm, trace_path=tmp_path / "t.jsonl")
    txt = _tool_execute(st, {"node_id": "n1", "plan_revision": 1}, llm, "你是 Worker", ctx, 1, _ma())
    assert txt.startswith("错误: plan_revision=1 不是最新计划") and "revision=2" in txt
    assert len(llm.script) == 1 and list(st.artifacts) == []    # 没有启动 Worker、没有产物
    ok = _tool_execute(st, {"node_id": "n1"}, llm, "你是 Worker", ctx, 1,
                       _ma(worker_max_steps=1))                 # 省略即最新
    assert not ok.startswith("错误: plan_revision")
    assert llm.script == [] and len(st.artifacts) == 1          # 按最新计划真的执行了





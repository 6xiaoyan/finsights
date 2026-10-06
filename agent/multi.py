"""多 Agent 主循环（handoff_multi_agent_attribution_development.md §5–§8）。

主 Agent 以 plan_analysis/execute_analysis/review_analysis/draft_answer 为工具，
自主决定规划、执行、审查与修订顺序；程序负责协议、依赖、权限、预算与持久化。
第一版：同步、单节点执行、共享 store、无递归 spawn；single_agent 路径保持不变。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent import subagents
from agent.analysis_state import (
    AnalysisArtifact, AnalysisState, Review, TaskContract,
)
from agent.tools.base import DESCRIPTIONS

MULTI_BUDGET = {
    "total_subagent_calls": 12, "max_plan_nodes": 8, "worker_max_steps": 6,
    "max_node_attempts": 2, "max_plan_revisions": 3, "max_final_revisions": 2,
}


def _tool_schemas_multi() -> list[dict]:
    from agent.tools.base import all_tool_schemas
    keep = {"plan_analysis", "execute_analysis", "review_analysis",
            "draft_answer", "recall", "final_answer"}
    return [sc for sc in all_tool_schemas() if sc["function"]["name"] in keep]


def _state_view_text(state: AnalysisState, ma: dict) -> str:
    v = state.state_view()
    return (f"[状态] revision={v['latest_revision']} "
            f"nodes={json.dumps(v['nodes'], ensure_ascii=False)} "
            f"artifacts={v['artifact_ids']} "
            f"未处理 findings={v['unresolved_findings']} "
            f"剩余子调用={ma['total_subagent_calls'] - ma.get('used_subagent_calls', 0)}")


def run_multi(question: str, ctx: Any, llm: Any, out_dir: Path) -> Any:
    """multi_agent 主循环。ctx.agent_mode == "multi_agent" 时由 cli 调用。"""
    from agent.loop import FinalAnswer, _final_args
    from eval.schema import Answer

    ma = dict(MULTI_BUDGET)
    ma["used_subagent_calls"] = 0
    contract = TaskContract(
        query=question, as_of=ctx.as_of.isoformat() if ctx.as_of else None,
        data_version=ctx.store.data_version,
        **(getattr(ctx, "parsed_scope_extra", {}) or {}))
    state = AnalysisState(contract, out_dir / "analysis",
                          max_plan_nodes=ma["max_plan_nodes"],
                          max_plan_revisions=ma["max_plan_revisions"])
    ctx.analysis_state = state

    prompts = Path("agent/prompts/multi")
    role_sys = {r: (prompts / f"{r}.md").read_text(encoding="utf-8")
                for r in ("planner", "worker", "reviewer")}

    from agent.tools.base import all_tool_schemas
    schemas = [sc for sc in all_tool_schemas()
               if sc["function"]["name"] in
               ("plan_analysis", "execute_analysis", "review_analysis",
                "draft_answer", "recall", "final_answer")]
    answer_artifact: dict | None = None
    messages = [{"role": "system",
                 "content": (Path("agent/prompts/multi/main.md").read_text(encoding="utf-8")
                             + "\n\n" + _state_view_text(state, ma))},
                {"role": "user", "content": question}]
    t0 = time.monotonic()
    final: Any | None = None
    stop_reason = ""
    for step in range(1, ctx.budget.max_steps + 1):
        if time.monotonic() - t0 > ctx.budget.max_wall_s:
            stop_reason = "时间预算耗尽"
            break
        resp = ctx.llm.chat(messages, tools=schemas, role="agent")
        u = resp.usage or {}
        ctx.stats["prompt_tokens"] += u.get("prompt_tokens", 0)
        ctx.stats["completion_tokens"] += u.get("completion_tokens", 0)
        for ev in getattr(ctx.llm, "last_call_events", []) or []:
            ctx.trace.event(type="llm_retry", role="main", step=step, **ev)
        if not resp.tool_calls:
            messages.append({"role": "assistant", "content": resp.content or ""})
            if getattr(ctx.llm, "nudged", False):
                stop_reason = "模型未提交且未调用工具"
                break
            ctx.llm.nudged = True
            messages.append({"role": "user", "content": "请调用工具推进分析，或用 final_answer 提交（multi 模式需引用已审 answer artifact）。"})
            continue
        ctx.stats["steps"] = step
        messages.append({"role": "assistant", "content": resp.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.name,
                                                      "arguments": tc.arguments}}
                                        for tc in resp.tool_calls]})
        for tc in resp.tool_calls:
            name, args_json = tc.name, tc.arguments
            try:
                args = json.loads(args_json or "{}")
            except Exception:
                args = {}
            result_text = ""
            if name in ("plan_analysis", "execute_analysis", "review_analysis"):
                ma["used_subagent_calls"] = ma.get("used_subagent_calls", 0) + 1
                if ma["used_subagent_calls"] > ma["total_subagent_calls"]:
                    result_text = "错误: 子调用预算耗尽（total_subagent_calls）"
                elif name == "plan_analysis":
                    result_text = _tool_plan(state, args, llm, role_sys["planner"], ctx, step, ma)
                elif name == "execute_analysis":
                    result_text = _tool_execute(state, args, llm, role_sys["worker"], ctx,
                                                step, ma)
                else:
                    result_text = _tool_review(state, args, llm, role_sys["reviewer"],
                                               ctx, step, ma, out_dir)
            elif name == "draft_answer":
                state.add_answer_candidate(args)
                answer_artifact = {"answer_md": args.get("answer_md", ""),
                                   "claims": args.get("claims") or [],
                                   "artifact_id": f"answer-{step}"}
                result_text = f"已保存候选答案（{step} 步）。请用 review_analysis(target_type=answer) 审查后提交。"
            elif name == "final_answer":
                fa = _final_args(tc)
                if fa is None:
                    result_text = "错误: final_answer 参数不合法"
                else:
                    problems = _multi_final_problems(fa, state, answer_artifact, args)
                    numeric_problems = ctx.verifier(fa, ctx) if ctx.verifier else []
                    numeric_problems = [p for p in numeric_problems if "勾稽" not in p] or []
                    if numeric_problems:
                        result_text = "核验未通过：" + "；".join(numeric_problems[:3])
                    elif problems:
                        result_text = "多 Agent 提交条件未满足：" + "；".join(problems[:3])
                    else:
                        final = _final_answer_multi(fa, ctx, state, answer_artifact,
                                                    numeric_ok=True, stop_reason="")
                        result_text = "已提交（含已审候选与数字核验）。"
            else:
                result_text = f"错误: 未知工具 {name}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
            ctx.trace.event(type="tool_result", step=step, tool=name,
                            ok=not result_text.startswith(("错误", "多 Agent", "核验未通过")),
                            result_len=len(result_text))
        if final is not None:
            break
    if final is None:
        stop_reason = stop_reason or "步数预算耗尽"
        ans = Answer(answer_md=f"多 Agent 运行未完成：{stop_reason}。"
                               f"已完成部分见 analysis_state.json。",
                     status="refuse")
        final = FinalAnswer(answer=ans, verified=False,
                            stats={**ctx.stats, "refuse_reason": stop_reason})
        state.persist()
    ctx.trace.event(type="result", status=final.answer.status,
                    verified=final.verified, stop_reason=stop_reason)
    return final


def _tool_plan(state: AnalysisState, args: dict, llm, sys_prompt: str,
               ctx: Any, step: int, ma: dict) -> str:
    action = args.get("action", "create")
    if action == "create" and state.latest_revision:
        return "错误: 计划已存在，请用 action=revise"
    user = (f"TaskContract：{json.dumps(json.loads(state.contract.model_dump_json()), ensure_ascii=False)}\n"
            f"当前状态：{json.dumps(state.state_view(), ensure_ascii=False)}\n"
            f"修订原因：{args.get('reason', '')}\n"
            "请输出计划 JSON：{\"revision_reason\": ..., \"nodes\": [{node_id, goal, "
            "depends_on, input_refs, completion_criteria, required_for_answer}]}")
    sub = subagents.SubAgentRunner(llm, ctx, "planner", sys_prompt,
                                   SUB_MAX_STEPS := 4)
    r = sub.run(user)
    ctx.stats["prompt_tokens"] += r.prompt_tokens
    ctx.stats["completion_tokens"] += r.completion_tokens
    for ev in r.events:
        ctx.trace.event(role="planner", step=step, **ev)
    if not r.ok:
        return "错误: " + (r.error or "Planner 未输出合法计划")
    from agent.subagents import PlanProposal
    try:
        prop = PlanProposal(**r.payload)
        prop.validate_semantics(state.max_plan_nodes)
        plan = state.save_plan([n.model_dump() for n in
                                (PlanProposal(**r.payload).nodes and
                                 [__import__("pydantic").TypeAdapter(dict).validate_python(x)
                                  for x in r.payload["nodes"]])],
                               prop.revision_reason)
    except (ValidationError, ValueError) as e:
        return f"plan_invalid: {e}"
    return (f"计划 revision={plan.revision} 已保存（{len(plan.nodes)} 节点）。"
            f"变更原因：{prop.revision_reason[:80]}。当前状态：{json.dumps(state.state_view(), ensure_ascii=False)}")


def _tool_execute(state: AnalysisState, args: dict, llm, sys_prompt: str,
                  ctx: Any, step: int, ma: dict) -> str:
    plan = state.latest_plan()
    node_id = args.get("node_id", "")
    if plan is None:
        return "错误: 尚无计划，请先 plan_analysis"
    node = next((n for n in plan.nodes if n.node_id == node_id), None)
    if node is None:
        return f"错误: 节点 {node_id} 不在当前计划"
    if node.execution_status == "completed" and node.review_status == "accepted":
        return f"错误: 节点 {node_id} 已完成并通过审核；如需重做请先修订计划"
    if node.attempt >= ma.get("max_node_attempts", 2):
        return f"错误: 节点 {node_id} 已达尝试上限"
    deps_bad = [d for d in node.depends_on
                if not any(n.node_id == d and n.execution_status == "completed"
                           for n in plan.nodes)]
    if deps_bad:
        return f"dependency_missing: 节点 {node_id} 缺依赖 {deps_bad}，请先补齐或改图"
    ctx.trace.event(type="subagent_start", role="worker", node_id=node_id, step=step)
    user = (f"任务目标：{node.goal}\n完成标准：{node.completion_criteria}\n"
            f"输入引用：{node.input_refs}\n"
            f"补充指令：{args.get('extra_instructions', '')}\n"
            f"待回应审核：{args.get('respond_to_review', '') or '无'}\n"
            "执行取数/计算/分析，输出 JSON：{\"facts\": [{rid, field, value, unit}], "
            "\"calculations\": [{expression, output}], \"interpretations\": [], "
            "\"hypotheses\": [{statement, relation}], \"limitations\": [], "
            "\"missing_inputs\": [], \"summary\": \"...\"}。synthetic 数据不得用于真实经营结论。")
    sub = subagents.SubAgentRunner(llm, ctx, "worker", sys_prompt,
                                   ma.get("worker_max_steps", 6))
    r = sub.run(user)
    ctx.stats["prompt_tokens"] += r.prompt_tokens
    ctx.stats["completion_tokens"] += r.completion_tokens
    for ev in r.events:
        ctx.trace.event(role="worker", step=step, **ev)
    if not r.ok:
        from agent.analysis_state import AnalysisArtifact as AA
        state.add_artifact(AA(artifact_id="", node_id=node_id, plan_revision=plan.revision,
                              attempt=node.attempt + 1, execution_status="failed",
                              summary=r.error or "worker 未输出合法结果"))
        return f"错误: 节点 {node_id} 执行失败（{r.error or '无合法输出'}），已记录"
    from agent.analysis_state import AnalysisArtifact as AA
    art = state.add_artifact(AA(
        artifact_id="", node_id=node_id, plan_revision=plan.revision,
        attempt=node.attempt + 1, execution_status="completed",
        facts=r.payload.get("facts") or [], calculations=r.payload.get("calculations") or [],
        interpretations=r.payload.get("interpretations") or [],
        hypotheses=r.payload.get("hypotheses") or [],
        limitations=r.payload.get("limitations") or [],
        missing_inputs=r.payload.get("missing_inputs") or [],
        summary=r.payload.get("summary", "")))
    return (f"节点 {node_id} 执行完成，artifact={art.artifact_id}。"
            f"摘要：{art.summary[:200] or '（见 artifact）'}。"
            f"建议用 review_analysis(target_type=artifact) 审查。")


def _tool_review(state: AnalysisState, args: dict, llm, sys_prompt: str,
                 ctx: Any, step: int, ma: dict, out_dir: Path) -> str:
    ttype = args.get("target_type", "")
    tid = args.get("target_id", "")
    tver = str(args.get("target_version", ""))
    if ttype == "artifact" and tid not in state.artifacts:
        return f"错误: artifact {tid} 不存在"
    target_content = ""
    if ttype == "artifact" and tid in state.artifacts:
        target_content = json.dumps(json.loads(state.artifacts[tid].model_dump_json()),
                                    ensure_ascii=False)
    elif ttype == "answer":
        target_content = json.dumps(state.answer_candidate or {}, ensure_ascii=False)
    elif ttype == "plan" and state.latest_plan():
        target_content = state.latest_plan().model_dump_json()
    if not target_content:
        return f"错误: 审查目标 {tid} 内容为空或不存在"
    user = (f"原始任务要求：{state.contract.query}\n"
            f"审查对象（{ttype}）：{target_content[:3000]}\n"
            f"重点：{args.get('focus', '') or '任务覆盖/口径/证据一致性/因果边界'}\n"
            "输出 JSON：{\"verdict\": \"accepted|needs_revision|inconclusive\", "
            "\"findings\": [{category, statement, explanation, suggested_action, node_id}], "
            "\"unresolved_items\": []}。分类只能用 task_scope/caliber/evidence_quality/"
            "contradiction/unsupported_cause/missing_alternative/other。")
    sub = subagents.SubAgentRunner(llm, ctx, "reviewer", sys_prompt,
                                   SUB_MAX_STEPS := 4)
    r = sub.run(user)
    ctx.stats["prompt_tokens"] += r.prompt_tokens
    ctx.stats["completion_tokens"] += r.completion_tokens
    for ev in r.events:
        ctx.trace.event(role="reviewer", step=step, **ev)
    if not r.ok:
        return "错误: Reviewer 未输出合法结果（inconclusive）"
    from agent.analysis_state import Review as R
    rv = state.add_review(R(review_id="", target_type=ttype, target_id=tid,
                            target_version=tver, verdict=r.payload["verdict"],
                            findings=r.payload.get("findings") or [],
                            unresolved_items=r.payload.get("unresolved_items") or []))
    return (f"审查 {rv.review_id} 完成：verdict={rv.verdict}，"
            f"findings={len(rv.findings)} 条。"
            + ("需修订后再提交。" if rv.verdict == "needs_revision" else ""))

def _multi_final_problems(fa, state: AnalysisState, answer_artifact: dict | None,
                          args: dict) -> list[str]:
    """multi 模式提交条件（§8）：候选已审、哈希一致、无未处理 stale。"""
    problems = []
    if answer_artifact is None:
        return ["尚未用 draft_answer 保存候选答案并审查"]
    aid = args.get("answer_artifact_id", "")
    if not aid:
        problems.append("final_answer 缺少 answer_artifact_id（multi 模式要求引用已审候选）")
        return problems
    cand = state.answer_candidate or {}
    if cand.get("artifact_id") != aid:
        problems.append(f"answer_artifact_id {aid} 与最近候选 {cand.get('artifact_id')} 不一致")
        return problems
    import hashlib
    h = lambda t: __import__("hashlib").sha256(t.encode("utf-8")).hexdigest()[:16]
    if h(fa.answer_md) != h(cand.get("answer_md", "")):
        problems.append("answer_md 与已审候选哈希不一致（防审查 A 提交 B）")
    reviews = [rv for rv in state.reviews.values()
               if rv.target_type == "answer" and rv.target_id == aid]
    if not reviews or reviews[-1].verdict != "accepted":
        problems.append(f"最终候选尚未被 review_analysis 接受（末次 verdict="
                        f"{reviews[-1].verdict if reviews else '未审查'}）")
    return problems


def _final_answer_multi(fa, ctx, state: AnalysisState, answer_artifact: dict | None,
                        numeric_ok: bool, stop_reason: str):
    from agent.loop import FinalAnswer
    from eval.schema import Answer
    sv = state.state_view()
    ans = Answer(answer_md=fa.answer_md, claims=fa.claims, status=fa.status)
    ctx.trace.event(type="final", mode="multi_agent",
                    semantic_review_status=("accepted" if answer_artifact else "未审查"),
                    task_coverage_status=("covered" if not any(
                        n["execution_status"] != "completed"
                        for n in sv["nodes"]) else "partial"))
    return FinalAnswer(answer=ans, verified=numeric_ok,
                       stats={**ctx.stats, "refuse_reason": stop_reason,
                              "semantic_review_status": ("accepted" if answer_artifact else "未审查")})

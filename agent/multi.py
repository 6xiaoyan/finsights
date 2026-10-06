"""多 Agent 主循环（handoff_multi_agent_attribution_development.md §5–§8）。

主 Agent 以 plan_analysis/execute_analysis/review_analysis/draft_answer 为工具，
自主决定规划、执行、审查与修订顺序；程序负责协议、依赖、权限、预算与持久化。
第一版：同步、单节点执行、共享 store、无递归 spawn；single_agent 路径保持不变。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent import hooks, subagents
from agent.analysis_state import (
    AnalysisArtifact, AnalysisState, Review, TaskContract, content_hash,
)
from agent.tools.base import DESCRIPTIONS

MULTI_BUDGET = {
    "total_subagent_calls": 12, "max_plan_nodes": 8, "worker_max_steps": 6,
    "max_node_attempts": 2, "max_plan_revisions": 3, "max_final_revisions": 2,
}

#: 子工具 → 被启动的角色（§9：三种角色各调了几次必须能分开统计）
_ROLE_OF_TOOL = {"plan_analysis": "planner", "execute_analysis": "worker",
                 "review_analysis": "reviewer"}


def _tool_schemas_multi() -> list[dict]:
    """主 Agent 工具面（§5）：三类子工具 + draft_answer + recall + final_answer。

    M1 缺陷（live 冒烟发现）：四类子工具的参数模型此前从未注册进任何 schema 生成器，
    按名字过滤 all_tool_schemas() 只剩 recall/final_answer——模型看不到子工具，
    于是整个 multi 路径退化成"取数一次就提交"。M2 改为 multi_tool_schemas() 显式构造。
    """
    from agent.tools.base import multi_tool_schemas
    return multi_tool_schemas()


def _state_view_text(state: AnalysisState, ma: dict) -> str:
    """子工具结果附带的精简状态（§7.3）：revision、就绪/受阻节点、有效 artifact、未处理 findings、剩余预算。"""
    v = state.state_view()
    return (f"[状态] revision={v['latest_revision']} "
            f"nodes={json.dumps(v['nodes'], ensure_ascii=False)} "
            f"就绪节点={v['ready_nodes']} 受阻节点={json.dumps(v['blocked_nodes'], ensure_ascii=False)} "
            f"有效 artifacts={v['valid_artifact_ids']}（全部 {v['artifact_ids']}）"
            f"未处理 findings={v['unresolved_findings']} "
            f"剩余子调用={ma['total_subagent_calls'] - ma.get('used_subagent_calls', 0)}")


def _validate_subtool_args(name: str, args: Any) -> str:
    """子工具参数预检：合法返回空串，非法返回可直接回给模型的错误文本。

    live 冒烟暴露的两类浪费：漏传 node_id 的 execute_analysis 仍吃掉一次子调用额度；
    参数错误的调用被算进 subagent_calls。预检放在预算检查之前，非法调用不启动子 Agent。
    """
    from agent.tools.base import MULTI_ARGS_MODELS
    model = MULTI_ARGS_MODELS.get(name)
    if model is None:
        return ""
    if not isinstance(args, dict):
        return (f"工具错误: {name} 的参数必须是 JSON 对象，收到 "
                f"{type(args).__name__}。本次调用未启动子 Agent、不占用子调用预算")
    try:
        model(**args)
    except (ValidationError, TypeError) as e:
        return (f"工具错误: {name} 参数不合法: {e}。"
                "本次调用未启动子 Agent、不占用子调用预算；请补全必填参数后重发")
    return ""


def _is_artifact_ref(ref: Any, state: AnalysisState) -> bool:
    """claim.ref 是否被误写成了分析产物/候选 ID（art1、answer-cand2）。

    第二次 live 冒烟的主因之一：Worker 产物 ID 看着就像引用编号，模型把 8 条 claim 的 ref
    写成 art6，数字核验按结果库 rid 查不到，反复"核验未通过"而数字本身其实是对的。
    程序只在提示层纠正，不改 verifier 判据。
    """
    s = str(ref or "")
    return s in state.artifacts or bool(re.fullmatch(r"(?:art|answer-cand)\d+", s))


def _claims_ref_hint(claims, state: AnalysisState) -> str:
    """claims 引用口径提示：ref 写成分析产物 ID 时，在 draft_answer 返回里就讲清楚。

    比等到 final_answer 反复"核验未通过"再猜原因便宜得多——第二次 live 冒烟就是靠
    8 条 "rid art6 不存在" 打回了同一个正确数字（verifier 判据不变，只补提示）。
    """
    bad = sorted({str(c.ref) for c in claims if _is_artifact_ref(c.ref, state)})
    if not bad:
        return ""
    return (f"但本候选有 {len(bad)} 条 claim 的 ref 写成了分析产物 ID（{bad[:5]}）："
            "数字核验只认取数工具返回的结果 rid（r# 形式，即 Worker facts/calculations 里的 rid）。"
            "请重新 draft_answer，把这些 ref 改成对应 rid，产物归属只写进 source_artifact_ids。")


def _subagent_refusal(ctx: Any, t0: float, used_tokens: int, ma: dict) -> str:
    """§9：子调用启动前的额度闸门；返回拒绝文本，额度充足返回空串。"""
    if used_tokens >= ctx.budget.max_tokens:
        return "错误: token 预算耗尽，不再启动子调用"
    if time.monotonic() - t0 > ctx.budget.max_wall_s:
        return "错误: 时间预算耗尽，不再启动子调用"
    if ma["used_subagent_calls"] >= ma["total_subagent_calls"]:
        return (f"错误: 子调用预算耗尽（total_subagent_calls={ma['total_subagent_calls']}），"
               f"不再启动子调用；各角色已用 {json.dumps(ma['role_calls'], ensure_ascii=False)}。"
               "后续只能基于现有有效证据 draft_answer，或在正文里说明未审查/未完成的节点")
    return ""


def run_multi(question: str, ctx: Any, llm: Any, out_dir: Path) -> Any:
    """multi_agent 主循环。ctx.agent_mode == "multi_agent" 时由 cli 调用。"""
    from agent.loop import FinalAnswer, _final_args
    from eval.schema import Answer

    ma = dict(MULTI_BUDGET)
    ma["used_subagent_calls"] = 0
    ma["role_calls"] = {}            # 每个角色实际启动次数（§9：三种角色是否真被调用要能查）
    ma["subagent_refusals"] = 0      # 因预算被拒的调用次数：与已启动次数分开计
    # 配置可收紧/放宽候选起点（config.yaml multi_agent 段），实际生效值必须进 run_meta（§9）
    for k, v in (getattr(ctx, "multi_cfg", None) or {}).items():
        if k in MULTI_BUDGET:
            try:
                ma[k] = int(v)
            except (TypeError, ValueError):
                pass    # 非法配置退回默认值，不静默改变预算语义
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

    schemas = _tool_schemas_multi()
    answer_artifact: dict | None = None
    messages = [{"role": "system",
                 "content": (Path("agent/prompts/multi/main.md").read_text(encoding="utf-8")
                             + "\n\n" + _state_view_text(state, ma))},
                {"role": "user", "content": question}]
    t0 = time.monotonic()
    ctx.stats["t_start"] = t0        # 与单 Agent 同一口径：单调时钟时差（§10 latency_s）
    final: Any | None = None
    stop_reason = ""
    final_rejects = 0                # §8：max_final_revisions 计数
    pending_stop = False
    for step in range(1, ctx.budget.max_steps + 1):
        used_tokens = (ctx.stats["prompt_tokens"] + ctx.stats["completion_tokens"])
        if used_tokens >= ctx.budget.max_tokens:
            stop_reason = "token 预算耗尽"
            break
        if time.monotonic() - t0 > ctx.budget.max_wall_s:
            stop_reason = "时间预算耗尽"
            break
        resp = ctx.llm.chat(messages, tools=schemas, role="agent")
        u = resp.usage or {}
        ctx.stats["prompt_tokens"] += u.get("prompt_tokens", 0)
        ctx.stats["completion_tokens"] += u.get("completion_tokens", 0)
        for ev in getattr(ctx.llm, "last_call_events", []) or []:
            # ev 自带 type="llm_retry"（agent/llm.py），不能再传 type，否则重复关键字
            ctx.trace.event(**ev, role="main", step=step)
        if not resp.tool_calls:
            messages.append({"role": "assistant", "content": resp.content or ""})
            if getattr(ctx.llm, "nudged", False):
                stop_reason = "模型未提交且未调用工具"
                break
            ctx.llm.nudged = True
            messages.append({"role": "user", "content": "请调用工具推进分析，或用 final_answer 提交（multi 模式需引用已审 answer artifact）。"})
            continue
        ctx.stats["steps"] = step
        ctx.trace.event(type="assistant", step=step, content=resp.content,
                        tool_calls=[{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                    for tc in resp.tool_calls],
                        usage=dict(resp.usage or {}))
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
                # 参数预检必须在预算检查之前：第二次 live 冒烟里模型发过漏传 node_id 的
                # execute_analysis，它照样吃掉一次子调用额度，12 次额度被无效调用浪费。
                # 非法调用既不启动子 Agent，也不计次、不扣额度。
                invalid = _validate_subtool_args(name, args)
                if invalid:
                    result_text = invalid
                # §9：启动子调用前检查剩余额度；父子合计同一 token/时间预算，耗尽不自动提高。
                # 计数只在真正启动子 Agent 时 +1：第一次 live 冒烟把被拒的调用也计进去了，
                # run_meta 报 subagent_calls=14 而 trace 只有 12 次 subagent_start，口径失真。
                elif (refusal := _subagent_refusal(ctx, t0, used_tokens, ma)):
                    result_text = refusal
                    ma["subagent_refusals"] = ma.get("subagent_refusals", 0) + 1
                else:
                    ma["used_subagent_calls"] += 1
                    ma["role_calls"][_ROLE_OF_TOOL[name]] = \
                        ma["role_calls"].get(_ROLE_OF_TOOL[name], 0) + 1
                    if name == "plan_analysis":
                        result_text = _tool_plan(state, args, llm, role_sys["planner"],
                                                 ctx, step, ma)
                    elif name == "execute_analysis":
                        result_text = _tool_execute(state, args, llm, role_sys["worker"], ctx,
                                                    step, ma)
                    else:
                        result_text = _tool_review(state, args, llm, role_sys["reviewer"],
                                                   ctx, step, ma, out_dir)
            elif name == "recall":
                result_text = _tool_recall(args, ctx, step, state)
            elif name == "draft_answer":
                from agent.tools.base import DraftAnswerArgs
                try:
                    da = DraftAnswerArgs(**args)
                except (ValidationError, TypeError) as e:
                    result_text = (f"工具错误: draft_answer 参数不合法: {e}。"
                                   "需要 answer_md（正文）与 claims（每项含 text/value/unit/ref）")
                else:
                    hint = _claims_ref_hint(da.claims, state)
                    state.add_answer_candidate({"answer_md": da.answer_md,
                                                "claims": [c.model_dump() for c in da.claims],
                                                "source_artifact_ids": da.source_artifact_ids})
                    cand = state.answer_candidate or {}
                    answer_artifact = {"answer_md": da.answer_md,
                                       "claims": [c.model_dump() for c in da.claims],
                                       "artifact_id": cand.get("artifact_id", "")}
                    result_text = (f"已保存候选答案（answer_artifact_id="
                                   f"{cand.get('artifact_id')}）。请用 review_analysis"
                                   f"(target_type=answer, target_id=该 ID) 审查后用 final_answer 引用提交。"
                                   + hint + _state_view_text(state, ma))
            elif name == "final_answer":
                fa, fa_err = _final_args(tc)   # 返回 (FinalAnswerArgs|None, 错误文本|None)
                if fa is None:
                    result_text = fa_err or "错误: final_answer 参数不合法"
                else:
                    problems = _multi_final_problems(fa, state, answer_artifact, args)
                    # 数字核验沿用 single 路径同一个 verifier（hooks.stop 默认 agent.verifier）：
                    # 判据不改、不跳过勾稽项；ctx.verifier=None 时也必须真正执行核验（§8）
                    verdict = hooks.stop(fa, ctx)
                    left = max(0, ma["max_final_revisions"] - final_rejects)
                    if not verdict.ok:
                        final_rejects += 1
                        result_text = ("核验未通过：" + (verdict.feedback or "数字/引用核验失败")
                                       + f"（剩余最终修订 {max(0, left - 1)} 次）")
                    elif problems:
                        final_rejects += 1
                        result_text = ("多 Agent 提交条件未满足：" + "；".join(problems[:3])
                                       + f"（剩余最终修订 {max(0, left - 1)} 次）")
                    else:
                        final = _final_answer_multi(fa, ctx, state, answer_artifact,
                                                    numeric_ok=True, stop_reason="", ma=ma)
                        result_text = "已提交（含已审候选与数字核验）。"
                    if final_rejects > ma["max_final_revisions"]:
                        # §8：上限后保留失败候选与明确"未通过"状态，不包装成合格结果
                        stop_reason = "最终候选核验/审查未通过（修订上限耗尽）"
                        pending_stop = True
            else:
                result_text = f"错误: 未知工具 {name}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
            # §10：multi 的 trace 必须能复盘"模型当时看到哪些工具、传了什么参数、得到什么"，
            # 单靠 result_len 无法定位 live 失败（第一次冒烟即因此看不清参数）。
            ctx.trace.event(type="tool_result", step=step, tool=name, call_id=tc.id,
                            args_head=(args_json or "")[:300],
                            ok=_tool_call_ok(result_text), result_len=len(result_text),
                            result_head=result_text[:400])
        if final is not None or pending_stop:
            break
    if final is None:
        stop_reason = stop_reason or "步数预算耗尽"
        ans = Answer(answer_md=f"多 Agent 运行未完成：{stop_reason}。"
                               f"已完成部分见 analysis_state.json。",
                     status="refuse")
        final = FinalAnswer(answer=ans, verified=False,
                            stats={**ctx.stats, "refuse_reason": stop_reason,
                                   **_multi_stats(state, ma, stop_reason, ctx)})
        state.persist()
    else:
        final.stats.update(_multi_stats(state, ma, stop_reason, ctx))
    ctx.trace.event(type="result", status=final.answer.status,
                    verified=final.verified, stop_reason=stop_reason)
    return final


def _multi_stats(state: AnalysisState, ma: dict, stop_reason: str, ctx: Any) -> dict:
    """§8/§10：多 Agent 独立状态字段与统计，不与 numeric_verified（原数字核验含义）混用。

    semantic_review_status 取最终候选的末次审查 verdict，不按"存在候选"推断；
    human_review_status 恒为 pending——模型审查 accepted 不等于业务 PASS。
    latency_s 用单调时钟时差（§10，不混用 Unix 时间戳）。
    """
    cand = state.answer_candidate or {}
    reviews = [r for r in state.reviews.values()
               if r.target_type == "answer" and r.target_id == cand.get("artifact_id")]
    nodes = state.state_view()["nodes"]
    incomplete = [n["node_id"] for n in nodes if n["execution_status"] != "completed"]
    t_start = ctx.stats.get("t_start")
    return {
        "agent_mode": "multi_agent",
        "semantic_review_status": reviews[-1].verdict if reviews else "not_reviewed",
        "task_coverage_status": ("no_plan" if not nodes else
                                 "partial" if incomplete else "covered"),
        "human_review_status": "pending",
        "subagent_calls": ma.get("used_subagent_calls", 0),
        "subagent_role_calls": dict(ma.get("role_calls") or {}),
        "subagent_refusals": ma.get("subagent_refusals", 0),
        "budgets_effective": {k: ma.get(k, MULTI_BUDGET[k]) for k in MULTI_BUDGET},
        "plan_revisions": state.latest_revision,
        "reviews_total": len(state.reviews),
        "findings_total": sum(len(r.findings) for r in state.reviews.values()),
        "unresolved_findings": state.state_view()["unresolved_findings"],
        "artifacts_total": len(state.artifacts),
        "artifacts_stale": sum(1 for a in state.artifacts.values() if a.stale),
        "nodes_incomplete": incomplete,
        "stop_reason": stop_reason or "",
        "latency_s": round(time.monotonic() - t_start, 2) if t_start else 0.0,
    }


def _tool_call_ok(text: str) -> bool:
    """工具结果是否成功（trace 的 ok 字段口径）。

    "工具错误: …"（参数校验失败，loop 的既有前缀）与 plan_invalid/dependency_missing
    都是失败；M1 只判断 ("错误","多 Agent","核验未通过")，把参数错误记成 ok=True。
    """
    return not text.startswith(("错误", "工具错误", "plan_invalid", "dependency_missing",
                                "核验未通过", "多 Agent"))


def _tool_recall(args: dict, ctx: Any, step: int, state: AnalysisState | None = None) -> str:
    """主 Agent 只读查看证据（§5/§7.3）：r# 走结果库，art#/answer-cand# 走分析状态。

    live 冒烟里主 Agent 想看失败节点的产物，recall(art4) 被结果库当 rid 查 → KeyError，
    等于给它一个死路；§7.3 本来就允许"一个简单只读状态工具或分页 artifact 读取"。
    """
    from agent.tools import data as tool_data
    from agent.tools.base import RecallArgs
    rid = str(args.get("result_id") or "")
    if state is not None and rid:
        if rid in state.artifacts:
            return state.artifacts[rid].model_dump_json(indent=1)
        cand = state.answer_candidate or {}
        if rid == cand.get("artifact_id"):
            return json.dumps(cand, ensure_ascii=False, indent=1)
        if rid.startswith(("art", "answer-cand")):
            return (f"错误: {rid} 不是当前状态里的 artifact/候选 ID"
                    "（可用 recall 读 r# 工具结果；artifact 清单见子工具结果末尾的 [状态]）")
    try:
        req = RecallArgs(**args)
    except (ValidationError, TypeError) as e:
        return f"工具错误: recall 参数不合法: {e}"
    out = tool_data.execute("recall", req.model_dump(), ctx.tool_ctx, step)
    return out.text if out.ok else f"错误: recall 失败：{out.text}"


def _run_role(role: str, sys_prompt: str, user: str, llm, ctx: Any, step: int,
              max_steps: int, **ident) -> Any:
    """同步执行一个子 Agent 并写调度事件（§10：角色、节点/目标、子步数、token、耗时、调用 ID）。

    子事件已自带 role 与子循环 step，转发时主循环步数记为 main_step，避免关键字冲突。
    """
    agent_call_id = f"{role}@{step}#{ident.get('node_id') or ident.get('target_id') or 'plan'}"
    ctx.trace.event(type="subagent_start", role=role, step=step,
                    agent_call_id=agent_call_id, **ident)
    t0 = time.perf_counter()
    sub = subagents.SubAgentRunner(llm, ctx, role, sys_prompt, max_steps)
    r = sub.run(user)
    ctx.stats["prompt_tokens"] += r.prompt_tokens
    ctx.stats["completion_tokens"] += r.completion_tokens
    # §9：主循环步数与子循环步数分别可查，不能用 max_steps 掩盖上百步子调用
    ctx.stats["subagent_child_steps"] = (ctx.stats.get("subagent_child_steps", 0)
                                         + r.child_steps)
    ctx.trace.event(type="subagent_end", role=role, step=step, ok=r.ok,
                    agent_call_id=agent_call_id,
                    child_steps=r.child_steps, prompt_tokens=r.prompt_tokens,
                    completion_tokens=r.completion_tokens,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    error=(r.error or "")[:200], **ident)
    for ev in r.events:
        ctx.trace.event(**ev, main_step=step, parent_call_id=agent_call_id)
    return r


def _tool_plan(state: AnalysisState, args: dict, llm, sys_prompt: str,
               ctx: Any, step: int, ma: dict) -> str:
    action = args.get("action", "create")
    if action == "create" and state.latest_revision:
        return "错误: 计划已存在，请用 action=revise"
    user = (f"TaskContract：{json.dumps(json.loads(state.contract.model_dump_json()), ensure_ascii=False)}\n"
            f"当前状态：{json.dumps(state.state_view(), ensure_ascii=False)}\n"
            f"修订原因：{args.get('reason', '')}\n"
            "请输出计划 JSON：{\"revision_reason\": ..., \"nodes\": [{node_id, goal, "
            "depends_on, input_refs, completion_criteria, required_for_answer}]}。\n"
            "修订规则（程序按此合并，不用重列整张图）：nodes 里只写要改动的节点即可，"
            "未列出的既有节点沿用原定义与原执行/审核状态；写了但定义未变的节点同样保留结果；"
            "定义变了的节点及其下游会失效，需要重新 execute_analysis。"
            "暂不支持删除节点，不想让它阻塞收尾就设 required_for_answer=false。")
    r = _run_role("planner", sys_prompt, user, llm, ctx, step, 4)
    if not r.ok:
        return "错误: " + (r.error or "Planner 未输出合法计划")
    from agent.subagents import PlanProposal
    try:
        prop = PlanProposal(**r.payload)
        prop.validate_semantics(state.max_plan_nodes)
        plan = state.save_plan(r.payload["nodes"], prop.revision_reason)
    except (ValidationError, ValueError) as e:
        return f"plan_invalid: {e}"
    ch = state.plan_change or {}
    return (f"计划 revision={plan.revision} 已保存（{len(plan.nodes)} 节点）。"
            f"变更原因：{prop.revision_reason[:80]}。"
            f"变更摘要：沿用未修改节点={ch.get('carried') or '（无）'}；"
            f"定义变化（结果失效，需重做）={ch.get('updated') or '（无）'}；"
            f"新增={ch.get('added') or '（无）'}；"
            f"本次失效 artifact={ch.get('stale_artifacts') or '（无）'}。"
            + _state_view_text(state, ma))


def _fact_provenance(facts: list[dict], calcs: list[dict], ctx: Any) -> tuple[list[dict], list[str], list[str]]:
    """程序侧出处校验（§6.3/§11-6/§11-11）：只检查 rid 是否存在、数据性质是否与来源一致。

    - fact 无 rid 或 rid 不在共享 Result Store → 记"出处缺失"，data_nature 标 unproven，
      不能因 artifact 里写了数值就当作原始财务事实。
    - 来源 rid 标了 synthetic（含混合）→ 按来源改写 fact.data_nature，模型无法把合成明细
      升级成真实经营事实。
    - calculations 表达式里没有任何 rid → 纯常量计算，明确标注它不是财务事实来源。
    返回 (修正后的 facts, 程序提示, evidence_refs)。使用是否合理仍交审核 Agent 判断。
    """
    import re
    notes: list[str] = []
    refs: list[str] = []
    out: list[dict] = []
    for raw in facts or []:
        f = dict(raw)
        rid = str(f.get("rid") or "")
        stored = ctx.store.get(rid) if rid else None
        if not rid:
            notes.append(f"fact「{f.get('field', '?')}」没有 rid：出处缺失，不能当作原始财务事实")
            f["data_nature"] = "unproven（无 rid）"
        elif stored is None:
            notes.append(f"fact「{f.get('field', '?')}」引用 rid={rid} 不在共享 Result Store："
                         "出处无法核验")
            f["data_nature"] = f"unproven（rid {rid} 不存在）"
        else:
            refs.append(rid)
            nature = str((stored.meta or {}).get("data_nature") or "")
            if "synthetic" in nature:
                declared = str(f.get("data_nature") or "未标注")
                if declared != nature:
                    notes.append(f"rid={rid} 来源标记为 {nature}，worker 申报 {declared}"
                                 "：已按来源改写，合成明细不得作为真实经营事实")
                f["data_nature"] = nature
        out.append(f)
    for c in calcs or []:
        expr = c.get("expression") if isinstance(c, dict) else str(c)
        used = sorted(set(re.findall(r"\br\d+\b", str(expr or ""))))
        unknown = [x for x in used if ctx.store.get(x) is None]
        if not used:
            notes.append(f"计算「{str(expr)[:60]}」未引用任何 rid：纯常量计算，"
                         "其结果不是财报事实来源")
        elif unknown:
            notes.append(f"计算「{str(expr)[:60]}」引用的 rid 不存在：{unknown}")
    return out, notes, sorted(set(refs))


def _tool_execute(state: AnalysisState, args: dict, llm, sys_prompt: str,
                  ctx: Any, step: int, ma: dict) -> str:
    plan = state.latest_plan()
    node_id = args.get("node_id", "")
    if plan is None:
        return "错误: 尚无计划，请先 plan_analysis"
    # plan_revision 省略/0 视为当前最新；显式指定却与最新不符则拒绝，而不是去旧图上找节点。
    # 第二次 live 冒烟里模型带着上一个 revision 的节点 ID 重试，程序只在"节点不在当前计划"
    # 处失败，模型看不出是版本问题，于是重复整轮取数。
    try:
        rev = int(args.get("plan_revision") or plan.revision)
    except (TypeError, ValueError):
        return (f"错误: plan_revision 必须是整数，收到 {args.get('plan_revision')!r}"
                "（省略或填 0 表示当前最新 revision）")
    if rev != plan.revision:
        return (f"错误: plan_revision={rev} 不是最新计划（当前 revision={plan.revision}）。"
                "计划修订后旧 revision 的节点不再执行；请按 [状态] 里的最新节点派发")
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
    user = (f"任务目标：{node.goal}\n完成标准：{node.completion_criteria}\n"
            f"输入引用：{node.input_refs}\n"
            f"补充指令：{args.get('extra_instructions', '')}\n"
            f"待回应审核：{args.get('respond_to_review', '') or '无'}\n"
            "执行取数/计算/分析，输出 JSON：{\"facts\": [{rid, field, value, unit, "
            "company, period, data_nature}], \"calculations\": [{input_rids, expression, "
            "output_rid, caliber}], \"interpretations\": [], "
            "\"hypotheses\": [{statement, relation}], \"limitations\": [], "
            "\"missing_inputs\": [], \"summary\": \"...\"}。"
            "每个数字都必须给出工具返回的 rid；rid 必须真实存在，不能凭记忆或估算填写。"
            "synthetic 数据必须如实标 data_nature=synthetic，不得用于真实经营结论；"
            "没有来源的数字请写进 missing_inputs 而不是编造 fact。")
    r = _run_role("worker", sys_prompt, user, llm, ctx, step,
                  ma.get("worker_max_steps", 6), node_id=node_id,
                  plan_revision=plan.revision, attempt=node.attempt + 1)
    if not r.ok:
        from agent.analysis_state import AnalysisArtifact as AA
        state.add_artifact(AA(artifact_id="", node_id=node_id, plan_revision=plan.revision,
                              attempt=node.attempt + 1, execution_status="failed",
                              missing_inputs=[r.error] if r.error else [],
                              summary=r.error or "worker 未输出合法结果"))
        return (f"错误: 节点 {node_id} 执行失败（{r.error or '无合法输出'}），已记录。"
                + _state_view_text(state, ma))
    from agent.analysis_state import AnalysisArtifact as AA
    facts, prov_notes, ev_refs = _fact_provenance(r.payload.get("facts") or [],
                                                  r.payload.get("calculations") or [], ctx)
    # 收尾轮产出的结果按 §6.3 记为 blocked：已完成部分入库可用，但不等于节点已完成
    partial = bool(r.finalized or r.steps_exhausted)
    miss = list(r.payload.get("missing_inputs") or [])
    lims = list(r.payload.get("limitations") or []) + prov_notes
    if partial:
        lims.append("子循环未在步数内自行收尾，由程序收尾轮保存已完成部分；"
                    "本节点按 blocked 计，需要补算请再次 execute_analysis")
        if not miss:
            miss = ["Worker 收尾轮未登记具体缺口：请核对节点目标是否已全部完成"]
    art = state.add_artifact(AA(
        artifact_id="", node_id=node_id, plan_revision=plan.revision,
        attempt=node.attempt + 1, execution_status="blocked" if partial else "completed",
        facts=facts, calculations=r.payload.get("calculations") or [],
        interpretations=r.payload.get("interpretations") or [],
        hypotheses=r.payload.get("hypotheses") or [],
        limitations=lims, missing_inputs=miss,
        evidence_refs=ev_refs,
        summary=r.payload.get("summary", "")))
    # §6.2/§7.2：本节点重做（新 attempt）后，下游节点的旧 artifact 必须失效
    stale_down = state.mark_stale_downstream(plan, node_id) if node.attempt >= 1 else []
    return (f"节点 {node_id} 执行{'部分完成（blocked）' if partial else '完成'}，"
            f"artifact={art.artifact_id}"
            f"（attempt {art.attempt}，状态 {art.execution_status}）。"
            f"缺口：{'；'.join(art.missing_inputs[:3]) or '无'}。"
            + (f"下游已标 stale：{stale_down}。" if stale_down else "")
            + (f"程序出处提示：{'；'.join(prov_notes[:3])}。" if prov_notes else "")
            + f"摘要：{art.summary[:200] or '（见 artifact）'}。"
            + ("blocked 产物仍需程序校验过的证据支撑结论："
               f"可 execute_analysis(node_id={node_id}) 补算，或"
               "在计划修订里把缺口写清；要查看本产物用 recall(result_id="
               f"{art.artifact_id})。" if partial else
               f"建议用 review_analysis(target_type=artifact, target_id={art.artifact_id}) 审查。")
            + _state_view_text(state, ma))   # §7.3：子工具结果附精简状态


def _artifact_version(a: AnalysisArtifact) -> str:
    return f"rev{a.plan_revision}-attempt{a.attempt}"


def _tool_review(state: AnalysisState, args: dict, llm, sys_prompt: str,
                 ctx: Any, step: int, ma: dict, out_dir: Path) -> str:
    ttype = args.get("target_type", "")
    tid = args.get("target_id", "")
    tver = str(args.get("target_version", ""))
    ver_note = ""
    if ttype == "artifact" and tid not in state.artifacts:
        return f"错误: artifact {tid} 不存在"
    target_content = ""
    if ttype == "artifact" and tid in state.artifacts:
        a = state.artifacts[tid]
        ver = _artifact_version(a)
        if tver and tver != ver:
            return (f"错误: 版本不一致（review_analysis 声明 {tver}，{tid} 实际是 {ver}）；"
                    f"请用当前版本重新审查——§7.3 要求对象存在、引用有效、版本一致")
        tver = ver
        if a.stale:
            return (f"错误: artifact {tid} 已被标记 stale（依赖节点重做），不能继承旧审核；"
                    f"请重新 execute_analysis 后再审查")
        target_content = a.model_dump_json()
    elif ttype == "answer":
        cand = state.answer_candidate or {}
        if not cand:
            return "错误: 尚无候选答案，请先 draft_answer"
        tid = tid or cand.get("artifact_id", "")      # 省略 ID 时按当前候选归一
        if tid != cand.get("artifact_id"):
            return (f"错误: 候选答案 ID 不一致（声明 {tid}，当前候选 "
                    f"{cand.get('artifact_id')}）")
        ver = content_hash(cand.get("answer_md", ""))
        # 正文哈希由程序分配，模型无法预先知道：声明不符时不rejct（会把流程逼进死角），
        # 而是按当前真实版本记录审查，并把不一致提示回给它；提交时仍以记录的版本比对兜底。
        if tver and tver != ver:
            ver_note = (f"注意：你声明的 target_version={tver} 与当前候选正文哈希 {ver} 不一致，"
                        f"本次按当前版本审查")
        else:
            ver_note = ""
        tver = ver
        target_content = json.dumps(cand, ensure_ascii=False)
    elif ttype == "plan" and state.latest_plan():
        if tver and tver != str(state.latest_revision):
            return (f"错误: 版本不一致（声明 {tver}，当前计划 revision "
                    f"{state.latest_revision}）")
        tver = str(state.latest_revision)
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
    r = _run_role("reviewer", sys_prompt, user, llm, ctx, step, 4,
                  target_type=ttype, target_id=tid, target_version=tver)
    if not r.ok:
        # §6.4/§7.3：审查无法给出依据时保存 inconclusive 记录，主 Agent 有可检查的失败产物
        from agent.analysis_state import Review as R
        rv = state.add_review(R(review_id="", target_type=ttype, target_id=tid,
                                target_version=tver, verdict="inconclusive",
                                unresolved_items=[f"reviewer 协议失败："
                                                  f"{(r.error or '无合法输出')[:160]}"]))
        return (f"审查 {rv.review_id}：inconclusive（Reviewer 未输出合法结果），已记录。"
                + _state_view_text(state, ma))
    from agent.analysis_state import Review as R, ReviewFinding as RF, VERDICTS
    payload = r.payload or {}
    verdict = payload.get("verdict")
    strict: list[str] = list(payload.get("unresolved_items") or [])
    # Review/ReviewFinding 是 extra=forbid + 枚举校验：真实模型给出 "approved" 之类会抛异常，
    # 不能让它把整个运行崩掉（离线 mock 恒给合法枚举，所以这条只在 live 路径暴露）
    if verdict not in VERDICTS:
        strict.append(f"reviewer 给出的 verdict 非法：{str(verdict)[:60]}，按 inconclusive 记录")
        verdict = "inconclusive"
    findings = []
    for f in payload.get("findings") or []:
        try:
            findings.append(RF(**f))
        except (ValidationError, TypeError) as e:
            strict.append(f"finding 不合协议（{str(e)[:100]}）")
    rv = state.add_review(R(review_id="", target_type=ttype, target_id=tid,
                            target_version=tver, verdict=verdict,
                            findings=findings, unresolved_items=strict))
    if ttype == "artifact" and tid in state.artifacts:
        # 执行状态/审核状态分离（§6）：review verdict 落到节点审核状态
        a = state.artifacts[tid]
        state.set_node_review_status(a.plan_revision, a.node_id, rv.verdict)
    return (f"审查 {rv.review_id} 完成：verdict={rv.verdict}，"
            f"findings={len(rv.findings)} 条。"
            + ("需修订后再提交。" if rv.verdict == "needs_revision" else "")
            + (ver_note + "。" if ver_note else "")
            + _state_view_text(state, ma))


def _multi_final_problems(fa, state: AnalysisState, answer_artifact: dict | None,
                          args: dict) -> list[str]:
    """multi 模式提交条件（§8）：候选已审且版本一致、必要节点有结果或明确缺口、无失效证据引用。

    未完成主任务时不能正常标完整 answered：这里返回的每条问题都会在修订预算内回给主 Agent，
    上限后按"未通过"终止并保留失败候选，不包装成合格结果。
    """
    problems: list[str] = []
    if answer_artifact is None:
        return ["尚未用 draft_answer 保存候选答案并审查："
                "必须按 plan_analysis → execute_analysis → review_analysis(artifact) "
                "→ draft_answer → review_analysis(target_type=answer) 得到 accepted "
                "→ final_answer(answer_artifact_id=候选 ID) 的顺序推进"]
    aid = getattr(fa, "answer_artifact_id", "") or args.get("answer_artifact_id", "")
    if not aid:
        problems.append("final_answer 缺少 answer_artifact_id（multi 模式要求引用已审候选，"
                        "该 ID 由 draft_answer 返回）")
        return problems
    cand = state.answer_candidate or {}
    if cand.get("artifact_id") != aid:
        problems.append(f"answer_artifact_id {aid} 与最近候选 {cand.get('artifact_id')} 不一致")
        return problems
    ver = content_hash(cand.get("answer_md", ""))
    if content_hash(fa.answer_md) != ver:
        problems.append("answer_md 与已审候选正文哈希不一致（防审查 A 提交 B）；"
                        "请重新 draft_answer 并再 review_analysis(answer)")
    reviews = [rv for rv in state.reviews.values()
               if rv.target_type == "answer" and rv.target_id == aid]
    if not reviews or reviews[-1].verdict != "accepted":
        problems.append(f"最终候选尚未被 review_analysis 接受（末次 verdict="
                        f"{reviews[-1].verdict if reviews else '未审查'}）")
    elif reviews[-1].target_version and reviews[-1].target_version != ver:
        problems.append(f"最近一次接受的审查版本是 {reviews[-1].target_version}，"
                        f"当前候选正文哈希 {ver}：候选已改动，旧 accepted 不继承，需重新审查")
    plan = state.latest_plan()
    if plan is None:
        problems.append("尚无任务计划：multi 模式需先 plan_analysis(action=create)，"
                        "证据由 execute_analysis 产出")
        return problems
    valid = set(state.valid_artifact_ids())
    unmet = []
    for n in plan.nodes:
        if not n.required_for_answer:
            continue
        kind = _node_evidence(state, n, valid)
        if kind == "ok" or kind == "explained":   # §8：有有效结果或明确缺口都可以收尾
            continue
        unmet.append(n.node_id + ("（产物已失效，需重新执行）" if kind == "stale" else ""))
    if unmet:
        problems.append("必要节点尚无结果或明确缺口：" + "，".join(unmet)
                        + "（请 execute_analysis 补齐，或在计划修订里说明缺口）")
    bad_refs = [x for x in (cand.get("source_artifact_ids") or []) if x not in valid]
    if bad_refs:
        problems.append(f"候选引用的 artifact 不存在或已被标 stale：{bad_refs}"
                        "（依赖重做后旧证据不能沿用）")
    # claims 的引用口径：ref 必须是取数工具返回的 rid，产物 ID 只进 source_artifact_ids。
    bad_claim_refs = sorted({c.get("ref") for c in (cand.get("claims") or [])
                             if _is_artifact_ref(c.get("ref"), state)})
    if bad_claim_refs:
        problems.append(f"claims 的 ref 写成了分析产物 ID：{bad_claim_refs}"
                        "（数字核验按工具 rid 查找，请重新 draft_answer 把 ref 改成 r# 形式的 rid；"
                        "产物归属只写 source_artifact_ids）")
    return problems


def _node_evidence(state: AnalysisState, node, valid: set[str]) -> str:
    """必要节点在提交时刻的证据状态（§8 提交条件与资源统计共用同一口径）。

    ok=有当前有效的完成产物；explained=已完成部分（blocked）或登记了明确缺口；
    stale=产物存在但已被上游重做标失效（必须重新执行，不能沿用）；missing=尚无可用产物。
    blocked 的 facts 仍是程序核对过的证据（§6.3），可以引用，但不等于节点已完成。
    """
    aid = node.latest_artifact_id or ""
    a = state.artifacts.get(aid)
    if a is None:
        return "missing"
    if a.stale:
        return "stale"
    if a.execution_status == "completed":
        return "ok" if aid in valid else "missing"
    if a.execution_status == "blocked" or a.missing_inputs:
        return "explained"
    return "missing"


def _final_answer_multi(fa, ctx, state: AnalysisState, answer_artifact: dict | None,
                        numeric_ok: bool, stop_reason: str, ma: dict | None = None):
    from agent.loop import FinalAnswer
    from eval.schema import Answer
    extra = _multi_stats(state, ma or {}, stop_reason, ctx)
    ctx.trace.event(type="final", mode="multi_agent",
                    numeric_verified=numeric_ok,
                    semantic_review_status=extra["semantic_review_status"],
                    task_coverage_status=extra["task_coverage_status"],
                    human_review_status=extra["human_review_status"])
    return FinalAnswer(answer=Answer(answer_md=fa.answer_md, claims=fa.claims,
                                     status=fa.status),
                       verified=numeric_ok,
                       stats={**ctx.stats, "refuse_reason": stop_reason, **extra})


def analysis_review_md(state: AnalysisState, stats: dict | None = None) -> str:
    """§10：人工可读审查摘要 analysis_review.md。

    只呈现可检查的中间产物（contract、任务图、artifact、review、状态字段与资源），
    不输出私有思维链；模型审查 verdict 不等于业务 PASS，human_review_status 恒为 pending。
    """
    st = stats or {}
    c = state.contract
    L = ["# 多 Agent 分析审查摘要", "",
         f"- 模式：multi_agent　终止原因：{st.get('stop_reason') or st.get('refuse_reason') or '（已提交）'}",
         f"- 数字核验 numeric_verified：{st.get('numeric_verified', '（未提交）')}",
         f"- semantic_review_status：{st.get('semantic_review_status', 'not_reviewed')}"
         f"　task_coverage_status：{st.get('task_coverage_status', 'partial')}",
         f"- human_review_status：pending（模型审查意见不构成业务正确判定）", "",
         "## 任务契约（TaskContract）", "",
         f"- query：{c.query}",
         f"- company={c.company}　period={c.period}　comparison={c.comparison}"
         f"　as_of={c.as_of}　data_version={c.data_version}",
         f"- 输出要求：{c.output_requirements}",
         f"- 说明/补充：{'; '.join(c.scope_notes) or '（无）'}",
         f"- 澄清状态：{c.clarification_status}", "",
         "## 计划版本与节点", ""]
    for rev in sorted(state.plans):
        plan = state.plans[rev]
        L += [f"### revision {rev}（{plan.revision_reason[:80] or '无原因记录'}）", "",
              "| node | goal | 依赖 | 执行状态 | 审核状态 | artifact | attempt |",
              "|---|---|---|---|---|---|---|"]
        for n in plan.nodes:
            L.append(f"| {n.node_id} | {n.goal[:60]} | {'，'.join(n.depends_on) or '—'} "
                     f"| {n.execution_status} | {n.review_status} "
                     f"| {n.latest_artifact_id or '—'} | {n.attempt} |")
        L.append("")
    L += ["## 执行产物（AnalysisArtifact）", ""]
    if not state.artifacts:
        L.append("（本次运行没有保存任何 artifact：执行环节未产出可检查结果）")
    for aid, a in state.artifacts.items():
        L += [f"### {aid}（节点 {a.node_id}，revision {a.plan_revision}，"
              f"attempt {a.attempt}，状态 {a.execution_status}）", ""]
        if a.summary:
            L += [f"摘要：{a.summary}", ""]
        if a.facts:
            L += ["- facts（rid → 字段 → 数据性质）："]
            for f in a.facts:
                L.append(f"  - {f.get('rid', '?')} {f.get('field', '?')}="
                         f"{f.get('value', '?')} {f.get('unit', '')}"
                         f"［性质：{f.get('data_nature') or f.get('nature') or '未标注'}］")
            L.append("")
        if a.calculations:
            L += ["- calculations："]
            for k in a.calculations:
                L.append(f"  - {k.get('expression', k)}（输入 rid："
                         f"{k.get('input_rids', k.get('inputs', '未记录'))}）")
            L.append("")
        if a.hypotheses:
            L += ["- hypotheses："]
            for hh in a.hypotheses:
                L.append(f"  - [{hh.get('relation', '?')}] {hh.get('statement', hh)}")
            L.append("")
        for key, label in (("interpretations", "interpretations"),
                           ("limitations", "limitations"),
                           ("missing_inputs", "missing_inputs")):
            vals = getattr(a, key) or []
            if vals:
                L += [f"- {label}：{ '；'.join(str(v) for v in vals) }"]
        if a.evidence_refs:
            L.append(f"- evidence_refs：{'，'.join(a.evidence_refs)}")
        if a.stale:
            L.append("- ⚠ 该 artifact 已被标记 stale（依赖节点重做），不能用于完成新计划")
        L.append("")
    L += ["## 审查意见（Review）", ""]
    if not state.reviews:
        L.append("（本次运行没有 review_analysis 产物）")
    for rid_, r in state.reviews.items():
        L += [f"### {rid_}：{r.target_type} {r.target_id} @ {r.target_version or '（未记版本）'}"
              f" → {r.verdict}", ""]
        for f in r.findings:
            L.append(f"- [{f.category}] {f.statement}"
                     + (f"（证据 {', '.join(f.evidence_refs)}）" if f.evidence_refs else "")
                     + (f"——{f.explanation}" if f.explanation else "")
                     + (f"　建议：{f.suggested_action}" if f.suggested_action else ""))
        if r.unresolved_items:
            L.append(f"- 未解决项：{'；'.join(r.unresolved_items)}")
        L.append("")
    cand = state.answer_candidate or {}
    L += ["## 最终候选答案", ""]
    if cand:
        L += [f"- artifact_id：{cand.get('artifact_id')}"
              f"　claims：{len(cand.get('claims') or [])} 条"
              f"　正文哈希（审查/提交比对用）：{content_hash(cand.get('answer_md', ''))}",
              f"- 来源 artifacts：{'，'.join(cand.get('source_artifact_ids') or []) or '（未登记）'}"
              f"　当前有效证据：{'，'.join(state.valid_artifact_ids()) or '（无）'}", "",
              str(cand.get("answer_md", ""))[:4000], ""]
    else:
        L.append("（未调用 draft_answer，无候选答案）")
    L += ["", "## 资源与调度", "",
          f"- 子 Agent 调用：{st.get('subagent_calls', 0)}"
          f"／生效预算：{json.dumps(st.get('budgets_effective') or {}, ensure_ascii=False)}",
          f"- 计划修订：{st.get('plan_revisions', 0)}　review 数：{st.get('reviews_total', 0)}　"
          f"findings：{st.get('findings_total', 0)}　未处理 findings：{len(st.get('unresolved_findings') or [])}",
          f"- token：prompt={st.get('prompt_tokens')}，completion={st.get('completion_tokens')}，"
          f"steps={st.get('steps')}，子循环合计步数={st.get('subagent_child_steps', 0)}，"
          f"latency_s={st.get('latency_s')}",
          f"- 未完成节点：{st.get('nodes_incomplete') or '（无）'}", "",
          "## 边界声明", "",
          "- 本文只汇总程序校验过的结构化产物与调度动作，不含模型私有思维链。",
          "- 语义审查 verdict 是模型意见；数字核验通过只代表现有数字/引用规则通过，"
          "不代表经营因果结论被证明。",
          "- facts_detail 合成数据标记为 synthetic，不得作为真实经营事实证据。"]
    return "\n".join(L) + "\n"

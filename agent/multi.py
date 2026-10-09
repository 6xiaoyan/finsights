"""多 Agent 主循环（handoff_multi_agent_attribution_development.md §5–§8）。

主 Agent 以 plan_analysis/execute_analysis/review_analysis/draft_answer 为工具，
自主决定规划、执行、审查与修订顺序；程序负责协议、依赖、权限、预算与持久化。
第一版：同步、单节点执行、共享 store、无递归 spawn；single_agent 路径保持不变。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent import hooks, subagents
from agent.analysis_state import (
    AnalysisArtifact, AnalysisState, RELEASING_STATES, Review, TaskContract, content_hash,
)
from agent.tools.base import DESCRIPTIONS

#: 开发裁定（handoff「最新裁定」2026-10-06）：multi 模式只以**最大轮数**控制运行。
#: 每轮＝一次模型请求（无工具输出、协议纠错、收尾交付都各算一轮）。数值是开发起点，不是验收标准。
MULTI_ROUNDS = {
    "main_max_steps": 20,      # 与 single 共用 ctx.budget.max_steps，这里只作展示兜底
    "planner_max_steps": 4,
    "worker_max_steps": 6,
    "reviewer_max_steps": 4,
}
#: 已取消的资源停止上限：仍完整记录消耗，但不再据此提前退出或前置拒绝。
CANCELED_LIMITS = ["时间(max_wall_s)", "token(max_tokens)", "子调用总数",
                   "任务图节点数", "单节点尝试次数", "计划修订次数", "最终答案修订次数"]
#: 保留的接口运行机制（不是整次分析的墙钟预算）：RPM 节流 / 单请求超时 / 有限网络重试。
KEPT_RUNTIME_GUARDS = ["API RPM 节流", "单请求超时", "有限网络重试"]


def _tool_schemas_multi() -> list[dict]:
    """主 Agent 工具面（§5）：三类子工具 + draft_answer + recall + final_answer。

    M1 缺陷（live 冒烟发现）：四类子工具的参数模型此前从未注册进任何 schema 生成器，
    按名字过滤 all_tool_schemas() 只剩 recall/final_answer——模型看不到子工具，
    于是整个 multi 路径退化成"取数一次就提交"。M2 改为 multi_tool_schemas() 显式构造。
    """
    from agent.tools.base import multi_tool_schemas
    return multi_tool_schemas()


def _state_view_text(state: AnalysisState, ma: dict) -> str:
    """子工具结果附带的精简状态（§7.3）：revision、就绪/受阻节点、有效 artifact、未处理 findings。

    开发裁定取消子调用总额度，这里只报已启动次数（观察用），不再报"剩余子调用"。
    """
    v = state.state_view()
    return (f"[状态] revision={v['latest_revision']} "
            f"nodes={json.dumps(v['nodes'], ensure_ascii=False)} "
            f"就绪节点={v['ready_nodes']} 受阻节点={json.dumps(v['blocked_nodes'], ensure_ascii=False)} "
            f"有效 artifacts={v['valid_artifact_ids']}（全部 {v['artifact_ids']}）"
            f"未处理 findings={v['unresolved_findings']} "
            f"已启动子调用={ma.get('used_subagent_calls', 0)}")


def _validate_subtool_args(name: str, args: Any) -> str:
    """子工具参数预检：合法返回空串，非法返回可直接回给模型的错误文本。

    预检的作用（开发裁定取消额度后仍然成立）：漏传 node_id 之类的非法调用不启动子 Agent，
    因此既不产生 subagent_start、也不计入 subagent_calls（M2 复审 §3 的口径一致性），
    而是单独记一次 subagent_refusals。正确性校验本身不因取消资源上限而放宽。
    """
    from agent.tools.base import MULTI_ARGS_MODELS
    model = MULTI_ARGS_MODELS.get(name)
    if model is None:
        return ""
    if not isinstance(args, dict):
        return (f"工具错误: {name} 的参数必须是 JSON 对象，收到 "
                f"{type(args).__name__}。本次调用未启动子 Agent、不计入子 Agent 启动数")
    try:
        model(**args)
    except (ValidationError, TypeError) as e:
        return (f"工具错误: {name} 参数不合法: {e}。"
                "本次调用未启动子 Agent、不计入子 Agent 启动数；请补全必填参数后重发")
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


class RunHalted(Exception):
    """控制信号：暂停 / 取消 / 执行权失效。

    刻意用异常而不是返回值：这三种都不是"分析跑完了"，如果塞进 FinalAnswer 里，
    调用方很容易把它当成一个 refuse 答案照单收下。抛出后由 Worker 决定
    保存检查点还是标记终态（PRD §7.3：取消优先于暂停和成功发布）。
    """

    def __init__(self, kind: str, reason: str = ""):
        super().__init__(f"{kind}: {reason}" if reason else kind)
        self.kind = kind          # paused | cancelled | no_execution_right
        self.reason = reason


@dataclass
class MultiCheckpoint:
    """multi 主循环的检查点（PRD §10.1/§10.3）。

    显式序列化纯数据：数据库连接、锁、模型客户端、trace 文件句柄恢复时重建，
    **不使用 pickle**。这里存的是"下一次模型请求需要的全部输入"，加上"恢复时要校验的
    引用"（任务图、产物版本、rid 计数器）。
    """

    schema_version: str = "mc1"
    next_step: int = 1              # 下一个主循环轮次
    messages: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)     # AnalysisState.snapshot() 负载
    store: dict = field(default_factory=dict)     # ResultStore.export_state()
    stats: dict = field(default_factory=dict)
    rounds: dict = field(default_factory=dict)    # ma 的子调用计数等
    versions: dict = field(default_factory=dict)  # data/prompt/config/engine 版本
    stop_reason: str = ""
    final_published: bool = False


def _capture_multi_checkpoint(*, next_step: int, messages: list[dict], state,
                              store, stats: dict, ma: dict, versions: dict,
                              stop_reason: str = "", final_published: bool = False) -> MultiCheckpoint:
    """把当前主循环状态打包成可 JSON 序列化的检查点。"""
    return MultiCheckpoint(next_step=next_step, messages=list(messages),
                           state=state.snapshot(), store=store.export_state(),
                           stats=dict(stats), rounds=dict(ma), versions=dict(versions),
                           stop_reason=stop_reason, final_published=final_published)


def _pending_batch(messages: list[dict]) -> tuple[list[dict], int] | None:
    """找出最后一条 assistant 消息里**尚未提交结果**的 tool_call 批次。

    PRD §10.2：一条主消息含多个 tool_call 时，保存待处理列表和 cursor；
    逐项完成并提交后推进 cursor；**恢复只执行未提交调用，不重复执行整个消息**。
    返回 (tool_calls, cursor)；没有未提交调用时返回 None。

    注意 assistant 的 tool_call 必须与 tool 结果一一配对（PRD §10.2），
    所以这里按"该 assistant 消息之后、属于它的 tool 消息条数"算 cursor，
    而不是按位置猜——否则中途中断后会错位执行或漏执行。
    """
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = m["tool_calls"]
            ids = {c.get("id") for c in calls}
            done = sum(1 for x in messages[i + 1:]
                       if x.get("role") == "tool" and x.get("tool_call_id") in ids)
            return (calls, done) if done < len(calls) else None
        if m.get("role") == "user":
            return None              # 已进入下一轮，没有悬空的调用批次
    return None


def _messages_wellformed(messages: list[dict]) -> bool:
    """校验主循环消息协议（PRD §10.2：恢复前必须验证 assistant/tool 一一配对）。

    合法状态有两种：批次已全部配对，或**最后一批只提交了前一部分**——后者正是
    "逐项提交后推进 cursor"要支持的恢复点，把它当成损坏会让续跑在最需要的时候失效。

    判为损坏的是：tool 结果没有对应的 assistant 批次、id 不属于当前批次、
    提交顺序与调用顺序不一致、上一批还没配对完又开了新批次。
    """
    open_ids: list[str] = []
    committed = 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            if open_ids:
                return False        # 上一批还没配对完就又开一批
            open_ids = [c.get("id") for c in m["tool_calls"]]
            committed = 0
        elif m.get("role") == "tool":
            tid = m.get("tool_call_id")
            if not open_ids or tid not in open_ids:
                return False        # 没有批次，或 id 不属于当前批次
            if tid != open_ids[committed]:
                return False        # 提交顺序与调用顺序不一致 → cursor 会错位
            committed += 1
            if committed == len(open_ids):
                open_ids = []      # 本批已配对完毕；不关闭的话下一批会被误判成嵌套
    return True


def run_multi(question: str, ctx: Any, llm: Any, out_dir: Path, *,
              resume: MultiCheckpoint | None = None,
              checkpoint_sink: Any = None, event_sink: Any = None,
              control: Any = None) -> Any:
    """multi_agent 主循环。ctx.agent_mode == "multi_agent" 时由 cli 调用。

    PRD §3.1/§10.3：`resume` 非空时载入既有主循环状态继续推进，而不是初始化空
    state/messages。`checkpoint_sink(ckpt, reason)` 在安全边界被调用；
    `event_sink(dict)` 抛业务阶段事件；`control` 提供取消/暂停/执行权查询。
    """
    from agent.loop import FinalAnswer
    from eval.schema import Answer

    ma = dict(MULTI_ROUNDS)
    ma["used_subagent_calls"] = 0    # 实际启动的子 Agent 数（在 _run_role 里 +1，与 subagent_start 一一对应）
    ma["role_calls"] = {}            # 每个角色实际启动次数（§9：三种角色是否真被调用要能查）
    ma["subagent_refusals"] = 0      # 未通过前置校验、根本没启动子 Agent 的调用：单独留痕，不计入启动数
    ma["final_rejects"] = 0          # final_answer 被核验/提交条件打回的次数（仅记录，不再据此提前停止）
    # 生效轮数可配置（config.yaml multi_agent 段）；资源上限已取消，这里只收轮数键
    for k, v in (getattr(ctx, "multi_cfg", None) or {}).items():
        if k in MULTI_ROUNDS:
            try:
                ma[k] = int(v)
            except (TypeError, ValueError):
                pass    # 非法配置退回默认值，不静默改变轮数语义
    versions = dict(getattr(ctx, "engine_versions", None) or {})
    versions.setdefault("data_version", ctx.store.data_version)
    cur_step = 1
    stop_reason = ""
    final: Any | None = None
    answer_artifact: dict | None = None

    def _ckpt(reason: str, next_step: int, published: bool = False) -> None:
        """在安全边界把主循环状态交出去（PRD §10.2）。

        sink 抛出异常就让它往上传播：检查点保存失败时不能继续往下跑，
        否则会出现"宣称可恢复、实际恢复不了"的状态（§12 把它列为高危告警）。
        """
        if checkpoint_sink is None:
            return
        checkpoint_sink(_capture_multi_checkpoint(
            next_step=next_step, messages=messages, state=state, store=ctx.store,
            stats=ctx.stats, ma=ma, versions=versions,
            stop_reason=stop_reason, final_published=published), reason)

    def _halt(kind: str, where: str, reason: str = "") -> None:
        """暂停要留下可恢复边界；取消不保证可恢复（P0 取消后不允许 resume）。"""
        if kind == "paused":
            _ckpt("paused", cur_step)
        raise RunHalted(kind, reason or where)

    def _gate(where: str) -> None:
        """执行权与取消/暂停检查（PRD §7.3）。顺序固定：执行权 → 取消 → 暂停。

        执行权最先查：失联的旧 Worker 必须立刻停手，不能因为"没收到取消请求"
        就继续调用模型——它的结果已经没人接收了。
        """
        if control is None:
            return
        control.check_execution_right()
        if control.should_cancel():
            _halt("cancelled", where, "已请求取消")
        if control.should_pause():
            _halt("paused", where, "已请求暂停")

    def _emit(event: dict) -> None:
        if event_sink is not None:
            event_sink(event)

    if resume is not None:
        # §10.3：必须载入既有主循环状态，而不是初始化空 state/messages。
        if resume.schema_version != MultiCheckpoint.schema_version:
            raise ValueError(f"CHECKPOINT_INCOMPATIBLE: multi checkpoint schema "
                             f"{resume.schema_version} != {MultiCheckpoint.schema_version}")
        if not _messages_wellformed(resume.messages):
            raise ValueError("CHECKPOINT_CORRUPT: assistant.tool_calls 与 tool 结果未一一配对")
        state = AnalysisState.restore(resume.state, out_dir / "analysis")
        ctx.analysis_state = state
        ctx.store.import_state(resume.store)
        messages = [dict(m) for m in resume.messages]
        # 累计用量/统计延续，不能因为换进程就清零（§10.1 消耗与控制）
        for k, v in (resume.stats or {}).items():
            if k != "t_start":
                ctx.stats[k] = v
        for k, v in (resume.rounds or {}).items():
            ma[k] = v
        cand = state.answer_candidate or {}
        answer_artifact = dict(cand) if cand else None
        stop_reason = resume.stop_reason or ""
        _emit({"type": "recovering", "next_step": resume.next_step,
               "artifacts": len(state.artifacts), "plans": state.latest_revision})
    else:
        contract = TaskContract(
            query=question, as_of=ctx.as_of.isoformat() if ctx.as_of else None,
            data_version=ctx.store.data_version,
            **(getattr(ctx, "parsed_scope_extra", {}) or {}))
        # 开发裁定：任务图节点数/计划修订次数不再是停止上限，传 0＝不拦（结构合法性仍由 save_plan 校验）
        state = AnalysisState(contract, out_dir / "analysis")
        ctx.analysis_state = state

    prompts = Path("agent/prompts/multi")
    role_sys = {r: (prompts / f"{r}.md").read_text(encoding="utf-8")
                for r in ("planner", "worker", "reviewer")}

    schemas = _tool_schemas_multi()
    if resume is None:
        messages = [{"role": "system",
                     "content": (Path("agent/prompts/multi/main.md").read_text(encoding="utf-8")
                                 + "\n\n" + _state_view_text(state, ma))},
                    {"role": "user", "content": question}]
    ctx.stats["t_start"] = time.monotonic()   # 与单 Agent 同一口径：单调时钟时差（§10 latency_s）
    # §10.2：首次初始化也是保存边界——进程可能在第一步之前就死
    _ckpt("initialized", 1)
    # 开发裁定（handoff「最新裁定」2026-10-06）：multi 只以最大轮数控制运行——主循环
    # ctx.budget.max_steps + 每个子 Agent 各自的 max_steps；每轮＝一次模型请求。
    # 旧的时间/token/子调用次数停止上限全部取消（消耗仍完整记录，见 _multi_stats），
    # 但不再据此提前 break 或前置拒绝；正确性校验（依赖/版本/角色/数据性质/引用/终审）全部保留。
    def _run_batch(calls: list[dict], cursor: int, step: int) -> None:
        """执行一条 assistant 消息里的 tool_call 批次，从 cursor 起。

        §10.2 的 cursor 语义：逐项提交后推进，**恢复只执行未提交调用**。
        `calls` 是普通 dict（不是 ToolCallOut），因为恢复时模型响应已经不存在了，
        只能从 messages 里那条 assistant 消息重建调用。
        """
        nonlocal answer_artifact, final, cur_step
        for i in range(cursor, len(calls)):
            cur_step = step
            tc = calls[i]
            name = tc.get("function", {}).get("name", "")
            args_json = tc.get("function", {}).get("arguments") or "{}"
            tc_id = tc.get("id") or f"call_{step}_{i}"
            _gate(f"tool_call:{name}")
            try:
                args = json.loads(args_json or "{}")
            except Exception:
                args = {}
            result_text, published = _dispatch_tool(state, name, args, args_json, tc,
                                                    llm, role_sys, ctx, step, ma, out_dir,
                                                    answer_artifact)
            if name == "draft_answer":
                answer_artifact = dict(state.answer_candidate or {}) or None
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})
            # §10：multi 的 trace 必须能复盘"模型当时看到哪些工具、传了什么参数、得到什么"，
            # 单靠 result_len 无法定位 live 失败（第一次冒烟即因此看不清参数）。
            ctx.trace.event(type="tool_result", step=step, tool=name, call_id=tc_id,
                            args_head=(args_json or "")[:300],
                            ok=_tool_call_ok(result_text), result_len=len(result_text),
                            result_head=result_text[:400])
            # 逐项提交后推进 cursor。next_step 仍指向本 step，意思是"恢复时先补完本批"
            _ckpt(f"tool_result:{name}", step)
            if published is not None:
                final = published
                _ckpt("final_published", step, published=True)
                return
        # 本批全部提交完毕 → 下一轮是新的模型请求
        _ckpt("batch_committed", step + 1)

    pending = _pending_batch(messages)
    if pending is not None:
        # 恢复：先补完上次没提交完的 tool_call 批次，**不重发那次模型请求**
        pcalls, pcursor = pending
        cur_step = resume.next_step
        _emit({"type": "resumed_pending_calls", "pending": len(pcalls) - pcursor,
               "of": len(pcalls), "step": resume.next_step})
        _run_batch(pcalls, pcursor, resume.next_step)

    first_step = resume.next_step + 1 if pending is not None else (
        resume.next_step if resume is not None else 1)
    for step in range(first_step, ctx.budget.max_steps + 1):
        cur_step = step
        _gate("model_call")
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
        _run_batch([{"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in resp.tool_calls], 0, step)
        if final is not None:
            break
    if final is None:
        # 只以最大轮数收敛：轮数用尽即如实报告"未达提交条件"，保存中间产物，绝不自标通过。
        stop_reason = stop_reason or f"主循环轮数用尽（max_steps={ctx.budget.max_steps}，未提交最终答案）"
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
        "final_answer_rejects": ma.get("final_rejects", 0),
        # 开发裁定：导出明确标记生效轮数 + 被取消的资源上限 + 完整消耗（记录不拦截）。
        "effective_rounds": {k: ma.get(k) for k in MULTI_ROUNDS},
        "canceled_limits": list(CANCELED_LIMITS),
        "kept_runtime_guards": list(KEPT_RUNTIME_GUARDS),
        "consumption": {
            "prompt_tokens": ctx.stats.get("prompt_tokens", 0),
            "completion_tokens": ctx.stats.get("completion_tokens", 0),
            "main_rounds": ctx.stats.get("steps", 0),
            "subagent_child_rounds": ctx.stats.get("subagent_child_steps", 0),
        },
        "plan_revisions": state.latest_revision,
        "reviews_total": len(state.reviews),
        "findings_total": sum(len(r.findings) for r in state.reviews.values()),
        "unresolved_findings": state.state_view()["unresolved_findings"],
        "artifacts_total": len(state.artifacts),
        "artifacts_stale": sum(1 for a in state.artifacts.values() if a.stale),
        "nodes_incomplete": incomplete,
        # PRD §4/§9：暂停节点与上下文续接的消耗单独可查，便于判断"是不是一直在原地打转"
        "nodes_paused": [n["node_id"] for n in nodes if n["execution_status"] == "paused"],
        "nodes_blocked": [n["node_id"] for n in nodes if n["execution_status"] == "blocked"],
        "continuations_total": len(getattr(state, "continuations", {}) or {}),
        "continuations_used": sum(1 for c in (getattr(state, "continuations", {}) or {}).values()
                                 if c.get("used")),
        "continuations_invalid": sum(1 for c in (getattr(state, "continuations", {}) or {}).values()
                                     if c.get("invalid_reason")),
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


def _as_call(tc: dict) -> Any:
    """把 messages 里的 tool_call dict 包成 _final_args 需要的最小对象。

    恢复路径上手上只有 dict（模型响应早已不存在），而 `_final_args` 按
    `.name` / `.arguments` 取值。这里不改动 loop.py 的签名。
    """
    from types import SimpleNamespace
    return SimpleNamespace(id=tc.get("id", ""),
                           name=tc.get("function", {}).get("name", ""),
                           arguments=tc.get("function", {}).get("arguments") or "{}")


def _dispatch_tool(state: AnalysisState, name: str, args: dict, args_json: str, tc: dict,
                   llm, role_sys: dict, ctx: Any, step: int, ma: dict, out_dir: Path,
                   answer_artifact: dict | None) -> tuple[str, Any | None]:
    """执行一个主 Agent 工具调用，返回 (给模型的文本, 已发布的 FinalAnswer|None)。

    从主循环里抽出来是为了让"恢复时补跑未提交调用"和"首次执行"走**完全同一段代码**——
    否则恢复路径很容易长出自己的简化分支，最后变成两套业务规则。
    """
    from agent.loop import _final_args
    result_text = ""
    published = None
    if name in ("plan_analysis", "execute_analysis", "review_analysis", "resume_analysis"):
        # 参数预检：非法调用根本不启动子 Agent。开发裁定取消了子调用额度，但被拒的调用
        # 仍要单独留痕（subagent_refusals），且绝不能计入真实启动数（M2 复审 §3：
        # run_meta 报 subagent_calls=14 而 trace 只有 12 次 subagent_start 的口径失真）。
        invalid = _validate_subtool_args(name, args)
        if invalid:
            result_text = invalid
            ma["subagent_refusals"] = ma.get("subagent_refusals", 0) + 1
        else:
            # 真实启动数只在 _run_role 内 +1（与 subagent_start 事件一一对应）。
            # 调用前后对比：若本次派发没进入 _run_role（依赖缺失/版本不符/节点已完成等
            # 正确性校验拒绝，或参数预检之后的早期返回），记一次 refusal，但不阻止后续推进。
            before = ma["used_subagent_calls"]
            if name == "plan_analysis":
                result_text = _tool_plan(state, args, llm, role_sys["planner"],
                                         ctx, step, ma)
            elif name == "execute_analysis":
                result_text = _tool_execute(state, args, llm, role_sys["worker"], ctx,
                                            step, ma)
            elif name == "resume_analysis":
                result_text = _tool_resume(state, args, llm, role_sys["worker"], ctx,
                                           step, ma)
            else:
                result_text = _tool_review(state, args, llm, role_sys["reviewer"],
                                           ctx, step, ma, out_dir)
            if ma["used_subagent_calls"] == before:
                ma["subagent_refusals"] = ma.get("subagent_refusals", 0) + 1
    elif name == "recall":
        result_text = _tool_recall(args, ctx, step, state)
    elif name == "read_analysis_object":
        result_text = _tool_read_object(state, args)
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
            result_text = (f"已保存候选答案（answer_artifact_id="
                           f"{cand.get('artifact_id')}）。请用 review_analysis"
                           "(target_type=answer, target_id=该 ID) 审查后用 final_answer 引用提交。"
                           + hint + _state_view_text(state, ma))
    elif name == "final_answer":
        fa, fa_err = _final_args(_as_call(tc))   # 返回 (FinalAnswerArgs|None, 错误文本|None)
        if fa is None:
            result_text = fa_err or "错误: final_answer 参数不合法"
        else:
            problems = _multi_final_problems(fa, state, answer_artifact, args)
            # 数字核验沿用 single 路径同一个 verifier（hooks.stop 默认 agent.verifier）：
            # 判据不改、不跳过勾稽项；ctx.verifier=None 时也必须真正执行核验（§8）
            verdict = hooks.stop(fa, ctx)
            # 开发裁定取消"最终答案修订次数"停止上限：核验/提交条件不满足只把问题回给
            # 主 Agent 让它继续修订，不再据此提前停止；final_rejects 仅作消耗记录。
            if not verdict.ok:
                ma["final_rejects"] += 1
                result_text = ("核验未通过：" + (verdict.feedback or "数字/引用核验失败")
                               + "（请修正数字/引用后重新 draft_answer 并提交）")
            elif problems:
                ma["final_rejects"] += 1
                result_text = ("多 Agent 提交条件未满足：" + "；".join(problems[:3])
                               + "（请按上述条件补齐后重新提交）")
            else:
                published = _final_answer_multi(fa, ctx, state, answer_artifact,
                                                numeric_ok=True, stop_reason="", ma=ma)
                result_text = "已提交（含已审候选与数字核验）。"
    else:
        result_text = f"错误: 未知工具 {name}"
    return result_text, published


def _run_role(role: str, sys_prompt: str, user: str, llm, ctx: Any, step: int,
              max_steps: int, ma: dict | None = None, *,
              resume_messages: list[dict] | None = None,
              extra_tools: dict[str, Any] | None = None, **ident) -> Any:
    """同步执行一个子 Agent 并写调度事件（§10：角色、节点/目标、子步数、token、耗时、调用 ID）。

    子事件已自带 role 与子循环 step，转发时主循环步数记为 main_step，避免关键字冲突。
    真实启动计数在这里 +1（M2 复审 §3）：_run_role 是唯一写 subagent_start 的入口，
    所以 subagent_calls 与 trace 中 subagent_start 数天然一一对应，被前置/正确性校验拒绝、
    根本没走到这里的调用不会计入启动数（改记 subagent_refusals）。

    `resume_messages` 非空时续接同一上下文（PRD §4.5）：工具历史仍在，因此不重复取数。
    """
    if ma is not None:
        ma["used_subagent_calls"] = ma.get("used_subagent_calls", 0) + 1
        ma["role_calls"][role] = ma["role_calls"].get(role, 0) + 1
    agent_call_id = f"{role}@{step}#{ident.get('node_id') or ident.get('target_id') or 'plan'}"
    ctx.trace.event(type="subagent_start", role=role, step=step,
                    agent_call_id=agent_call_id, **ident)
    t0 = time.perf_counter()
    sub = subagents.SubAgentRunner(llm, ctx, role, sys_prompt, max_steps,
                                   extra_tools=extra_tools)
    r = sub.run(user, resume_messages=resume_messages)
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
    r = _run_role("planner", sys_prompt, user, llm, ctx, step,
                  ma["planner_max_steps"], ma)
    if not r.ok:
        return "错误: " + (r.error or "Planner 未输出合法计划")
    from agent.subagents import PlanProposal
    try:
        prop = PlanProposal(**r.payload)
        prop.validate_semantics()   # 仅补丁局部校验；合并整图校验（依赖/环/自依赖）在 save_plan
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


def _judge_completion(r, node, plan) -> tuple[str, list[str], list[str]]:
    """判定一个 Worker 阶段交付的真实状态（PRD §3/§4）。

    返回 (execution_status, required_missing, next_actions)。

    程序**不信任** Worker 自称的完成：只按可确定的事实判断——
      1. 协议失败（没有合法 JSON）→ failed
      2. 必需输入缺失 → blocked（缺必需输入确实无法继续）
      3. completion_criteria 里有未满足项 → 有下一步动作记 paused，否则 blocked
      4. 阶段轮数用尽但交付合法 → paused（"仍有可执行动作但阶段耗尽"）
      5. 否则 → completed

    关键修正（PRD §2 第 1 行 / §4.2）：**阶段用完或交付发生在预留轮都不再自动判 blocked**。
    原实现 `partial = finalized or steps_exhausted` → blocked，正是 runs/ds_multi_S01 里
    n1/n6 被判 blocked、连锁 6 次计划修订、7 次 dependency_missing 的根因。
    `finalized` 只说明交付发生在预留的那一轮，是中性事实，不是不合格信号。
    """
    if not r.ok:
        return "failed", [], []
    p = r.payload or {}
    has_required_field = "required_missing_inputs" in p
    required = list(r.required_missing_inputs or [])
    if not has_required_field:
        # 旧协议只有 missing_inputs：保守当作必需缺口（保持既有 blocked 行为不变）
        required = required or list(p.get("missing_inputs") or [])
    if required:
        return "blocked", required, list(r.next_actions or [])

    # 逐条核对 completion_criteria：Worker 自报的 completion_report 必须覆盖每条标准且都满足
    unsat: list[str] = []
    crit = (node.completion_criteria or "").strip()
    if crit:
        report = {(str(c.get("criterion", "")).strip()): bool(c.get("satisfied"))
                  for c in (r.completion_report or [])}
        if report:
            for line in crit.splitlines():
                line = line.strip().lstrip("-*0123456789.、 ").strip()
                if not line:
                    continue
                if not any(line[:12] in k or k[:12] in line for k in report):
                    unsat.append(f"完成标准未在 completion_report 中报告：{line[:40]}")
                elif not any(line[:12] in k and v for k, v in report.items() if line[:12] in k):
                    unsat.append(f"完成标准被标记为未满足：{line[:40]}")
    if unsat:
        return ("paused" if r.next_actions else "blocked"), unsat, list(r.next_actions or [])

    # 阶段耗尽但没有自述下一步：说明确实还有活没干完 → paused（可续接），不是 blocked
    if r.steps_exhausted and not r.finalized:
        return "paused", [], list(r.next_actions or ["阶段轮数用完；请用 resume_analysis 续接或修订计划"])
    return "completed", [], list(r.next_actions or [])


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
    # 开发裁定取消"单节点尝试次数"停止上限：attempt 仍作为历史递增记录（artifact 溯源用），
    # 但不再据此拒绝执行。同一节点重复 execute_analysis 允许（补算/重做），只保留下面依赖校验。
    deps_bad = [d for d in node.depends_on
                if not any(n.node_id == d and n.execution_status in RELEASING_STATES
                           for n in plan.nodes)]
    if deps_bad:
        # PRD §5：只有 completed 释放下游。paused/blocked/failed 不自动释放——
        # 请显式 resume 续接、或修订计划调整依赖，而不是把边删掉绕过。
        detail = {d: next((n.execution_status for n in plan.nodes if n.node_id == d), "?")
                  for d in deps_bad}
        return (f"dependency_missing: 节点 {node_id} 缺依赖 {detail}（只有 completed 才释放下游）。"
                "请先 resume_analysis 续接 paused 节点、补齐 blocked 节点，或 plan_analysis 修订依赖。")
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
                  ma["worker_max_steps"], ma, node_id=node_id,
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
    status, required_miss, next_actions = _judge_completion(r, node, plan)
    optional_miss = list(r.optional_missing_inputs or [])
    # 旧协议兜底：required 判定已把 missing_inputs 并入，这里只保留真正可选的
    miss = list(required_miss) + optional_miss
    lims = list(r.payload.get("limitations") or []) + prov_notes
    if optional_miss:
        lims.append("以下为可选补充数据缺失，不影响本节点完成：" + "；".join(optional_miss[:5]))
    if status == "paused":
        lims.append("阶段轮数用尽但节点尚未完成，已保存上下文可显式续接"
                    "（resume_analysis）；这不等于节点阻塞，也不代表已完成。")
    cont_id = None
    if status == "paused":
        cont_id = state.save_continuation(
            node_id=node_id, attempt=node.attempt + 1, plan_revision=plan.revision,
            messages=r.messages, payload=r.payload,
            progress=(r.payload or {}).get("summary", ""),
            next_actions=next_actions, node_def_hash=content_hash(
                f"{node.goal}|{node.completion_criteria}|{node.input_refs}"))
    art = state.add_artifact(AA(
        artifact_id="", node_id=node_id, plan_revision=plan.revision,
        attempt=node.attempt + 1, execution_status=status,
        facts=facts, calculations=r.payload.get("calculations") or [],
        interpretations=r.payload.get("interpretations") or [],
        hypotheses=r.payload.get("hypotheses") or [],
        limitations=lims, missing_inputs=miss,
        declared_status=r.declared_status,
        completion_report=r.completion_report,
        required_missing_inputs=required_miss, optional_missing_inputs=optional_miss,
        next_actions=next_actions, continuation_id=cont_id,
        evidence_refs=ev_refs,
        summary=r.payload.get("summary", "")))
    # §6.2/§7.2：本节点重做（新 attempt）后，下游节点的旧 artifact 必须失效
    stale_down = state.mark_stale_downstream(plan, node_id) if node.attempt >= 1 else []
    hint = {
        "completed": f"建议用 review_analysis(target_type=artifact, target_id={art.artifact_id}) 审查。",
        "paused": (f"可用 resume_analysis(continuation_id={cont_id}) 续接同一上下文，"
                   "不会重复已完成的取数；或修订计划调整该节点目标。"),
        "blocked": (f"缺本节点必需输入，当前无法继续；已有证据仍可被引用"
                    f"（recall(result_id={art.artifact_id})）。"),
        "failed": "本阶段执行失败，请修订计划或换个节点目标再试。",
    }.get(status, "")
    return (f"节点 {node_id} 执行{ {'completed': '完成', 'paused': '暂停（可续接）', 'blocked': '阻塞', 'failed': '失败'}[status] }，"
            f"artifact={art.artifact_id}"
            f"（attempt {art.attempt}，状态 {art.execution_status}）。"
            f"必需缺口：{'；'.join(art.required_missing_inputs[:3]) or '无'}。"
            + (f"可选缺口：{'；'.join(art.optional_missing_inputs[:3])}。" if art.optional_missing_inputs else "")
            + (f"下一步：{'；'.join(art.next_actions[:3])}。" if art.next_actions else "")
            + (f"下游已标 stale：{stale_down}。" if stale_down else "")
            + (f"程序出处提示：{'；'.join(prov_notes[:3])}。" if prov_notes else "")
            + f"摘要：{art.summary[:200] or '（见 artifact）'}。"
            + hint
            + _state_view_text(state, ma))   # §7.3：子工具结果附精简状态


def _artifact_version(a: AnalysisArtifact) -> str:
    return f"rev{a.plan_revision}-attempt{a.attempt}"


def _tool_read_object(state: AnalysisState, args: dict) -> str:
    """read_analysis_object：父子共用的只读产物/候选/审核读取（PRD §6/§8）。

    存在的理由：审查对象的摘要会被截断，而旧实现没有第二条读取通道，Reviewer 只能对
    半截内容下结论。这里返回完整字段或分页，并明确告知是否还有剩余。
    """
    oid = str(args.get("object_id") or "").strip()
    section = str(args.get("section") or "").strip()
    offset = max(0, int(args.get("offset") or 0))
    limit = max(1, min(int(args.get("limit") or 20), 200))
    if not oid:
        return "错误: object_id 不能为空"
    obj: object | None = None
    if oid in state.artifacts:
        obj = state.artifacts[oid]
    elif oid == (state.answer_candidate or {}).get("artifact_id"):
        obj = state.answer_candidate
    elif oid in state.reviews:
        obj = state.reviews[oid]
    if obj is None:
        return (f"错误: 对象 {oid} 不存在。可读对象："
                f"artifacts={sorted(state.artifacts)}，"
                f"answer_candidate={(state.answer_candidate or {}).get('artifact_id')}，"
                f"reviews={sorted(state.reviews)}")
    data = obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)
    if not section:
        toc = {k: (len(v) if isinstance(v, (list, dict)) else 1)
               for k, v in data.items() if k not in ("model_config",)}
        return (f"对象 {oid} 目录（字段 → 条目数；用 section 指定要读的部分）：\n"
                + json.dumps(toc, ensure_ascii=False)
                + "\n提示：section 可用 " + "、".join(sorted(data.keys())) + "；"
                  "对列表可用 offset/limit 分页。")
    if section not in data:
        return f"错误: 对象 {oid} 没有字段 {section}（可用：{sorted(data.keys())}）"
    val = data[section]
    if isinstance(val, list):
        page = val[offset:offset + limit]
        more = len(val) > offset + limit
        return (f"对象 {oid} 的 {section}（第 {offset + 1}-{offset + len(page)} 条，"
                f"共 {len(val)} 条{'，还有更多' if more else ''}）：\n"
                + json.dumps(page, ensure_ascii=False, default=str)
                + (f"\n（还有 {len(val) - offset - limit} 条，用 offset={offset + limit} 继续读）"
                   if more else ""))
    text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False, default=str)
    return f"对象 {oid} 的 {section}（共 {len(text)} 字符）：\n{text}"


def _tool_resume(state: AnalysisState, args: dict, llm, sys_prompt: str,
                 ctx: Any, step: int, ma: dict) -> str:
    """resume_analysis：续接 paused 节点的同一上下文（PRD §4.5）。

    不重新发送初始任务——直接沿用保存的 messages，因此已取得的工具结果仍在上下文里，
    不会重复取数。这正是 PRD §2 第 3 行"重开 Worker 容易重复工作"的修法。
    """
    cid = str(args.get("continuation_id") or "").strip()
    cont = state.get_continuation(cid)
    if cont is None:
        return f"错误: continuation {cid} 不存在"
    if cont.get("_invalid"):
        return (f"错误: continuation {cid} 已失效（{cont.get('invalid_reason')}）。"
                "请用 execute_analysis 开新 attempt，旧上下文不直接沿用。")
    if cont.get("used"):
        return (f"错误: continuation {cid} 已被 resume_analysis 使用过，不能重复续接。"
                "请用 execute_analysis 开新 attempt。")
    plan = state.latest_plan()
    node = next((n for n in plan.nodes if n.node_id == cont["node_id"]), None) if plan else None
    if node is None:
        return f"错误: continuation {cid} 指向的节点 {cont['node_id']} 不在当前计划"
    node.execution_status = "running"
    state.persist()
    user = (f"续接上一次未完成的阶段。当前进展：{cont.get('progress') or '(未记录)'}；"
            f"上次的下一步：{'；'.join(cont.get('next_actions') or []) or '(未记录)'}。"
            f"补充指令：{args.get('extra_instructions', '') or '无'}。"
            "请在已有工具结果之上继续，不要重新取已经拿到的同一份数据。")
    r = _run_role("worker", sys_prompt, user, llm, ctx, step,
                  ma["worker_max_steps"], ma, node_id=node.node_id,
                  plan_revision=plan.revision, attempt=node.attempt + 1,
                  resume_messages=cont.get("messages") or None,
                  resumed_from=cid)
    cont["used"] = True
    state.persist()
    if not r.ok:
        from agent.analysis_state import AnalysisArtifact as AA
        state.add_artifact(AA(artifact_id="", node_id=node.node_id, plan_revision=plan.revision,
                              attempt=node.attempt + 1, execution_status="failed",
                              missing_inputs=[r.error] if r.error else [],
                              summary=f"续接失败：{r.error or '无合法输出'}"))
        return f"续接失败（{r.error or '无合法输出'}），已记录。" + _state_view_text(state, ma)
    facts, prov_notes, ev_refs = _fact_provenance(r.payload.get("facts") or [],
                                                  r.payload.get("calculations") or [], ctx)
    status, required_miss, next_actions = _judge_completion(r, node, plan)
    optional_miss = list(r.optional_missing_inputs or [])
    lims = list(r.payload.get("limitations") or []) + prov_notes
    if optional_miss:
        lims.append("以下为可选补充数据缺失，不影响本节点完成：" + "；".join(optional_miss[:5]))
    cont_id = None
    if status == "paused":
        cont_id = state.save_continuation(
            node_id=node.node_id, attempt=node.attempt + 1, plan_revision=plan.revision,
            messages=r.messages, payload=r.payload,
            progress=(r.payload or {}).get("summary", ""),
            next_actions=next_actions, node_def_hash=content_hash(
                f"{node.goal}|{node.completion_criteria}|{node.input_refs}"))
    from agent.analysis_state import AnalysisArtifact as AA
    art = state.add_artifact(AA(
        artifact_id="", node_id=node.node_id, plan_revision=plan.revision,
        attempt=node.attempt + 1, execution_status=status, facts=facts,
        calculations=r.payload.get("calculations") or [],
        interpretations=r.payload.get("interpretations") or [],
        hypotheses=r.payload.get("hypotheses") or [],
        limitations=lims, missing_inputs=list(required_miss) + optional_miss,
        declared_status=r.declared_status, completion_report=r.completion_report,
        required_missing_inputs=required_miss, optional_missing_inputs=optional_miss,
        next_actions=next_actions, continuation_id=cont_id,
        evidence_refs=ev_refs, summary=r.payload.get("summary", "")))
    return (f"节点 {node.node_id} 续接后状态 {art.execution_status}，artifact={art.artifact_id}"
            + (f"；仍可续接：{cont_id}" if cont_id else "")
            + f"摘要：{art.summary[:200] or '（见 artifact）'}。"
            + _state_view_text(state, ma))


def _object_digest(content: str, limit: int = 1200) -> str:
    """审查对象的结构摘要：不再按字符粗暴截断 JSON（PRD §8）。

    旧实现 `target_content[:3000]` 会把 artifact 后半段的 calculations/limitations 直接切掉，
    造成"长 artifact 后半段含错误但 Reviewer 看不到"。这里保留头部身份信息 + 完整长度提示，
    并明确告知可用 read_analysis_object 分页读全文。
    """
    if len(content) <= limit:
        return content
    head = content[:limit]
    return (f"{head}\n"
            f"……（共 {len(content)} 字符，以上为前 {limit} 字符。"
            f"完整内容必须用 read_analysis_object 读取，不要据此断言对象没有该项。）")


def _review_scope_note(state: AnalysisState, ttype: str, tid: str) -> str:
    """按对象类型给出审查范围（PRD §8）：期初取数节点不能被要求独立回答整题。

    runs/ds_multi_S01 的 rev2 里，Reviewer 对一个"取期初存货"的节点也拿整题归因标准去审，
    于是因为"没有结论"判 needs_revision——那是审查范围错配，不是节点没做好。
    """
    if ttype == "artifact" and tid in state.artifacts:
        a = state.artifacts[tid]
        node = next((n for p in state.plans.values() for n in p.nodes
                     if n.node_id == a.node_id and n.latest_artifact_id == tid), None)
        goal = node.goal if node else "(未知)"
        crit = node.completion_criteria if node else ""
        return (f"**本节点的审查范围**：目标={goal}；完成标准={crit or '(未声明)'}；"
                f"必需输出={node.required_output_refs if node else []}。"
                "只按该节点的目标与完成标准审查——一个取数节点不需要独立完成整题归因，"
                "不要因为它没有给出总体结论而判 needs_revision。\n")
    if ttype == "answer":
        return ("**审查范围**：最终候选答案。检查主问题是否被回答、结论与证据是否相容、"
                "边界是否诚实、引用是否有效；不要重新执行取数。\n")
    if ttype == "plan":
        return ("**审查范围**：任务图。检查是否覆盖用户主问题、依赖是否正确、"
                "是否有把关键工作拆得过细或缺失的节点。\n")
    return ""


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
            + _review_scope_note(state, ttype, tid)
            + f"审查对象（{ttype}，{tid}，版本 {tver}）完整内容摘要：\n{_object_digest(target_content)}\n"
            + "**上面的摘要可能省略了部分条目。若要判断必须读完整对象："
              "调用 read_analysis_object(object_id='" + tid + "', section=..., offset=..., limit=...) "
              "分页读取；未读到就下结论等于 input_unavailable，请给 inconclusive 并说明缺哪部分。\n"
            f"重点：{args.get('focus', '') or '任务覆盖/口径/证据一致性/因果边界'}\n"
            "输出 JSON：{\"verdict\": \"accepted|needs_revision|inconclusive\", "
            "\"findings\": [{category, severity, statement, explanation, suggested_action, "
            "evidence_refs, node_id}], \"unresolved_items\": []}。"
            "分类只能用 task_scope/caliber/evidence_quality/contradiction/unsupported_cause/"
            "missing_alternative/other；severity 只能是 must_fix/limitation/suggestion——"
            "must_fix 表示不改就不能算完成，limitation 可以通过明确保留限制收尾，"
            "suggestion 只是建议。")
    r = _run_role("reviewer", sys_prompt, user, llm, ctx, step,
                  ma["reviewer_max_steps"], ma,
                  # PRD §8：Reviewer 必须能读完整对象/候选/审核与底层 rid，不能只靠截断摘要
                  extra_tools={"read_analysis_object": lambda a: _tool_read_object(state, a)},
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
    state_rev_no = state.counters.get("review", 0) + 1
    for i, f in enumerate(payload.get("findings") or []):
        try:
            if isinstance(f, dict) and not f.get("finding_id"):
                f = {**f, "finding_id": f"rev{state_rev_no}-f{i + 1}"}
            findings.append(RF(**f))
        except (ValidationError, TypeError) as e:
            strict.append(f"finding 不合协议（{str(e)[:100]}）")
    rv = state.add_review(R(review_id="", target_type=ttype, target_id=tid,
                            target_version=tver, verdict=verdict,
                            findings=findings, unresolved_items=strict))
    # PRD §8：must_fix 未解决不得 accepted。Reviewer 自评 accepted 但留下 must_fix 时，
    # 由程序纠正为 needs_revision——否则"accepted 中仍有未落实的修订建议"这一失败模式无法关闭。
    must_fix = rv.unresolved_must_fix()
    if must_fix and rv.verdict == "accepted":
        rv.verdict = "needs_revision"
        rv.unresolved_items.append(
            f"程序纠正：Reviewer 给出 accepted，但有 {len(must_fix)} 条未解决的 must_fix，"
            "按 §8 不可 accepted")
        state.persist()
        corrected = True
    else:
        corrected = False
    if ttype == "artifact" and tid in state.artifacts:
        # 执行状态/审核状态分离（§6）：review verdict 落到节点审核状态
        a = state.artifacts[tid]
        state.set_node_review_status(a.plan_revision, a.node_id, rv.verdict)
    return (f"审查 {rv.review_id} 完成：verdict={rv.verdict}，"
            f"findings={len(rv.findings)} 条"
            + (f"（must_fix {len(must_fix)} 条）" if must_fix else "")
            + ("；已按 §8 纠正为 needs_revision（存在未解决的 must_fix）。" if corrected else "")
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
    cons = st.get("consumption") or {}
    L += ["", "## 资源与调度", "",
          f"- 开发裁定：仅以最大轮数控制运行；已取消资源停止上限="
          f"{st.get('canceled_limits') or CANCELED_LIMITS}",
          f"- 保留的接口运行机制（非整次分析墙钟预算）：{st.get('kept_runtime_guards') or KEPT_RUNTIME_GUARDS}",
          f"- 生效轮数：{json.dumps(st.get('effective_rounds') or MULTI_ROUNDS, ensure_ascii=False)}",
          f"- 子 Agent 真实启动：{st.get('subagent_calls', 0)}（与 trace subagent_start 一一对应）"
          f"／角色分布：{json.dumps(st.get('subagent_role_calls') or {}, ensure_ascii=False)}"
          f"／前置或正确性校验拒绝（未启动）：{st.get('subagent_refusals', 0)}",
          f"- 计划修订：{st.get('plan_revisions', 0)}　review 数：{st.get('reviews_total', 0)}　"
          f"findings：{st.get('findings_total', 0)}　未处理 findings：{len(st.get('unresolved_findings') or [])}"
          f"　final_answer 打回：{st.get('final_answer_rejects', 0)} 次（仅记录，不据此提前停止）",
          f"- 实际消耗（记录不拦截）：prompt={cons.get('prompt_tokens', st.get('prompt_tokens'))}，"
          f"completion={cons.get('completion_tokens', st.get('completion_tokens'))}，"
          f"主循环轮数={cons.get('main_rounds', st.get('steps'))}，"
          f"子循环合计轮数={cons.get('subagent_child_rounds', st.get('subagent_child_steps', 0))}，"
          f"latency_s={st.get('latency_s')}",
          f"- 未完成节点：{st.get('nodes_incomplete') or '（无）'}", "",
          "## 边界声明", "",
          "- 本文只汇总程序校验过的结构化产物与调度动作，不含模型私有思维链。",
          "- 语义审查 verdict 是模型意见；数字核验通过只代表现有数字/引用规则通过，"
          "不代表经营因果结论被证明。",
          "- facts_detail 合成数据标记为 synthetic，不得作为真实经营事实证据。"]
    return "\n".join(L) + "\n"

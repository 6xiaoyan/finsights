"""P4.2：主循环（plan 6.2）——单一循环 + 工具，全 mock LLM 可测。

要点（plan 6.2）：
- llm.chat 的错误重试在 llm.py 内部；工具执行出错不抛异常，错误文本作为工具结果返回。
- 每一步写 trace：输入/输出 token、工具调用参数、结果的 result_id、耗时。
- 预算（步数/token/时间）耗尽返回 status=refuse，不编造答案。
- final_answer 走 hooks.stop 核验，最多打回 ctx.stop_max_retries 次，
  仍不通过则返回并在答案中标注"未通过验证"。
- 一条 assistant 消息里的多个只读数据工具并发执行（V4.12），tool_call_id 一一对应。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

from agent import context as context_manager
from agent import hooks, skills
from agent.context import CompState, ContextTooLong, SUBMIT_BAD, SUBMIT_OK
from agent.ledger import Ledger
from agent.result_store import ResultStore
from agent.tools import data as tool_data
from agent.tools.base import (ARGS_MODELS, READ_ONLY_TOOLS, FinalAnswerArgs, LoadSkillArgs,
                              TodoItem, TodoWriteArgs, all_tool_schemas)
from agent.tools.data import ToolContext
from eval.schema import Answer

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
SYSTEM_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"
NUDGE_TEXT = "请调用 final_answer 工具提交答案。如果你还需要数据，请先调用相应工具。"
REFUSE_TEXT = ("运行预算已经用尽，无法完成有依据的回答，现拒答。\n"
               "拒答是为了避免给出没有出处的数字；请缩小问题范围或允许更多步数。")


@dataclass
class Budget:
    max_steps: int
    max_tokens: int
    max_wall_s: float


@dataclass
class TraceWriter:
    """JSONL trace：内存里留全量（测试断言用），给了路径就同步落盘。"""
    path: str | Path | None = None

    def __post_init__(self):
        self.events: list[dict] = []
        self._f = open(self.path, "a", encoding="utf-8") if self.path else None

    def event(self, **kw):
        self.events.append(kw)
        if self._f:
            self._f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")
            self._f.flush()

    def close(self):
        if self._f:
            self._f.close()
            self._f = None


@dataclass
class RunContext:
    run_id: str
    db_path: str
    as_of: date | None
    store: ResultStore
    todos: list[TodoItem]
    loaded_skills: dict[str, str]
    budget: Budget
    trace: TraceWriter
    tool_ctx: ToolContext
    llm: Any = None                                  # LLMClient 或测试注入的 mock
    verifier: Callable | None = None                 # P4.4 的 verifier；默认放行
    stop_max_retries: int = 2                        # config: agent.final_answer_max_retries
    tools_run: list[str] = field(default_factory=list)   # 已成功执行的数据类工具
    cfg: dict = field(default_factory=dict)          # config.yaml 的 context 段（压缩阈值，V4.43）
    ledger: Ledger = field(default_factory=Ledger)   # 证据账本（P4.5a，代码生成）
    comp: CompState = field(default_factory=CompState)  # 压缩冻结状态（P4.5b）
    messages: list[dict] = field(default_factory=list)  # 原始历史引用（reactive 兜底重建视图用）
    stats: dict = field(default_factory=lambda: {
        "steps": 0, "tool_calls": 0, "blocked": 0, "prompt_tokens": 0, "completion_tokens": 0})


@dataclass
class FinalAnswer:
    answer: Answer
    verified: bool
    stats: dict


def load_budget(config_path: Path | str = CONFIG_PATH) -> tuple[Budget, dict, int]:
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    a = cfg["agent"]
    return (Budget(int(a["max_steps"]), int(a["max_tokens_budget"]), float(a["max_wall_s"])),
            dict(cfg["context"]), int(a["final_answer_max_retries"]))


def make_run_ctx(db_path: str, llm: Any, *, run_id: str = "test-run",
                 as_of: date | None = None, config_path: Path | str = CONFIG_PATH,
                 trace_path: str | Path | None = None, verifier: Callable | None = None,
                 max_steps: int | None = None) -> RunContext:
    budget, cfg, stop_retries = load_budget(config_path)
    if max_steps is not None:
        budget.max_steps = max_steps
    store = ResultStore(tool_data.make_data_version(db_path))
    ledger = Ledger(as_of=as_of.isoformat() if as_of else None,
                    data_version=store.data_version)
    return RunContext(run_id=run_id, db_path=db_path, as_of=as_of, store=store,
                      todos=[], loaded_skills={}, budget=budget,
                      trace=TraceWriter(trace_path),
                      tool_ctx=ToolContext(db_path=db_path, store=store, cfg=cfg),
                      llm=llm, verifier=verifier, stop_max_retries=stop_retries,
                      cfg=cfg, ledger=ledger)


def build_system_prompt() -> str:
    """稳定 system prompt：skills 清单（只有 name+description）由代码注入，无动态内容（V4.13/V4.16）。"""
    return SYSTEM_PATH.read_text(encoding="utf-8").replace("{{skills}}", skills.skills_prompt_block())


def system_message() -> dict:
    return {"role": "system", "content": build_system_prompt()}


def user_message(question: str, ctx: RunContext) -> dict:
    content = question
    if ctx.as_of is not None:
        content += f"\n（as_of 数据截止提醒：动态信息以 {ctx.as_of.isoformat()} 为准）"
    return {"role": "user", "content": content}


def _tool_msg(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _final_args(call) -> tuple[FinalAnswerArgs | None, str | None]:
    """解析并校验 final_answer 参数；失败返回错误文本（不算核验失败）。"""
    try:
        args = json.loads(call.arguments)
        return FinalAnswerArgs(**args), None
    except Exception as e:
        return None, f"工具错误: final_answer 参数不合法: {type(e).__name__}: {e}"


def _exec_call(call, ctx: RunContext, step: int) -> dict:
    """执行一个非 final_answer 工具调用，返回 tool 消息（错误以文本返回，不抛异常）。"""
    t0 = time.perf_counter()
    rid = None
    args: dict = {}
    stored = None
    try:
        args = json.loads(call.arguments)
    except Exception as e:
        text = f"工具错误: 参数 JSON 解析失败: {type(e).__name__}: {e}"
    else:
        if call.name == "todo_write":
            try:
                todo_args = TodoWriteArgs(**args)
            except Exception as e:
                text = f"工具错误: 参数不合法: {e}"
            else:
                ctx.todos = todo_args.items
                done = sum(1 for t in todo_args.items if t.status == "done")
                text = f"计划已更新：共 {len(ctx.todos)} 项，已完成 {done} 项。"
        elif call.name == "load_skill":
            try:
                skill_args = LoadSkillArgs(**args)
                body = skills.load_skill_body(skill_args.name)
            except Exception as e:
                text = f"工具错误: {type(e).__name__}: {e}"
            else:
                ctx.loaded_skills[skill_args.name] = body   # 已加载 skills（压缩时永不存根化）
                text = f"<skill name={skill_args.name}>\n{body}\n</skill>"
        else:
            out = tool_data.execute(call.name, args, ctx.tool_ctx, step)
            text = out.text
            rid = out.rid
            stored = out.stored
            if out.ok and call.name in ARGS_MODELS:
                ctx.tools_run.append(call.name)
    ctx.stats["tool_calls"] += 1
    if text.startswith("工具错误"):
        ctx.ledger.note_failure(call.name, text)                # 账本：失败记录
    elif stored is not None:
        ctx.ledger.note_scope(call.name, args, unit=stored.unit)  # 账本：口径累积
    ctx.trace.event(type="tool_result", step=step, tool=call.name, call_id=call.id,
                    result_id=rid, latency_ms=int((time.perf_counter() - t0) * 1000),
                    ok=not text.startswith("工具错误"), result_head=text[:400])
    return _tool_msg(call.id, text)


def _execute_batch(calls: list, ctx: RunContext, step: int) -> list[dict]:
    """多个只读数据工具并发执行（V4.12）；tool_call_id 与结果一一对应。"""
    if len(calls) > 1 and all(c.name in READ_ONLY_TOOLS for c in calls):
        with ThreadPoolExecutor(max_workers=min(len(calls), 8)) as pool:
            return list(pool.map(lambda c: _exec_call(c, ctx, step), calls))
    return [_exec_call(c, ctx, step) for c in calls]


def _refuse(ctx: RunContext, reason: str) -> FinalAnswer:
    answer = Answer(answer_md=REFUSE_TEXT, claims=[], status="refuse")
    ctx.stats["refuse_reason"] = reason
    ctx.trace.event(type="refuse", reason=reason)
    return FinalAnswer(answer=answer, verified=False, stats=_finish_stats(ctx))


def _finish_stats(ctx: RunContext) -> dict:
    if "t_start" in ctx.stats:
        ctx.stats["latency_s"] = round(time.monotonic() - ctx.stats["t_start"], 2)
    return dict(ctx.stats,
                total_tokens=ctx.stats["prompt_tokens"] + ctx.stats["completion_tokens"])


def run(question: str, ctx: RunContext) -> FinalAnswer:
    TOOL_SCHEMAS = all_tool_schemas()
    messages = [system_message(), user_message(question, ctx)]
    ctx.messages = messages                     # reactive 兜底重建视图用（context.on_context_overflow）
    stop_retries = 0
    nudged = False
    t_start = ctx.stats["t_start"] = time.monotonic()
    try:
        for step in range(ctx.budget.max_steps):
            ctx.stats["steps"] = step + 1
            used = ctx.stats["prompt_tokens"] + ctx.stats["completion_tokens"]
            if used >= ctx.budget.max_tokens:
                return _refuse(ctx, "token 预算耗尽")
            if time.monotonic() - t_start > ctx.budget.max_wall_s:
                return _refuse(ctx, "时间预算耗尽")
            view = context_manager.build_view(messages, ctx)   # 6.6：只改发送视图
            t0 = time.perf_counter()
            try:
                resp = ctx.llm.chat(view, tools=TOOL_SCHEMAS)
            except ContextTooLong:
                resp = ctx.llm.chat(context_manager.on_context_overflow(view, ctx),
                                    tools=TOOL_SCHEMAS)
            ctx.stats["prompt_tokens"] += resp.usage.get("prompt_tokens", 0)
            ctx.stats["completion_tokens"] += resp.usage.get("completion_tokens", 0)
            ctx.trace.event(type="assistant", step=step, content=resp.content,
                            tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments}
                                        for c in resp.tool_calls],
                            usage=resp.usage,
                            latency_ms=int((time.perf_counter() - t0) * 1000))
            messages.append(resp.message)

            if not resp.tool_calls:
                if not nudged:                 # 提醒 1 次；再犯按预算处理（refuse）
                    nudged = True
                    messages.append({"role": "user", "content": NUDGE_TEXT})
                    ctx.trace.event(type="nudge", step=step)
                    continue
                return _refuse(ctx, "模型连续不调用任何工具就结束")

            pending: list = []
            finals: list[tuple] = []           # (call, FinalAnswerArgs|None, err|None)
            for call in resp.tool_calls:
                hook = hooks.pre_tool(call, ctx)   # 可拒绝（V4.11 f）
                if hook.blocked:
                    ctx.stats["blocked"] += 1
                    ctx.trace.event(type="tool_blocked", step=step, tool=call.name,
                                    call_id=call.id, reason=hook.reason)
                    messages.append(_tool_msg(call.id, hook.reason))
                    continue
                if call.name == "final_answer":
                    fa, err = _final_args(call)
                    finals.append((call, fa, err))
                    continue
                pending.append(call)
            # 先执行数据/状态工具，保证 assistant 的每个 tool_call 都有结果
            messages.extend(_execute_batch(pending, ctx, step))

            for call, fa, err in finals:
                ctx.stats["tool_calls"] += 1
                if err is not None:
                    messages.append(_tool_msg(call.id, err))
                    continue
                verdict = hooks.stop(fa, ctx)          # 6.8 护栏
                if verdict.ok or stop_retries >= ctx.stop_max_retries:
                    # 写入提交标记，供 L2 轮次折叠识别"已完成且通过核验"的问答轮
                    messages.append(_tool_msg(call.id, SUBMIT_OK if verdict.ok else SUBMIT_BAD))
                    return _finalize(fa, verdict, ctx)
                stop_retries += 1
                ctx.trace.event(type="stop_reject", step=step, call_id=call.id,
                                attempt=stop_retries, feedback=verdict.feedback)
                messages.append(_tool_msg(call.id, verdict.feedback))
        return _refuse(ctx, "步数预算耗尽")
    finally:
        _finish_stats(ctx)
        ctx.trace.close()


def _finalize(fa: FinalAnswerArgs, verdict: hooks.Verdict, ctx: RunContext) -> FinalAnswer:
    md = fa.answer_md
    if not verdict.ok:                          # V4.11 e：多次打回后仍不通过 → 标注
        md += f"\n\n⚠ 未通过验证（护栏反馈：{verdict.feedback}）"
    ctx.trace.event(type="final", status=fa.status, verified=verdict.ok,
                    claims=[c.model_dump() for c in fa.claims])
    return FinalAnswer(answer=Answer(answer_md=md, claims=fa.claims, status=fa.status),
                       verified=verdict.ok, stats=_finish_stats(ctx))

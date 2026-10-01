"""P4.5b/c：上下文压缩流水线（plan 6.6.3）——L2 轮次折叠 / L3 结果存根化 / L4 账本压缩。

L1（结果预算）在 tools/data.render 里每次工具返回时已强制执行，本文件不需要重复。

五条不变量（V4.19–V4.23）：
1. 只按 API 轮次分组整体操作：assistant 消息与其后的 tool/user 结果不拆开。
2. 原始历史永不改动：本文件只从历史生成投影视图（视图内消息是新 dict）。
3. view[0]（system prompt）逐字节不变。
4. 压缩后的每个 rid 仍可召回：Result Store 永不清理，存根保留 rid + digest + recall 指引。
5. 每次压缩动作写 trace 事件 {type:"compact", level, tokens_before, tokens_after, rids}。

所有阈值一律从 ctx.cfg 读取，本文件不写死数值（V4.43）。
修改历史要攒批一次性做（无 cache_edits，见 docs/questions.md）：L3/L4 的触发结果
冻结进 CompState，之后各步只复用冻结状态，视图前缀对缓存友好（V4.24）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent import ledger as ledger_mod
from agent.ledger import _args_line

# final_answer 被接受/带标注返回时的 tool 结果标记（loop 写入；L2 只折叠通过核验的轮）
SUBMIT_OK = "答案已提交（通过护栏核验）"
SUBMIT_BAD = "答案已提交（⚠ 未通过验证）"

# L3 存根化白名单 = 账本里的取数 + 分析工具；todo_write/load_skill/护栏反馈/用户消息永不存根化
STUB_TOOLS = ledger_mod.DATA_TOOLS | ledger_mod.HYPOTHESIS_TOOLS

_RESULT_HEAD_RE = re.compile(r"^\[(r\d+)\] (\w+)\(")
_ERROR_HEAD = "工具错误:"
_RID_REF_RE = re.compile(r"\br\d+\b")


class ContextTooLong(Exception):
    """API 上下文超长；reactive 兜底也救不回来时抛出（plan 6.6.3 兜底行）。"""


@dataclass
class CompState:
    """跨步冻结的压缩状态：触发才变，变了也不回退（V4.24 前缀稳定）。"""
    stubbed: frozenset[str] = frozenset()
    errors: frozenset[str] = frozenset()   # 已压成一行的报错 tool 消息的 call_id
    fold_count: int = 0            # 已折叠轮数（用于只在变化时写 L2 事件）
    ledger_cut: int = 0            # L4：视图开头的 API 轮次组被账本替换的组数
    ledger_text: str = ""          # 触发时刻冻结的账本渲染（避免每步变动前缀）
    overflow_retries: int = 0      # reactive 兜底已用次数
    force: bool = False            # 超长兜底：本步起无视阈值全量存根 + 账本投影到最近 1 组


def est_tokens(msgs: list[dict[str, Any]]) -> int:
    """估算 token 数（chars/4 粗估；无 tokenizer 可用，plan 6.6 工程细节）。"""
    n = 0
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", tc)
            n += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return n // 4


def _log_compact(ctx: Any, level: str, before: int, after: int, rids: list[str]) -> None:
    ctx.trace.event(type="compact", level=level, tokens_before=before,
                    tokens_after=after, rids=rids)


# ---------------- L2：轮次折叠（只折叠已完成且通过护栏的问答轮）

def _find_submits(messages: list[dict]) -> list[int]:
    return [i for i, m in enumerate(messages)
            if m.get("role") == "tool" and (m.get("content") or "").startswith(SUBMIT_OK)]


def _folded_answer(messages: list[dict], lo: int, submit_idx: int) -> dict:
    """从 final_answer 的 assistant 消息里取 answer_md + claims，生成折叠后的 assistant 消息。"""
    call_id = messages[submit_idx].get("tool_call_id")
    for m in messages[lo:submit_idx]:
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            if tc.get("id") != call_id or fn.get("name") != "final_answer":
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                break
            claims = [str(c.get("ref", "")) for c in args.get("claims", []) if c.get("ref")]
            content = "[已完成并通过护栏核验的问答轮] " + str(args.get("answer_md", ""))
            if claims:
                content += "\nclaims 引用: " + ", ".join(claims)
            return {"role": "assistant", "content": content}
    return {"role": "assistant", "content": "[已完成并通过护栏核验的问答轮] " + SUBMIT_OK}


def _apply_l2(messages: list[dict], ctx: Any) -> list[dict]:
    submits = _find_submits(messages)
    if not submits:
        return messages
    out, i, folded = [], 0, 0
    while i < len(messages):
        nxt = next((j for j in submits if j >= i), None)
        if nxt is None:
            out.extend(messages[i:])
            break
        q = next((k for k in range(i, nxt) if messages[k].get("role") == "user"), None)
        start = q if q is not None else i
        out.extend(messages[i:start])
        if q is not None:
            out.append(messages[q])
        out.append(_folded_answer(messages, start, nxt))
        folded += 1
        i = nxt + 1
    if folded != ctx.comp.fold_count:
        ctx.comp.fold_count = folded
        _log_compact(ctx, "L2", est_tokens(messages), est_tokens(out), [])
    return out


# ---------------- L3：结果存根化（白名单 + 保留规则 + 攒批触发）

def _eligible_rids(view: list[dict]) -> list[tuple[int, str]]:
    """按出现顺序返回 (消息下标, rid)：结果头匹配 `[rN] tool(` 且 tool 在白名单。"""
    out = []
    for i, m in enumerate(view):
        if m.get("role") != "tool":
            continue
        mt = _RESULT_HEAD_RE.match(m.get("content") or "")
        if mt and mt.group(2) in STUB_TOOLS:
            out.append((i, mt.group(1)))
    return out


def _last_assistant_rids(view: list[dict]) -> set[str]:
    for m in reversed(view):
        if m.get("role") == "assistant":
            text = m.get("content") or ""
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", tc)
                text += " " + str(fn.get("arguments", ""))
            return set(_RID_REF_RE.findall(text))
    return set()


def _stub_text(res: Any) -> str:
    return (f"[{res.id} 已压缩] {res.tool}({_args_line(res)})\n"
            f"摘要: {res.digest}\n"
            f'原始数据: recall("{res.id}")')


def _err_fold(content: str) -> str:
    return "已失败: " + content.splitlines()[0][len(_ERROR_HEAD):].strip()


def _apply_l3(view: list[dict], ctx: Any) -> list[dict]:
    cfg, comp = ctx.cfg, ctx.comp
    occ = _eligible_rids(view)
    # 保留规则（V4.28）：最近 N 个可压缩结果 + 最近一条 assistant 引用过的 rid 不存根
    keep_last = int(cfg["stub_keep_last"])
    protected = {rid for _, rid in occ[-keep_last:]} | _last_assistant_rids(view)
    candidates = [(i, rid) for i, rid in occ if rid not in comp.stubbed and rid not in protected]
    stub_cache = {}
    releasable = 0
    for _, rid in candidates:
        res = ctx.store.get(rid)
        if res is None:
            continue
        stub_cache[rid] = _stub_text(res)
        original = sum(len(view[i].get("content") or "") for i, r in occ if r == rid)
        releasable += max(0, original - len(stub_cache[rid])) // 4
    # 报错结果：触发后压成一行（最近一条报错保留全文，模型要靠它自我纠错）
    err_idx = [i for i, m in enumerate(view)
               if m.get("role") == "tool" and (m.get("content") or "").startswith(_ERROR_HEAD)]
    err_new = [i for i in err_idx[:-1] if view[i].get("tool_call_id") not in comp.errors]
    releasable += sum(max(0, len(view[i]["content"]) - len(_err_fold(view[i]["content"])))
                      // 4 for i in err_new)
    tokens = est_tokens(view)
    eff_window = int(cfg["window"]) - int(cfg["reserve_output"])
    trigger = comp.force or (releasable >= int(cfg["clear_at_least"])
                             and tokens > float(cfg["l3_ratio"]) * eff_window)
    if trigger and (candidates or err_new):
        comp.stubbed = frozenset(comp.stubbed | {rid for _, rid in candidates})
        comp.errors = frozenset(comp.errors | {view[i].get("tool_call_id") for i in err_new})
        _log_compact(ctx, "L3", tokens, tokens - releasable,
                     sorted({rid for _, rid in candidates}))
    out = []
    for m in view:
        if m.get("role") == "tool":
            content = m.get("content") or ""
            mt = _RESULT_HEAD_RE.match(content)
            if mt and mt.group(1) in comp.stubbed:
                res = ctx.store.get(mt.group(1))
                if res is not None:
                    stub = stub_cache.get(mt.group(1)) or _stub_text(res)
                    m = {**m, "content": stub}
            elif m.get("tool_call_id") in comp.errors:
                m = {**m, "content": _err_fold(content)}
        out.append(m)
    return out


# ---------------- L4：账本压缩（投影视图，攒批推进 cut）

def split_groups(messages: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    """(head, groups)：head=首条 assistant 之前的消息；每组=一条 assistant + 其后续 tool/user。"""
    head: list[dict] = []
    groups: list[list[dict]] = []
    cur: list[dict] | None = None
    for m in messages:
        if m.get("role") == "assistant":
            if cur is not None:
                groups.append(cur)
            cur = [m]
        elif cur is None:
            head.append(m)
        else:
            cur.append(m)
    if cur is not None:
        groups.append(cur)
    return head, groups


def _skills_block(ctx: Any) -> str:
    if not ctx.loaded_skills:
        return ""
    cap = int(ctx.cfg["skill_reinject_max_tokens"]) * 4
    parts, used = [], 0
    for name, body in ctx.loaded_skills.items():
        chunk = f"<skill name={name}>\n{body}\n</skill>"
        if used + len(chunk) > cap:
            parts.append(chunk[:max(0, cap - used)] + "…（已截断）")
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


def _apply_l4(view: list[dict], ctx: Any) -> list[dict]:
    cfg, comp = ctx.cfg, ctx.comp
    head, groups = split_groups(view)
    keep = 1 if comp.force else int(cfg["ledger_keep_recent_turns"])
    tokens = est_tokens(view)
    if (groups and len(groups) > keep
            and (comp.force or tokens > int(cfg["compact_threshold"]))):
        target = len(groups) - keep
        if target > comp.ledger_cut:
            dropped = [m for g in groups[:target] for m in g]
            rids = sorted(set(_RID_REF_RE.findall(
                " ".join(str(m.get("content") or "") +
                          " ".join(str((tc.get("function", tc)).get("arguments", ""))
                                   for tc in m.get("tool_calls") or [])
                          for m in dropped))))
            comp.ledger_cut = target
            comp.ledger_text = ctx.ledger.render(ctx.store, ctx.todos)
            new_view = _l4_view(head, groups, comp, ctx)
            _log_compact(ctx, "L4", tokens, est_tokens(new_view), rids)
            return new_view
    if comp.ledger_cut:
        return _l4_view(head, groups, comp, ctx)
    return view


def _l4_view(head: list[dict], groups: list[list[dict]], comp: CompState, ctx: Any) -> list[dict]:
    """视图 = system + 账本 + 已加载 skills + 用户原始问题 + 最近 keep 轮，不多不少（V4.35）。"""
    system = head[0]
    question = next((m for m in head[1:] if m.get("role") == "user"), None)
    if question is None:
        question = {"role": "user", "content": "（原始问题已不可见）"}
    out = [system, {"role": "user", "content": comp.ledger_text}]
    sk = _skills_block(ctx)
    if sk:
        out.append({"role": "user", "content": sk})
    out.append({"role": "user", "content": question.get("content", "")})
    for g in groups[comp.ledger_cut:]:
        out.extend(g)
    return out


# ---------------- 流水线入口

def build_view(history: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    """每次调用模型前按 L2→L3→L4 生成投影视图；原始 history 永不被修改。"""
    view = _apply_l2(history, ctx)
    view = _apply_l3(view, ctx)
    view = _apply_l4(view, ctx)
    return view


def on_context_overflow(view: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    """reactive 兜底（plan 6.6.3）：强制全量存根 + 账本投影到最近 1 组，最多 reactive_max_retries 次。

    完整的 PTL（按轮次分组丢弃）与熔断在 P4.5d 实现；这里先保证能按批降级重试。
    """
    cfg = ctx.cfg
    ctx.comp.overflow_retries += 1
    if ctx.comp.overflow_retries > int(cfg["reactive_max_retries"]):
        raise ContextTooLong(f"上下文超长：reactive 压缩已重试 {cfg['reactive_max_retries']} 次仍不够")
    ctx.comp.force = True   # L3 全量存根 + L4 投影到最近 1 组，由下一次 build_view 攒批执行
    return build_view(ctx.messages, ctx)

"""P4.5b/c/d：上下文压缩流水线（plan 6.6.3）——L2 轮次折叠 / L3 结果存根化 / L4 账本压缩 / L5 全量摘要。

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
    ptl_drop: int = 0              # PTL：已溢出的次数，每次比上一次多丢弃最早的一个轮次组
    failures: int = 0              # 连续压缩失败计数（达 cfg compact_fail_limit → 熔断）
    force: bool = False            # 超长兜底：本步起无视阈值全量存根 + 账本投影
    summary_text: str = ""         # L5 冻结摘要（第 2/3/6/7 段为代码生成）
    summary_cut: int = 0           # L5：L4 视图开头的多少个轮次组被摘要替换
    summary_gave_up: bool = False  # L5 摘要核验两次都不通过 → 本次运行退回只用账本


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
    base_keep = 1 if comp.force else int(cfg["ledger_keep_recent_turns"])
    # PTL（V4.41）：每次溢出比上一次多丢弃一个最早的轮次组
    keep = max(0, base_keep - max(0, comp.ptl_drop - 1))
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


# ---------------- L5：全量摘要（领域版 9 段模板，plan 6.6.3）
# 第 2/3/6/7 段由代码从证据账本与历史原文填写；LLM 只写第 1/4/5/8/9 段的推理。
# 摘要中的数字必须带 rid 且能在 Result Store 中找到；核验不过 → 重生成一次 →
# 仍不过 → 退回 L4 视图（只用账本），两种结局都写 trace 事件（V4.38）。

_L5_TITLES = {1: "用户问题与意图", 2: "口径与约定", 3: "已取得的数据", 4: "错误与修复",
              5: "假设检验记录", 6: "用户的所有消息", 7: "待办任务", 8: "当前工作",
              9: "可选的下一步"}
_LLM_SECTIONS = (1, 4, 5, 8, 9)

_L5_INSTRUCTION = """\
把下面的 agent 对话历史压缩成结构化摘要。先在 <analysis> 标签内按时间顺序梳理对话\
（这部分会被程序删除），再在 <summary> 标签内输出恰好 9 段，每段以 "## <编号>. <段名>" 开头：
## 1. 用户问题与意图
## 2. 口径与约定（程序会覆盖，写占位即可）
## 3. 已取得的数据（程序会覆盖，写占位即可）
## 4. 错误与修复（报错、被护栏打回的原因及修正方式）
## 5. 假设检验记录（程序会附上代码生成的记录，你补充推理）
## 6. 用户的所有消息（程序会覆盖，写占位即可）
## 7. 待办任务（程序会覆盖，写占位即可）
## 8. 当前工作
## 9. 可选的下一步
硬约束：第 1、4、5、8、9 段中出现的每个数字都必须能在证据账本里找到出处，\
数字后紧跟 (rN)；没有出处支撑就只写定性结论，绝不写具体数值。"""

_ANALYSIS_RE = re.compile(r"<analysis>.*?</analysis>", re.S)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)
_SECTION_HEAD_RE = re.compile(r"^##\s*(\d+)\b")


def _strip_analysis(text: str) -> str:
    """V4.37：删除 <analysis> 块；取 <summary> 正文。"""
    text = _ANALYSIS_RE.sub("", text)
    m = _SUMMARY_RE.search(text)
    return (m.group(1) if m else text).strip()


def _split_llm_sections(body: str) -> dict[int, str]:
    secs: dict[int, str] = {}
    cur: int | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = _SECTION_HEAD_RE.match(line)
        if m is not None:
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur = int(m.group(1))
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs


def _ledger_parts(ledger_text: str) -> dict[str, str]:
    """把账本按 "## 节名" 拆开（节名去掉括号后缀，与 ledger.verify_ledger 同规则）。"""
    parts: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in ledger_text.splitlines():
        if line.startswith("## "):
            if cur is not None:
                parts[cur] = "\n".join(buf).strip()
            cur = line[3:].split("（")[0].strip()
            buf = []
        elif line.startswith("# "):
            if cur is not None:
                parts[cur] = "\n".join(buf).strip()
            cur, buf = None, []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        parts[cur] = "\n".join(buf).strip()
    return parts


def _serialize_for_llm(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        text = str(m.get("content") or "")
        calls = "; ".join(
            f"{tc.get('function', {}).get('name', '')}({tc.get('function', {}).get('arguments', '')})"
            for tc in m.get("tool_calls") or [])
        lines.append(f"[{m.get('role')}] {text}" + (f" 调用: {calls}" if calls else ""))
    return "\n".join(lines)


def _verify_summary(secs: dict[int, str], store: Any) -> list[str]:
    """只核验 LLM 撰写（第 4、5 段推理、8、9 段）的文字：含数字的行必须带 rid 且数字在该 rid 结果中。"""
    problems: list[str] = []
    for k in _LLM_SECTIONS:
        for line in secs.get(k, "").splitlines():
            nums = ledger_mod._signed_numbers(line)
            if not nums:
                continue
            rids = _RID_REF_RE.findall(line)
            if not rids:
                problems.append(f"摘要第 {k} 段有数字但无 rid: {line[:60]}")
                continue
            rid = rids[-1]
            for n in nums:
                if not store.find_value(rid, n):
                    problems.append(f"摘要第 {k} 段: rid {rid} 中找不到 {n}（行: {line[:60]}）")
    return problems


def _summarize_once(tail_text: str, ctx: Any) -> tuple[dict[int, str], list[str]]:
    prompt = (_L5_INSTRUCTION + "\n\n=== 证据账本（数字出处参考，均可 recall）===\n"
              + ctx.comp.ledger_text + "\n\n=== 需要压缩的对话历史 ===\n" + tail_text)
    resp = ctx.llm.chat([{"role": "system", "content": "你是会话压缩器，只输出规定格式的摘要文本。"},
                         {"role": "user", "content": prompt}])
    secs = _split_llm_sections(_strip_analysis(resp.content or ""))
    return secs, _verify_summary(secs, ctx.store)


def _assemble_summary(secs: dict[int, str], ctx: Any, user_texts: list[str]) -> str:
    lp = _ledger_parts(ctx.comp.ledger_text)
    out = ["# 会话摘要（L5：第 2、3、6、7 段及第 5 段的记录部分由代码生成，数字均可 recall 核验）"]
    for i in range(1, 10):
        out.append(f"## {i}. {_L5_TITLES[i]}")
        if i == 2:
            out.append(lp.get("口径", "（无）"))
        elif i == 3:
            out.append(lp.get("已取得的数据", "（无）"))
        elif i == 5:
            extra = secs.get(5, "")
            out.append(lp.get("假设检验记录", "（无）")
                       + (("\n推理补充: " + extra) if extra else ""))
        elif i == 6:
            out += [f"- {t}" for t in user_texts] or ["- （无）"]
        elif i == 7:
            out.append(lp.get("计划", "（无）"))
        else:
            out.append(secs.get(i, "（模型未提供）"))
    return "\n".join(out)


def _l5_view(head: list[dict], groups: list[list[dict]], comp: CompState) -> list[dict]:
    """L4 视图头部之后插入摘要消息；被摘要覆盖的轮次组不再进入视图。"""
    return ([head[0], {"role": "user", "content": comp.summary_text}] + head[1:]
            + [m for g in groups[comp.summary_cut:] for m in g])


def _apply_l5(view: list[dict], ctx: Any, history: list[dict]) -> list[dict]:
    cfg, comp = ctx.cfg, ctx.comp
    if not cfg.get("l5_enabled") or getattr(ctx, "llm", None) is None or not view:
        return view
    head, groups = split_groups(view)
    if comp.summary_text:
        if head and len(groups) >= comp.summary_cut:
            return _l5_view(head, groups, comp)
        return view
    if comp.summary_gave_up or not head:
        return view
    tokens = est_tokens(view)
    if tokens <= int(cfg["compact_threshold"]):
        return view
    target = len(groups) - int(cfg["l5_keep_last_turns"])
    if not groups or target < 1:
        return view
    if not comp.ledger_text:
        comp.ledger_text = ctx.ledger.render(ctx.store, ctx.todos)
    tail = [m for g in groups[:target] for m in g]
    dropped_rids = sorted(set(_RID_REF_RE.findall(
        " ".join(str(m.get("content") or "") +
                  " ".join(str((tc.get("function", tc)).get("arguments", ""))
                           for tc in m.get("tool_calls") or [])
                  for m in tail))))
    user_texts = [str(m.get("content") or "") for m in history if m.get("role") == "user"]
    ok = False
    for attempt in (1, 2):        # V4.38：核验不通过只重新生成一次
        secs, problems = _summarize_once(_serialize_for_llm(tail), ctx)
        if not problems:
            comp.summary_text = _assemble_summary(secs, ctx, user_texts)
            comp.summary_cut = target
            ok = True
            break
        ctx.trace.event(type="compact", level="L5", attempt=attempt, applied=False,
                        tokens_before=tokens, tokens_after=tokens, rids=dropped_rids,
                        problems=problems[:5])
    if not ok:
        comp.summary_gave_up = True     # 退回 L4 的结果，只用账本
        return view
    new_view = _l5_view(head, groups, comp)
    _log_compact(ctx, "L5", tokens, est_tokens(new_view), dropped_rids)
    return new_view


# ---------------- 流水线入口

def build_view(history: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    """每次调用模型前按 L2→L3→L4→L5 生成投影视图；原始 history 永不被修改。"""
    view = _apply_l2(history, ctx)
    view = _apply_l3(view, ctx)
    view = _apply_l4(view, ctx)
    view = _apply_l5(view, ctx, history)
    return view


def on_context_overflow(view: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    """reactive/PTL 兜底（plan 6.6.3，V4.40/V4.41）：强制全量压缩，\
每次溢出比上一次多丢弃一个最早的轮次组；超过 reactive_max_retries 抛 ContextTooLong\
（由主循环累计进熔断计数，V4.42）。"""
    cfg = ctx.cfg
    ctx.comp.overflow_retries += 1
    if ctx.comp.overflow_retries > int(cfg["reactive_max_retries"]):
        raise ContextTooLong(f"上下文超长：reactive/PTL 已重试 {cfg['reactive_max_retries']} 次仍不够")
    ctx.comp.force = True
    ctx.comp.ptl_drop += 1
    return build_view(ctx.messages, ctx)

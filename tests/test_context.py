"""P4.5b/c/d 验收：上下文压缩流水线（verify 6.1：V4.19–V4.31、V4.35–V4.42）。全部离线（合成历史/真实 DB 播种/mock LLM）。"""
from __future__ import annotations

import json
import random
import re
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import ledger as ledger_mod  # noqa: E402
from agent.context import (STUB_TOOLS, CompState, ContextTooLong, SUBMIT_BAD,  # noqa: E402
                           SUBMIT_OK, build_view, est_tokens, on_context_overflow,
                           split_groups, _stub_text)
from agent.ledger import Ledger  # noqa: E402
from agent.loop import TraceWriter, make_run_ctx, run  # noqa: E402
from agent.result_store import ResultStore  # noqa: E402
from agent.tools import data as tool_data  # noqa: E402
from test_ledger import ANALYSIS_ARGS, DATA_ARGS, _seed_all  # noqa: E402
from test_loop import DB, MockLLM, asst, call, _final, _seed  # noqa: E402

# 小阈值配置：让 L3/L4 在合成历史上必然触发（阈值全部来自 cfg，V4.43）
TRIG_CFG = dict(window=100000, reserve_output=20000, compact_threshold=600,
                clear_at_least=40, l3_ratio=0.01, stub_keep_last=3,
                ledger_keep_recent_turns=2, skill_reinject_max_tokens=3000,
                reactive_max_retries=3, result_max_rows=50, result_max_tokens=2000)
# 大阈值配置：L3/L4 都不触发（做纯折叠 / 结构测试）
BIG_CFG = dict(TRIG_CFG, compact_threshold=4_000_000, clear_at_least=4_000_000,
               l3_ratio=0.99)
NO_L4 = dict(TRIG_CFG, compact_threshold=4_000_000)      # 只开 L2/L3
NO_L3 = dict(TRIG_CFG, clear_at_least=4_000_000, l3_ratio=0.99)  # 只开 L2/L4


class FakeCtx:
    """build_view 需要的最小运行上下文（不依赖真实 DB 与 LLM）。"""

    def __init__(self, cfg=None):
        self.cfg = dict(cfg or TRIG_CFG)
        self.store = ResultStore("test-v")
        self.comp = CompState()
        self.trace = TraceWriter()
        self.todos = []
        self.loaded_skills = {}
        self.ledger = Ledger(data_version="test-v")
        self.messages = []


def _pad(rng, lo=120, hi=300):
    return "备注 " + "填。" * rng.randint(lo, hi)


def _amsg(cid, name, args):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": name,
                                         "arguments": json.dumps(args, ensure_ascii=False)}}]}


def _tmsg(cid, content):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def gen_history(rng, ctx, n_turns=None):
    """合成合法历史：每轮 = user 问题 + assistant(1–3 个 tool_call)+结果，
    约半数轮以 final_answer + 提交标记（OK/BAD）收尾。数据结果直接入 store（rid 真实可用）。"""
    msgs = [{"role": "system", "content": "SYSTEM-PROMPT-BYTES-不变化"},
            {"role": "user", "content": f"问题 #{rng.randint(100, 999)}：联想存货为什么上升？"}]
    cid = 0
    for t in range(n_turns if n_turns is not None else rng.randint(1, 6)):
        if t > 0:
            msgs.append({"role": "user", "content": f"追问 #{t}：换成 HP 呢？"})
        tcs, tools = [], []
        for _ in range(rng.randint(1, 3)):
            cid += 1
            kind = rng.choices(["data", "err", "skill", "todo"], weights=[7, 2, 1, 1])[0]
            if kind == "data":
                tool = rng.choice(sorted(STUB_TOOLS))
                df = pd.DataFrame({"company": ["Lenovo", "HP"], "fiscal_year": [24, 24],
                                   "fiscal_quarter": [2, 2],
                                   "value": [rng.uniform(100, 9999), rng.uniform(100, 9999)]})
                res = ctx.store.put(tool, {"i": cid}, df)
                content = tool_data.render(res, ctx.cfg) + "\n" + _pad(rng)
            elif kind == "err":
                tool, content = "run_sql", "工具错误: CatalogException: 表不存在\n" + _pad(rng)
            elif kind == "skill":
                tool, content = "load_skill", "<skill name=attribution>\n" + _pad(rng) + "\n</skill>"
            else:
                tool, content = "todo_write", "计划已更新：共 2 项，已完成 1 项。"
            tcs.append({"id": f"c{cid}", "type": "function",
                        "function": {"name": tool, "arguments": json.dumps({"i": cid})}})
            tools.append(_tmsg(f"c{cid}", content))
        if rng.random() < 0.5 and ctx.store._by_id:
            cid += 1
            rid = rng.choice(sorted(ctx.store._by_id))
            fa_args = {"answer_md": f"存货上升约 18%（{rid}），结论标签 seasonal。",
                       "status": "answered",
                       "claims": [{"text": "变化率", "value": 18.2, "unit": "pct", "ref": rid}]}
            tcs.append({"id": f"c{cid}", "type": "function",
                        "function": {"name": "final_answer",
                                     "arguments": json.dumps(fa_args, ensure_ascii=False)}})
            mark = SUBMIT_OK if rng.random() < 0.7 else SUBMIT_BAD
            tools.append(_tmsg(f"c{cid}", mark))
        msgs.append({"role": "assistant", "content": None, "tool_calls": tcs})
        msgs.extend(tools)
    msgs.append({"role": "user", "content": "最后一问：现在该收敛了。"})
    msgs.append({"role": "assistant",
                 "content": f"参考 r1 与 r2 的结果，还要再看 r{rng.randint(1, 3)}。"})
    return msgs


def check_invariants(history, view, ctx, label=""):
    """不变量 1（V4.19 配对/无连续 assistant）、3（V4.21 system 不变）、4（V4.22 rid 可召回）。"""
    errs = []
    open_calls, seen_ids, prev = {}, set(), None
    for m in view:
        r = m.get("role")
        if r not in {"system", "user", "assistant", "tool"}:
            errs.append(f"非法角色 {r}")
        if r == "assistant" and prev == "assistant":
            errs.append("出现连续两条 assistant")
        if r == "tool":
            if m["tool_call_id"] not in seen_ids:
                errs.append(f"孤立 tool 结果 {m['tool_call_id']}")
            open_calls.pop(m["tool_call_id"], None)
        if r == "assistant":
            for tc in m.get("tool_calls") or []:
                seen_ids.add(tc["id"])
                open_calls[tc["id"]] = True
        prev = r
    if open_calls:
        errs.append(f"没有结果的 tool_call: {sorted(open_calls)}")
    if view and view[0] != history[0]:
        errs.append("不变量 3：system 消息变了")
    for m in history:  # 历史中出现过的每个 rid 都仍可召回
        mt = re.match(r"^\[(r\d+)\]", m.get("content") or "")
        if mt and ctx.store.get(mt.group(1)) is None:
            errs.append(f"rid {mt.group(1)} 不可召回")
    return [f"{label}: {e}" for e in errs]


def hist_hash(msgs):
    return sha256(json.dumps(msgs, sort_keys=True, ensure_ascii=False, default=str)
                  .encode("utf-8")).hexdigest()


# ---------------- V4.19–V4.24：随机历史 × 每一层压缩，不变量全保持

def test_v4_19_24_invariants_over_200_random_histories():
    layers = [("L2only", dict(BIG_CFG)), ("L3", dict(NO_L4)), ("L4", dict(NO_L3)),
              ("all", dict(TRIG_CFG))]
    for i in range(200):
        rng = random.Random(1000 + i)
        ctx0 = FakeCtx(BIG_CFG)
        base_msgs = gen_history(rng, ctx0)
        for name, cfg in layers:
            ctx = FakeCtx(cfg)
            ctx.store = ctx0.store            # 共享 store，rid/digest 一致
            history = deepcopy(base_msgs)
            h0 = hist_hash(history)
            view = build_view(history, ctx)
            assert check_invariants(history, view, ctx, f"#{i}/{name}") == []
            assert hist_hash(history) == h0, f"#{i}/{name} 不变量 2：原始历史被改动"
            assert build_view(history, ctx) == view, f"#{i}/{name} V4.24 同历史两次构建不同"


def test_v4_23_compact_events_complete():
    ctx = FakeCtx(TRIG_CFG)
    build_view(gen_history(random.Random(99), ctx, n_turns=6), ctx)
    evs = [e for e in ctx.trace.events if e["type"] == "compact"]
    assert {"L3", "L4"} <= {e["level"] for e in evs}
    for e in evs:
        assert {"level", "tokens_before", "tokens_after", "rids"} <= set(e)
        assert e["tokens_after"] <= e["tokens_before"], e


def test_v4_24_prefix_stable_when_appending():
    ctx = FakeCtx(TRIG_CFG)
    history = gen_history(random.Random(7), ctx, n_turns=2)
    v1 = build_view(history, ctx)
    state = (ctx.comp.stubbed, ctx.comp.ledger_cut, ctx.comp.fold_count, ctx.comp.errors)
    res = ctx.store.put("query_metric", {"i": 999},
                        pd.DataFrame({"company": ["Dell"], "value": [123.4]}))
    history.append({"role": "user", "content": "再查一次。"})
    history.append(_amsg("c999", "query_metric", {}))
    history.append(_tmsg("c999", tool_data.render(res, ctx.cfg)))
    v2 = build_view(history, ctx)
    assert check_invariants(history, v2, ctx) == []
    if (ctx.comp.stubbed, ctx.comp.ledger_cut, ctx.comp.fold_count,
            ctx.comp.errors) == state:      # 期间没有触发新的压缩
        assert v2[:len(v1)] == v1, "V4.24 前缀必须逐字节保持"


# ---------------- V4.25 L1 结果预算（渲染层，300 行）

def test_v4_25_l1_result_budget_300_rows():
    ctx = FakeCtx(TRIG_CFG)
    df = pd.DataFrame({"company": ["Lenovo"] * 300, "fiscal_year": [24] * 300,
                       "fiscal_quarter": [2] * 300, "value": [float(i) for i in range(300)]})
    res = ctx.store.put("query_metric", {"big": True}, df)
    text = tool_data.render(res, ctx.cfg)
    data_rows = sum(1 for ln in text.splitlines()
                    if ln.startswith("| ") and not ln.startswith("| company"))
    assert data_rows <= int(ctx.cfg["result_max_rows"]) == 50
    assert "完整结果可用 recall(" in text and '"r1", offset=' in text
    assert len(ctx.store.get(res.id).df) == 300           # store 里仍是完整 300 行


# ---------------- V4.26 L2 只折叠通过核验的轮

def _two_turn_history(ctx):
    res = ctx.store.put("query_metric", {"i": 1},
                        pd.DataFrame({"company": ["Lenovo"], "value": [111.0]}))
    fa1 = {"answer_md": f"存货 111（{res.id}），结论标签 no_anomaly。", "status": "answered",
           "claims": [{"text": "存货", "value": 111.0, "unit": "usd_mn", "ref": res.id}]}
    fa2 = {"answer_md": "第二轮结论。", "status": "answered", "claims": []}
    return [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "第一轮问题"},
        _amsg("q1", "query_metric", {}), _tmsg("q1", tool_data.render(res, ctx.cfg)),
        _amsg("f1", "final_answer", fa1), _tmsg("f1", SUBMIT_OK),
        {"role": "user", "content": "第二轮问题"},
        _amsg("f2", "final_answer", fa2), _tmsg("f2", SUBMIT_BAD),
        {"role": "user", "content": "第三轮问题（当前）"},
    ]


def test_v4_26_l2_folds_only_verified_turns():
    ctx = FakeCtx(BIG_CFG)
    history = _two_turn_history(ctx)
    view = build_view(history, ctx)
    assert any(m["role"] == "user" and m["content"] == "第一轮问题" for m in view), "问题必须保留"
    folded = [m for m in view if m["role"] == "assistant"
              and (m.get("content") or "").startswith("[已完成并通过护栏核验的问答轮]")]
    assert len(folded) == 1 and "111" in folded[0]["content"] and "r1" in folded[0]["content"]
    assert not any(m.get("tool_call_id") == "q1" for m in view), "折叠轮中间的取数工具轮应被移除"
    # 未通过核验的第二轮原样保留
    assert any(m.get("content") == SUBMIT_BAD for m in view)
    assert any(m.get("tool_call_id") == "f2" for m in view)
    assert check_invariants(history, view, ctx) == []


# ---------------- V4.27–V4.30 L3 白名单 / 保留规则 / 触发条件 / 存根格式

def _many_results(ctx, n, pad=300):
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "问题"}]
    for i in range(n):
        df = pd.DataFrame({"company": ["Lenovo"], "fiscal_year": [24], "fiscal_quarter": [2],
                           "value": [100.0 + i]})
        res = ctx.store.put("query_metric", {"i": i}, df)
        msgs.append(_amsg(f"c{i}", "query_metric", {"i": i}))
        msgs.append(_tmsg(f"c{i}", tool_data.render(res, ctx.cfg) + "\n备注 " + "填。" * pad))
    return msgs


def test_v4_27_l3_whitelist_never_stubs_specials():
    ctx = FakeCtx(NO_L4)
    msgs = _many_results(ctx, 6)
    msgs += [_amsg("sk", "load_skill", {"name": "attribution"}),
             _tmsg("sk", "<skill name=attribution>\n正文一\n</skill>")]
    msgs += [_amsg("td", "todo_write", {"items": []}),
             _tmsg("td", "计划已更新：共 2 项，已完成 1 项。")]
    msgs += [_amsg("vf", "final_answer", {"answer_md": "x", "status": "answered", "claims": []}),
             _tmsg("vf", "答案未通过核验：claim 1 的数值 9.9 在 r1 中找不到")]
    msgs.append({"role": "user", "content": "请调用 final_answer 提交答案。"})
    view = build_view(msgs, ctx)
    stubbed = [m for m in view if "已压缩" in (m.get("content") or "")]
    assert stubbed, "白名单结果应被存根化（releasable 远超 clear_at_least）"
    for cid, text in (("sk", "<skill name=attribution>\n正文一\n</skill>"),
                      ("td", "计划已更新：共 2 项，已完成 1 项。"),
                      ("vf", "答案未通过核验：claim 1 的数值 9.9 在 r1 中找不到")):
        got = next(m for m in view if m.get("tool_call_id") == cid)
        assert got["content"] == text, f"{cid} 不该被压缩"
    assert any(m["role"] == "user" and m["content"] == "请调用 final_answer 提交答案。"
               for m in view)


def test_v4_28_l3_keep_rules_and_errors():
    ctx = FakeCtx(NO_L4)
    msgs = _many_results(ctx, 6)          # 结果 rid 依次为 r1..r6
    msgs += [_amsg("e1", "run_sql", {"sql": "select 1"}),
             _tmsg("e1", "工具错误: CalcError: 表达式含不允许的调用\n" + "详" * 500)]
    msgs += [_amsg("e2", "run_sql", {"sql": "select 2"}),
             _tmsg("e2", "工具错误: SyntaxError: SQL 解析失败\n" + "细" * 500)]
    msgs.append({"role": "assistant", "content": "先看 r2 的存货趋势，再参照 e2 修正。"})
    view = build_view(msgs, ctx)
    stubbed = {m.get("tool_call_id") for m in view if "已压缩" in (m.get("content") or "")}
    assert stubbed == {"c0", "c2"}, "最近 3 个（c3–c5）与 assistant 引用过的 r2（c1）保持原样"
    e1 = next(m for m in view if m.get("tool_call_id") == "e1")
    e2 = next(m for m in view if m.get("tool_call_id") == "e2")
    assert e1["content"].startswith("已失败: ") and "\n" not in e1["content"]
    assert e2["content"].startswith("工具错误:"), "最近一条报错保留全文供自我纠错"
    assert check_invariants(msgs, view, ctx) == []


def test_v4_29_l3_no_trigger_below_clear_at_least():
    ctx = FakeCtx(dict(TRIG_CFG, clear_at_least=10_000))
    view = build_view(_many_results(ctx, 2), ctx)
    assert ctx.comp.stubbed == frozenset()
    assert all("已压缩" not in (m.get("content") or "") for m in view)
    assert all(not (m.get("content") or "").startswith("已失败") for m in view)


def test_v4_30_stub_format_and_digest_stable():
    ctx = FakeCtx(TRIG_CFG)
    res = ctx.store.put("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                         "last_n": 8},
                        pd.DataFrame({"company": ["Lenovo"], "fiscal_year": [24],
                                      "fiscal_quarter": [2], "value": [6123.0]}))
    s1, s2 = _stub_text(res), _stub_text(res)
    assert s1 == s2
    assert re.match(r'^\[r\d+ 已压缩\] query_metric\(.*\)\n摘要: .*\n原始数据: recall\("r\d+"\)$',
                    s1, re.S), s1
    assert len(res.digest) <= 200


# ---------------- V4.31 digest 中的数字是真的（真实 DB 播种全部 10 个存储工具）

def test_v4_31_digest_numbers_traceable():
    ctx = _seed_all(make_run_ctx(DB, None), list(DATA_ARGS) + list(ANALYSIS_ARGS))
    for res in ctx.store.all():
        for num in ledger_mod._signed_numbers(res.digest):
            assert ctx.store.find_value(res.id, num), \
                f"{res.tool} digest 中的 {num} 不在 {res.id} 的结果里"


# ---------------- V4.35 L4 之后的视图结构（不多不少）

def test_v4_35_l4_view_structure():
    ctx = FakeCtx(TRIG_CFG)
    msgs = _many_results(ctx, 8)
    msgs += [_amsg("sk", "load_skill", {"name": "attribution"}),
             _tmsg("sk", "<skill name=attribution>\n第一步：variance。\n</skill>")]
    ctx.loaded_skills = {"attribution": "第一步：variance。"}
    view = build_view(msgs, ctx)
    assert ctx.comp.ledger_cut >= 1, "L4 未触发"
    head, groups = split_groups(view)
    n_keep = int(TRIG_CFG["ledger_keep_recent_turns"])
    assert len(groups) == n_keep, f"应只剩最近 {n_keep} 轮，实得 {len(groups)}"
    # A2（V4.47 批阅授权）：L4 视图在账本后追加活动证据块（ledger_user 内追加，不新增消息数）
    assert [m["role"] for m in view[:4]] == ["system", "user", "user", "user"]
    assert view[0] == msgs[0]
    assert ctx.comp.ledger_text in view[1]["content"]  # 账本在前
    assert "活动证据" in view[1]["content"]  # 活动证据块追加于账本之后
    assert ctx.comp.ledger_text == ctx.ledger.render(ctx.store, ctx.todos)
    assert view[2]["content"].startswith("<skill name=attribution>")
    assert view[3]["content"] == "问题"
    assert len(view) == 4 + sum(len(g) for g in groups)   # 不多不少
    assert check_invariants(msgs, view, ctx) == []


# ---------------- reactive 兜底：按批降级，重试超限抛 ContextTooLong

def test_reactive_overflow_degrades_then_gives_up():
    ctx = FakeCtx(TRIG_CFG)
    history = _many_results(ctx, 5)
    ctx.messages = history
    v0 = est_tokens(history)
    v1 = on_context_overflow(history, ctx)          # 第 1 次 reactive
    assert ctx.comp.force and est_tokens(v1) < v0
    assert check_invariants(history, v1, ctx) == []
    for _ in range(int(TRIG_CFG["reactive_max_retries"]) - 1):   # 累计到上限
        v1 = on_context_overflow(v1, ctx)
    try:
        on_context_overflow(v1, ctx)
        assert False, "超过 reactive_max_retries 必须抛 ContextTooLong"
    except ContextTooLong:
        pass


# ---------------- 主循环写入提交标记（L2 折叠的依据）

def test_loop_appends_submit_markers():
    qargs = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}
    ctx = make_run_ctx(DB, MockLLM([asst(calls=[call("c1", "query_metric", qargs)])]))
    seeded = _seed(ctx, "query_metric", qargs)
    v = float(seeded.stored.df["inventory"].iloc[-1])
    ctx.llm.script.append(asst(calls=[_final(md=f"最新存货 {v:.6g} 百万美元（r1）。",
                                             claims=[{"text": "存货", "value": v,
                                                     "unit": "usd_mn", "ref": "r1"}])]))
    out = run("联想最新存货是多少？", ctx)
    assert out.verified
    assert ctx.messages[-1]["content"] == SUBMIT_OK

    # 核验连败打回至上限 → 带"未通过"标记
    bad_ctx = make_run_ctx(DB, MockLLM([]), verifier=lambda a, c: type("V", (), {"ok": False, "feedback": "核验不通过：测试注入"})())
    bad = {"text": "x", "value": 987654.3, "unit": "usd_mn", "ref": "r9"}
    for _ in range(4):
        bad_ctx.llm.script.append(asst(calls=[_final(md="结论已给出，不含数值主张。", claims=[bad])]))
    out2 = run("随便问问？", bad_ctx)
    assert not out2.verified
    assert bad_ctx.messages[-1]["content"] == SUBMIT_BAD


# ---------------- V4.36–V4.39 L5 全量摘要（领域版 9 段模板）

L5_CFG = dict(TRIG_CFG, compact_threshold=400, l5_enabled=True, l5_keep_last_turns=1)


class SummaryLLM:
    """L5 摘要器用的 LLM：按脚本返回 content，并记录每次 prompt（离线 mock）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    def chat(self, messages, tools=None, **kw):
        self.calls.append(deepcopy(messages))
        text = self.replies.pop(0)
        if isinstance(text, Exception):
            raise text
        return type("R", (), {"content": text, "usage": {}})()


def _l5_history(ctx):
    """4 个取数轮（r1..r4，每轮结果 ~1200 字符）+ 多条用户消息：
    L4 之后视图仍 > compact_threshold → 必然触发 L5。"""
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "问题：联想存货为什么上升？"}]
    for i in range(4):
        if i:
            msgs.append({"role": "user", "content": f"追问{i}：再查一季，重点是季度对比。"})
        res = ctx.store.put("query_metric", {"i": i},
                            pd.DataFrame({"company": ["Lenovo"], "fiscal_year": [24 + i],
                                          "fiscal_quarter": [1], "value": [6123.0 + i * 500]}))
        msgs.append(_amsg(f"c{i}", "query_metric", {"i": i}))
        msgs.append(_tmsg(f"c{i}", tool_data.render(res, ctx.cfg) + "\n备注 " + "填。" * 600))
    return msgs


def _l5_reply(bad=False):
    fab = " 库存周转 987.65 (r2)" if bad else ""
    return ("<analysis>按时间梳理：连续取数四次后需要归因。ANALYSIS-MARKER-甲</analysis>\n"
            "<summary>\n"
            "## 1. 用户问题与意图\n调查联想存货上升的原因（参考 r1）。\n"
            "## 2. 口径与约定\nPOISON-2 错误口径：港币计价 88888888.5\n"
            "## 3. 已取得的数据\nPOISON-3 r99 中值为 1234567.0\n"
            "## 4. 错误与修复\nrun_sql 曾报表不存在，改用 query_metric 后成功，无数字主张。\n"
            "## 5. 假设检验记录\n最新值 6123 (r1)，需继续验证季节性。" + fab + "\n"
            "## 6. 用户的所有消息\nPOISON-6 编造的用户消息\n"
            "## 7. 待办任务\nPOISON-7 编造的计划\n"
            "## 8. 当前工作\n正在做存货归因，r1 显示最新 6123。\n"
            "## 9. 可选的下一步\n调用 seasonal_check 与 peer_compare 补证据，无需新数字。\n"
            "</summary>")


def test_v4_36_37_39_l5_domain_template():
    ctx = FakeCtx(L5_CFG)
    history = _l5_history(ctx)
    ctx.llm = SummaryLLM([_l5_reply()])
    view = build_view(history, ctx)
    dump = json.dumps(view, ensure_ascii=False)
    assert ctx.comp.summary_text and ctx.comp.summary_cut == 1
    assert view[0] == history[0] and view[1]["role"] == "user"
    assert view[1]["content"].startswith("# 会话摘要")
    # V4.37：<analysis> 由代码删除，不进入视图
    assert "ANALYSIS-MARKER-甲" not in dump
    # V4.36：第 2/3/6/7 段以代码内容为准，LLM 写错的内容没有进入摘要
    for p in ("POISON-2", "POISON-3", "POISON-6", "POISON-7"):
        assert p not in dump
    summary = view[1]["content"]
    assert "期间口径: fiscal" in summary              # 第 2 段来自账本（代码）
    assert "r1 query_metric" in summary              # 第 3 段 rid 索引来自账本（代码）
    assert "推理补充: 最新值 6123 (r1)" in summary     # 第 5 段=代码记录+LLM推理
    # V4.39：每一条用户消息的原文都在第 6 段
    for m in history:
        if m["role"] == "user":
            assert m["content"] in summary
    assert check_invariants(history, view, ctx) == []
    evs = [e for e in ctx.trace.events if e["type"] == "compact" and e["level"] == "L5"]
    assert evs and evs[0]["tokens_after"] < evs[0]["tokens_before"]
    # 冻结复用：重建不再调 LLM，视图逐字节不变（前缀稳定）
    n_calls = len(ctx.llm.calls)
    assert build_view(history, ctx) == view and len(ctx.llm.calls) == n_calls


def test_v4_38_l5_summary_verification():
    # 摘要含编造数字 → 重生成一次仍不过 → 退回 L4 视图（只用账本）；都写 trace 事件
    ctx = FakeCtx(L5_CFG)
    history = _l5_history(ctx)
    ctx.llm = SummaryLLM([_l5_reply(bad=True), _l5_reply(bad=True)])
    view = build_view(history, ctx)
    assert ctx.comp.summary_gave_up and not ctx.comp.summary_text
    assert not any((m.get("content") or "").startswith("# 会话摘要") for m in view)
    fails = [e for e in ctx.trace.events
             if e["type"] == "compact" and e["level"] == "L5" and e.get("applied") is False]
    assert len(fails) == 2 and len(ctx.llm.calls) == 2
    assert "987.65" not in json.dumps(view, ensure_ascii=False)

    # 第一次编造、重新生成后合格 → 采用摘要
    ctx2 = FakeCtx(L5_CFG)
    h2 = _l5_history(ctx2)
    ctx2.llm = SummaryLLM([_l5_reply(bad=True), _l5_reply()])
    v2 = build_view(h2, ctx2)
    assert ctx2.comp.summary_text and v2[1]["content"].startswith("# 会话摘要")
    assert len(ctx2.llm.calls) == 2
    evs = [e for e in ctx2.trace.events if e["type"] == "compact" and e["level"] == "L5"]
    assert any(e.get("applied") is False for e in evs) and any("applied" not in e for e in evs)


# ---------------- V4.40–V4.42 reactive / PTL / 熔断

class FirstOverflowLLM(MockLLM):
    """第一次 chat 抛 ContextTooLong（模拟 API 超长），之后按脚本正常返回。"""

    def __init__(self, script):
        super().__init__(script)
        self.raised = False

    def chat(self, messages, tools=None, **kw):
        if not self.raised:
            self.raised = True
            raise ContextTooLong("mock: maximum context length exceeded")
        return super().chat(messages, tools, **kw)


class AlwaysTooLongLLM:
    """所有 chat 都抛 ContextTooLong：驱动熔断路径（V4.42）。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        raise ContextTooLong("mock: maximum context length exceeded")


def test_v4_40_reactive_retry_once_per_turn():
    qargs = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}
    llm = FirstOverflowLLM([asst(calls=[call("c1", "query_metric", qargs)])])
    ctx = make_run_ctx(DB, llm)
    seeded = _seed(ctx, "query_metric", qargs)     # 预填 store：重试调用会缓存命中同一 rid
    v = float(seeded.stored.df["inventory"].iloc[-1])
    llm.script.append(asst(calls=[_final(md=f"最新存货 {v:.6g} 百万美元（r1）。",
                                        claims=[{"text": "存货", "value": v,
                                                 "unit": "usd_mn", "ref": "r1"}])]))
    out = run("联想最新存货是多少？", ctx)
    assert out.verified
    assert ctx.comp.overflow_retries == 1 and ctx.comp.force
    assert ctx.comp.failures == 0
    assert not [e for e in ctx.trace.events if e["type"] == "compact_fail"]
    # 同一轮最多兜底 1 次：超长那轮只有"压缩后的重试"一次成功调用被记录
    assert len(llm.views) == 2


def test_v4_41_ptl_dumps_oldest_groups_each_retry():
    ctx = FakeCtx(TRIG_CFG)
    history = _many_results(ctx, 4)
    ctx.messages = history
    assert len(split_groups(history)[1]) == 4
    v1 = on_context_overflow(history, ctx)         # 第 1 次溢出：投影到最近 1 组
    g1 = split_groups(v1)[1]
    assert len(g1) == 1 and '"c3"' in json.dumps(g1, ensure_ascii=False)
    v2 = on_context_overflow(v1, ctx)              # 第 2 次：再丢弃最早的组
    assert len(split_groups(v2)[1]) == 0
    assert est_tokens(v2) < est_tokens(v1)
    assert ctx.comp.ptl_drop == 2
    assert check_invariants(history, v2, ctx) == []


def test_v4_42_circuit_breaker_refuses():
    llm = AlwaysTooLongLLM()
    ctx = make_run_ctx(DB, llm)
    out = run("联想最新存货是多少？", ctx)
    assert out.answer.status == "refuse"
    assert "熔断" in ctx.stats["refuse_reason"]
    limit = int(ctx.cfg["compact_fail_limit"])
    fails = [e for e in ctx.trace.events if e["type"] == "compact_fail"]
    assert len(fails) == limit
    assert ctx.comp.failures == limit
    # 每轮 = 原始请求 + 1 次 reactive 兜底；熔断后不再尝试（run 已结束）
    assert llm.calls == 2 * limit
    assert ctx.comp.overflow_retries == limit

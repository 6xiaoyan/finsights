"""P4.2 验收：主循环 + trace + 预算（verify 6.1：V4.11 场景 a–f、V4.12）。全部离线（mock LLM）。"""
from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.hooks import Verdict  # noqa: E402
from agent.llm import ChatResult, ToolCallOut  # noqa: E402
from agent.loop import NUDGE_TEXT, make_run_ctx, run  # noqa: E402

DB = "data/finsights.duckdb"


def call(cid: str, name: str, args: dict) -> ToolCallOut:
    return ToolCallOut(id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))


def asst(content=None, calls=()) -> ChatResult:
    tc = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
          for c in calls]
    msg: dict = {"role": "assistant", "content": content}
    if tc:
        msg["tool_calls"] = tc
    return ChatResult(content=content, tool_calls=list(calls),
                      usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                      message=msg)


class MockLLM:
    """按脚本返回；脚本耗尽立刻报错（防止循环行为与预期不符时悄悄空转）。"""

    def __init__(self, script):
        self.script = list(script)
        self.views: list[list[dict]] = []   # 每次 chat 收到的完整视图（用于断言消息）

    def chat(self, messages, tools=None, **kw) -> ChatResult:
        self.views.append(deepcopy(messages))
        if tools is not None:
            names = {t["function"]["name"] for t in tools}
            assert {"final_answer", "query_metric", "todo_write"} <= names
        if not self.script:
            raise AssertionError("mock LLM 脚本耗尽：循环做了预期之外的事")
        return self.script.pop(0)


def _final(cid="cf", md="Lenovo 存货数据已取到（r1）。", ref="r1"):
    return call(cid, "final_answer", {"answer_md": md, "status": "answered",
                                      "claims": [{"text": "最新存货", "value": 1.0,
                                                  "unit": "usd_mn", "ref": ref}]})


def _tool_msgs(view: list[dict]):
    return [m for m in view if m["role"] == "tool"]


# ---------------- V4.11 a 正常：查数 → final_answer

def test_v4_11_a_normal_flow():
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[call("c1", "query_metric",
                         {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2})]),
        asst(calls=[_final()]),
    ]))
    out = run("联想最新存货是多少？", ctx)
    assert out.answer.status == "answered" and out.verified
    assert ctx.store.get("r1") is not None
    assert out.stats["steps"] == 2 and out.stats["tool_calls"] == 2
    # trace：assistant 事件带 usage 和延迟；tool_result 事件带 result_id
    a_ev = [e for e in ctx.trace.events if e["type"] == "assistant"]
    tr_ev = [e for e in ctx.trace.events if e["type"] == "tool_result"]
    assert len(a_ev) == 2 and "prompt_tokens" in a_ev[0]["usage"] and a_ev[0]["latency_ms"] >= 0
    assert tr_ev[0]["result_id"] == "r1" and tr_ev[0]["latency_ms"] >= 0
    # 工具结果按 6.3 渲染进上下文
    assert ctx.llm.views[1][-1]["content"].startswith("[r1] query_metric(")


# ---------------- V4.11 b 没调用工具就结束：提醒 1 次，再犯按预算处理

def test_v4_11_b_nudge_once_then_refuse():
    ctx = make_run_ctx(DB, MockLLM([asst(content="让我想一想。"), asst(content="还是不说了。")]),
                       max_steps=5)
    out = run("随便问问", ctx)
    assert out.answer.status == "refuse"
    assert len(ctx.llm.views) == 2   # 第二次没再提醒：按预算处理直接拒答
    assert sum(1 for m in ctx.llm.views[1] if m.get("content") == NUDGE_TEXT) == 1
    assert out.stats["steps"] == 2


# ---------------- V4.11 c 工具报错：错误文本进上下文，循环继续

def test_v4_11_c_tool_error_as_text():
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[call("c1", "run_sql", {"sql": "SELECT * FROM 表不存在_xyz"})]),
        asst(calls=[_final()]),
    ]))
    out = run("查一个不存在的表", ctx)
    err_msg = _tool_msgs(ctx.llm.views[1])[0]
    assert err_msg["tool_call_id"] == "c1"
    assert err_msg["content"].startswith("工具错误")
    assert out.answer.status == "answered"  # 报错没有终止循环


# ---------------- V4.11 d 预算耗尽：refuse 且不编数字

def test_v4_11_d_steps_exhausted():
    q = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[call("c1", "query_metric", q)]),
        asst(calls=[call("c2", "query_metric", dict(q, last_n=3))]),
    ]), max_steps=2)
    out = run("一直查下去", ctx)
    assert out.answer.status == "refuse"
    assert out.answer.claims == []
    assert not re.search(r"\d", out.answer.answer_md), "拒答文本不能含任何数字"
    assert out.stats["refuse_reason"] == "步数预算耗尽"


def test_v4_11_d_token_budget_exhausted():
    ctx = make_run_ctx(DB, MockLLM([asst(), asst()]), max_steps=5)
    ctx.budget.max_tokens = 110   # 第一步就用掉 120 → 第二步开始时超限
    out = run("token 不够", ctx)
    assert out.answer.status == "refuse"
    assert out.stats["refuse_reason"] == "token 预算耗尽"


# ---------------- V4.11 e final_answer 连续 3 次核验失败

def test_v4_11_e_stop_retries_capped():
    calls = {"n": 0}

    def failing_verifier(args, ctx):
        calls["n"] += 1
        return Verdict(ok=False, feedback=f"第 {calls['n']} 次：数字找不到出处")

    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[_final(md="存货变化的解释见结果（r1）。")]),
        asst(calls=[_final(cid="cf2", md="换了说法，仍然没有出处（r1）。")]),
        asst(calls=[_final(cid="cf3", md="第三次提交（r1）。")]),
    ]), verifier=failing_verifier)
    out = run("归因一下", ctx)
    assert calls["n"] == 3                       # 打回 2 次后第 3 次直接返回
    assert not out.verified
    assert "未通过验证" in out.answer.answer_md
    assert out.answer.status == "answered"
    # 前两次核验失败的反馈作为工具结果回到上下文
    rejects = [e for e in ctx.trace.events if e["type"] == "stop_reject"]
    assert [r["attempt"] for r in rejects] == [1, 2]


# ---------------- V4.11 f 有数字但没调用过数据工具 → pre_tool 拦截

def test_v4_11_f_numbers_without_data_blocked():
    bad = _final(cid="c1", md="戴尔存货增长了 18.2%，达到 6123.4 百万美元。", ref="r9")
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[bad]),
        asst(calls=[call("c2", "query_metric",
                         {"metrics": ["inventory"], "companies": ["Dell"], "last_n": 4})]),
        asst(calls=[_final(cid="c3", md="戴尔最新存货为 6123.4 百万美元（r1）。")]),
    ]))
    out = run("戴尔存货", ctx)
    blocked = [e for e in ctx.trace.events if e["type"] == "tool_blocked"]
    assert len(blocked) == 1 and blocked[0]["tool"] == "final_answer"
    first_tool_msg = _tool_msgs(ctx.llm.views[1])[0]
    assert "没有任何出处" in first_tool_msg["content"]
    # 取数之后同样的答案被放行
    assert out.answer.status == "answered"
    assert ctx.tools_run == ["query_metric"]


# ---------------- V4.12 一条消息里 3 个只读工具并发执行

def test_v4_12_concurrent_readonly_tools():
    calls = [
        call("c1", "query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}),
        call("c2", "query_metric", {"metrics": ["inventory"], "companies": ["HP"], "last_n": 2}),
        call("c3", "query_metric", {"metrics": ["revenue"], "companies": ["Dell"], "last_n": 3}),
    ]
    ctx = make_run_ctx(DB, MockLLM([asst(calls=calls), asst(calls=[_final()])]))
    out = run("三家公司存货", ctx)
    msgs = _tool_msgs(ctx.llm.views[1])
    assert [m["tool_call_id"] for m in msgs] == ["c1", "c2", "c3"]   # 一一对应
    rids = []
    for m, c in zip(msgs, calls):
        assert m["content"].startswith("[r")
        rid = re.match(r"\[(r\d+)\]", m["content"]).group(1)
        rids.append(rid)
        assert ctx.store.get(rid).tool == c.name
    assert len(set(rids)) == 3
    assert out.answer.status == "answered"


# ---------------- todo_write：状态工具不进 store，但更新计划并有回执

def test_todo_write_updates_plan():
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[call("c1", "todo_write", {"items": [
            {"content": "取数", "status": "in_progress"}, {"content": "对比同行"}]})]),
        asst(calls=[_final()]),
    ]))
    out = run("复杂问题", ctx)
    assert [(t.content, t.status) for t in ctx.todos] == [("取数", "in_progress"),
                                                          ("对比同行", "pending")]
    assert ctx.store.get("r1") is None          # todo_write 不产生 rid
    assert "计划已更新" in _tool_msgs(ctx.llm.views[1])[0]["content"]
    assert out.answer.status == "answered"


# ---------------- 动态内容不进 system prompt（V4.16 的前置）：as_of 只进 user 消息

def test_as_of_goes_to_user_message_not_system():
    from datetime import date
    ctx = make_run_ctx(DB, MockLLM([asst(calls=[_final(md="固定口径解释（无数字）。")])]),
                       as_of=date(2025, 6, 30))
    out = run("问一个带截止日期的问题", ctx)
    assert out.answer.status == "answered"
    view = ctx.llm.views[0]
    assert view[0]["role"] == "system"
    assert "as_of" not in view[0]["content"]
    assert "as_of" in view[1]["content"] and "2025-06-30" in view[1]["content"]

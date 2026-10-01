"""P4.6：v1 适配层离线测试（runner 需要的 info 形状 + 限速包装）。全部离线（mock LLM + 真实 DB）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.tools import data as tool_data  # noqa: E402
from agent.v1 import Paced, run  # noqa: E402
from test_loop import DB, MockLLM, asst, call, _final, make_run_ctx  # noqa: E402


def test_v1_adapter_end_to_end_shape():
    """真实 DB + mock LLM 走完整循环：答案数字来自本次查询的 r1，verifier 应当通过；
    info 含 runner 记录所需的全部键。"""
    qargs = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}
    ctx = make_run_ctx(DB, None)
    probe = tool_data.execute("query_metric", qargs, ctx.tool_ctx)
    assert probe.ok
    v = float(probe.stored.df["inventory"].iloc[-1])
    llm = MockLLM([asst(calls=[call("c1", "query_metric", qargs)]),
                   asst(calls=[_final(md=f"最新存货 {v:.6g} 百万美元（r1）。",
                                      claims=[{"text": "存货", "value": v,
                                               "unit": "usd_mn", "ref": "r1"}])])])
    answer, info = run("联想最新存货是多少？", llm=llm, db_path=Path(DB))
    assert answer.status == "answered" and info["verified"]
    assert info["sql_errors"] == 0
    assert {"steps", "tool_calls", "prompt_tokens", "completion_tokens",
            "latency_s", "total_tokens", "trace", "verified"} <= set(info)
    assert info["steps"] >= 2 and info["tool_calls"] >= 2
    assert any(e["type"] == "tool_result" for e in info["trace"])
    assert any(e["type"] == "final" for e in info["trace"])


def test_v1_adapter_counts_run_sql_errors_in_info():
    """run_sql 报错（工具错误文本）计入 sql_errors，循环继续。"""
    bad = {"sql": "SELECT * FROM 不存在的表"}
    llm = MockLLM([
        asst(calls=[call("s1", "run_sql", bad)]),
        asst(calls=[_final(md="结论已给出，不含数值主张。")]),
    ])
    answer, info = run("随便查一下？", llm=llm, db_path=Path(DB))
    assert answer.status == "answered"
    assert info["sql_errors"] == 1


def test_paced_calls_limiter_per_chat():
    waits = []

    class Lim:
        def wait(self):
            waits.append(1)

    class Echo:
        def chat(self, messages, tools=None, **kw):
            return {"tools": tools, **kw}

    p = Paced(Echo(), Lim())
    r = p.chat([{"role": "user", "content": "x"}], tools=[{"t": 1}])
    assert r["tools"] == [{"t": 1}]
    p.chat([{"role": "user", "content": "y"}])
    assert len(waits) == 2

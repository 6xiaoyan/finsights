"""multi 主循环断点续跑的离线验收（PRD §10.1–§10.3、§14 场景 5/6/7/8）。

全部 mock 模型、不联网。核心断言是"恢复**复用**已完成工作"：
已完成节点的 Worker 启动次数不得增加，任务图不重建，
同一条主消息里已提交的 tool_call 不得重做。
"""
from __future__ import annotations

import json

import pytest

from agent.llm import ChatResult, ToolCallOut


class Crash(BaseException):
    """模拟进程被杀。用 BaseException 以免被 run_multi 内部的 except Exception 吞掉。"""


class ScriptLLM:
    """按脚本返回回复；记录每次 chat 的入参，便于断言重发了什么。"""

    def __init__(self, script):
        self.script = list(script)
        self.nudged = False
        self.last_call_events: list[dict] = []
        self.calls: list[dict] = []
        self._base: int | None = None      # 本次进程开始时已有的消息条数

    def chat(self, messages, tools=None, **kw):
        if self._base is None:
            self._base = len(messages)
        self.calls.append({"role": kw.get("role", "agent"),
                           "messages": list(messages), "tools": tools})
        self.last_call_events = []
        if not self.script:
            raise AssertionError("mock 脚本耗尽：主循环做了预期之外的事")
        return self.script.pop(0)

    def tool_names_asked(self, after: int = 0) -> list[str]:
        """消息列表下标 >= after 的请求里出现的工具调用名。

        `after` 传恢复检查点的 messages 长度，就能只看**恢复之后新加的消息**：
        既排除了恢复载入的历史，也不会把子 Agent 自己的短消息算进来
        （子循环的 messages 不共享主循环前缀，下标切片自然把它们排除）。
        """
        return [tc["function"]["name"] for c in self.calls for m in c["messages"][after:]
                for tc in (m.get("tool_calls") or [])]


def _call(cid, name, args):
    return ToolCallOut(id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))


def _asst(calls=(), content=None):
    return ChatResult(content=content, tool_calls=list(calls),
                      usage={"prompt_tokens": 100, "completion_tokens": 10})


def _sub(payload):
    return ChatResult(content="```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```",
                      tool_calls=[], usage={"prompt_tokens": 50, "completion_tokens": 5})


NODES = [
    {"node_id": "n1", "goal": "取存货余额", "depends_on": [], "input_refs": [],
     "completion_criteria": "", "required_for_answer": True},
    {"node_id": "n2", "goal": "算存货同比", "depends_on": ["n1"], "input_refs": [],
     "completion_criteria": "", "required_for_answer": True},
    {"node_id": "n3", "goal": "归因分析", "depends_on": ["n2"], "input_refs": [],
     "completion_criteria": "", "required_for_answer": True},
]


def _plan_reply():
    return _sub({"revision_reason": "初版", "nodes": NODES})


def _work_reply(note):
    return _sub({"facts": [], "calculations": [], "interpretations": [], "hypotheses": [],
                 "limitations": [], "required_missing_inputs": [], "summary": note})


def _ctx(tmp_path, llm, name="run"):
    from agent.loop import make_run_ctx
    ctx = make_run_ctx("data/finsights.duckdb", llm,
                       trace_path=tmp_path / f"{name}.jsonl", max_steps=20)
    ctx.llm = llm
    return ctx


def _worker_starts(ctx) -> dict[str, int]:
    """按 node_id 统计 Worker 真实启动次数（subagent_start 是一一对应的入口）。"""
    out: dict[str, int] = {}
    for e in ctx.trace.events:
        if e.get("type") == "subagent_start" and e.get("role") == "worker":
            out[e["node_id"]] = out.get(e["node_id"], 0) + 1
    return out


class Sink:
    """检查点收集器；`crash_at` = (reason, 第几次出现) 时模拟进程崩溃。"""

    def __init__(self, crash_at: tuple[str, int] | None = None):
        self.saved: list[tuple[str, object]] = []
        self.reasons: list[str] = []
        self.crash_at = crash_at
        self._seen: dict[str, int] = {}

    def __call__(self, ckpt, reason):
        self.reasons.append(reason)
        self.saved.append((reason, ckpt))          # 崩溃前也存一份：它已提交到库
        if self.crash_at and reason == self.crash_at[0]:
            self._seen[reason] = self._seen.get(reason, 0) + 1
            if self._seen[reason] == self.crash_at[1]:
                raise Crash(f"进程在第 {self._seen[reason]} 次 {reason} 后被杀")

    @property
    def last(self):
        return self.saved[-1][1]


# ---------------- 场景 5：n1/n2 已提交，n3 未开始，新进程恢复 ----------------

def test_resume_reuses_completed_nodes_and_reexecutes_pending_only(tmp_path):
    """PRD §14 场景 5：n1/n2 的 Worker 启动次数不得增加，只有未执行的 n3 重做。"""
    from agent.multi import run_multi

    llm1 = ScriptLLM([
        _asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
        _plan_reply(),
        _asst([_call("t2", "execute_analysis", {"node_id": "n1"})]),
        _work_reply("n1 完成"),
        _asst([_call("t3", "execute_analysis", {"node_id": "n2"})]),
        _work_reply("n2 完成"),
    ])
    sink1 = Sink(crash_at=("batch_committed", 3))
    ctx1 = _ctx(tmp_path, llm1, "r1")
    with pytest.raises(Crash):
        run_multi("测试题", ctx1, llm1, tmp_path / "run", checkpoint_sink=sink1)

    starts1 = _worker_starts(ctx1)
    assert starts1.get("n1") == 1 and starts1.get("n2") == 1
    assert "n3" not in starts1, "n3 不该在崩溃前启动"

    ckpt = sink1.last
    assert ckpt.next_step == 4, "n2 批次已提交完，恢复从第 4 轮开始"
    assert ckpt.state["latest_revision"] == 1
    assert [n["node_id"] for n in ckpt.state["plans"]["1"]["nodes"]] == ["n1", "n2", "n3"]
    assert ckpt.messages and ckpt.store is not None

    llm2 = ScriptLLM([
        _asst([_call("t4", "execute_analysis", {"node_id": "n3"})]),
        _work_reply("n3 完成"),
        _asst(content="还没到提交条件"),
        _asst(content="仍不到提交条件"),
    ])
    sink2 = Sink()
    ctx2 = _ctx(tmp_path, llm2, "r2")
    run_multi("测试题", ctx2, llm2, tmp_path / "run", resume=ckpt, checkpoint_sink=sink2)

    starts2 = _worker_starts(ctx2)
    assert starts2.get("n3") == 1, "崩溃时尚未执行的 n3 必须真正执行"
    assert "n1" not in starts2 and "n2" not in starts2, \
        f"已完成的 n1/n2 不能重跑，实际启动={starts2}"
    assert "plan_analysis" not in llm2.tool_names_asked(len(ckpt.messages)), \
        "恢复不能重建任务图（主 Agent 不该重新 plan_analysis）"
    st = sink2.last.state
    nodes = {n["node_id"]: n for n in st["plans"]["1"]["nodes"]}
    assert nodes["n3"]["execution_status"] == "completed"
    assert st["latest_revision"] == 1, "计划版本不应因恢复而 +1"
    assert ckpt.stats["prompt_tokens"] <= sink2.last.stats["prompt_tokens"], \
        "累计用量延续而不是清零"


def test_resume_continues_rid_allocation_without_reuse(tmp_path):
    """§10.1：恢复后 rid 从已保存最大编号后继续分配，(run_id, rid) 永不指向不同结果。"""
    import pandas as pd
    from agent.multi import run_multi

    llm1 = ScriptLLM([
        _asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
        _plan_reply(),
        _asst([_call("t2", "execute_analysis", {"node_id": "n1"})]),
        _work_reply("n1 完成"),
        _asst([_call("t3", "execute_analysis", {"node_id": "n2"})]),
        _work_reply("n2 完成"),
    ])
    sink1 = Sink(crash_at=("batch_committed", 3))
    ctx1 = _ctx(tmp_path, llm1, "q1")
    with pytest.raises(Crash):
        run_multi("测试题", ctx1, llm1, tmp_path / "runq", checkpoint_sink=sink1)
    rids_before = {r.id for r in ctx1.store.all()}
    ckpt = sink1.last
    assert ckpt.store["counter"] == ctx1.store._n

    llm2 = ScriptLLM([_asst(content="停"), _asst(content="停")])
    ctx2 = _ctx(tmp_path, llm2, "q2")
    run_multi("测试题", ctx2, llm2, tmp_path / "runq", resume=ckpt)
    assert ctx2.store._n == ckpt.store["counter"], "rid 计数器必须接着往后"
    assert {r.id for r in ctx2.store.all()} == rids_before
    ctx2.store.put("calc", {"expression": "1+1"}, pd.DataFrame([{"v": 2}]), None, step=9)
    assert ctx2.store.all()[-1].id not in rids_before


# ---------------- 场景 6：同一条主消息的多个 tool_call 执行一半就崩溃 ----------------

def test_resume_finishes_half_executed_tool_call_batch(tmp_path):
    """PRD §14 场景 6：已提交的调用不重做；消息保持 assistant/tool 一一配对。"""
    from agent.multi import _messages_wellformed, run_multi

    llm1 = ScriptLLM([
        # 一条主消息里三个调用：plan + 两个无依赖节点
        _asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"}),
               _call("t2", "execute_analysis", {"node_id": "n1"}),
               _call("t3", "execute_analysis", {"node_id": "n2"})]),
        _plan_reply(), _work_reply("n1 完成"), _work_reply("n2 完成"),
    ])
    sink1 = Sink(crash_at=("tool_result:execute_analysis", 1))
    with pytest.raises(Crash):
        run_multi("测试题", _ctx(tmp_path, llm1, "h1"), llm1, tmp_path / "runh",
                  checkpoint_sink=sink1)

    ckpt = sink1.last
    assert ckpt.next_step == 1, "批次未完成，恢复先补完本批，next_step 仍指向本轮"
    assert _messages_wellformed(ckpt.messages), "检查点里的消息必须配对合法"
    assistant = [m for m in ckpt.messages if m.get("tool_calls")][0]
    assert [c["id"] for c in assistant["tool_calls"]] == ["t1", "t2", "t3"]
    assert [m["tool_call_id"] for m in ckpt.messages if m.get("role") == "tool"] == ["t1", "t2"], \
        "只应提交 t1/t2，t3 尚未提交"

    # 恢复只补跑 t3，所以第 1 个回复是 n2 的 Worker 交付，不是 planner
    llm2 = ScriptLLM([_work_reply("n2 完成"), _asst(content="还没好"), _asst(content="仍不好")])
    sink2 = Sink()
    run_multi("测试题", _ctx(tmp_path, llm2, "h2"), llm2, tmp_path / "runh",
              resume=ckpt, checkpoint_sink=sink2)
    assert "plan_analysis" not in llm2.tool_names_asked(len(ckpt.messages)), "已提交的 plan_analysis 不能重做"
    assert sink2.last.messages and _messages_wellformed(sink2.last.messages)
    assert [m["tool_call_id"] for m in sink2.last.messages if m.get("role") == "tool"] \
        == ["t1", "t2", "t3"], "恢复后应补上 t3"


# ---------------- 场景 8：损坏 / 版本不符的检查点必须明确拒绝 ----------------

def test_incompatible_checkpoint_schema_is_refused(tmp_path):
    from agent.multi import MultiCheckpoint, run_multi
    llm = ScriptLLM([_asst(content="不会用到")])
    with pytest.raises(ValueError, match="CHECKPOINT_INCOMPATIBLE"):
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "r",
                  resume=MultiCheckpoint(schema_version="mc0", messages=[]))


def test_unpaired_tool_calls_are_refused(tmp_path):
    """真正损坏的消息协议：tool 结果的 id 不属于任何批次，必须拒绝而不是带着半条消息跑。"""
    from agent.multi import MultiCheckpoint, run_multi
    llm = ScriptLLM([_asst(content="不会用到")])
    bad = [{"role": "assistant", "content": "",
            "tool_calls": [{"id": "a", "type": "function",
                            "function": {"name": "recall", "arguments": "{}"}},
                           {"id": "b", "type": "function",
                            "function": {"name": "recall", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "zzz", "content": "不属于该批次"}]
    with pytest.raises(ValueError, match="CHECKPOINT_CORRUPT"):
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "r",
                  resume=MultiCheckpoint(messages=bad))


def test_out_of_order_tool_results_are_refused(tmp_path):
    """提交顺序与调用顺序不一致会让 cursor 错位，恢复时会执行错调用。"""
    from agent.multi import MultiCheckpoint, run_multi
    llm = ScriptLLM([_asst(content="不会用到")])
    bad = [{"role": "assistant", "content": "",
            "tool_calls": [{"id": "a", "type": "function",
                            "function": {"name": "recall", "arguments": "{}"}},
                           {"id": "b", "type": "function",
                            "function": {"name": "recall", "arguments": "{}"}}]},
           {"role": "tool", "tool_call_id": "b", "content": "先提交了 b"}]
    with pytest.raises(ValueError, match="CHECKPOINT_CORRUPT"):
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "r",
                  resume=MultiCheckpoint(messages=bad))


def test_partially_committed_batch_is_a_valid_resume_point():
    """对照：只提交了前一部分的批次是**合法**恢复点，不能当成损坏（§10.2 的 cursor 语义）。"""
    from agent.multi import _messages_wellformed, _pending_batch
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "a", "type": "function",
                             "function": {"name": "recall", "arguments": "{}"}},
                            {"id": "b", "type": "function",
                             "function": {"name": "recall", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "已提交 a"}]
    assert _messages_wellformed(msgs) is True
    calls, cursor = _pending_batch(msgs)
    assert [c["id"] for c in calls] == ["a", "b"] and cursor == 1
    # 全部提交完就不再有悬空批次
    msgs.append({"role": "tool", "tool_call_id": "b", "content": "已提交 b"})
    assert _pending_batch(msgs) is None


def test_state_restore_rejects_other_schema_version(tmp_path):
    from agent.analysis_state import AnalysisState
    with pytest.raises(ValueError, match="CHECKPOINT_INCOMPATIBLE"):
        AnalysisState.restore({"contract": {"query": "q"}, "schema_version": "as0"},
                              tmp_path / "a")


def test_result_store_rejects_other_schema_version(tmp_path):
    from agent.loop import make_run_ctx
    ctx = make_run_ctx("data/finsights.duckdb", None, trace_path=None)
    with pytest.raises(ValueError, match="CHECKPOINT_INCOMPATIBLE"):
        ctx.store.import_state({"schema_version": "rs0", "items": []})


# ---------------- 控制信号 ----------------

class Ctrl:
    """`pause_after` = 前 N 次检查放行，第 N+1 次才暂停，用来制造"跑了一段才暂停"。"""

    def __init__(self, cancel=False, pause=False, pause_after=0):
        self.cancel, self.pause, self.pause_after = cancel, pause, pause_after
        self._n = 0

    def check_execution_right(self):
        self._n += 1

    def should_cancel(self):
        return self.cancel

    def should_pause(self):
        return self.pause and self._n > self.pause_after


def test_pause_saves_checkpoint_before_raising(tmp_path):
    from agent.multi import RunHalted, run_multi
    llm = ScriptLLM([_asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
                     _plan_reply()])
    sink = Sink()
    with pytest.raises(RunHalted) as ei:
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "p", control=Ctrl(pause=True),
                  checkpoint_sink=sink)
    assert ei.value.kind == "paused"
    assert sink.reasons[-1] == "paused", "暂停必须先留下可恢复边界再抛"
    assert sink.last is not None and sink.last.messages


def test_paused_checkpoint_is_actually_resumable(tmp_path):
    """暂停留下的检查点要能真的继续跑，否则"可恢复"是空话。"""
    from agent.multi import RunHalted, run_multi
    llm1 = ScriptLLM([_asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
                      _plan_reply()])
    sink = Sink()
    with pytest.raises(RunHalted):
        run_multi("测试题", _ctx(tmp_path, llm1, "pa"), llm1, tmp_path / "p",
                  control=Ctrl(pause=True, pause_after=2), checkpoint_sink=sink)
    ckpt = sink.last
    assert ckpt.next_step == 2 and ckpt.state["latest_revision"] == 1

    llm2 = ScriptLLM([_asst([_call("t2", "execute_analysis", {"node_id": "n1"})]),
                      _work_reply("n1 完成"), _asst(content="停"), _asst(content="停")])
    ctx2 = _ctx(tmp_path, llm2, "pb")
    run_multi("测试题", ctx2, llm2, tmp_path / "p", resume=ckpt)
    assert _worker_starts(ctx2).get("n1") == 1
    assert "plan_analysis" not in llm2.tool_names_asked(len(ckpt.messages)), "恢复沿用既有任务图"


def test_cancel_stops_before_next_model_call(tmp_path):
    from agent.multi import RunHalted, run_multi
    llm = ScriptLLM([_asst([_call("t1", "plan_analysis", {"action": "create", "reason": "首轮"})]),
                     _plan_reply()])
    with pytest.raises(RunHalted) as ei:
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "c", control=Ctrl(cancel=True))
    assert ei.value.kind == "cancelled"
    assert llm.calls == [], "取消后不能再发模型请求"


def test_lost_execution_right_stops_before_model_call(tmp_path):
    """失联的旧 Worker 必须立刻停手（§7.1 第 4 条）。"""
    from agent.multi import RunHalted, run_multi

    class NoRight(Ctrl):
        def check_execution_right(self):
            raise RunHalted("no_execution_right", "租约已过期")

    llm = ScriptLLM([_asst(content="不会用到")])
    with pytest.raises(RunHalted) as ei:
        run_multi("测试题", _ctx(tmp_path, llm), llm, tmp_path / "n", control=NoRight())
    assert ei.value.kind == "no_execution_right"
    assert llm.calls == []


# ---------------- 证据往返 ----------------

def test_result_store_checkpoint_roundtrip_is_exact(tmp_path):
    """PRD §10.1：DataFrame 要保存类型/精度/日期/null，不能只存摘要。"""
    from agent.loop import make_run_ctx
    from agent.tools import data as tool_data
    ctx = make_run_ctx("data/finsights.duckdb", None, trace_path=None)
    rids = []
    for name, args in [("list_metrics", {}),
                       ("query_metric", {"metrics": ["inventory"], "companies": ["Lenovo"],
                                        "last_n": 6}),
                       ("working_capital", {"company": "Lenovo",
                                            "periods": ["FY2027Q1", "FY2026Q1"]})]:
        o = tool_data.execute(name, args, ctx.tool_ctx, step=len(rids))
        if o.rid:
            rids.append(o.rid)
    blob = ctx.store.export_state()
    json.dumps(blob)                     # 必须可 JSON 化（要塞进 MySQL JSON 列）

    ctx2 = make_run_ctx("data/finsights.duckdb", None, trace_path=None)
    ctx2.store.import_state(blob)
    assert ctx2.store._n == ctx.store._n
    for rid in rids:
        a, b = ctx.store.get(rid), ctx2.store.get(rid)
        assert a.df.equals(b.df), f"{rid} 恢复后 DataFrame 不等价"
        assert list(map(str, a.df.dtypes)) == list(map(str, b.df.dtypes))
        assert a.digest == b.digest and a.unit == b.unit
    ctx2.store.import_state(blob)         # 幂等
    assert len(ctx2.store.all()) == len(rids)


def test_result_store_frame_encoding_handles_awkward_frames():
    """检查点格式不能在这些形状上崩：崩了就等于丢掉整个检查点。"""
    import numpy as np
    import pandas as pd
    from agent.result_store import _decode_frame, _encode_frame
    cases = {
        "dates/int32/float/inf/bool/None": pd.DataFrame({
            "d": pd.to_datetime(["2026-04-30", "2026-07-31"]),
            "i": np.array([1, 2], dtype="int32"),
            "f": np.array([1.123456789012345678, np.nan]),
            "inf": np.array([np.inf, -np.inf]),
            "b": [True, False], "s": ["x", None]}),
        "list cell (list_metrics dims)": pd.DataFrame({"dims": [["company", "period"], ["a"]]}),
        "duplicate columns": pd.DataFrame([[1.5, 2]], columns=["a", "a"]),
        "non-str columns": pd.DataFrame([[1, 2, 3]], columns=[10, 20, 30]),
        "empty frame": pd.DataFrame(),
        "empty rows": pd.DataFrame({"x": []}),
    }
    for tag, df in cases.items():
        blob = json.loads(json.dumps(_encode_frame(df)))     # 必须是标准 JSON
        back = _decode_frame(blob)
        assert df.equals(back), tag
        assert list(map(str, df.dtypes)) == list(map(str, back.dtypes)), tag


def test_analysis_state_restore_keeps_ids_and_continues_counters(tmp_path):
    from agent.analysis_state import (AnalysisArtifact, AnalysisState, Review,
                                      ReviewFinding, TaskContract)
    s = AnalysisState(TaskContract(query="q"), tmp_path / "a1")
    s.save_plan([{"node_id": "n1", "goal": "g"}], "初版")
    s.add_artifact(AnalysisArtifact(artifact_id="", node_id="n1", plan_revision=1, attempt=1,
                                    execution_status="completed"))
    s.add_review(Review(review_id="", target_type="artifact", target_id="art1",
                        target_version="v1", verdict="accepted",
                        findings=[ReviewFinding(category="other", statement="x")]))

    s2 = AnalysisState.restore(s.snapshot(), tmp_path / "a2")
    assert s2.latest_revision == 1 and s2.counters["artifact"] == 1
    assert s2.counters["review"] == 1
    a2 = s2.add_artifact(AnalysisArtifact(artifact_id="", node_id="n1", plan_revision=1,
                                          attempt=2, execution_status="completed"))
    assert a2.artifact_id == "art2" and "art1" in s2.artifacts
    r2 = s2.add_review(Review(review_id="", target_type="artifact", target_id="art2",
                              target_version="v1", verdict="accepted"))
    assert r2.review_id == "rev2"
    # 落盘文件不是空状态：再从文件恢复一次仍能续上
    again = AnalysisState.restore(
        json.loads((tmp_path / "a2" / "analysis_state.json").read_text(encoding="utf-8")),
        tmp_path / "a3")
    assert again.counters["artifact"] == 2 and len(again.artifacts) == 2
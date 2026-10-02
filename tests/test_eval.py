"""P3 评测框架离线测试（V3.1–V3.6、V3.9、V3.10 + v0 主循环 mock）。

全部离线：mock agent / mock LLM，不联网；真实 v0 运行是 live 检查（scripts/live/）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

import agent.v0 as v0
from eval import compare, runner
from eval.generators.template_l1_l2 import build_questions
from eval.schema import Answer, Claim, Question
from eval.graders import get_grader

DB = Path("data/finsights.duckdb")
_TEST_KEY = "test-key"  # 同 tests/test_llm.py：常量注入，避免 G4 字面量扫描误报


def _load_all() -> list[Question]:
    qs: list[Question] = []
    for f in ("eval/datasets/l1.jsonl", "eval/datasets/l2.jsonl"):
        for ln in Path(f).read_text(encoding="utf-8").splitlines():
            if ln.strip():
                qs.append(Question.model_validate_json(ln))
    return qs


# ---------- V3.1–V3.3 题目 ----------

def test_v3_1_format_and_counts():
    qs = _load_all()
    assert sum(1 for q in qs if q.category == "L1") == 4
    assert sum(1 for q in qs if q.category == "L2") == 6
    assert len({q.id for q in qs}) == 10


def test_v3_2_gold_reproducible():
    con = duckdb.connect(str(DB), read_only=True)
    fresh = {q["id"]: q for q in build_questions(con)}
    con.close()
    for q in _load_all():
        assert q.id in fresh
        assert abs(fresh[q.id]["gold"]["value"] - q.gold.value) < 1e-9, q.id
        if q.gold_sql:  # L1 gold_sql 直接重执行（V3.2 前半）
            c = duckdb.connect(str(DB), read_only=True)
            v = float(c.execute(q.gold_sql).fetchone()[0])
            c.close()
            assert abs(v - q.gold.value) < 0.01, q.id  # gold 存了两位小数


def test_v3_3_coverage():
    qs = _load_all()
    l1 = [q for q in qs if q.category == "L1"]
    assert any("自然季度" in q.question for q in l1), "L1 缺自然季度写法"
    assert any("亿美元" in q.question for q in l1), "L1 缺亿美元单位"
    tags2 = {t for q in qs if q.category == "L2" for t in q.tags}
    assert {"dio", "dso", "gross_margin", "qoq", "peer_compare"} <= tags2


# ---------- V3.4–V3.6 打分器自检（mock agent） ----------

def _mock_agent_factory(qs: list[Question], multiplier: float):
    def mock_agent(q) -> tuple[Answer, dict]:  # 现在直接收 Question 对象（runner 路由 db 用）
        ans = Answer(answer_md="mock 答案", status="answered",
                     claims=[Claim(text="值", value=q.gold.value * multiplier, unit=q.gold.unit)])
        return ans, {"trace": [{"type": "assistant", "step": 0}], "steps": 1,
                     "tool_calls": 1, "sql_errors": 0, "prompt_tokens": 10,
                     "completion_tokens": 5, "latency_s": 0.05}
    return mock_agent


def _run_mock_suite(tmp_path: Path, multiplier: float, run_id: str) -> dict:
    qs = _load_all()
    return runner.run_suite(qs, _mock_agent_factory(qs, multiplier), trials=3,
                            run_id=run_id, agent_name="mock",
                            runs_root=tmp_path, reports_root=tmp_path / "reports",
                            concurrency=4)


def _accs(summary: dict) -> dict[str, float]:
    return {c: s["acc_mean"] for c, s in runner.category_stats(summary).items()}


def test_v3_4_grader_positive(tmp_path):
    s = _run_mock_suite(tmp_path, 1.0, "mock100")
    assert _accs(s) == {"L1": 1.0, "L2": 1.0}
    # runner 产出结构（离线预演 V3.7 的形态）
    assert len(list((tmp_path / "mock100").glob("*_*.jsonl"))) == 30
    assert (tmp_path / "mock100" / "summary.json").exists()
    assert (tmp_path / "reports" / "mock100.md").exists()
    assert "mock" in (tmp_path / "reports" / "leaderboard.md").read_text(encoding="utf-8")


def test_v3_5_grader_negative(tmp_path):
    s = _run_mock_suite(tmp_path, 1.01, "mock101")
    assert _accs(s) == {"L1": 0.0, "L2": 0.0}


def test_v3_6_grader_boundary(tmp_path):
    s = _run_mock_suite(tmp_path, 1.004, "mock1004")
    assert _accs(s) == {"L1": 1.0, "L2": 1.0}


def test_grader_status_and_text():
    q = _load_all()[0]
    g = get_grader("L1")
    ok = Answer(answer_md="x", status="answered",
                claims=[Claim(text="t", value=q.gold.value, unit=q.gold.unit)])
    assert g(q, ok)["correct"]
    refuse = Answer(answer_md="x", claims=[], status="refuse")
    assert not g(q, refuse)["correct"]
    wrong_unit = Answer(answer_md="x", status="answered",
                        claims=[Claim(text="t", value=q.gold.value * 2, unit="days")])
    assert not g(q, wrong_unit)["correct"]


# ---------- V3.9 compare 自比 ----------

def test_v3_9_compare_self_not_significant(tmp_path):
    _run_mock_suite(tmp_path, 1.0, "runX")
    out = compare.compare("runX", "runX", runs_root=tmp_path, reports_root=tmp_path / "reports")
    assert "显著" in out  # 有判定列
    assert "（显著）" not in out and "| 显著" not in out  # 全部为"不显著"


# ---------- V3.10 + v0 主循环（mock LLM） ----------

def test_v3_10_v0_tools_only_two():
    assert [t["function"]["name"] for t in v0.TOOLS] == ["run_sql", "final_answer"]


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(content=None, tool_calls=None):
    tcs = [SimpleNamespace(id=c[0], function=SimpleNamespace(name=c[1], arguments=c[2]))
           for c in (tool_calls or [])]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120))


def _v0_client(script) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(script)))


def test_v0_loop_normal(monkeypatch):
    client = v0.LLMClient(api_key=_TEST_KEY)
    client._client = _v0_client([
        _resp(tool_calls=[("c1", "run_sql", '{"sql": "SELECT 42.0 AS value"}')]),
        _resp(tool_calls=[("c2", "final_answer",
                           json.dumps({"answer_md": "是 42 百万美元",
                                       "claims": [{"text": "存货", "value": 42.0, "unit": "usd_mn"}],
                                       "status": "answered"}))]),
    ])
    ans, info = v0.run("测试题：42 是多少？", llm=client, db_path=DB)
    assert ans.status == "answered" and ans.claims[0].value == 42.0
    assert info["steps"] == 2 and info["tool_calls"] == 2 and info["sql_errors"] == 0
    assert any(ev["type"] == "tool_result" for ev in info["trace"])
    assert info["prompt_tokens"] == 200 and info["completion_tokens"] == 40


def test_v0_loop_sql_error_returns_text(monkeypatch):
    client = v0.LLMClient(api_key=_TEST_KEY)
    client._client = _v0_client([
        _resp(tool_calls=[("c1", "run_sql", '{"sql": "SELECT * FROM no_such_table"}')]),
        _resp(tool_calls=[("c2", "final_answer",
                           json.dumps({"answer_md": "查不到", "claims": [], "status": "refuse"}))]),
    ])
    ans, info = v0.run("坏 SQL 题", llm=client, db_path=DB)
    assert info["sql_errors"] == 1
    assert ans.status == "refuse"
    err_line = [ev for ev in info["trace"] if ev["type"] == "tool_result"][0]
    assert err_line["result"].startswith("SQL 错误")


def test_v0_loop_no_final_answer_refuses():
    client = v0.LLMClient(api_key=_TEST_KEY)
    client._client = _v0_client([_resp(content="我再想想"), _resp(content="我还是不会")])
    ans, info = v0.run("诱导不结束", llm=client, db_path=DB, max_steps=6)
    assert ans.status == "refuse"
    assert info["steps"] <= 6

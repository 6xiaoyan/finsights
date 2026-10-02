"""MVP CLI 离线接线测试（交接 §3.3：CLI 接线/导出完整性/失败状态，全部 mock LLM，不联网）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.cli as cli_mod  # noqa: E402
from agent.llm import ChatResult, ToolCallOut  # noqa: E402

DB = "data/finsights.duckdb"


def _call(cid, name, args):
    return ToolCallOut(id=cid, name=name, arguments=json.dumps(args, ensure_ascii=False))


def _asst(calls=()):
    tc = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
          for c in calls]
    return ChatResult(content=None, tool_calls=list(calls),
                      usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                      message={"role": "assistant", "content": None,
                               **({"tool_calls": tc} if tc else {})})


class FakeLLM:
    model = "mock-test-model"

    def __init__(self, script, repeat_last=False):
        self.script, self.repeat_last = list(script), repeat_last
        self.last = None

    def chat(self, messages, tools=None, **kw):
        if not self.script:
            if self.repeat_last and self.last is not None:
                return self.last
            raise AssertionError("mock 脚本耗尽：循环做了预期之外的事")
        self.last = self.script.pop(0)
        return self.last


def _final(md, claims=(), status="answered"):
    return _asst(calls=[_call("cf", "final_answer",
                              {"answer_md": md, "status": status, "claims": list(claims)})])


QUERY_INV = _asst(calls=[_call("c1", "query_metric",
                               {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2})])


def _run_main(tmp_path, script, monkeypatch, extra=(), repeat_last=False):
    monkeypatch.setattr(cli_mod, "LLMClient",
                        lambda config_path=None: FakeLLM(script, repeat_last))
    argv = ["--question", "联想最近两季存货变化？", "--out-root", str(tmp_path),
            "--max-rpm", "100000", *extra]
    return cli_mod.main(argv)


def _run_dir(tmp_path):
    dirs = list(tmp_path.glob("cli-*"))
    assert len(dirs) == 1
    return dirs[0]


def test_cli_exports_full_package(tmp_path, monkeypatch):
    rc = _run_main(tmp_path, [QUERY_INV, _final("结论已给出，不含数值主张。")], monkeypatch)
    assert rc == 0
    d = _run_dir(tmp_path)
    for f in ("question.json", "answer.md", "trace.jsonl", "results.json", "run_meta.json"):
        assert (d / f).exists(), f
    q = json.loads((d / "question.json").read_text(encoding="utf-8"))
    assert q["question"] and q["as_of"] is None and "当前库" in q["note"]
    events = [json.loads(l) for l in
              (d / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["type"] == "meta" and events[-1]["type"] == "result"
    assert any(e["type"] == "tool_result" for e in events)
    res = json.loads((d / "results.json").read_text(encoding="utf-8"))["results"]
    assert res and res[0]["id"] == "r1" and res[0]["tool"] == "query_metric"
    assert isinstance(res[0]["rows"], list) and len(res[0]["rows"]) > 0
    assert (d / "answer.md").read_text(encoding="utf-8").startswith("# 联想最近两季存货变化？")


def test_cli_run_meta_records_version(tmp_path, monkeypatch):
    _run_main(tmp_path, [QUERY_INV, _final("无数字主张。")], monkeypatch)
    m = json.loads((_run_dir(tmp_path) / "run_meta.json").read_text(encoding="utf-8"))
    for k in ("commit", "dirty", "db_sha256_12", "data_version", "budget", "usage", "model",
              "config"):
        assert k in m
    assert m["model"] == "mock-test-model"
    assert m["disclosure_index"]["path"].endswith("index.json")
    cfg = json.dumps(m["config"], ensure_ascii=False)
    assert "sk-" not in cfg  # 脱敏：不得出现密钥原文


def test_cli_as_of_routes_to_snapshot(tmp_path, monkeypatch):
    made = {}

    def fake_make_snapshot(as_of, **kw):
        made["as_of"] = as_of
        return Path(DB)

    import etl.snapshot
    monkeypatch.setattr(etl.snapshot, "make_snapshot", fake_make_snapshot)
    rc = _run_main(tmp_path, [QUERY_INV, _final("无数字主张。")], monkeypatch,
                   extra=["--as-of", "2024-06-30"])
    assert rc == 0
    assert made["as_of"].isoformat() == "2024-06-30"
    q = json.loads((_run_dir(tmp_path) / "question.json").read_text(encoding="utf-8"))
    assert "快照" in q["note"] and q["as_of"] == "2024-06-30"


def test_cli_unverified_exits_2(tmp_path, monkeypatch, capsys):
    # 数字主张与引用行不符 → 核验拒绝修正穷尽 → 退出码 2，不得报成核验通过
    bad = {"text": "存货", "value": 999999999.0, "unit": "usd_mn", "ref": "r1"}
    rc = _run_main(tmp_path, [QUERY_INV, _final("存货为 999999999 百万美元。", claims=[bad])],
                   monkeypatch, repeat_last=True)
    assert rc == 2
    out = capsys.readouterr().out
    assert "核验未通过" in out
    m = json.loads((_run_dir(tmp_path) / "run_meta.json").read_text(encoding="utf-8"))
    assert m["verified"] is False


def test_cli_refuse_shows_banner(tmp_path, monkeypatch, capsys):
    rc = _run_main(tmp_path, [_final("预算用尽，拒答。", status="refuse")], monkeypatch)
    assert rc == 3
    assert "拒答" in capsys.readouterr().err


def test_cli_clarify_shows_banner(tmp_path, monkeypatch, capsys):
    rc = _run_main(tmp_path, [_final("请澄清：FY24Q3 是财年季还是自然季？", status="clarify")],
                   monkeypatch)
    assert rc == 3
    assert "澄清" in capsys.readouterr().err


def test_cli_export_row_cap_unit(tmp_path):
    """导出防御上限：超限行截断并记 truncated，不无界倾倒。"""
    df = pd.DataFrame({"v": range(cli_mod.MAX_EXPORT_ROWS + 50)})
    stored = [SimpleNamespace(id="r1", tool="run_sql", args={}, sql="SELECT 1", unit="",
                              data_version="dv", created_step=1, digest="d", df=df)]
    ctx = SimpleNamespace(store=SimpleNamespace(all=lambda: stored, data_version="dv"),
                          budget=SimpleNamespace(max_steps=1, max_tokens=1, max_wall_s=1.0))
    out = SimpleNamespace(answer=SimpleNamespace(answer_md="x", claims=[], status="answered"),
                          verified=True)
    info = {"trace": [], "steps": 1, "prompt_tokens": 1, "completion_tokens": 1,
            "latency_s": 0.1, "sql_errors": 0, "refuse_reason": None}
    client = SimpleNamespace(model="m")
    d = tmp_path / "cli-x"
    cli_mod._export_package(d, "q", None, Path(DB), client, "config.yaml", ctx, out, info, "x")
    payload = json.loads((d / "results.json").read_text(encoding="utf-8"))
    assert payload["any_truncated"] is True
    rec = payload["results"][0]
    assert len(rec["rows"]) == cli_mod.MAX_EXPORT_ROWS
    assert rec["truncated"] == 50

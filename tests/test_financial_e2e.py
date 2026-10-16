"""E2E 评测与数据集工具测试（离线，不调用真实 LLM）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.contracts import NumericAssertion, ScoringPoint, SourceRef
from eval.financial_e2e.adapter import (answer_body, load_run_artifact, load_trace_events,
                                        parse_claims)
from eval.financial_e2e.generate import _extract_json_array, apply_candidate
from eval.financial_e2e.grader import (aggregate_case, grade_case, verify_numeric)
from eval.financial_e2e.judge import CallableJudge, _extract_json
from eval.financial_e2e.report import summary_report_md
from eval.financial_e2e.schema import CaseRecord, load_dataset, save_dataset


def make_case(case_id="C-001", points=None, nums=None) -> CaseRecord:
    return CaseRecord(
        case_id=case_id, question="联想 FY2026Q1 收入同比多少？",
        scope="联想集团合并口径（usd_mn）", case_revision=1,
        reference_answer="FY2026Q1 收入 18,829.869，同比 +21.9%",
        sources=[SourceRef(title="业绩公佈", url="https://www1.hkexnews.hk/x.pdf",
                           publish_date="2025-08-14", period="FY2026Q1", page=13)],
        scoring_points=points or [
            ScoringPoint(point_id="p1", atomic_judgment="给出 Q1 收入约 18,830 百万美元"),
            ScoringPoint(point_id="p2", atomic_judgment="给出同比约 +22%"),
        ],
        numeric_assertions=nums or [
            NumericAssertion(metric="revenue", company="Lenovo", period="FY2026Q1",
                             value=18829.869, unit="usd_mn"),
        ],
        question_type="multi_period", real_synthetic="real", split="dev",
    )


def test_case_input_excludes_gold():
    c = make_case()
    inp = c.to_input()
    dumped = inp.model_dump()
    assert "reference_answer" not in dumped
    assert "scoring_points" not in dumped
    assert "sources" not in dumped
    assert set(dumped) == {"case_id", "question", "scope", "as_of", "data_refs"}


def test_dataset_roundtrip(tmp_path: Path):
    c = make_case()
    p = save_dataset([c], tmp_path / "cases.jsonl")
    back = load_dataset(p)
    assert len(back) == 1
    assert back[0].case_id == c.case_id
    assert back[0].scoring_points[0].point_id == "p1"


def test_sample_dataset_is_valid():
    cases = load_dataset("eval/datasets/financial_real_v1/cases.jsonl")
    assert len(cases) >= 5
    for c in cases:
        assert c.review_status == "draft"          # 生成器不得自批
        assert c.sources, f"{c.case_id} 缺来源"
        assert c.required_points(), f"{c.case_id} 缺必答点"
    types = {c.question_type for c in cases}
    assert {"multi_period", "attribution", "judgment", "forecast"} <= types


def test_dataset_30_composition_and_no_autapprove():
    cases = load_dataset("eval/datasets/financial_real_v1/cases.jsonl")
    assert len(cases) == 30
    from collections import Counter
    comp = Counter(c.question_type for c in cases)
    assert comp == {"multi_period": 6, "attribution": 10, "judgment": 8, "forecast": 6}
    assert all(c.review_status != "approved" for c in cases)   # 生成器不得自批
    assert all(c.sources for c in cases if not c.blocked)      # 可答题必须有来源
    # 至少 24 题需要多步推理（PRD §5.1）
    multi = sum(1 for c in cases if len(c.reasoning_requirements) >= 2)
    assert multi >= 24
    # 每题有原子得分点与稳定 point_id
    for c in cases:
        ids = [p.point_id for p in c.scoring_points]
        assert ids and len(ids) == len(set(ids)), c.case_id


def test_reused_cases_keep_history():
    cases = {c.case_id: c for c in load_dataset("eval/datasets/financial_real_v1/cases.jsonl")}
    reused = [c for c in cases.values() if c.origin == "reused"]
    assert len(reused) >= 5
    assert all(c.selected_id for c in reused)                  # 保留审核历史来源
    # S01–S07 全部覆盖
    covered = {c.selected_id for c in reused if c.selected_id}
    assert {"S01", "S02", "S03", "S04", "S05", "S06", "S07"} <= covered
    # 复用题一律留在开发集
    assert all(c.split == "dev" for c in reused)


def test_split_and_dataset_meta():
    from collections import Counter
    import json
    cases = load_dataset("eval/datasets/financial_real_v1/cases.jsonl")
    splits = Counter(c.split for c in cases)
    assert splits == {"dev": 20, "holdout": 10}
    meta = json.loads(Path("eval/datasets/financial_real_v1/dataset_meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["n_cases"] == 30
    assert meta["db_sha256_12"]
    assert meta["blocked"] == ["LV-E2E-FY2027Q1-SEGMENT-GAP-001"]


def test_verify_sources_reported_hits():
    """已披露项应能在引用来源页命中（派生值由已披露项计算，不计入）。"""
    from eval.financial_e2e.cli import _is_derived
    from eval.financial_e2e.cli import _source_contains
    from eval.financial_e2e.pageindex import thousands
    cases = load_dataset("eval/datasets/financial_real_v1/cases.jsonl")
    misses = []
    for c in cases:
        for a in c.numeric_assertions:
            if _is_derived(a.metric, a.formula):
                continue
            if not any(_source_contains(s, thousands(a.value)) for s in c.sources):
                misses.append(f"{c.case_id}:{a.metric}:{a.period}")
    assert not misses, f"已披露项未命中来源页: {misses}"


def test_forecast_case_has_as_of_and_truth_isolated():
    cases = {c.case_id: c for c in load_dataset("eval/datasets/financial_real_v1/cases.jsonl")}
    fc = [c for c in cases.values() if c.question_type == "forecast"]
    assert fc, "缺少预测题"
    for c in fc:
        assert c.as_of, "预测题必须指定 as_of"
        # forecast_truth（真实实绩）可缺失：目标期间尚未披露时留空，不伪造
        # 不得进入准入 Agent 的内容
        assert "forecast_truth" not in c.to_input().model_dump()


def test_parse_claims_and_answer_body():
    md = ("# 联想 FY2026Q1 收入\n- status: answered\n- 核验: 通过\n\n"
          "收入为 18,829.869 百万美元。\n\n## 引用\n"
          "- 收入  [18829.869 usd_mn → r2]\n"
          "\n- 概念字典版本: v1\n")
    claims = parse_claims(md)
    assert len(claims) == 1
    assert claims[0].value == 18829.869
    assert claims[0].ref == "r2"
    body = answer_body(md)
    assert "18,829.869 百万美元" in body
    assert "引用" not in body
    assert "status: answered" not in body


def _write_run(tmp_path: Path, status: str, answer_md: str) -> Path:
    d = tmp_path / "cli-test"
    d.mkdir()
    (d / "run_meta.json").write_text(json.dumps(
        {"run_id": "test", "status": status, "usage": {"steps": 3, "prompt_tokens": 10}}),
        encoding="utf-8")
    (d / "answer.md").write_text(answer_md, encoding="utf-8")
    (d / "trace.jsonl").write_text("\n".join([
        json.dumps({"type": "meta", "run_id": "test"}),
        json.dumps({"type": "tool_skipped", "tool": "query_financial_data", "ok": None}),
        json.dumps({"type": "tool_result", "tool": "query_metric", "ok": True, "rid": "r1"}),
    ]) + "\n", encoding="utf-8")
    return d


def test_adapter_undelivered_has_null_final_answer(tmp_path: Path):
    d = _write_run(tmp_path, "refuse", "# q\n- status: refuse\n\n未完成\n")
    art = load_run_artifact(d, "C-001")
    assert art.final_status == "refuse"
    assert art.final_answer is None            # 未提交 → 最终答案为空
    assert art.process_objects["answer_md_raw"]  # 原文仍保留供诊断
    assert art.usage["steps"] == 3


def test_trace_events_map_types(tmp_path: Path):
    d = _write_run(tmp_path, "answered", "x")
    events = load_trace_events(d)
    assert [e.event_type for e in events] == ["tool_skipped", "tool_result"]
    skipped = events[0]
    assert skipped.status is None               # 跳过不记为成功
    assert events[1].result_ids == ["r1"]


def test_verify_numeric_measured():
    c = make_case()
    res = verify_numeric(c, "收入为 18,829.869 百万美元，同比 +21.9%。")
    assert res[0]["ok"] is True
    assert res[0]["method"].startswith("measured")


def test_verify_numeric_model_judged_when_absent():
    c = make_case()
    res = verify_numeric(c, "无法提取具体数字。")
    assert res[0]["ok"] is None
    assert res[0]["method"] == "model_judged"


def test_grade_case_undelivered_is_missing_no_judge():
    c = make_case()
    called = {"n": 0}

    def pf(q, p, f, a):
        called["n"] += 1
        return "met"
    res = grade_case(c, "", CallableJudge(point_fn=pf))
    assert res["aggregate"]["delivered"] is False
    assert res["aggregate"]["required_missing"] == 2
    assert res["aggregate"]["required_hit_rate"] == 0.0
    assert called["n"] == 0                     # 未交付不调用 judge


def test_grade_case_with_judge_and_optional_not_in_denominator():
    pts = [ScoringPoint(point_id="p1", atomic_judgment="x"),
           ScoringPoint(point_id="p2", atomic_judgment="y", required="optional")]
    c = make_case(points=pts)
    res = grade_case(c, "答案", CallableJudge(point_fn=lambda q, p, f, a: "met"))
    agg = res["aggregate"]
    assert agg["required_total"] == 1
    assert agg["optional_total"] == 1
    assert agg["required_hit_rate"] == 1.0
    assert agg["optional_hit_rate"] == 1.0


def test_aggregate_unknown_keeps_denominator_and_range():
    c = make_case()
    pts = [ScoringPoint(point_id="p1", atomic_judgment="x"),
           ScoringPoint(point_id="p2", atomic_judgment="y")]
    from eval.contracts import GradeResult

    def g(pid, verdict):
        return GradeResult(grader_name="point", grader_version="v", case_id="C",
                           run_id="r", object_id=pid, verdict=verdict)
    from eval.contracts import GradeResult as GR
    whole = GR(grader_name="whole", grader_version="v", case_id="C", run_id="r",
               object_id="whole", verdict="met")
    agg = aggregate_case(c, [g("p1", "met"), g("p2", "unknown")], whole, [], delivered=True)
    assert agg["required_unknown"] == 1
    assert agg["required_hit_rate"] == 0.5
    assert agg["required_hit_range"] == [0.5, 1.0]


def test_judge_extract_json_variants():
    assert _extract_json('{"verdict": "met"}')["verdict"] == "met"
    assert _extract_json('前缀 ```json\n{"verdict": "missing"}\n``` 后缀')["verdict"] == "missing"
    assert _extract_json('blah {"verdict": "unknown"} tail')["verdict"] == "unknown"


def test_generate_apply_candidate_forces_draft():
    c = make_case()
    pts = [ScoringPoint(point_id="p1", atomic_judgment="z")]
    out = apply_candidate(c, pts, {"model": "m"})
    assert out.review_status == "draft"
    assert out.scoring_points[0].point_id == "p1"
    assert out.generator["model"] == "m"


def test_generate_points_json_array_parse():
    arr = _extract_json_array('```json\n[{"point_id": "p1", "atomic_judgment": "a"}]\n```')
    assert arr[0]["point_id"] == "p1"


class _FakeClient:
    model = "fake-gen"

    class _R:
        content = '[{"point_id": "p1", "atomic_judgment": "给出收入"}]'
        usage = {"prompt_tokens": 5, "completion_tokens": 6}

    def chat(self, messages, role="agent"):  # noqa: ARG002
        return self._R()


def test_generate_points_from_fake_client():
    from eval.financial_e2e.generate import generate_points
    c = make_case()
    pts, prov = generate_points(c, _FakeClient())
    assert pts[0].point_id == "p1"
    assert prov["model"] == "fake-gen"
    assert prov["prompt_version"]


def test_cli_validate_ok(capsys):
    from eval.financial_e2e.cli import main
    rc = main(["prog", "validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cases=" in out


def test_cli_validate_rejects_blocked_without_reason(tmp_path: Path):
    c = make_case().model_copy(update={"blocked": True, "blocked_reason": None})
    p = save_dataset([c], tmp_path / "cases.jsonl")
    from eval.financial_e2e.cli import main
    rc = main(["prog", "validate", "--cases", str(p)])
    assert rc == 1


def test_review_approve_requires_reviewer(tmp_path: Path):
    p = save_dataset([make_case()], tmp_path / "cases.jsonl")
    from eval.financial_e2e.cli import main
    with pytest.raises(SystemExit):
        main(["prog", "review", "--cases", str(p), "--case-id", "C-001",
              "--status", "approved"])
    # 提供 reviewer 后成功，并写 review_log.md
    rc = main(["prog", "review", "--cases", str(p), "--case-id", "C-001",
               "--status", "approved", "--reviewer", "human-a"])
    assert rc == 0
    assert load_dataset(p)[0].review_status == "approved"
    assert (tmp_path / "review_log.md").exists()


def test_summary_report_flags_blocked():
    c = make_case().model_copy(update={"blocked": True, "blocked_reason": "缺分部数据"})
    result = grade_case(c, "", CallableJudge(point_fn=lambda q, p, f, a: "met"))
    items = [{"case": c, "result": result, "run_id": "r1", "final_status": "answered"}]
    md = summary_report_md(items, "ds", "callable", None, ["r1"])
    assert "blocked" in md
    assert "缺分部数据" in md


def test_cli_score_run_and_report(tmp_path: Path):
    from eval.financial_e2e.cli import main
    cases_path = save_dataset([make_case()], tmp_path / "cases.jsonl")
    run_dir = _write_run(tmp_path, "answered",
                         "# q\n- status: answered\n\n收入 18,829.869 百万美元，同比约 +22%。\n")
    out = tmp_path / "out"
    rc = main(["prog", "score-run", "--run-dir", str(run_dir), "--case-id", "C-001",
               "--cases", str(cases_path), "--out", str(out), "--judge-fake"])
    assert rc == 0
    results = out / "results.jsonl"
    assert results.exists()
    rc = main(["prog", "report", "--results", str(results), "--cases", str(cases_path),
               "--out", str(out / "summary.md")])
    assert rc == 0
    assert (out / "summary.md").exists()

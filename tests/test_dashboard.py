"""看板规则与索引测试（dashboard_design.md §2.2/§6.1）。执行为离线，不发 API。"""
from __future__ import annotations

from dashboard.rules import classify, summarize


def _result(status="answered", verified=True, refuse_reason=None):
    return {"type": "result", "status": status, "verified": verified,
            "refuse_reason": refuse_reason}


def test_r3_verification_failure():
    ev = [{"type": "assistant", "step": 1}, {"type": "stop_reject", "feedback": "数字 92 无 claim"},
          _result("answered", verified=False)]
    flags = classify(None, ev, {"usage": {}}, {})
    ids = [f["rule_id"] for f in flags]
    assert "R3" in ids and "W1" in ids  # 核验失败 + 打回过程警告并存
    label, red = summarize(flags)
    assert red and "核验失败" in label


def test_r2_budget_refusal_is_red():
    ev = [_result("refuse", verified=False, refuse_reason="步数预算耗尽")]
    flags = classify(None, ev, {"usage": {"refuse_reason": "步数预算耗尽"}}, {})
    assert any(f["rule_id"] == "R2" for f in flags)
    assert not any(f["rule_id"] == "W2" for f in flags)  # 预算耗尽不归"待人工判断"
    label, red = summarize(flags)
    assert red and "预算耗尽" in label


def test_w2_clarify_is_yellow():
    ev = [_result("clarify", verified=False, refuse_reason=None)]
    flags = classify(None, ev, {"usage": {}}, {})
    assert any(f["rule_id"] == "W2" for f in flags)
    _, red = summarize(flags)
    assert not red  # 合法澄清不标红


def test_neutral_answered_verified():
    flags = classify(None, [_result("answered", verified=True)], {"usage": {}}, {})
    assert flags == []  # 中性：未发现自动失败，业务质量仍待人工


def test_r1_runtime_error():
    ev = [{"type": "error", "error": "Error code: 429"}]
    flags = classify(None, ev, {"usage": {"refuse_reason": "运行异常: 429"}}, {})
    assert any(f["rule_id"] == "R1" for f in flags)


def test_r6_incomplete_package():
    files = {"question.json": True, "answer.md": False, "results.json": True,
             "run_meta.json": True, "trace.jsonl": True}
    flags = classify(_result("refuse"), [], {"usage": {}, "status": "refuse"}, files)
    assert any(f["rule_id"] == "R6" for f in flags)


def test_r4_scoring_failure_only_with_record():
    rec = {"correct": False, "status": "answered", "detail": "标签不符"}
    flags = classify(rec, [_result("answered", verified=True)], {"usage": {}}, {})
    assert any(f["rule_id"] == "R4" for f in flags)
    # 无评分记录时不出现 R4
    assert not any(f["rule_id"] == "R4" for f in classify(None, [_result("answered", True)], {"usage": {}}, {}))


def test_process_warning_not_red():
    ev = [{"type": "tool_result", "ok": False}, _result("answered", verified=True)]
    flags = classify(None, ev, {"usage": {}}, {})
    label, red = summarize(flags)
    assert not red and "工具失败" in label  # 过程问题保留，默认不计入红色 bad case

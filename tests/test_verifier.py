"""P4.4 验收：verifier 护栏（verify 6.2：V4.17 六用例 + V4.18 反馈具体性）。全部离线。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.loop import make_run_ctx  # noqa: E402
from agent.tools.base import FinalAnswerArgs  # noqa: E402
from agent.verifier import verify_answer  # noqa: E402
from eval.schema import Claim  # noqa: E402
from test_loop import DB, MockLLM, asst, call, _claim, _final, _seed  # noqa: E402

INV = {"metrics": ["inventory"], "companies": ["Lenovo"], "last_n": 2}


def _seeded():
    """返回 (ctx, r1 中最新存货值)。"""
    ctx = make_run_ctx(DB, MockLLM([]))
    out = _seed(ctx, "query_metric", INV)
    return ctx, float(out.stored.df["inventory"].iloc[-1])


def _args(md, claims):
    return FinalAnswerArgs(answer_md=md, claims=claims, status="answered")


# ---------------- V4.17 用例

def test_v4_17_1_correct_passes():
    ctx, v = _seeded()
    verdict = verify_answer(_args(f"联想最新存货 {v:.6g} 百万美元（r1）。",
                                  [Claim(text="最新存货", value=v, unit="usd_mn", ref="r1")]), ctx)
    assert verdict.ok, verdict.feedback


def test_v4_17_2_wrong_claim_value_fails():
    ctx, v = _seeded()
    bad = v * 1.5
    verdict = verify_answer(_args(f"联想最新存货 {bad:.6g} 百万美元（r1）。",
                                  [Claim(text="最新存货", value=bad, unit="usd_mn", ref="r1")]), ctx)
    assert not verdict.ok
    assert "claim 1" in verdict.feedback and "r1" in verdict.feedback


def test_v4_17_3_unquoted_number_fails():
    ctx, v = _seeded()
    verdict = verify_answer(_args(
        f"联想最新存货 {v:.6g} 百万美元（r1）。此外现金约为 9876543.21 百万美元。",
        [Claim(text="最新存货", value=v, unit="usd_mn", ref="r1")]), ctx)
    assert not verdict.ok
    assert "9876543.21" in verdict.feedback and "没有对应的 claim" in verdict.feedback


def test_v4_17_4_labels_not_false_positive():
    ctx, _ = _seeded()
    verdict = verify_answer(_args(
        "与 FY24Q2 相比，2024 年的第 3 点结论（详见 r1），另外两点见 r2。", []), ctx)
    assert verdict.ok, verdict.feedback


def test_v4_17_5_missing_rid_fails():
    ctx, v = _seeded()
    verdict = verify_answer(_args(f"结论 {v:.6g}（r42）。",
                                  [Claim(text="x", value=v, unit="usd_mn", ref="r42")]), ctx)
    assert not verdict.ok
    assert "r42 不存在" in verdict.feedback


def test_v4_17_6_identity_violation_flagged(tmp_path):
    db = tmp_path / "bad.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE facts(company_id VARCHAR, fiscal_year INT, fiscal_quarter INT,"
                " account_code VARCHAR, value DOUBLE, version VARCHAR)")
    con.execute("CREATE TABLE periods(company_id VARCHAR, fiscal_year INT, fiscal_quarter INT,"
                " period_end DATE, calendar_quarter VARCHAR, days INT)")
    con.execute("INSERT INTO periods VALUES ('Lenovo',2024,1,DATE '2024-03-31','2024Q1',90)")
    con.execute("INSERT INTO facts VALUES "
                "('Lenovo',2024,1,'total_assets',100,'original'),"
                "('Lenovo',2024,1,'total_liabilities',60,'original'),"
                "('Lenovo',2024,1,'total_equity',30,'original')")   # A ≠ L+E
    con.close()
    ctx = make_run_ctx(str(db), MockLLM([]))
    _seed(ctx, "query_metric", {"metrics": ["total_assets"], "companies": ["Lenovo"], "last_n": 1})
    verdict = verify_answer(_args("联想 FY24Q1 总资产 100 百万美元（r1）。",
                                  [Claim(text="总资产", value=100.0, unit="usd_mn", ref="r1")]), ctx)
    assert not verdict.ok
    assert "勾稽违反" in verdict.feedback and "A-L-E" in verdict.feedback


def test_v4_17_7_attribution_label_required():
    ctx, v = _seeded()
    ctx.loaded_skills["attribution"] = "正文…"
    md0 = f"联想最新存货 {v:.6g} 百万美元（r1）。变化是经营性的。"
    claims = [Claim(text="最新存货", value=v, unit="usd_mn", ref="r1")]
    verdict = verify_answer(_args(md0, claims), ctx)
    assert not verdict.ok and "根因标签" in verdict.feedback
    verdict2 = verify_answer(_args(md0 + " label=company_specific。", claims), ctx)
    assert verdict2.ok, verdict2.feedback


# ---------------- V4.18 反馈具体到 claim / rid / 期望值与实际值

def test_v4_18_feedback_is_specific():
    ctx, v = _seeded()
    bad = v * 1.5
    verdict = verify_answer(_args(f"联想最新存货 {bad:.6g} 百万美元（r1）。",
                                  [Claim(text="最新存货", value=bad, unit="usd_mn", ref="r1")]), ctx)
    fb = verdict.feedback
    assert "claim 1" in fb                       # 哪个 claim
    assert "r1" in fb                            # 哪个 rid
    assert f"期望 {bad}" in fb                   # claim 引用的值
    assert "最接近的是" in fb                    # rid 中最接近的候选值
    assert "容差 0.5%" in fb


# ---------------- 主循环集成：错误答案被打回 → 修正后通过（默认 verifier）

def test_loop_rejects_then_accepts():
    ctx = make_run_ctx(DB, MockLLM([asst(calls=[call("c1", "query_metric", INV)])]))
    seeded = _seed(ctx, "query_metric", INV)
    v = float(seeded.stored.df["inventory"].iloc[-1])
    bad = v * 1.5
    ctx.llm.script += [
        asst(calls=[_final(cid="cf1", md=f"联想最新存货 {bad:.6g} 百万美元（r1）。",
                           claims=[_claim(bad, "r1")])]),
        asst(calls=[_final(cid="cf2", md=f"联想最新存货 {v:.6g} 百万美元（r1）。",
                           claims=[_claim(v, "r1")])]),
    ]
    from agent.loop import run
    out = run("联想最新存货？", ctx)
    rejects = [e for e in ctx.trace.events if e["type"] == "stop_reject"]
    assert len(rejects) == 1 and "claim 1" in rejects[0]["feedback"]
    reject_msg = [m for m in ctx.llm.views[2] if m["role"] == "tool"][-1]
    assert "最接近的是" in reject_msg["content"]   # 反馈定向送回模型
    assert out.verified and out.answer.status == "answered"

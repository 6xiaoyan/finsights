"""P4.3 验收：skills 渐进加载 + system prompt（verify 6.2：V4.13–V4.16）。全部离线。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import skills as sk  # noqa: E402
from agent.loop import build_system_prompt, make_run_ctx, run  # noqa: E402
from test_loop import DB, MockLLM, asst, call, _final  # noqa: E402

ALL_SKILLS = {"attribution", "retrieval", "working_capital"}


# ---------------- V4.13 skill 渐进加载：清单在 prompt，正文不在

def test_v4_13_prompt_lists_names_and_descriptions_only():
    prompt = build_system_prompt()
    metas = sk.discover_skills()
    assert set(metas) == ALL_SKILLS
    for name, m in metas.items():
        assert f"- {name}: {m.description}" in prompt, name
    for name, m in metas.items():
        for line in m.body().splitlines():
            line = line.strip()
            if len(line) >= 12:   # 正文的任何一句完整话都不能出现在 system prompt
                assert line not in prompt, f"{name}: {line[:30]} 泄漏进 system prompt"


# ---------------- V4.14 load_skill：正文一致；不存在的 skill → 错误+可用列表

def test_v4_14_body_matches_skill_md():
    text = (sk.SKILLS_ROOT / "attribution" / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip()
    assert sk.load_skill_body("attribution") == body


def test_v4_14_unknown_skill_lists_available():
    try:
        sk.load_skill_body("forecast")
        raise AssertionError("应抛 KeyError")
    except KeyError as e:
        for name in ALL_SKILLS:
            assert name in str(e)


def test_v4_14_load_skill_through_loop():
    body = sk.load_skill_body("attribution")
    ctx = make_run_ctx(DB, MockLLM([
        asst(calls=[call("c1", "load_skill", {"name": "attribution"})]),
        asst(calls=[_final(md="按归因流程完成了分析，结论标签 seasonal。")]),
    ]))
    out = run("联想 FY24Q2 存货为什么上升？", ctx)
    tool_msg = [m for m in ctx.llm.views[1] if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "c1"
    assert body in tool_msg["content"]          # 全文进入上下文（供后续步骤遵循）
    assert ctx.loaded_skills == {"attribution": body}
    assert out.answer.status == "answered"


# ---------------- V4.15 attribution skill 内容（plan 6.7 七步骤 + 规则 + 枚举）

def test_v4_15_attribution_content():
    body = sk.load_skill_body("attribution")
    steps = ["variance", "seasonal_check", "peer_compare", "对手科目",
             "get_filing_notes", "contribution", "根因标签"]
    for kw in steps:
        assert kw in body, f"缺少步骤要素: {kw}"
    # V4.15 REVIEWED-FAIL 修订后：no_anomaly 原则保留但改为第七步按优先级判定，
    # 禁止第二步提前结束；新增标签优先级、可判定条件与观察性表述纪律
    assert "no_anomaly" in body
    assert "标签判定优先级" in body
    assert "不得提前结束" in body
    assert "观察性分类" in body
    assert "|z| < 2 且分位 < 95%" in body
    for label in ["no_anomaly", "seasonal", "industry_wide", "company_specific",
                  "mix_shift", "reclassification"]:
        assert label in body, f"缺少根因标签: {label}"
    # V5.10 首轮 live 发现输出格式漂移：技能与打分器（eval/graders/l3）约定统一为
    # 独立行「根因标签: <label>」+ 结构化 confidence 字段
    assert "根因标签: <label>" in body and '"confidence"' in body   # 归因答案格式
    assert "可检验假设" in body                    # 业务软先验小节（PRD 4.3）


# ---------------- V4.16 system prompt 稳定：不同 as_of/问题/db → 逐字节相同

def test_v4_16_system_prompt_byte_stable(tmp_path):
    fake_db = tmp_path / "other.duckdb"
    fake_db.write_bytes(b"not-a-real-db")       # 仅用于 data_version，不触库
    prompts = []
    scripts = [
        (None, "问题一"),
        (__import__("datetime").date(2025, 6, 30), "问题二"),
    ]
    for as_of, q in scripts:
        ctx = make_run_ctx(DB, MockLLM([asst(calls=[_final(md="固定口径解释（无数字）。")])]),
                           as_of=as_of)
        run(q, ctx)
        prompts.append(ctx.llm.views[0][0]["content"])
    ctx2 = make_run_ctx(str(fake_db), MockLLM([asst(calls=[_final(md="固定口径解释（无数字）。")])]))
    run("问题三", ctx2)
    prompts.append(ctx2.llm.views[0][0]["content"])
    digests = {hashlib.sha256(p.encode("utf-8")).hexdigest() for p in prompts}
    assert len(digests) == 1
    # 第一条消息恒为 system，且不含动态内容
    assert all(v[0]["role"] == "system" for v in ctx.llm.views + ctx2.llm.views)

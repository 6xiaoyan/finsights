"""P4.4：护栏 verifier（plan 6.8 stop 钩子）——final_answer 的四项核验。

1. 引用核验：每个 claim 的 ref 必须存在于 Result Store，value 能在该结果中找到
   （store.check_value，容差 0.5%，处理 百万/亿、%/小数 换算）。
2. 未引用数字扫描：answer_md 里的"真数值"（排除年份/季度标签/序号/rid 引用，规则同 hooks）
   都必须对应某个 claim。
3. 勾稽校验：claims 涉及结果中的 (公司, 期间) 跑 check_identities，违反项作为问题返回。
4. 归因题额外检查：加载过 attribution skill 时，答案必须含枚举内的根因标签。

反馈必须具体到哪个 claim、哪个 rid、期望值与实际值（V4.18），供模型定向修正。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent.hooks import Verdict, bare_numbers
from agent.tools import data as tool_data

if TYPE_CHECKING:
    from agent.loop import RunContext
    from agent.tools.base import FinalAnswerArgs

ATTRIBUTION_LABELS = ["no_anomaly", "seasonal", "industry_wide",
                      "company_specific", "mix_shift", "reclassification"]
_MATCH_FACTORS = (1.0, 100.0, 0.01)   # 文本数字 vs claim.value 的单位习惯换算（%/亿 ↔ 小数/百万）


def _num(token: str) -> float:
    return float(token.replace(",", "").rstrip("%"))


def _matches_claim(tok: str, claim_value: float) -> bool:
    x = _num(tok)
    if x == 0:
        return claim_value == 0
    for f in _MATCH_FACTORS:
        v = claim_value * f
        if v and abs(v - x) / abs(x) <= 0.005:
            return True
    return False


def _identity_problems(pairs: dict[str, list[str]], ctx: "RunContext") -> list[str]:
    """claims 涉及的 (公司, 期间) 跑勾稽校验；通过时不产生问题，也不打扰模型。"""
    problems: list[str] = []
    for company, periods in pairs.items():
        out = tool_data.execute("check_identities",
                                {"company": company, "periods": sorted(set(periods))},
                                ctx.tool_ctx, step=-1)
        if not out.ok or out.stored is None:
            problems.append(f"勾稽校验无法执行（{company}）：{out.text}")
            continue
        df = out.stored.df
        if "violation" in df.columns:
            problems += [f"勾稽违反：{v}" for v in df["violation"].astype(str)]
    return problems


def verify_answer(args: "FinalAnswerArgs", ctx: "RunContext") -> Verdict:
    if args.status != "answered":
        return Verdict(ok=True)   # clarify/refuse 不主张数据结论，不做数字核验

    problems: list[str] = []

    # 1. 引用核验
    for i, c in enumerate(args.claims, start=1):
        if not c.ref:
            problems.append(f"claim {i}（{c.text[:30]}… value={c.value}）没有引用 result_id")
            continue
        ok, best, msg = ctx.store.check_value(c.ref, c.value, c.unit)
        if not ok:
            detail = msg or (f"{c.ref} 中最接近的是 {best}，期望 {c.value}（单位 {c.unit}，容差 0.5%）"
                             if best is not None else f"{c.ref} 中找不到 {c.value}")
            problems.append(f"claim {i} 的数值 {c.value}（{c.unit}）在 {c.ref} 中找不到——{detail}")

    # 2. 未引用数字扫描
    for tok in bare_numbers(args.answer_md):
        if not any(_matches_claim(tok, c.value) for c in args.claims):
            problems.append(f"正文中的数字 {tok} 没有对应的 claim（应来自工具结果并在 claims 里引用 rid）")

    # 3. 勾稽校验（claims 引用的结果中出现的 公司×期间）
    pairs: dict[str, list[str]] = {}
    for c in args.claims:
        res = ctx.store.get(c.ref) if c.ref else None
        if res is None or res.df.empty:
            continue
        cols = set(res.df.columns)
        if not {"company", "fiscal_year", "fiscal_quarter"} <= cols:
            continue
        for _, r in res.df[["company", "fiscal_year", "fiscal_quarter"]].dropna().iterrows():
            try:
                pairs.setdefault(str(r["company"]), []).append(
                    f"FY{int(r['fiscal_year']) % 100}Q{int(r['fiscal_quarter'])}")
            except (TypeError, ValueError):
                continue
    problems += _identity_problems(pairs, ctx)

    # 4. 归因题额外检查
    if "attribution" in ctx.loaded_skills:
        if not any(lbl in args.answer_md for lbl in ATTRIBUTION_LABELS):
            problems.append("这是归因问题：answer_md 必须包含枚举内的根因标签"
                            f"（{'/'.join(ATTRIBUTION_LABELS)} 之一）")

    if problems:
        feedback = "答案未通过核验，请修正后重新提交 final_answer：\n" + "\n".join(
            f"{j}. {p}" for j, p in enumerate(problems, start=1))
        return Verdict(ok=False, feedback=feedback)
    return Verdict(ok=True)

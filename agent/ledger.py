"""P4.5a：证据账本（plan 6.6.3 L4）——完全由代码生成的假设检验记录。

设计要点：
- 账本文本的每个数字都直接取自 Result Store 的数值单元格（或 digest），
  因此 `verify_ledger` 能像核验答案一样核验账本（V4.33）。
- 分析类工具（variance/seasonal_check/peer_compare/working_capital/check_identities/
  get_filing_notes）→ "假设检验记录"；取数类 → "已取得的数据"；工具报错 → "失败记录"。
- 渲染是纯函数：同一 store + 同一 scope 永远得到同一文本（不调用 LLM，V4.32）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.result_store import StoredResult

HYPOTHESIS_TOOLS = {"variance", "seasonal_check", "peer_compare",
                    "working_capital", "check_identities", "get_filing_notes"}
DATA_TOOLS = {"list_metrics", "query_metric", "run_sql", "calc", "recall"}

_SECTION_TITLES = ["口径", "已取得的数据", "假设检验记录", "失败记录", "计划"]


def _pct(x: float) -> str:
    return f"{100 * x:+.2f}%"


def _rid(res: StoredResult) -> str:
    return f"({res.id})"


def _money(x: float) -> str:
    """金额格式：千分位、10 位有效数字——避免 %g 产生 2e+04 这类科学计数法（verify_ledger 要能解析）。"""
    return f"{float(x):+,.10g}"


def _args_line(res: StoredResult) -> str:
    parts = []
    for k, v in res.args.items():
        if isinstance(v, (list, tuple)):
            v = "[" + ",".join(str(x) for x in v) + "]"
        parts.append(f"{k}={v}")
    return ", ".join(parts)


# ---------------- 每类分析工具的 to_ledger_entry（输出行必须带 rid）

def _e_query_metric(res):
    return f"- {res.id} query_metric({_args_line(res)}) → {res.digest}"


def _e_passthrough(res):
    return f"- {res.id} {res.tool}({_args_line(res)}) → {res.digest}"


def _e_variance(res):
    df = res.df
    lines = []
    for basis, label in (("qoq", "环比"), ("yoy", "同比")):
        g = df[df["basis"] == basis] if "basis" in df.columns else df.iloc[0:0]
        if len(g):
            r = g.iloc[0]
            lines.append(f"- 变化量化: {label} {_pct(float(r['change_pct']))}"
                         f"（绝对变化 {_money(r['change_abs'])}）{_rid(res)}")
    c = df[df["basis"] == "qoq_contribution"] if "basis" in df.columns else df.iloc[0:0]
    if len(c):
        top = c.iloc[c["contribution"].abs().values.argmax()]
        lines.append(f"- 贡献度: {top['part']} {_money(top['contribution'])}"
                     f"（占变化 {_pct(float(top['share_of_change']))}）{_rid(res)}")
    return "\n".join(lines) or f"- variance 无可用变化行 {_rid(res)}"


def _e_seasonal_check(res):
    r = res.df.iloc[0]
    cur, p5, p95 = float(r["current_change"]), float(r["p5"]), float(r["p95"])
    inside = p5 <= cur <= p95
    mark = "✓ 在历年同季度区间内" if inside else "✗ 超出历年同季度区间"
    return (f"- seasonal: {mark}（当前变化 {_pct(cur)}，区间 "
            f"{_pct(p5)}~{_pct(p95)}，z={float(r['z_score']):.4f}）{_rid(res)}")


def _e_peer_compare(res):
    parts = ", ".join(f"{r.company} {_pct(float(r.change_pct))}" for r in res.df.itertuples())
    return f"- industry_wide: 待判断 → 各公司同自然季度环比 {parts} {_rid(res)}"


def _e_working_capital(res):
    r = res.df.iloc[-1]
    return (f"- 营运资本: CCC {float(r['ccc']):.1f} 天"
            f"（DIO {float(r['dio']):.1f}、DSO {float(r['dso']):.1f}、"
            f"DPO {float(r['dpo']):.1f}）{_rid(res)}")


def _e_check_identities(res):
    if "violation" in res.df.columns:
        return f"- 勾稽: ✗ 存在违反（明细见 {_rid(res)}）"
    return f"- 勾稽: ✓ 全部通过（所查期间 A=L+E 与分项闭合）{_rid(res)}"


def _e_get_filing_notes(res):
    if len(res.df) == 0:
        return f"- reclassification: ✓ 该期附注无口径变更披露 {_rid(res)}"
    return f"- reclassification: ✗ 附注有相关披露，需按口径变化解读（全文 recall {_rid(res)})"


LEDGER_BUILDERS: dict[str, Any] = {
    "list_metrics": _e_passthrough,
    "query_metric": _e_query_metric,
    "run_sql": _e_passthrough,
    "calc": _e_passthrough,
    "recall": _e_passthrough,
    "variance": _e_variance,
    "seasonal_check": _e_seasonal_check,
    "peer_compare": _e_peer_compare,
    "working_capital": _e_working_capital,
    "check_identities": _e_check_identities,
    "get_filing_notes": _e_get_filing_notes,
}


def to_ledger_entry(res: StoredResult) -> str:
    fn = LEDGER_BUILDERS.get(res.tool)
    if fn is None:
        return _e_passthrough(res)
    try:
        return fn(res)
    except Exception:
        # 账本渲染绝不能让压缩管线崩溃（L4 在主循环内调用）
        return _e_passthrough(res)


# ---------------- 账本本体

@dataclass
class Ledger:
    """运行期累积的口径与失败记录；数据行渲染时直接从 store 取（纯函数）。"""
    as_of: str | None = None
    companies: list[str] = field(default_factory=list)
    basis: str = "fiscal"
    units: list[str] = field(default_factory=list)
    data_version: str = ""
    failures: list[str] = field(default_factory=list)

    def note_scope(self, tool: str, args: dict, unit: str | None = None) -> None:
        if tool == "query_metric":
            for c in args.get("companies", []):
                if c not in self.companies:
                    self.companies.append(c)
            self.basis = args.get("period_basis", self.basis)
        if unit and unit not in self.units:
            self.units.append(unit)

    def note_failure(self, tool: str, err_text: str) -> None:
        line = f"- {tool}: {err_text.splitlines()[0][:100]}"
        if line not in self.failures:
            self.failures.append(line)

    def render(self, store, todos) -> str:
        out = ["# 证据账本（代码自动生成，所有数字可 recall 回原结果核验）"]
        out.append("## 口径")
        out.append(f"公司: {', '.join(self.companies) or '未知'} | 期间口径: {self.basis}"
                   f" | as_of: {self.as_of or '全量'} | 单位: {', '.join(self.units) or '未知'}"
                   f" | db: {self.data_version[:12]}")
        data_lines, hyp_lines = [], []
        for res in store.all():
            entry = to_ledger_entry(res)
            (hyp_lines if res.tool in HYPOTHESIS_TOOLS else data_lines).append(entry)
        out.append("## 已取得的数据")
        out += data_lines or ["- （暂无）"]
        out.append("## 假设检验记录（由分析工具的结果自动生成）")
        out += hyp_lines or ["- （暂无）"]
        out.append("## 失败记录")
        out += self.failures or ["- （无）"]
        out.append("## 计划（todo 的当前状态）")
        if todos:
            mark = {"done": "[x]", "in_progress": "[>]", "pending": "[ ]"}
            out += [f"- {mark.get(t.status, '[ ]')} {t.content}" for t in todos]
        else:
            out.append("- （未设置）")
        return "\n".join(out)


# ---------------- 账本核验：数据/假设检验两节的每个数字都能在其 rid 中找到

# 带符号数值：账本行里的 +18.20%、-3.4 等必须连同符号核验（bare_numbers 会丢符号）。
_SIGNED_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])[+-]?\d+(?:[.,]\d+)*%?")


def _signed_numbers(text: str) -> list[float]:
    from agent.hooks import _EXCLUDE_RE
    out = []
    for tok in _SIGNED_NUM_RE.findall(_EXCLUDE_RE.sub(" ", text)):
        try:
            out.append(float(tok.rstrip("%").replace(",", "")))
        except ValueError:
            out.append(float("nan"))   # 解析失败（如 "1,2" 千分位不完整）由核验报"找不到"
    return out


def verify_ledger(text: str, store) -> list[str]:
    """返回问题列表（空 = 通过）。只覆盖主张数据的两节；
    口径/todo 是输入不是结论，失败记录引用原始报错（可能含代码位置数字）。"""
    problems: list[str] = []
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].split("（")[0].strip()
            continue
        if section not in ("已取得的数据", "假设检验记录") or not line.startswith("- "):
            continue
        body = line.split("→", 1)[1] if "→" in line else line
        rids = re.findall(r"\br\d+\b", line)
        if not rids:
            problems.append(f"账本行没有 rid: {line[:60]}")
            continue
        rid = rids[-1]
        for num in _signed_numbers(body):
            if not store.find_value(rid, num):
                problems.append(f"{rid} 中找不到 {num}（行: {line[:60]}）")
    return problems

"""P2.2：语义层编译器（plan 4.3）——指标请求 → 确定性 SQL（单条 SELECT，只读）。

设计：
- agent 只选择指标/维度/过滤，SQL 由代码生成（不允许 LLM 写 SQL）。
- 指标名或维度名错误时，报错信息列出最接近的合法名称（difflib，V2.6）。
- base 指标：facts 透视（MAX CASE）；带 region/product_line 分组时查 facts_detail（每行一个明细值）。
- derived 指标：在 base 之上用窗口函数（LAG 上期，均值周转 / 比率），全部在一条 SELECT 内（V2.7）。
- 期间过滤：FY24Q1 形式 → 财年区间；2024Q1 形式 → calendar_quarter 区间（V2.10）。
"""
from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

METRICS_PATH = Path("semantic/metrics.yaml")


class MetricRequest(BaseModel):
    metrics: list[str]
    companies: list[str] = ["Lenovo", "HP", "Dell"]
    period_from: str | None = None   # "FY24Q1"（财年）或 "2024Q1"（自然季度）
    period_to: str | None = None
    last_n: int | None = None
    period_basis: Literal["fiscal", "calendar"] = "fiscal"
    group_by: list[str] = []         # 可选 region / product_line（仅 base 指标）
    filters: dict[str, list[str]] = {}

    @field_validator("metrics")
    @classmethod
    def metrics_nonempty(cls, v):
        if not v:
            raise ValueError("metrics 不能为空")
        return v


def load_metrics() -> dict[str, dict]:
    data = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))
    return {m["name"]: m for m in data["metrics"]}


def _suggest(name: str, valid: list[str]) -> str:
    near = get_close_matches(name, valid, n=3, cutoff=0.5)
    return f"未知的指标或维度: '{name}'。最接近的合法名称: {near}" if near else f"未知的指标或维度: '{name}'。合法名称: {valid}"


# derived 指标的 base 依赖（与 metrics.yaml 的 formula 对应）
DERIVED_DEPS = {
    "gross_margin": ["gross_profit", "revenue"],
    "dio": ["inventory", "cogs"],
    "dso": ["accounts_receivable", "revenue"],
    "dpo": ["accounts_payable", "cogs"],
    "ccc": ["inventory", "cogs", "accounts_receivable", "accounts_payable"],
    "current_ratio": ["total_current_assets", "total_current_liabilities"],
    "debt_to_equity": ["total_liabilities", "total_equity"],
}
# 需要上期值（LAG）做均值周转的 base 科目
LAG_ACCOUNTS = ["inventory", "accounts_receivable", "accounts_payable"]


def _validate(req: MetricRequest, metrics: dict) -> None:
    known = set(metrics)
    bad = [m for m in req.metrics if m not in known]
    if bad:
        raise ValueError(_suggest(bad[0], sorted(known)))
    bad_dims = [d for d in req.group_by + list(req.filters) if d not in ("region", "product_line")]
    if bad_dims:
        raise ValueError(_suggest(bad_dims[0], ["region", "product_line"]))
    has_detail = bool(req.group_by or any(d in req.filters for d in ("region", "product_line")))
    if has_detail and any(metrics[m]["type"] == "derived" for m in req.metrics):
        raise ValueError("derived 指标不支持 region/product_line 分组，请分开请求")


def _required_base(req: MetricRequest, metrics: dict) -> list[str]:
    out: list[str] = []
    for m in req.metrics:
        if metrics[m]["type"] == "base":
            out.append(metrics[m]["account"])
        else:
            out += DERIVED_DEPS[m]
    return sorted(set(out))


def _fiscal_range_cond(bound: str, v: str, alias: str = "p") -> str:
    fy, q = v[2:].split("Q")
    fy = int(fy) + (2000 if int(fy) < 100 else 0)  # FY24 或 FY2024 均可
    a = f"{alias}." if alias else ""
    if bound == "period_from":
        return f"({a}fiscal_year > {fy} OR ({a}fiscal_year = {fy} AND {a}fiscal_quarter >= {q}))"
    return f"({a}fiscal_year < {fy} OR ({a}fiscal_year = {fy} AND {a}fiscal_quarter <= {q}))"


def _period_conds(req: MetricRequest, alias: str = "p") -> dict[str, str]:
    conds: dict[str, str] = {}
    for bound in ("period_from", "period_to"):
        v = getattr(req, bound)
        if not v:
            continue
        v = str(v).upper()
        if v.startswith("FY"):
            conds[bound] = _fiscal_range_cond(bound, v, alias)
        elif "Q" in v:
            y, qq = v.split("Q")
            op = ">=" if bound == "period_from" else "<="
            col = f"{alias + '.' if alias else ''}calendar_quarter"
            conds[bound] = f"{col} {op} '{y}Q{int(qq)}'"
        else:
            raise ValueError(f"period 格式应为 FY24Q1 或 2024Q1: {v}")
    return conds


def compile(req: MetricRequest) -> str:
    metrics = load_metrics()
    _validate(req, metrics)
    bases = _required_base(req, metrics)
    base_list = ", ".join(repr(a) for a in bases)
    want_detail = bool(req.group_by or any(d in req.filters for d in ("region", "product_line")))

    conds = ["f.version = 'original'",
             "p.company_id IN (" + ", ".join(repr(c) for c in req.companies) + ")"]
    needs_lag = any(metrics[m]["type"] == "derived" and m in ("dio", "dso", "dpo", "ccc") for m in req.metrics)
    pc = _period_conds(req)
    outer_from = None
    if needs_lag and "period_from" in pc:
        # LAG 窗口需看到 period_from 之前的期间：下界过滤下沉到窗口之后
        outer_from = _period_conds(req, alias="t")["period_from"]
        pc.pop("period_from")
    conds += list(pc.values())
    for dim, allowed in req.filters.items():
        conds.append(f"{dim} IN (" + ", ".join(repr(x) for x in allowed) + ")")
    where = " AND ".join(conds)

    if want_detail:
        # 明细模式：每行 = (公司, 期间, region, product_line, 指标名, 值)，仅 base 指标
        sql = f"""SELECT p.company_id AS company, p.fiscal_year, p.fiscal_quarter, p.calendar_quarter, p.days,
       d.region, d.product_line, d.account_code AS metric, d.value
FROM facts_detail d
JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
JOIN facts f ON f.company_id = p.company_id AND f.fiscal_year = p.fiscal_year
            AND f.fiscal_quarter = p.fiscal_quarter AND f.account_code = d.account_code
WHERE {where}
ORDER BY p.company_id, p.period_end, d.region, d.product_line, d.account_code"""
        return sql

    cases = ",\n       ".join(f"MAX(CASE WHEN f.account_code = '{a}' THEN f.value END) AS {a}" for a in bases)
    inner = f"""SELECT p.company_id AS company, p.fiscal_year, p.fiscal_quarter, p.calendar_quarter, p.days,
       {cases}
FROM facts f
JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
WHERE {where}
GROUP BY 1, 2, 3, 4, 5"""

    if needs_lag:
        lag_cols = ",\n       ".join(f"LAG(t2.{a}) OVER w AS {a}_prev" for a in LAG_ACCOUNTS if a in bases)
        inner = f"""SELECT t2.*,
       {lag_cols}
FROM ({inner}) t2
WINDOW w AS (PARTITION BY t2.company ORDER BY t2.fiscal_year, t2.fiscal_quarter)"""

    derived_expr = {
        "gross_margin": "t.gross_profit / NULLIF(t.revenue, 0)",
        "dio": "CASE WHEN t.cogs <> 0 THEN (t.inventory + t.inventory_prev) / 2.0 / t.cogs * t.days END",
        "dso": "CASE WHEN t.revenue <> 0 THEN (t.accounts_receivable + t.accounts_receivable_prev) / 2.0 / t.revenue * t.days END",
        "dpo": "CASE WHEN t.cogs <> 0 THEN (t.accounts_payable + t.accounts_payable_prev) / 2.0 / t.cogs * t.days END",
        "ccc": "(CASE WHEN t.cogs <> 0 THEN (t.inventory + t.inventory_prev) / 2.0 / t.cogs * t.days END)"
               " + (CASE WHEN t.revenue <> 0 THEN (t.accounts_receivable + t.accounts_receivable_prev) / 2.0 / t.revenue * t.days END)"
               " - (CASE WHEN t.cogs <> 0 THEN (t.accounts_payable + t.accounts_payable_prev) / 2.0 / t.cogs * t.days END)",
        "current_ratio": "t.total_current_assets / NULLIF(t.total_current_liabilities, 0)",
        "debt_to_equity": "t.total_liabilities / NULLIF(t.total_equity, 0)",
    }
    out_cols = ",\n       ".join(
        f"t.{metrics[m]['account']} AS {m}" if metrics[m]["type"] == "base" else f"({derived_expr[m]}) AS {m}"
        for m in req.metrics)

    where_outer = f"\nWHERE {outer_from}" if outer_from else ""
    sql = f"""SELECT t.company, t.fiscal_year, t.fiscal_quarter, t.calendar_quarter, t.days,
       {out_cols}
FROM ({inner}) t{where_outer}
ORDER BY t.company, t.fiscal_year, t.fiscal_quarter"""

    if req.last_n:
        # last_n = 最近 N 期（ROW_NUMBER 按时间倒序编号后取前 N），外层再恢复升序输出
        sql = f"""SELECT * FROM (
SELECT ROW_NUMBER() OVER (PARTITION BY company ORDER BY fiscal_year DESC, fiscal_quarter DESC) AS _rn, *
FROM ({sql})
) WHERE _rn <= {int(req.last_n)} ORDER BY company, fiscal_year, fiscal_quarter"""
    return sql

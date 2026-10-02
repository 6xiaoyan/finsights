"""P5.2：异常注入器（plan 7.2）——在基础库副本上注入已知异常，生成场景库 + 标准答案。

五种类型（每种都必须保持 A = L + E 与分项加总，恒等式由 _recompute_totals 按分项重算保证）：
- company_specific：只改一家公司，科目 ±Δ，对手科目联动（存货↔应付 同号、应收↔现金 反号）
- industry_wide：三家公司同一自然季度都改，幅度 = 各自 Δ × U(0.8, 1.2)
- mix_shift：同一合计科目的两个子科目之间转移 Δ，合计不变
- reclassification：自 t 期起持续把 other_current_assets 的一部分划入 inventory（persist），附注写入场景库 filing_notes
- none：不修改（阴性对照；阴性对照也有自己的场景库文件，agent 无法通过库名猜答案）

幅度（V5.4）：|Δ| = magnitude_sigma × σ，σ = 该 (公司, 科目) 历年同财季环比变化的绝对差标准差，
magnitude_sigma ∈ [2.5, 4]；选期（V5.5）：真实 |z| < 1 的期间（不与真实异常叠加）。
场景库文件名 = 随机 id（不含类型词，V5.6）；同种子完全可复现（V5.7）。
"""
from __future__ import annotations

import random
import re
import shutil
from pathlib import Path

import duckdb
from pydantic import BaseModel, field_validator

from fincalc.calc import IDENTITY_TREES, IDENTITY_TREES_HP

DB_BASE = Path("data/finsights.duckdb")
SCENARIO_DIR = Path("data/scenarios")
GOLD_DIR = Path("eval/datasets/l3_gold")

TYPES = ("company_specific", "industry_wide", "mix_shift", "reclassification", "none")
INJECTABLE = ("inventory", "accounts_receivable", "accounts_payable")
# 对手科目与符号：主科目变化 × sign → 对手科目变化（保持 A = L + E）
COUNTERPART = {
    "inventory": ("accounts_payable", +1),          # 存货↑ 赊购 → 应付↑（A+Δ, L+Δ）
    "accounts_receivable": ("cash", -1),            # 应收↑ 挤占现金（资产内部一升一降）
    "accounts_payable": ("inventory", +1),          # 应付↑ 对应存货↑（L+Δ, A+Δ）
}
MIX_PAIRS = [("cash", "inventory"), ("accounts_receivable", "other_current_assets")]
Z_SELECT_MAX = 1.0          # V5.5：选期要求真实 |z| < 1
JITTER = 0.2                # industry_wide 幅度抖动 ±20%


class Injection(BaseModel):
    type: str
    companies: list[str]
    calendar_quarter: str            # 如 "2023Q3"（industry_wide 三家共用；其余为该公司期末所在自然季度）
    account: str
    direction: str = "up"            # up | down
    magnitude_sigma: float = 3.0     # V5.4：2.5–4
    persist: bool = False            # reclassification：自 t 期起持续
    seed: int = 0

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v):
        if v not in TYPES:
            raise ValueError(f"type 必须是 {TYPES}")
        return v

    @field_validator("direction")
    @classmethod
    def _dir_ok(cls, v):
        if v not in ("up", "down"):
            raise ValueError("direction 必须是 up/down")
        return v


def _connect(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path))


def _fy_of(con, cal_q: str, company: str) -> tuple[int, int]:
    """自然季度 → 该公司财年 (fy, q)：**以 periods 表为准**。

    （2026-10-02 修复）旧版按"财年止月"硬编码推算：Dell 全部分支、Lenovo q≥2 分支
    与实际财季错位一个季度，industry_wide 的同伴注入落错期间、V5.5 选期保证失效；
    live V5.10 的 l3_002/l3_003 三轮全错即此 bug（harness 侧，非 agent 侧）。
    """
    row = con.execute("SELECT fiscal_year, fiscal_quarter FROM periods"
                      " WHERE company_id=? AND calendar_quarter=?",
                      [company, cal_q]).fetchone()
    if row is None:
        raise ValueError(f"{company} 没有自然季度 {cal_q} 对应的报告期")
    return int(row[0]), int(row[1])


def _qoq_series(con: duckdb.DuckDBPyConnection, company: str, account: str) -> list[dict]:
    """连续财季的环比序列（交接一步骤 1）：diff_t = value_t - value_{t-1}，t-1 为上一连续财季。

    返回按时间升序的 [{fy, q, cal_q, value, diff}]（首期无 diff）。
    """
    rows = con.execute("""
        SELECT p.fiscal_year, p.fiscal_quarter, p.calendar_quarter, f.value
        FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.company_id = ? AND f.account_code = ? AND f.version = 'original'
          AND f.derivation IS NULL
        ORDER BY p.period_end""", [company, account]).fetchall()
    out = []
    for i in range(1, len(rows)):
        fy, q, cq, v = rows[i]
        prev_v = rows[i - 1][3]
        out.append({"fy": fy, "q": q, "cal_q": cq, "value": v, "diff": v - prev_v})
    return out


def _sigma_of(con, company: str, account: str, fq: int) -> float:
    """历年同财季环比变化的绝对差标准差（V5.4 分母；排除当前点之外同权计算）。"""
    samples = [d["diff"] for d in _qoq_series(con, company, account)
               if d["q"] == fq]
    if len(samples) < 2:
        return 0.0
    mean = sum(samples) / len(samples)
    return (sum((d - mean) ** 2 for d in samples) / len(samples)) ** 0.5


def real_z(con: duckdb.DuckDBPyConnection, company: str, account: str,
           fy: int, q: int) -> float | None:
    """当前期环比相对历年同财季环比分布的 z 分数（排除当前点；样本 <3 或零方差 → None）。"""
    series = _qoq_series(con, company, account)
    samples = [d["diff"] for d in series if d["q"] == q and (d["fy"], d["q"]) != (fy, q)]
    cur = [d for d in series if d["fy"] == fy and d["q"] == q]
    if len(samples) < 3 or not cur:
        return None
    mean = sum(samples) / len(samples)
    var = sum((d - mean) ** 2 for d in samples) / len(samples)
    std = var ** 0.5
    if std == 0:
        return None  # 零方差：显式不可计算（交接一步骤 1），不补造 z
    return (cur[0]["diff"] - mean) / std


def _children(con, company, fy, q, total: str) -> dict[str, float]:
    trees = IDENTITY_TREES_HP if company == "HP" else IDENTITY_TREES
    out = {}
    for c in trees[total]:
        v = con.execute("""SELECT f.value FROM facts f JOIN periods p
            USING (company_id, fiscal_year, fiscal_quarter)
            WHERE f.company_id=? AND f.account_code=? AND p.fiscal_year=? AND p.fiscal_quarter=?
              AND f.version='original'""", [company, c, fy, q]).fetchone()
        out[c] = float(v[0]) if v and v[0] is not None else 0.0
    return out


def _set_fact(con, company, fy, q, account: str, value: float) -> None:
    con.execute("""UPDATE facts SET value = ? WHERE company_id=? AND account_code=?
                   AND fiscal_year=? AND fiscal_quarter=? AND version='original'""",
                [round(value, 3), company, account, fy, q])


def _recompute_totals(con, company, fy, q) -> None:
    """按分项重算各级合计（HP 用不含 dr_nc 的树），保证 A = L + E 与分项闭合。"""
    trees = IDENTITY_TREES_HP if company == "HP" else IDENTITY_TREES
    tca = sum(_children(con, company, fy, q, "total_current_assets").values())
    _set_fact(con, company, fy, q, "total_current_assets", tca)
    ta = tca + sum(_children(con, company, fy, q, "total_assets").get(c, 0.0)
                   for c in ("ppe_net", "goodwill", "intangibles", "other_noncurrent_assets"))
    _set_fact(con, company, fy, q, "total_assets", ta)
    tcl = sum(_children(con, company, fy, q, "total_current_liabilities").values())
    _set_fact(con, company, fy, q, "total_current_liabilities", tcl)
    tl = tcl + sum(_children(con, company, fy, q, "total_liabilities").get(c, 0.0)
                   for c in ("long_term_debt", "deferred_revenue_noncurrent", "other_noncurrent_liabilities")
                   if not (company == "HP" and c == "deferred_revenue_noncurrent"))
    _set_fact(con, company, fy, q, "total_liabilities", tl)
    te = con.execute("""SELECT f.value FROM facts f JOIN periods p
        USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.company_id=? AND f.account_code='total_equity' AND p.fiscal_year=? AND p.fiscal_quarter=?
          AND f.version='original'""", [company, fy, q]).fetchone()[0]
    _set_fact(con, company, fy, q, "total_liabilities_and_equity", tl + te)


def _leaf_value(con, company, fy, q, account: str) -> float:
    return con.execute("""SELECT f.value FROM facts f JOIN periods p
        USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.company_id=? AND f.account_code=? AND p.fiscal_year=? AND p.fiscal_quarter=?
          AND f.version='original'""", [company, account, fy, q]).fetchone()[0]


def _apply_company_delta(con, company, fy, q, account: str, delta: float) -> None:
    """主科目 ±Δ + 对手科目联动 + 合计重算（company_specific / industry_wide 的单家动作）。"""
    _set_fact(con, company, fy, q, account, _leaf_value(con, company, fy, q, account) + delta)
    counterpart, sign = COUNTERPART[account]
    _set_fact(con, company, fy, q, counterpart,
              _leaf_value(con, company, fy, q, counterpart) + sign * delta)
    _recompute_totals(con, company, fy, q)


def apply_injection(con: duckdb.DuckDBPyConnection, inj: Injection) -> dict:
    """在已连接的场景库上执行注入，返回标准答案（gold）字典。"""
    sign = 1.0 if inj.direction == "up" else -1.0
    fy, q = _fy_of(con, inj.calendar_quarter, inj.companies[0])
    gold = {"type": inj.type, "label": "no_anomaly" if inj.type == "none" else inj.type,
            "account": inj.account,
            "direction": inj.direction, "companies": list(inj.companies),
            "calendar_quarter": inj.calendar_quarter, "deltas_mn": {}, "notes": []}
    rng = random.Random(inj.seed)
    if inj.type == "none":
        return gold
    if inj.type in ("company_specific", "industry_wide"):
        for company in inj.companies:
            cfy, cq = _fy_of(con, inj.calendar_quarter, company)
            sigma = _sigma_of(con, company, inj.account, cq)
            jitter = rng.uniform(1 - JITTER, 1 + JITTER) if inj.type == "industry_wide" else 1.0
            delta = sign * inj.magnitude_sigma * sigma * jitter
            _apply_company_delta(con, company, cfy, cq, inj.account, delta)
            gold["deltas_mn"][company] = round(delta, 3)
        return gold
    if inj.type == "mix_shift":
        if inj.account not in ("inventory", "accounts_receivable"):
            raise ValueError(f"mix_shift 仅支持子科目对定义的科目: {inj.account}")
        src, dst = MIX_PAIRS[0] if inj.account == "inventory" else MIX_PAIRS[1]
        company = inj.companies[0]
        sigma = _sigma_of(con, company, src, q)
        if sigma <= 0:
            raise ValueError(f"mix_shift σ<=0: {company} {src} FY{fy}Q{q}")
        delta = sign * inj.magnitude_sigma * sigma
        _set_fact(con, company, fy, q, src, _leaf_value(con, company, fy, q, src) - delta)
        _set_fact(con, company, fy, q, dst, _leaf_value(con, company, fy, q, dst) + delta)
        gold["deltas_mn"][company] = round(-delta, 3)  # src 的变化量（合计不变）
        gold["mix"] = {"from": src, "to": dst}
        return gold
    if inj.type == "reclassification":
        company = inj.companies[0]
        # σ 以标的科目（借方，raw 序列）起始财季计——other_current_assets 在库中全部
        # derivation='residual'，被 _qoq_series 排除，旧版以其为 σ 源导致 |Δ|=0、
        # 整类注入成为无数据足迹的空注入（2026-10-02 修复）。自 t 期起各期转移等额。
        sigma0 = _sigma_of(con, company, inj.account, q)
        if sigma0 <= 0:
            raise ValueError(f"reclassification σ<=0: {company} {inj.account} FY{fy}Q{q}")
        delta = sign * inj.magnitude_sigma * sigma0
        con.execute("INSERT INTO filing_notes VALUES (?,?,?,?)",
                    [company, fy, q,
                     f"自本期起，部分其他流动资产重分类计入{name_of(inj.account)}；此前各期未重述。"])
        gold["notes"].append("filing_notes 已写入重分类附注")
        # 自 t 期起所有后续期间同额转移（真实重分类语义：持续、不衰减）
        periods = con.execute("""SELECT p.fiscal_year, p.fiscal_quarter FROM periods p
            WHERE p.company_id = ? AND (p.fiscal_year > ? OR (p.fiscal_year = ? AND p.fiscal_quarter >= ?))
            ORDER BY p.fiscal_year, p.fiscal_quarter""", [company, fy, fy, q]).fetchall()
        for (y, qq) in periods:
            src_v = _leaf_value(con, company, y, qq, "other_current_assets")
            dst_v = _leaf_value(con, company, y, qq, inj.account)
            _set_fact(con, company, y, qq, "other_current_assets", src_v - delta)
            _set_fact(con, company, y, qq, inj.account, dst_v + delta)
            _recompute_totals(con, company, y, qq)
            gold["deltas_mn"][f"{y}Q{qq}"] = round(delta, 3)
        return gold
    raise ValueError(f"未知类型: {inj.type}")


def name_of(account: str) -> str:
    from semantic.catalog import describe
    try:
        return describe(account)["label_zh"]
    except Exception:
        return account


def new_scenario_id(rng: random.Random) -> str:
    return "s" + "".join(rng.choice("0123456789abcdef") for _ in range(10))

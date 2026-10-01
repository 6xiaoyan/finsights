"""P2.3：fincalc 确定性计算（plan 4.4）——同比/环比、贡献度分解、营运资本、季节性基线、同行对比、勾稽。

全部为纯函数：输入/输出 pandas DataFrame 或 dict，无任何 IO（V2.12）。
所有数字由代码计算，供 agent 引用（证据链里的派生数字即来自这里）。
"""
from __future__ import annotations

import pandas as pd


def _sort(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["company", "period_end"]).reset_index(drop=True)


def qoq(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """环比：相邻季度（按 period_end 排序、公司内）的绝对变化与百分比。需要列: company, period_end, value_col。"""
    out = _sort(df.copy())
    out["value_prev"] = out.groupby("company")[value_col].shift(1)
    out["change_abs"] = out[value_col] - out["value_prev"]
    out["change_pct"] = out["change_abs"] / out["value_prev"]
    return out


def yoy(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """同比：与 4 个季度前比较（公司内按 period_end 排序）。需要列: company, period_end, value_col。"""
    out = _sort(df.copy())
    out["value_prev"] = out.groupby("company")[value_col].shift(4)
    out["change_abs"] = out[value_col] - out["value_prev"]
    out["change_pct"] = out["change_abs"] / out["value_prev"]
    return out


def contribution(total_df: pd.DataFrame, parts_df: pd.DataFrame,
                 value_col: str = "value") -> pd.DataFrame:
    """贡献度分解（V2.11）：各分项对总额变化的贡献，加总必须等于总变化（误差 < 1e-9）。

    total_df / parts_df 均需列: company, period_end, value_col（以及分项列 part）。
    贡献 c_i = Δpart_i；若分项变化之和 ≠ 总变化（分项不含尽），残差按 |Δpart_i| 比例分摊，保证加总精确闭合。
    返回列: company, period_end, part, contribution, share_of_change。
    """
    total = _sort(total_df.copy())
    parts = _sort(parts_df.copy())
    t_prev = total.groupby("company")[value_col].shift(1)
    t_new = total[value_col]
    d_total = (t_new - t_prev).rename("d_total")
    keys = ["company", "period_end"]
    tt = pd.concat([total[keys], d_total], axis=1)
    pp = parts.copy()
    pp = pp.sort_values(["company", "part", "period_end"])
    pp["d_part"] = pp[value_col] - pp.groupby(["company", "part"])[value_col].shift(1)
    out = pp.merge(tt, on=keys, how="left")
    out = out[out["d_part"].notna()].copy()  # 首期无上期，不产生贡献
    out["residual"] = out["d_total"] - out.groupby(keys)["d_part"].transform("sum")
    abs_sum = out.groupby(keys)["d_part"].transform(lambda x: x.abs().sum())
    out["weight"] = (out["d_part"].abs() / abs_sum).fillna(0.0)
    out["contribution"] = out["d_part"] + out["residual"] * out["weight"]
    tot_abs = out.groupby(keys)["contribution"].transform(lambda x: x.abs().sum())
    out["share_of_change"] = (out["contribution"].abs() / tot_abs).fillna(0.0)
    return out[keys + ["part", "contribution", "share_of_change"]]


def working_capital(df: pd.DataFrame) -> pd.DataFrame:
    """DSO / DIO / DPO / CCC（V2.9 手算一致性）。

    df 需列: company, period_end, days, accounts_receivable, inventory, accounts_payable, revenue, cogs
    周转天数 = 平均余额 / 流量 × days；平均余额 = (本期 + 上期) / 2（上期用 LAG，首期为自身）。
    """
    out = _sort(df.copy())
    for col in ("accounts_receivable", "inventory", "accounts_payable"):
        out[f"{col}_prev"] = out.groupby("company")[col].shift(1)
        out[f"{col}_avg"] = ((out[col] + out[f"{col}_prev"]) / 2).fillna(out[col])
    out["dio"] = (out["inventory_avg"] / out["cogs"] * out["days"]).where(out["cogs"] != 0)
    out["dso"] = (out["accounts_receivable_avg"] / out["revenue"] * out["days"]).where(out["revenue"] != 0)
    out["dpo"] = (out["accounts_payable_avg"] / out["cogs"] * out["days"]).where(out["cogs"] != 0)
    out["ccc"] = out["dio"] + out["dso"] - out["dpo"]
    return out


def seasonal_baseline(values: list[float], quarters: list[int], current_quarter: int) -> dict:
    """季节性基线（V2.13）：历年同季度的环比变化分布 + 当前值的 z-score 与分位。

    values/quarters 等长（按时间升序）；对每个历史时点 t（quarters[t] == current_quarter），
    取其环比 (values[t] - values[t-1]) / |values[t-1]| 作为"历年同季度的变化"样本。
    返回 {n, mean, std, p5, p25, p50, p75, p95, current_change, z_score, percentile}。
    """
    if len(values) != len(quarters) or len(values) < 3:
        raise ValueError("values/quarters 长度需一致且 ≥3")
    changes = []
    for i in range(1, len(values)):
        if quarters[i] == current_quarter and values[i - 1] != 0:
            changes.append((values[i] - values[i - 1]) / abs(values[i - 1]))
    if not changes:
        raise ValueError(f"同季度（Q{current_quarter}）历史样本不足")
    current = changes[-1]
    hist = pd.Series(changes[:-1])  # 当前值不进基线样本（否则分位永远 <100%）
    mean, std = float(hist.mean()), float(hist.std(ddof=0))
    z = (current - mean) / std if std else 0.0
    return {
        "n": int(len(hist)), "mean": mean, "std": std,
        "p5": float(hist.quantile(0.05)), "p25": float(hist.quantile(0.25)),
        "p50": float(hist.quantile(0.50)), "p75": float(hist.quantile(0.75)),
        "p95": float(hist.quantile(0.95)),
        "current_change": current, "z_score": float(z),
        "percentile": float((hist < current).mean()),
    }


def peer_compare(values: dict[str, float], prev_values: dict[str, float]) -> list[dict]:
    """同行对比：同一自然季度各公司的环比变化与相对位置。

    values / prev_values: {company: value}；返回按变化排序的列表，附 change_pct 与 rank（1 = 变化最大）。
    """
    rows = []
    for cid, v in values.items():
        p = prev_values.get(cid)
        if p in (None, 0):
            continue
        rows.append({"company": cid, "value": v, "value_prev": p, "change_pct": (v - p) / abs(p)})
    rows.sort(key=lambda x: -x["change_pct"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


IDENTITY_TREES = {
    "total_assets": ["total_current_assets", "ppe_net", "goodwill", "intangibles", "other_noncurrent_assets"],
    "total_current_assets": ["cash", "short_term_investments", "accounts_receivable",
                             "financing_receivables", "inventory", "other_current_assets"],
    "total_current_liabilities": ["accounts_payable", "short_term_debt", "accrued_liabilities",
                                  "deferred_revenue_current", "other_current_liabilities"],
    "total_liabilities": ["total_current_liabilities", "long_term_debt", "deferred_revenue_noncurrent",
                          "other_noncurrent_liabilities"],
}


def check_identities(df: pd.DataFrame, tol: float = 0.005) -> list[str]:
    """勾稽校验（纯函数版，供 verifier 与 HP 之外的公司使用）：返回违反项描述列表。

    df 为宽表（每行一个期间），需列: company, fiscal_year, fiscal_quarter,
    total_assets, total_liabilities, total_equity 及各分项。
    """
    violations: list[str] = []
    for _, r in df.iterrows():
        tag = f"{r['company']} FY{r['fiscal_year']}Q{r['fiscal_quarter']}"
        a, l, e = r.get("total_assets"), r.get("total_liabilities"), r.get("total_equity")
        if None in (a, l, e) or pd.isna(a) or pd.isna(l) or pd.isna(e):
            violations.append(f"{tag}: a/l/e 缺失")
            continue
        if abs(a - l - e) / abs(a) > tol:
            violations.append(f"{tag}: |A-L-E|/A = {abs(a - l - e) / abs(a):.4%}")
        for total, children in IDENTITY_TREES.items():
            if total not in r or pd.isna(r[total]):
                continue
            s = sum(r.get(c, 0.0) or 0.0 for c in children)
            if abs(s - r[total]) / max(abs(r[total]), 1) > tol:
                violations.append(f"{tag}: {total} 分项和 {s:.1f} ≠ {r[total]:.1f}")
    return violations

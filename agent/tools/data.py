"""P4.1：数据类工具实现（plan 6.4）——handler 全部走语义层/fincalc，数字由代码计算。

- 结果统一进 Result Store 并渲染成 plan 6.3 格式（rid 头行 + rows/cols/unit + markdown 表）。
- L1 结果预算：超过 max_rows 行或 max_tokens（估算）只显示前 N 行并提示 recall()。
- 工具执行错误不抛异常，以"工具错误: …"文本返回给模型（plan 6.2）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

import fincalc.calc as fc
from agent.result_store import ResultStore, StoredResult
from agent.tools import guard
from agent.tools.base import STORED_TOOLS, validate_args
from agent.tools.db import query_df
from semantic.catalog import list_catalog
from semantic.compiler import MetricRequest, compile as compile_sql, load_metrics

DEFAULT_CFG = {"result_max_rows": 50, "result_max_tokens": 2000}


def make_data_version(db_path: str) -> str:
    """数据版本标识：db 文件大小 + 修改时间 + 首部内容哈希（plan 6.5 data_version）。"""
    import hashlib
    import os
    st = os.stat(db_path)
    h = hashlib.sha256()
    with open(db_path, "rb") as f:
        h.update(f.read(65536))
    return f"{st.st_size}-{st.st_mtime_ns}-{h.hexdigest()[:12]}"


@dataclass
class ToolContext:
    db_path: str
    store: ResultStore
    cfg: dict = field(default_factory=lambda: dict(DEFAULT_CFG))

    @property
    def data_version(self) -> str:
        return self.store.data_version


@dataclass
class Outcome:
    text: str
    rid: str | None = None
    ok: bool = True
    stored: StoredResult | None = None


# ---------------------------------------------------------------- 渲染

def _args_line(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if isinstance(v, (list, tuple)):
            v = "[" + ",".join(str(x) for x in v) + "]"
        parts.append(f"{k}={v}")
    return ", ".join(parts)


def _fmt_cell(x: Any) -> str:
    if isinstance(x, (list, tuple, set)) or hasattr(x, "__len__"):
        return str(list(x)) if hasattr(x, "__len__") and not isinstance(x, str) else str(x)
    if isinstance(x, float) and pd.isna(x):
        return ""
    if x is None:
        return ""
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    if isinstance(x, float):
        return f"{x:.6g}"
    return str(x)


def render(res: StoredResult, cfg: dict | None = None) -> str:
    cfg = cfg or DEFAULT_CFG
    max_rows = int(cfg.get("result_max_rows", 50))
    max_tokens = int(cfg.get("result_max_tokens", 2000))
    df = res.df
    head = [f"[{res.id}] {res.tool}({_args_line(res.args)})",
            f"rows={len(df)} cols=[{', '.join(map(str, df.columns))}]"
            + (f" unit={res.unit}" if res.unit else "")]
    if res.sql:
        head.append(f"SQL: {res.sql}")
    rows_shown, budget_chars = 0, max_tokens * 4 - sum(len(h) for h in head) - 200
    per_row = max(8, int(df.shape[1] * 12)) if len(df) else 1
    rows_shown = max(0, min(max_rows, budget_chars // per_row))
    body = _table(df.head(rows_shown))
    out = "\n".join(head + [body])
    if len(df) > rows_shown or rows_shown == 0 and len(df) > 0:
        out += f"\n（结果共 {len(df)} 行，此处只显示前 {rows_shown} 行；" \
               f'完整结果可用 recall("{res.id}", offset={rows_shown}) 查看）'
    return out


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(空结果)"
    cols = list(map(str, df.columns))
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(_fmt_cell(x) for x in r) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 公共取数

_PERIOD_ITEM = re.compile(r"^FY?(\d{2,4})Q([1-4])$", re.IGNORECASE)


def _fy_key(label: str) -> tuple[int, int]:
    m = _PERIOD_ITEM.match(label.strip().upper())
    if not m:
        raise ValueError(f"期间标签应为 FY24Q2 / FY2024Q2 形式: {label}")
    y = int(m.group(1))
    return (y + 2000 if y < 100 else y, int(m.group(2)))


def _expand_periods(items: list[str]) -> tuple[str, str]:
    """periods → (period_from, period_to)。支持 ['FY24Q1-FY25Q4'] 区间或列表。"""
    if len(items) == 1 and "-" in items[0]:
        a, b = items[0].split("-", 1)
        return a.strip(), b.strip()
    keys = [(lab, _fy_key(lab)) for lab in items]
    keys.sort(key=lambda kv: kv[1])
    return keys[0][0], keys[-1][0]


def _periods_map(ctx: ToolContext) -> pd.DataFrame:
    return query_df(
        "SELECT company_id AS company, fiscal_year, fiscal_quarter, period_end,"
        " calendar_quarter, days FROM periods", ctx.db_path)


def _fetch_series_ex(ctx: ToolContext, metrics: list[str], companies: list[str],
                     last_n: int | None = None, period_from: str | None = None,
                     period_to: str | None = None, basis: str = "fiscal") -> tuple[pd.DataFrame, str]:
    """取数并返回 (df, sql)：working_capital / check_identities 需保留底层输入 SQL（V4.47 批阅）。"""
    req = MetricRequest(metrics=metrics, companies=companies, last_n=last_n,
                        period_from=period_from, period_to=period_to, period_basis=basis)
    sql = compile_sql(req)
    ctx.store.db_reads += 1
    df = query_df(sql, ctx.db_path)
    if not df.empty:
        pm = _periods_map(ctx)
        df = df.merge(pm[["company", "fiscal_year", "fiscal_quarter", "period_end"]],
                      on=["company", "fiscal_year", "fiscal_quarter"], how="left")
    df = df.sort_values(["company", "fiscal_year", "fiscal_quarter"]).reset_index(drop=True)
    return df, sql


def _fetch_series(ctx: ToolContext, metrics: list[str], companies: list[str],
                  last_n: int | None = None, period_from: str | None = None,
                  period_to: str | None = None, basis: str = "fiscal") -> pd.DataFrame:
    return _fetch_series_ex(ctx, metrics, companies, last_n, period_from, period_to, basis)[0]


def _metric_unit(metric: str) -> str:
    m = load_metrics().get(metric)
    return m["unit"] if m else ""


# ---------------------------------------------------------------- handlers

def _h_list_metrics(args, ctx: ToolContext, step: int) -> Outcome:
    return _stored("list_metrics", {}, pd.DataFrame(list_catalog()), ctx, step, None)


def _h_query_metric(args: MetricRequest, ctx: ToolContext, step: int) -> Outcome:
    args_dict = args.model_dump()
    sql = compile_sql(args)
    ctx.store.db_reads += 1
    df = query_df(sql, ctx.db_path)
    unit = _metric_unit(args.metrics[0]) if len(args.metrics) == 1 else ""
    return _stored("query_metric", args_dict, df, ctx, step, sql, unit)


def _h_run_sql(args, ctx: ToolContext, step: int) -> Outcome:
    safe_sql = guard.validate_select(args.sql)
    ctx.store.db_reads += 1
    df = query_df(safe_sql, ctx.db_path)
    return _stored("run_sql", {"sql": args.sql}, df, ctx, step, safe_sql)


def _h_calc(args, ctx: ToolContext, step: int) -> Outcome:
    value, used = guard.calc_value(args.expression, ctx.store)
    df = pd.DataFrame([{"value": value, "expression": args.expression}])
    return _stored("calc", {"expression": args.expression, "refs": used},
                   df[["value"]], ctx, step, None)


def _change_rows(series: pd.DataFrame, col: str, basis: str) -> pd.DataFrame:
    long = series[["company", "period_end", col]].rename(columns={col: "value"})
    fn = fc.yoy if basis == "yoy" else fc.qoq
    out = fn(long)
    out = out[out["value_prev"].notna()].copy()
    out = out[out.groupby("company")["period_end"].transform("max").eq(out["period_end"])]
    out.insert(0, "basis", basis)
    return out.rename(columns={"value": col})


def _contribution_rows(ctx: ToolContext, metric: str, company: str,
                       series: pd.DataFrame) -> pd.DataFrame:
    m = load_metrics().get(metric)
    if m is None or m["type"] != "base" or not {"region", "product_line"} & set(m.get("dims", [])):
        return pd.DataFrame()
    s = series.sort_values("period_end")
    if len(s) < 2:
        return pd.DataFrame()
    t0, t1 = s["period_end"].iloc[-2], s["period_end"].iloc[-1]
    acct = m["account"]
    ctx.store.db_reads += 1
    total_df = query_df(
        "SELECT p.company_id AS company, p.period_end, f.value FROM facts f"
        " JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)"
        " WHERE f.version='original' AND p.company_id=? AND f.account_code=?"
        " AND p.period_end IN (?, ?) ORDER BY 1, 2",
        ctx.db_path, [company, acct, str(t0.date()), str(t1.date())])
    ctx.store.db_reads += 1
    parts_df = query_df(
        "SELECT p.company_id AS company, p.period_end,"
        " d.region || '|' || d.product_line AS part, SUM(d.value) AS value"
        " FROM facts_detail d JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)"
        " WHERE p.company_id=? AND d.account_code=?"
        " AND p.period_end IN (?, ?) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        ctx.db_path, [company, acct, str(t0.date()), str(t1.date())])
    if parts_df.empty or len(total_df) < 2:
        return pd.DataFrame()
    contrib = fc.contribution(total_df, parts_df)
    out = pd.DataFrame([{"basis": "qoq_contribution", "company": company,
                         "period_end": r.period_end, "part": r.part,
                         "contribution": r.contribution,
                         "share_of_change": r.share_of_change}
                        for r in contrib.itertuples()])
    return out


def _h_variance(args, ctx: ToolContext, step: int) -> Outcome:
    col_m = load_metrics().get(args.metric)
    if col_m is None:
        raise KeyError(f"指标不存在: {args.metric}")
    col = args.metric
    series = _fetch_series(ctx, [col], [args.company], last_n=6, period_to=args.period)
    if series.empty:
        raise ValueError(f"{args.company} 在 {args.period} 及之前没有 {col} 数据")
    frames: list[pd.DataFrame] = []
    bases = ["yoy", "qoq"] if args.basis == "both" else [args.basis]
    for b in bases:
        frames.append(_change_rows(series, col, b))
    if "qoq" in bases:
        cr = _contribution_rows(ctx, args.metric, args.company, series)
        if not cr.empty:
            frames.append(cr)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df = df.sort_values([c for c in ("basis", "part") if c in df.columns]).reset_index(drop=True)
    return _stored("variance", args.model_dump(), df, ctx, step, None, _metric_unit(args.metric))


def _h_seasonal_check(args, ctx: ToolContext, step: int) -> Outcome:
    series = _fetch_series(ctx, [args.metric], [args.company], last_n=20,
                           period_to=args.period)
    s = series.sort_values("period_end")
    if len(s) < 3:
        raise ValueError("历史样本不足（<3 期）")
    res = fc.seasonal_baseline([float(x) for x in s[args.metric]],
                               [int(x) for x in s["fiscal_quarter"]],
                               int(s["fiscal_quarter"].iloc[-1]))
    df = pd.DataFrame([{**res, "company": args.company, "period": args.period,
                        "metric": args.metric}])
    return _stored("seasonal_check", args.model_dump(), df, ctx, step, None)


def _prev_calendar_quarter(cq: str) -> str:
    y, q = int(cq[:4]), int(cq[-1])
    return f"{y - 1}Q4" if q == 1 else f"{y}Q{q - 1}"


def _h_peer_compare(args, ctx: ToolContext, step: int) -> Outcome:
    cq = args.calendar_quarter
    pq = _prev_calendar_quarter(cq)
    df = _fetch_series(ctx, [args.metric], ["Lenovo", "HP", "Dell"],
                      period_from=pq, period_to=cq, basis="calendar")
    def grab(label: str) -> dict[str, float]:
        g = df[df["calendar_quarter"] == label]
        return {r.company: float(getattr(r, args.metric)) for r in g.itertuples()
                if pd.notna(getattr(r, args.metric))}
    rows = fc.peer_compare(grab(cq), grab(pq))
    out = pd.DataFrame(rows)
    return _stored("peer_compare", args.model_dump(), out, ctx, step, None,
                   _metric_unit(args.metric))


def _prev_fiscal(label: str) -> str:
    y, q = _fy_key(label)
    return f"FY{y - 1}Q4" if q == 1 else f"FY{y}Q{q - 1}"


def _h_working_capital(args, ctx: ToolContext, step: int) -> Outcome:
    pf, pt = _expand_periods(list(args.periods))
    # 平均余额口径要求最早期的上一期也在帧内；否则 fincalc 静默用期末值代替
    # 平均值（V4.45 实测 bad case：单期调用退化出 ~1–4% 系统偏差）
    pf = _prev_fiscal(pf)
    need = ["accounts_receivable", "inventory", "accounts_payable", "revenue", "cogs"]
    df, sql = _fetch_series_ex(ctx, need, [args.company], period_from=pf, period_to=pt)
    if df.empty:
        raise ValueError(f"{args.company} 在 {pf}~{pt} 没有数据")
    out = fc.working_capital(df)
    return _stored("working_capital", args.model_dump(), out, ctx, step, sql, "days")


def _h_check_identities(args, ctx: ToolContext, step: int) -> Outcome:
    accounts = sorted(set(fc.IDENTITY_TREES) | {a for kids in fc.IDENTITY_TREES.values()
                                                for a in kids}
                      | {"total_assets", "total_liabilities", "total_equity"})
    accounts = [a for a in accounts if a in load_metrics()]
    pf, pt = _expand_periods(list(args.periods))
    df, sql = _fetch_series_ex(ctx, accounts, [args.company], period_from=pf, period_to=pt)
    if df.empty:
        raise ValueError(f"{args.company} 在 {pf}~{pt} 没有数据")
    detail = fc.check_identities_detail(df)
    out = pd.DataFrame(detail)  # 结构化数值列（total/computed/diff/missing），可被 claim 引用（V4.47 批阅）
    if out.empty:
        out = pd.DataFrame([{"company": args.company, "identity": "A=L+E", "ok": False,
                             "total": None, "computed": None, "diff": None, "missing": ["无数据"]}])
    return _stored("check_identities", args.model_dump(), out, ctx, step, sql)


def _h_get_filing_notes(args, ctx: ToolContext, step: int) -> Outcome:
    fy, q = _fy_key(args.period)
    ctx.store.db_reads += 1
    df = query_df("SELECT company_id AS company, fiscal_year, fiscal_quarter, note"
                  " FROM filing_notes WHERE company_id=? AND fiscal_year=? AND fiscal_quarter=?"
                  " ORDER BY 1, 2, 3", ctx.db_path, [args.company, fy, q])
    return _stored("get_filing_notes", args.model_dump(), df, ctx, step, None)


def _h_recall(args, ctx: ToolContext, step: int) -> Outcome:
    res = ctx.store.get(args.result_id)
    if res is None:
        raise KeyError(f"{args.result_id} 不存在（rid 来自此前的工具结果头行）")
    part = res.df.iloc[args.offset: args.offset + int(ctx.cfg.get("result_max_rows", 50))]
    view = StoredResult(id=res.id, tool="recall",
                        args={"result_id": res.id, "offset": args.offset},
                        data_version=res.data_version, df=part, sql=res.sql,
                        created_step=step, digest=res.digest, unit=res.unit)
    return Outcome(render(view, ctx.cfg), rid=res.id, ok=True, stored=res)


def _stored(tool: str, args: dict, df: pd.DataFrame, ctx: ToolContext, step: int,
            sql: str | None, unit: str = "") -> Outcome:
    res = ctx.store.put(tool, args, df, sql=sql, step=step, unit=unit)
    return Outcome(render(res, ctx.cfg), rid=res.id, ok=True, stored=res)


_HANDLERS: dict[str, Callable] = {
    "list_metrics": _h_list_metrics,
    "query_metric": _h_query_metric,
    "run_sql": _h_run_sql,
    "calc": _h_calc,
    "variance": _h_variance,
    "seasonal_check": _h_seasonal_check,
    "peer_compare": _h_peer_compare,
    "working_capital": _h_working_capital,
    "check_identities": _h_check_identities,
    "get_filing_notes": _h_get_filing_notes,
    "recall": _h_recall,
}


def execute(name: str, args: dict, ctx: ToolContext, step: int = 0) -> Outcome:
    """统一分发：参数校验 → store 缓存 → handler；一切错误转为工具错误文本。"""
    if name not in _HANDLERS:
        return Outcome(f"工具错误: 未知工具 {name}（可用: {', '.join(sorted(_HANDLERS))}）", ok=False)
    try:
        model = validate_args(name, args)
    except Exception as e:
        return Outcome(f"工具错误: 参数不合法: {e}", ok=False)
    if name in STORED_TOOLS:
        cached = ctx.store.lookup(name, model.model_dump())
        if cached is not None:
            return Outcome(render(cached, ctx.cfg), rid=cached.id, ok=True, stored=cached)
    try:
        return _HANDLERS[name](model, ctx, step)
    except Exception as e:
        return Outcome(f"工具错误: {type(e).__name__}: {e}", ok=False)

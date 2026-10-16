"""执行器：查询计划 → 确定性 SQL → 只读执行 → 精确过滤 → 比较 → 排序 → 落库（PRD §5.3/§6）。

关键点（对应 Review Focus）：
- 离散期间：区间取数后按请求集合精确裁剪，被裁掉的期间不参与比较/排序。
- not_in：对目录合法值求补集，再映射成编译器的 `in`；补集为空 → empty（不退化成全量）。
- 变化率：基期为 0 → value=null 且 flag=zero_baseline。
- 排序/TopN：在完整结果上排序，null 末尾，并列稳定；预览截断不影响排名。
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from agent.query.prompt import PROMPT_VERSION
from agent.query.schema import (QueryIssue, QueryPlan, QueryToolResult, _period_key,
                                classify_issues)
from agent.tools.db import query_df
from semantic.compiler import MetricRequest, compile as compile_sql

_ROW_KEYS = ("company", "metric", "period", "period_basis", "unit", "value",
             "operation", "current", "baseline", "flag")


def _filters_for(plan: QueryPlan, catalog) -> tuple[dict[str, list[str]], bool]:
    """(编译器 filters, 是否空集)。同字段多子句取交集；not_in → 补集。"""
    by_field: dict[str, list[str]] = {}
    for f in plan.filters:
        legal = list(catalog.regions if f.field == "region" else catalog.product_lines)
        if f.operator == "in":
            allowed = list(dict.fromkeys(f.values))
        else:
            excluded = set(f.values)
            allowed = [v for v in legal if v not in excluded]
        cur = by_field.get(f.field)
        by_field[f.field] = allowed if cur is None else [v for v in cur if v in set(allowed)]
    return by_field, any(not v for v in by_field.values())


def _min_max_period(periods: list[str], fiscal: bool) -> tuple[str, str]:
    keys = [_period_key(p, fiscal) for p in periods]
    lo, hi = min(keys), max(keys)
    if fiscal:
        return f"FY{lo[0]}Q{lo[1]}", f"FY{hi[0]}Q{hi[1]}"
    return str(lo), str(hi)


def plan_to_metric_request(plan: QueryPlan, catalog) -> MetricRequest:
    metrics = [catalog.resolve_metric(m) for m in plan.metrics]
    companies = [catalog.resolve_company(c) for c in plan.companies]
    fiscal = plan.period_basis == "fiscal"
    periods = list(dict.fromkeys(plan.periods))
    period_from, period_to = _min_max_period(periods, fiscal)
    filters, _ = _filters_for(plan, catalog)
    group_by = [g for g in plan.group_by if g in ("region", "product_line")]
    return MetricRequest(metrics=metrics, companies=companies,
                         period_from=period_from, period_to=period_to,
                         period_basis="fiscal" if fiscal else "calendar",
                         group_by=group_by, filters=filters)


def filter_to_requested_periods(df: pd.DataFrame, plan: QueryPlan, catalog) -> pd.DataFrame:
    """把区间取数结果精确裁剪到请求的离散期间集合。"""
    if df.empty:
        return df
    fiscal = plan.period_basis == "fiscal"
    if fiscal:
        wanted = {_period_key(p, True) for p in plan.periods}
        keep = [(int(fy), int(fq)) in wanted
                for fy, fq in zip(df["fiscal_year"], df["fiscal_quarter"])]
    else:
        wanted = {_period_key(p, False) for p in plan.periods}
        keep = [str(c) in wanted for c in df["calendar_quarter"]]
    return df[pd.Series(keep, index=df.index)].reset_index(drop=True)


def _missing(v) -> bool:
    """DuckDB → pandas 会把 NULL 变成 NaN（float 列），必须和 None 一样按缺失处理。"""
    if v is None:
        return True
    try:
        return bool(v != v)
    except Exception:  # noqa: BLE001
        return False


def _row(company, metric, period, plan, catalog, value, region, product_line) -> dict:
    row = {"company": company, "metric": metric, "period": period,
           "period_basis": plan.period_basis, "unit": catalog.metric_unit(metric),
           "value": value, "operation": None, "current": None, "baseline": None, "flag": None}
    if region is not None:
        row["region"] = region
    if product_line is not None:
        row["product_line"] = product_line
    return row


def _to_long(df: pd.DataFrame, plan: QueryPlan, catalog) -> tuple[pd.DataFrame, int]:
    """明细/宽表 → 统一长表；返回 (长表, 缺失数值个数)。缺失值被剔除，不当作 0。"""
    fiscal = plan.period_basis == "fiscal"
    resolved = [catalog.resolve_metric(m) for m in plan.metrics]
    acct_to_metric = {m.get("account"): n for n, m in catalog.metrics.items() if m.get("account")}
    detail = "metric" in df.columns and "value" in df.columns
    rows: list[dict] = []
    missing = 0
    for rec in df.to_dict("records"):
        fy, fq = rec.get("fiscal_year"), rec.get("fiscal_quarter")
        period = f"FY{int(fy)}Q{int(fq)}" if fiscal else str(rec.get("calendar_quarter"))
        if detail:
            metric = acct_to_metric.get(str(rec.get("metric")), str(rec.get("metric")))
            if metric not in resolved:
                continue
            val = rec.get("value")
            if _missing(val):
                missing += 1
                continue
            rows.append(_row(rec.get("company"), metric, period, plan, catalog,
                             float(val), rec.get("region"), rec.get("product_line")))
        else:
            for m in resolved:
                val = rec.get(m)
                if _missing(val):
                    missing += 1
                    continue
                rows.append(_row(rec.get("company"), m, period, plan, catalog,
                                 float(val), None, None))
    empty_df = pd.DataFrame(columns=list(_ROW_KEYS))
    return (pd.DataFrame(rows) if rows else empty_df), missing


def compute_comparisons(df: pd.DataFrame, plan: QueryPlan) -> pd.DataFrame:
    """按 (公司, 指标, 明细维度) 分组计算差额/变化率（代码计算，不用 SQL）。"""
    if df.empty or not plan.comparisons:
        return df
    detail_cols = [c for c in ("region", "product_line") if c in df.columns]
    key_cols = ["company", "metric"] + detail_cols
    by_key: dict[tuple, dict[str, dict]] = {}
    for rec in df.to_dict("records"):
        key = tuple(rec.get(c) for c in key_cols)
        by_key.setdefault(key, {})[str(rec["period"])] = rec
    comp_rows: list[dict] = []
    for _, period_map in by_key.items():
        for cmp in plan.comparisons:
            cur, base = period_map.get(cmp.current), period_map.get(cmp.baseline)
            if cur is None or base is None:
                continue
            cv, bv = cur.get("value"), base.get("value")
            if cmp.operation == "difference":
                val = None if (cv is None or bv is None) else cv - bv
                unit, flag = cur.get("unit"), None
            else:
                if bv is None:
                    val, flag = None, None
                elif bv == 0:
                    val, flag = None, "zero_baseline"
                elif cv is None:
                    val, flag = None, None
                else:
                    val, flag = (cv - bv) / bv, None
                unit = "ratio"
            row = dict(cur)
            row.update({"period": cmp.current, "value": val, "unit": unit,
                        "operation": cmp.operation, "current": cmp.current,
                        "baseline": cmp.baseline, "flag": flag})
            comp_rows.append(row)
    if not comp_rows:
        return df
    return pd.concat([df, pd.DataFrame(comp_rows)], ignore_index=True)


def apply_sort(df: pd.DataFrame, plan: QueryPlan, catalog) -> pd.DataFrame:
    """在完整结果上排序（null 末尾，稳定并列）；top_n 取排名前 N。"""
    if plan.sort is None or df.empty:
        return df
    s = plan.sort
    sm = catalog.resolve_metric(s.metric) or s.metric
    keys: list = []
    for rec in df.to_dict("records"):
        k = None
        if rec.get("metric") == sm:
            if s.by == "value":
                k = rec.get("value") if rec.get("operation") is None else None
            elif (rec.get("operation") == s.by and rec.get("current") == s.current
                  and rec.get("baseline") == s.baseline):
                k = rec.get("value")
        keys.append(k)
    out = df.copy()
    key_s = pd.to_numeric(pd.Series(keys, index=out.index, dtype="object"), errors="coerce")
    out["_isnull"] = key_s.isna().astype(int)
    out["_key"] = key_s.abs() if s.absolute else key_s
    out = out.sort_values(by=["_isnull", "_key", "company", "metric", "period"],
                          ascending=[True, s.direction == "asc", True, True, True],
                          kind="mergesort", na_position="last")
    if s.top_n:
        out = out[out["_isnull"] == 0].head(s.top_n)
    return out.drop(columns=["_isnull", "_key"]).reset_index(drop=True)


def _clean(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        d: dict = {}
        for k, v in r.items():
            if v is None:
                d[k] = None
            elif isinstance(v, float) and v != v:
                d[k] = None
            elif hasattr(v, "item"):
                d[k] = v.item()
            else:
                d[k] = v
        out.append(d)
    return out


def _provenance(store, catalog, trace_id: str | None) -> dict:
    return {"data_version": getattr(store, "data_version", ""),
            "catalog_version": catalog.catalog_version(),
            "prompt_version": PROMPT_VERSION,
            "query_trace_id": trace_id or ""}


def execute_plan(plan: QueryPlan, catalog, store, db_path: str, *,
                 preview_rows: int = 50) -> QueryToolResult:
    """执行校验通过的查询计划；任何失败都返回结构化状态，绝不抛给调用方。"""
    issues = plan.validate_against(catalog)
    if issues:
        return QueryToolResult(status=classify_issues(issues), issues=issues, plan=None,
                               provenance=_provenance(store, catalog, None))

    _, empty = _filters_for(plan, catalog)
    provenance = _provenance(store, catalog, None)
    if empty:
        return QueryToolResult(
            status="empty",
            issues=[QueryIssue(code="empty_filter",
                               message="筛选条件排除后没有剩余合法取值")],
            plan=plan, provenance=provenance)

    try:
        req = plan_to_metric_request(plan, catalog)
        sql = compile_sql(req)
        raw = query_df(sql, db_path)
    except Exception as e:  # noqa: BLE001 —— 执行失败一律转成 error 状态
        return QueryToolResult(status="error",
                               issues=[QueryIssue(code="query_error", message=str(e)[:300])],
                               plan=None, provenance=provenance)

    raw = filter_to_requested_periods(raw, plan, catalog)
    base, missing = _to_long(raw, plan, catalog)
    if base.empty:
        return QueryToolResult(
            status="empty",
            issues=[QueryIssue(code="no_data",
                               message="请求的公司/期间/指标没有数据（空结果不等于 0）")],
            plan=plan, provenance=provenance)

    out_issues: list[QueryIssue] = []
    if missing:
        out_issues.append(QueryIssue(code="partial_missing",
                                     message=f"{missing} 个请求数值缺失，已从结果中剔除"))
    combined = compute_comparisons(base, plan)
    combined = apply_sort(combined, plan, catalog)

    trace_id = hashlib.sha256(
        (sql + json.dumps(plan.model_dump(), sort_keys=True, ensure_ascii=False))
        .encode("utf-8")).hexdigest()[:12]
    provenance = _provenance(store, catalog, trace_id)
    stored = store.put("query_financial_data", {"plan": plan.model_dump()}, combined, sql=sql,
                       step=0, unit="",
                       meta={"catalog_version": catalog.catalog_version(),
                             "prompt_version": PROMPT_VERSION, "query_trace_id": trace_id})
    total = len(combined)
    preview = _clean(combined.head(preview_rows).to_dict("records"))
    return QueryToolResult(status="ok", issues=out_issues, plan=plan,
                           result_ids=[stored.id], columns=list(combined.columns),
                           rows=preview, total_rows=total,
                           truncated=total > preview_rows, provenance=provenance)

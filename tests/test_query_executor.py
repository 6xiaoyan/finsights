"""Task 4：执行器（离散期间、比较、排序、not_in 补集、落库与 provenance）。"""
from __future__ import annotations

import pytest

from agent.query.catalog import BusinessCatalog
from agent.query.executor import apply_sort, compute_comparisons, execute_plan, plan_to_metric_request
from agent.query.schema import Comparison, FilterClause, QueryPlan, SortSpec
from agent.result_store import ResultStore
from agent.tools.data import make_data_version


def _plan(**kw) -> QueryPlan:
    base = dict(metrics=["inventory"], companies=["Lenovo"], period_basis="fiscal",
                periods=["FY2024Q2"], group_by=[], filters=[], comparisons=[], sort=None)
    base.update(kw)
    return QueryPlan(**base)


def _cat(query_db, metrics_path) -> BusinessCatalog:
    return BusinessCatalog.build(query_db, metrics_path=metrics_path)


def _store(db_path) -> ResultStore:
    return ResultStore(data_version=make_data_version(db_path))


def test_single_metric_plan_to_request(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    req = plan_to_metric_request(_plan(periods=["FY2024Q1", "FY2024Q3"]), cat)
    assert req.companies == ["Lenovo"]
    assert req.metrics == ["inventory"]
    assert req.group_by == []
    assert req.filters == {}
    assert req.period_from == "FY2024Q1" and req.period_to == "FY2024Q3"


def test_discrete_periods_filtered(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(periods=["FY2024Q1", "FY2024Q3"])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert res.status == "ok"
    assert sorted({r["period"] for r in res.rows}) == ["FY2024Q1", "FY2024Q3"]
    assert 500.0 not in [r["value"] for r in res.rows]  # FY2024Q2 被精确过滤


def test_not_in_complement_empty_returns_empty(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["revenue"], periods=["FY2024Q2"],
                 filters=[FilterClause(field="region", operator="not_in",
                                       values=["Americas", "EMEA", "APAC"])])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert res.status == "empty"
    assert res.total_rows == 0 and res.rows == [] and res.result_ids == []


def test_not_in_complement_maps_to_in(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["revenue"], periods=["FY2024Q2"],
                 filters=[FilterClause(field="region", operator="not_in", values=["APAC"])])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert res.status == "ok"
    assert {r["region"] for r in res.rows} == {"Americas", "EMEA"}


def test_group_by_region_detail(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["revenue"], periods=["FY2024Q2"], group_by=["region"])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    got = {r["region"]: r["value"] for r in res.rows}
    assert got == {"Americas": 500.0, "EMEA": 400.0, "APAC": 300.0}


def test_difference_and_change_rate(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["revenue"], periods=["FY2024Q2", "FY2024Q3"],
                 comparisons=[Comparison(current="FY2024Q3", baseline="FY2024Q2",
                                        operation="difference"),
                              Comparison(current="FY2024Q3", baseline="FY2024Q2",
                                        operation="change_rate")])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    comp = [r for r in res.rows if r["operation"]]
    diff = next(r for r in comp if r["operation"] == "difference")
    rate = next(r for r in comp if r["operation"] == "change_rate")
    assert diff["value"] == pytest.approx(50.0)
    assert rate["value"] == pytest.approx(50 / 1200)
    assert diff["current"] == "FY2024Q3" and diff["baseline"] == "FY2024Q2"


def test_zero_baseline_change_rate_null_with_flag(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["inventory"], periods=["FY2024Q1", "FY2024Q2"],
                 comparisons=[Comparison(current="FY2024Q2", baseline="FY2024Q1",
                                        operation="change_rate")])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    rate = next(r for r in res.rows if r["operation"] == "change_rate")
    assert rate["value"] is None
    assert rate["flag"] == "zero_baseline"


def test_empty_result_is_empty_not_zero(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(companies=["Dell"], metrics=["inventory"], periods=["FY2024Q2"])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert res.status == "empty"
    assert res.rows == [] and res.result_ids == [] and res.total_rows == 0


def test_sort_value_topn(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["inventory"], companies=["Lenovo", "HP"],
                 periods=["FY2024Q2"],
                 sort=SortSpec(by="value", metric="inventory", direction="desc", top_n=1))
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert len(res.rows) == 1
    assert res.rows[0]["company"] == "Lenovo" and res.rows[0]["value"] == 500.0


def test_sort_change_rate_null_last(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["inventory"], companies=["Lenovo", "HP"],
                 periods=["FY2024Q1", "FY2024Q2"],
                 comparisons=[Comparison(current="FY2024Q2", baseline="FY2024Q1",
                                        operation="change_rate")],
                 sort=SortSpec(by="change_rate", metric="inventory",
                               current="FY2024Q2", baseline="FY2024Q1", direction="desc"))
    res = execute_plan(plan, cat, _store(query_db), query_db)
    rate = [r for r in res.rows if r["operation"] == "change_rate"]
    assert rate[0]["company"] == "HP"
    assert rate[-1]["company"] == "Lenovo" and rate[-1]["value"] is None


def test_sort_uses_full_set_before_preview(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["inventory"], companies=["Lenovo", "HP"],
                 periods=["FY2024Q2"],
                 sort=SortSpec(by="value", metric="inventory", direction="desc"))
    res = execute_plan(plan, cat, _store(query_db), query_db, preview_rows=1)
    assert res.total_rows == 2 and res.truncated is True
    assert res.rows[0]["company"] == "Lenovo"


def test_derived_detail_unsupported(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    plan = _plan(metrics=["gross_margin"], group_by=["region"])
    res = execute_plan(plan, cat, _store(query_db), query_db)
    assert res.status == "unsupported" and res.result_ids == []


def test_result_saved_with_provenance(query_db, metrics_path):
    cat = _cat(query_db, metrics_path)
    store = _store(query_db)
    plan = _plan(metrics=["inventory"], periods=["FY2024Q2"])
    res = execute_plan(plan, cat, store, query_db)
    assert res.status == "ok" and res.result_ids
    rid = res.result_ids[0]
    assert store.get(rid) is not None
    for k in ("data_version", "catalog_version", "prompt_version", "query_trace_id"):
        assert k in res.provenance

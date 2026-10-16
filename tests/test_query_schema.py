"""Task 2：查询计划与返回协议 schema（严格校验，PRD §5.2/§5.3）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.query.catalog import BusinessCatalog
from agent.query.schema import (
    Comparison,
    ExtractionResult,
    FilterClause,
    QueryPlan,
    QueryToolResult,
    SortSpec,
    classify_issues,
)


def _plan(**kw) -> QueryPlan:
    base = dict(metrics=["inventory"], companies=["Lenovo"], period_basis="fiscal",
                periods=["FY2024Q1", "FY2024Q2"], group_by=[], filters=[],
                comparisons=[], sort=None)
    base.update(kw)
    return QueryPlan(**base)


def _codes(plan, cat) -> list[str]:
    return [i.code for i in plan.validate_against(cat)]


def test_plan_rejects_unknown_field():
    with pytest.raises(ValidationError):
        QueryPlan(metrics=["inventory"], companies=["Lenovo"], period_basis="fiscal",
                  periods=["FY2024Q2"], group_by=[], filters=[], comparisons=[],
                  sort=None, unit="usd")


def test_plan_rejects_mixed_basis_format(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert "period_format" in _codes(_plan(period_basis="fiscal", periods=["2024Q2"]), cat)


def test_period_prefix_and_basis_ok(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert _codes(_plan(period_basis="fiscal", periods=["FY2024Q2"]), cat) == []
    assert _codes(_plan(period_basis="calendar", periods=["2024Q2"]), cat) == []


def test_comparison_baseline_must_be_in_periods(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    p = _plan(periods=["FY2024Q2"],
              comparisons=[Comparison(current="FY2024Q2", baseline="FY2024Q1",
                                      operation="difference")])
    assert "baseline_not_in_periods" in _codes(p, cat)


def test_comparison_mismatched_basis_formats(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    p = _plan(period_basis="fiscal", periods=["FY2024Q1", "FY2024Q2"],
              comparisons=[Comparison(current="2024Q3", baseline="2024Q2",
                                      operation="difference")])
    assert "comparison_format" in _codes(p, cat)


def test_sort_by_comparison_requires_declared_relation(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    p = _plan(sort=SortSpec(by="difference", metric="inventory",
                            current="FY2024Q2", baseline="FY2024Q1"))
    assert "sort_relation_undeclared" in _codes(p, cat)
    p2 = _plan(sort=SortSpec(by="value", metric="inventory", baseline="FY2024Q1"))
    assert "sort_value_baseline" in _codes(p2, cat)


def test_group_by_and_filter_unknown_value(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert "unknown_group_by" in _codes(_plan(group_by=["channel"]), cat)
    p2 = _plan(filters=[FilterClause(field="region", operator="in", values=["Mars"])])
    assert "unknown_filter_value" in _codes(p2, cat)


def test_derived_detail_unsupported(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    p = _plan(metrics=["gross_margin"], group_by=["region"])
    assert "unsupported_detail" in _codes(p, cat)


def test_unknown_metric_and_company(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert "unknown_metric" in _codes(_plan(metrics=["foo"]), cat)
    assert "unknown_company" in _codes(_plan(companies=["IBM"]), cat)


def test_extraction_result_invariant():
    with pytest.raises(ValidationError):
        ExtractionResult(status="needs_clarification", issues=[], plan=_plan())
    with pytest.raises(ValidationError):
        ExtractionResult(status="ok", issues=[], plan=None)
    ok = ExtractionResult(status="ok", issues=[], plan=_plan())
    assert ok.plan is not None


def test_classify_issues_maps_to_status():
    from agent.query.schema import QueryIssue
    assert classify_issues([QueryIssue(code="unknown_metric", message="x")]) == "unknown_value"
    assert classify_issues([QueryIssue(code="unsupported_detail", message="x")]) == "unsupported"
    assert classify_issues([]) == "ok"


def test_query_tool_result_shape():
    r = QueryToolResult(status="empty")
    assert r.result_ids == [] and r.rows == [] and r.total_rows == 0

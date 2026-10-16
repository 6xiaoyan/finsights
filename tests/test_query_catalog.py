"""Task 1：业务目录 BusinessCatalog（只读元信息 → 合法公司/指标/期间/明细维度）。"""
from __future__ import annotations

from pathlib import Path

from agent.query.catalog import BusinessCatalog


def test_catalog_builds_from_db(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert cat.companies == ["Dell", "HP", "Lenovo"]


def test_catalog_metrics_and_units(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert "inventory" in cat.metrics
    assert cat.metrics["inventory"]["type"] == "base"
    assert cat.metric_unit("inventory") == "usd_mn"
    assert cat.metric_type("gross_margin") == "derived"
    assert "ratio" == cat.metric_unit("gross_margin")


def test_catalog_metric_aliases_include_label_zh(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert cat.resolve_metric("inventory") == "inventory"
    assert cat.resolve_metric("存货") == "inventory"
    assert cat.resolve_metric("不存在指标") is None


def test_catalog_company_aliases(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert cat.resolve_company("联想") == "Lenovo"
    assert cat.resolve_company("lenovo") == "Lenovo"
    assert cat.resolve_company("IBM") is None


def test_catalog_detail_dims_from_facts_detail(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert cat.regions == ["APAC", "Americas", "EMEA"]
    assert "PC" in cat.product_lines
    assert {"inventory", "revenue", "accounts_receivable"} <= cat.detail_metrics
    assert "cogs" not in cat.detail_metrics


def test_catalog_periods_per_company(query_db, metrics_path):
    cat = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    assert (2024, 2) in cat.periods_fiscal["Lenovo"]
    assert (2023, 4) in cat.periods_fiscal["Lenovo"]
    assert "2024Q1" in cat.periods_calendar["HP"]


def test_catalog_version_stable_and_short(query_db, metrics_path):
    c1 = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    c2 = BusinessCatalog.build(query_db, metrics_path=metrics_path)
    v = c1.catalog_version()
    assert len(v) == 12
    assert v == c2.catalog_version()

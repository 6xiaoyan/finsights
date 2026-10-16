"""查询工具测试夹具：临时 DuckDB 小样本（完全离线，不依赖 data/finsights.duckdb）。

用来验证 query_financial_data 的协议与执行逻辑：目录、离散期间、比较/变化率、
零基期、明细分组与 not_in 补集。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

SCHEMA = """
CREATE TABLE companies (company_id TEXT PRIMARY KEY, name TEXT, fy_end_month INT, source TEXT);
CREATE TABLE periods (company_id TEXT, fiscal_year INT, fiscal_quarter INT,
  period_end DATE, calendar_quarter TEXT, days INT,
  PRIMARY KEY (company_id, fiscal_year, fiscal_quarter));
CREATE TABLE accounts (code TEXT PRIMARY KEY, name_zh TEXT, name_en TEXT, statement TEXT,
  parent_code TEXT, is_total BOOLEAN, sign INT);
CREATE TABLE facts (company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  value DOUBLE, available_date DATE, version TEXT DEFAULT 'original', source TEXT,
  source_ref TEXT, derivation TEXT, is_derived BOOLEAN DEFAULT FALSE);
CREATE TABLE facts_detail (company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  region TEXT, product_line TEXT, value DOUBLE, synthetic BOOLEAN DEFAULT TRUE);
CREATE TABLE filing_notes (company_id TEXT, fiscal_year INT, fiscal_quarter INT, note TEXT);
"""

COMPANIES = [
    ("Lenovo", "联想集团", 3, "hkex"),
    ("HP", "HP Inc.", 10, "sec"),
    ("Dell", "Dell Technologies Inc.", 1, "sec"),
]

# (company, fy, q, period_end, calendar_quarter, days)
PERIODS = [
    ("Lenovo", 2023, 4, "2024-03-31", "2024Q1", 91),
    ("Lenovo", 2024, 1, "2024-06-30", "2024Q2", 91),
    ("Lenovo", 2024, 2, "2024-09-30", "2024Q3", 92),
    ("Lenovo", 2024, 3, "2024-12-31", "2024Q4", 92),
    ("HP", 2024, 1, "2024-01-31", "2024Q1", 92),
    ("HP", 2024, 2, "2024-04-30", "2024Q2", 90),
    ("HP", 2024, 3, "2024-07-31", "2024Q3", 92),
    ("Dell", 2024, 1, "2023-04-30", "2023Q2", 90),
    ("Dell", 2024, 2, "2023-07-31", "2023Q3", 92),
    ("Dell", 2024, 3, "2023-10-31", "2023Q4", 92),
]

# (company, fy, q, account, value)  version='original'
FACTS = [
    # Lenovo inventory：FY2024Q1 = 0 用于零基期；FY2024Q2=500、Q3=550
    ("Lenovo", 2023, 4, "inventory", 400.0),
    ("Lenovo", 2024, 1, "inventory", 0.0),
    ("Lenovo", 2024, 2, "inventory", 500.0),
    ("Lenovo", 2024, 3, "inventory", 550.0),
    ("Lenovo", 2023, 4, "revenue", 1000.0),
    ("Lenovo", 2024, 1, "revenue", 1100.0),
    ("Lenovo", 2024, 2, "revenue", 1200.0),
    ("Lenovo", 2024, 3, "revenue", 1250.0),
    ("Lenovo", 2023, 4, "cogs", 600.0),
    ("Lenovo", 2024, 1, "cogs", 700.0),
    ("Lenovo", 2024, 2, "cogs", 750.0),
    ("Lenovo", 2024, 3, "cogs", 760.0),
    ("Lenovo", 2023, 4, "accounts_receivable", 300.0),
    ("Lenovo", 2024, 1, "accounts_receivable", 310.0),
    ("Lenovo", 2024, 2, "accounts_receivable", 330.0),
    ("Lenovo", 2024, 3, "accounts_receivable", 340.0),
    ("Lenovo", 2023, 4, "accounts_payable", 250.0),
    ("Lenovo", 2024, 1, "accounts_payable", 260.0),
    ("Lenovo", 2024, 2, "accounts_payable", 270.0),
    ("Lenovo", 2024, 3, "accounts_payable", 280.0),
    ("Lenovo", 2024, 1, "gross_profit", 400.0),
    ("Lenovo", 2024, 2, "gross_profit", 450.0),
    ("Lenovo", 2024, 3, "gross_profit", 490.0),
    ("Lenovo", 2024, 1, "total_current_assets", 1000.0),
    ("Lenovo", 2024, 2, "total_current_assets", 1050.0),
    ("Lenovo", 2024, 1, "total_current_liabilities", 600.0),
    ("Lenovo", 2024, 2, "total_current_liabilities", 620.0),
    # HP
    ("HP", 2024, 1, "inventory", 300.0),
    ("HP", 2024, 2, "inventory", 320.0),
    ("HP", 2024, 3, "inventory", 310.0),
    ("HP", 2024, 1, "revenue", 900.0),
    ("HP", 2024, 2, "revenue", 950.0),
    ("HP", 2024, 3, "revenue", 970.0),
    # Dell 只有 revenue（用于"空结果"测试：Dell 查 inventory 无数据）
    ("Dell", 2024, 1, "revenue", 700.0),
    ("Dell", 2024, 2, "revenue", 720.0),
]

# (company, fy, q, account, region, product_line, value)
FACTS_DETAIL = [
    # Lenovo FY2024Q2 revenue 明细（region 合计 1200）
    ("Lenovo", 2024, 2, "revenue", "Americas", "PC", 500.0),
    ("Lenovo", 2024, 2, "revenue", "EMEA", "PC", 400.0),
    ("Lenovo", 2024, 2, "revenue", "APAC", "PC", 300.0),
    ("Lenovo", 2024, 1, "revenue", "Americas", "PC", 450.0),
    ("Lenovo", 2024, 1, "revenue", "EMEA", "PC", 380.0),
    ("Lenovo", 2024, 1, "revenue", "APAC", "PC", 270.0),
    # Lenovo FY2024Q2 inventory 明细（region 合计 500）
    ("Lenovo", 2024, 2, "inventory", "Americas", "PC", 200.0),
    ("Lenovo", 2024, 2, "inventory", "EMEA", "PC", 180.0),
    ("Lenovo", 2024, 2, "inventory", "APAC", "PC", 120.0),
    # Lenovo FY2024Q2 accounts_receivable 明细按 product_line 拆（region 各自取值）
    ("Lenovo", 2024, 2, "accounts_receivable", "Americas", "PC", 150.0),
    ("Lenovo", 2024, 2, "accounts_receivable", "EMEA", "Infra", 100.0),
    ("Lenovo", 2024, 2, "accounts_receivable", "APAC", "Other", 80.0),
]


def make_query_db(path: str | Path) -> str:
    """建一个含小样本的 DuckDB，返回路径。"""
    path = str(path)
    con = duckdb.connect(path)
    try:
        con.execute(SCHEMA)
        con.executemany("INSERT INTO companies VALUES (?,?,?,?)", COMPANIES)
        con.executemany("INSERT INTO periods VALUES (?,?,?,?,?,?)", PERIODS)
        con.executemany(
            "INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
            [("inventory", "存货", "Inventory", "BS", None, False, 1),
             ("revenue", "营业收入", "Revenue", "IS", None, False, 1)])
        con.executemany(
            "INSERT INTO facts (company_id, fiscal_year, fiscal_quarter, account_code, value, "
            "available_date, version) VALUES (?,?,?,?,?, NULL, 'original')", FACTS)
        con.executemany(
            "INSERT INTO facts_detail (company_id, fiscal_year, fiscal_quarter, account_code, "
            "region, product_line, value, synthetic) VALUES (?,?,?,?,?,?,?, TRUE)",
            FACTS_DETAIL)
    finally:
        con.close()
    return path


@pytest.fixture
def query_db(tmp_path: Path) -> str:
    return make_query_db(tmp_path / "finsights_test.duckdb")


@pytest.fixture
def metrics_path() -> Path:
    """真实指标定义（相对仓库根）。"""
    return Path("semantic/metrics.yaml")

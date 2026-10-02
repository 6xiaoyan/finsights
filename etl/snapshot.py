"""P6.1（plan 8.1）：as_of 快照库。

从基础库生成 `data/snapshots/asof_<date>.duckdb`：
- facts：只保留 available_date <= as_of 的事实，且每个 (公司, 期间, 科目) 取当时**已披露
  的最早版本**（original 先于任何之后的 restated）。
- facts_detail / filing_notes / periods 无 available_date：按 facts 中同一 (公司, 期间)
  的首次披露日期关联过滤，不整表复制。
- accounts / companies：静态元数据，整表复制。
结果缓存复用（存在即返回）。CLI：
  .venv/Scripts/python -m etl.snapshot 2023-06-30 [more dates...]
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

SNAP_DIR = Path("data/snapshots")
BASE_DB = Path("data/finsights.duckdb")

_SQL = """
ATTACH '{src}' AS src (READ_ONLY);

CREATE TABLE accounts   AS SELECT * FROM src.accounts;
CREATE TABLE companies  AS SELECT * FROM src.companies;

CREATE TABLE facts AS
SELECT company_id, fiscal_year, fiscal_quarter, account_code, value,
       available_date, version, source, source_ref, derivation, is_derived
FROM (
    SELECT f.*, row_number() OVER (
        PARTITION BY company_id, fiscal_year, fiscal_quarter, account_code
        ORDER BY available_date ASC, version ASC) AS rn
    FROM src.facts f
    WHERE available_date <= DATE '{asof}'
)
WHERE rn = 1;

CREATE TABLE first_disclosure AS
SELECT company_id, fiscal_year, fiscal_quarter, min(available_date) AS first_available
FROM facts GROUP BY 1, 2, 3;

CREATE TABLE facts_detail AS
SELECT d.* FROM src.facts_detail d
JOIN first_disclosure k USING (company_id, fiscal_year, fiscal_quarter)
WHERE k.first_available <= DATE '{asof}';

CREATE TABLE filing_notes AS
SELECT n.* FROM src.filing_notes n
JOIN first_disclosure k USING (company_id, fiscal_year, fiscal_quarter)
WHERE k.first_available <= DATE '{asof}';

CREATE TABLE periods AS
SELECT p.* FROM src.periods p
LEFT JOIN first_disclosure k USING (company_id, fiscal_year, fiscal_quarter)
WHERE k.first_available IS NOT NULL AND k.first_available <= DATE '{asof}';

DROP TABLE first_disclosure;
DETACH src;
"""


def make_snapshot(as_of: date, base_db: Path = BASE_DB, snap_dir: Path = SNAP_DIR) -> Path:
    snap_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    if as_of.year > 9999 or not (1 <= as_of.month <= 12):
        raise ValueError(f"非法 as_of: {as_of}")
    target = snap_dir / f"asof_{as_of.isoformat()}.duckdb"
    if target.exists():
        return target                       # 缓存复用
    tmp = target.with_suffix(".tmp.duckdb")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    con.execute(_SQL.format(src=str(base_db).replace("'", "''"), asof=as_of.isoformat()))
    con.close()
    tmp.rename(target)
    return target


def main(argv: list[str]) -> int:
    for a in argv[1:]:
        p = make_snapshot(date.fromisoformat(a))
        con = duckdb.connect(str(p), read_only=True)
        n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
        mx = con.execute("SELECT max(available_date) FROM facts").fetchone()[0]
        con.close()
        print(f"{p}  facts={n}  max_available={mx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))

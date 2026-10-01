"""P4.1：数据库连接入口——只读（V4.10：绕过护栏也用这个连接，写操作直接报错）。"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def connect(db_path: str | Path):
    return duckdb.connect(str(db_path), read_only=True)


def query_df(sql: str, db_path: str | Path, params: list | None = None) -> pd.DataFrame:
    con = connect(db_path)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()

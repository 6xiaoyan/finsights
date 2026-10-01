"""P4.1：Result Store（plan 6.5）。

每个数据类工具的结果都存进来并分配 rid（r1, r2, ...），支撑证据链、数字核验与压缩：
- 缓存：(tool, args, data_version) 相同直接返回已有结果，不重复查库。
- check_value/find_value：claim 数字核验，处理 百万/亿、%/小数 换算（容差 0.5%）。
  候选集合 = 结果中的数值单元格 + 相邻期的派生变化（绝对/百分比）——
  digest 中的 QoQ 等派生数字因此也能追溯回结果本身。
- digest：纯代码生成的摘要（≤200 字符、确定性），供 L3 存根与账本使用。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

TOL = 0.005          # 数字核验相对容差（与打分器一致）
UNIT_FACTORS = (1.0, 100.0, 0.01)   # 百万↔亿、ratio↔pct 都是 100 倍关系
MAX_DIGEST_CHARS = 200

# digest/派生序列要忽略的"期次与元数据"列（它们不是指标数值）
META_COLS = {"company", "fiscal_year", "fiscal_quarter", "calendar_quarter",
             "period_end", "days", "_rn", "part", "basis", "metric", "period",
             "expression", "result_id", "offset", "region", "product_line"}

_PERIOD_COLS = ("fiscal_year", "fiscal_quarter")


def _canon(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def _numeric(col: pd.Series) -> pd.Series:
    """列 → 数值（list/dict 等非标量列整列视为非数值，不抛异常）。"""
    try:
        return pd.to_numeric(col, errors="coerce").dropna()
    except (TypeError, ValueError):
        return pd.Series(dtype=float)


def _num_cells(df: pd.DataFrame) -> list[float]:
    vals: list[float] = []
    for col in df.columns:
        vals.extend(float(x) for x in _numeric(df[col]))
    return vals


def _series_derivs(df: pd.DataFrame) -> list[float]:
    """按公司分组的相邻期派生：Δ 与环比变化率（digest/答案常用）。"""
    out: list[float] = []
    if "company" not in df.columns:
        return out
    sort_cols = [c for c in ("period_end", *_PERIOD_COLS, "calendar_quarter") if c in df.columns]
    if not sort_cols:
        return out
    for _, g in df.sort_values(sort_cols).groupby("company", sort=True):
        for col in g.columns:
            if col in META_COLS:
                continue
            s = _numeric(g[col]).to_numpy(dtype=float)
            for prev, cur in zip(s[:-1], s[1:]):
                out.append(cur - prev)
                if prev != 0:
                    out.append(cur / prev - 1.0)
    return out


def _label(row: pd.Series) -> str:
    if "fiscal_year" in row.index and pd.notna(row.get("fiscal_year")):
        return f"FY{int(row['fiscal_year']) % 100}Q{int(row['fiscal_quarter'])}"
    return str(row.get("calendar_quarter", "?"))


def make_digest(res: "StoredResult") -> str:
    """digest 生成规则（plan 6.6.3）：单值→数值；时间序列→最新/上期/变化率/极值期；多公司→分段。"""
    df = res.df
    if len(df) == 0:
        return "结果为空"
    num_cols = [c for c in df.columns
                if len(_numeric(df[c])) > 0 and c not in META_COLS]
    if not num_cols:
        return "结果无数值"
    if len(df) == 1:
        parts = [f"{c}={_fmt(_first_num(df[c]))}" for c in num_cols[:6]]
        text = "; ".join(parts)
        return text[:MAX_DIGEST_CHARS]
    if "company" in df.columns and any(c in df.columns for c in ("fiscal_year", "calendar_quarter")):
        segments: list[str] = []
        col = num_cols[0] if num_cols else None
        if col is None:
            return "无数值列"
        for comp, g in sorted(df.groupby("company"), key=lambda kv: str(kv[0])):
            g = g.sort_values([c for c in ("period_end", *_PERIOD_COLS, "calendar_quarter")
                               if c in g.columns])
            s = _numeric(g[col])
            if s.empty:
                continue
            seg = f"{comp} 最新 {_fmt(float(s.iloc[-1]))} ({_label(g.iloc[-1])})"
            if len(s) >= 2:
                prev = float(s.iloc[-2])
                seg += f" 上期 {_fmt(prev)}"
                if prev != 0:
                    seg += f" 环比 {100 * (float(s.iloc[-1]) / prev - 1):.2f}%"
            seg += f"; 区间 {_fmt(float(s.min()))}~{_fmt(float(s.max()))}"
            segments.append(seg)
        return _join_limited(segments)[:MAX_DIGEST_CHARS]
    seg = [f"最新 {_fmt(float(pd.to_numeric(df[num_cols[0]], errors='coerce').dropna().iloc[-1]))}"]
    if len(df) > 1:
        seg.append(f"首值 {_fmt(float(pd.to_numeric(df[num_cols[0]], errors='coerce').dropna().iloc[0]))}")
    return "; ".join(seg)[:MAX_DIGEST_CHARS]


def _first_num(col: pd.Series) -> float:
    return float(pd.to_numeric(col, errors="coerce").dropna().iloc[0])


def _fmt(x: float) -> str:
    if x == 0:
        return "0"
    s = f"{x:.6g}"
    return s


def _join_limited(segments: list[str], limit: int = MAX_DIGEST_CHARS) -> str:
    out = ""
    for seg in segments:
        nxt = seg if not out else out + "; " + seg
        if len(nxt) > limit:
            break
        out = nxt
    return out


@dataclass
class StoredResult:
    id: str
    tool: str
    args: dict
    data_version: str
    df: pd.DataFrame
    sql: str | None
    created_step: int
    digest: str = ""
    unit: str = ""

    def candidates(self) -> list[float]:
        return _num_cells(self.df) + _series_derivs(self.df)


class ResultStore:
    def __init__(self, data_version: str):
        self.data_version = data_version
        self._by_id: dict[str, StoredResult] = {}
        self._by_key: dict[str, str] = {}
        self._n = 0
        self._lock = threading.Lock()  # 主循环并发执行只读工具时保护 rid 分配（V4.12）
        self.db_reads = 0   # 供测试断言"缓存命中时没有重复查库"

    def lookup(self, tool: str, args: dict) -> StoredResult | None:
        rid = self._by_key.get(f"{tool}|{_canon(args)}|{self.data_version}")
        return self._by_id[rid] if rid else None

    def put(self, tool: str, args: dict, df: pd.DataFrame, sql: str | None = None,
            step: int = 0, unit: str = "") -> StoredResult:
        key = f"{tool}|{_canon(args)}|{self.data_version}"
        with self._lock:
            if key in self._by_key:
                return self._by_id[self._by_key[key]]
            self._n += 1
            res = StoredResult(id=f"r{self._n}", tool=tool, args=dict(args),
                               data_version=self.data_version, df=df, sql=sql,
                               created_step=step, unit=unit)
            res.digest = make_digest(res)
            self._by_id[res.id] = res
            self._by_key[key] = res.id
        return res

    def get(self, rid: str) -> StoredResult | None:
        return self._by_id.get(rid)

    def check_value(self, rid: str, value: float, unit: str = "",
                    tol: float = TOL) -> tuple[bool, float | None, str]:
        """返回 (是否找到, 最接近的候选值, 错误信息)。rid 不存在不抛异常。"""
        res = self._by_id.get(rid)
        if res is None:
            return False, None, f"rid {rid} 不存在"
        cands = res.candidates()
        if not cands:
            return False, None, "结果中没有数值"
        best, best_err = None, float("inf")
        for c in cands:
            for f in UNIT_FACTORS:
                v = c * f
                err = abs(v - value) / max(abs(value), 1e-12)
                if err < best_err:
                    best, best_err = v, err
        return best_err <= tol, best, ""

    def find_value(self, rid: str, value: float, unit: str = "", tol: float = TOL) -> bool:
        return self.check_value(rid, value, unit, tol)[0]

    def dump(self, path: str | Path) -> None:
        """落盘为 JSON-lines（trace 证据链：每个 rid 能追溯到 SQL 和原始数据）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for res in self._by_id.values():
            lines.append(json.dumps({
                "id": res.id, "tool": res.tool, "args": res.args,
                "data_version": res.data_version, "sql": res.sql,
                "created_step": res.created_step, "digest": res.digest, "unit": res.unit,
                "rows": res.df.to_dict(orient="records"),
            }, ensure_ascii=False, default=_jsonable))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    return str(x)

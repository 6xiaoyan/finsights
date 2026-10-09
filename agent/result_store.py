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
        if df[col].dtype == bool:  # 布尔列（如勾稽 ok）不是指标数值，不进 digest
            continue
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
                if df[c].dtype != bool and len(_numeric(df[c])) > 0 and c not in META_COLS]
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
    meta: dict = field(default_factory=dict)  # 概念字典绑定（交接 §3.4）

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
            step: int = 0, unit: str = "", meta: dict | None = None) -> StoredResult:
        key = f"{tool}|{_canon(args)}|{self.data_version}"
        with self._lock:
            if key in self._by_key:
                return self._by_id[self._by_key[key]]
            self._n += 1
            res = StoredResult(id=f"r{self._n}", tool=tool, args=dict(args),
                               data_version=self.data_version, df=df, sql=sql,
                               created_step=step, unit=unit, meta=dict(meta or {}))
            res.digest = make_digest(res)
            self._by_id[res.id] = res
            self._by_key[key] = res.id
        return res

    def get(self, rid: str) -> StoredResult | None:
        return self._by_id.get(rid)

    def all(self) -> list[StoredResult]:
        return list(self._by_id.values())

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
                "meta": res.meta,
                "rows": res.df.to_dict(orient="records"),
            }, ensure_ascii=False, default=_jsonable))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- 检查点级导出/导入（PRD §10.1）----
    #: 证据负载的 schema 版本。DataFrame 结构化方式变了就必须拒绝旧检查点。
    STORE_SCHEMA_VERSION = "rs1"

    def export_state(self) -> dict:
        """恢复用导出：保留字段类型与日期/null，**不能只存摘要**（PRD §10.1）。

        与 dump() 的区别是硬性的：dump() 把 Timestamp 字符串化、丢 dtype，
        它服务于"人能读的证据链"；这里服务于"恢复后能得到等价 DataFrame"。
        dtype 漂移会让数字核验在恢复后误判（例如日期列变 object 后不再被排除出数值候选），
        所以这里必须往返可重建。
        """
        items = [_export_result(r) for r in self._by_id.values()]
        return {"schema_version": self.STORE_SCHEMA_VERSION,
                "data_version": self.data_version,
                "counter": self._n,          # rid 计数器：恢复后从其后继续分配
                "db_reads": self.db_reads,
                "items": items}

    def import_state(self, payload: dict) -> None:
        """从 export_state() 的负载重建。

        已有条目保持不动（幂等：重复导入同一份检查点不会重复分配 rid）。
        """
        ver = payload.get("schema_version")
        if ver and ver != self.STORE_SCHEMA_VERSION:
            raise ValueError(f"CHECKPOINT_INCOMPATIBLE: tool_results schema {ver} "
                             f"≠ {self.STORE_SCHEMA_VERSION}")
        with self._lock:
            for item in payload.get("items") or []:
                rid = item["id"]
                if rid in self._by_id:
                    continue
                res = _import_result(item)
                res.digest = res.digest or make_digest(res)
                self._by_id[rid] = res
                self._by_key[f"{res.tool}|{_canon(res.args)}|{res.data_version}"] = rid
                n = rid[1:]
                if n.isdigit():
                    self._n = max(self._n, int(n))
            # 计数器可能被压到单个条目之后（例如只恢复了部分 rid），取两者较大值
            self._n = max(self._n, int(payload.get("counter") or 0))
            self.db_reads = int(payload.get("db_reads") or self.db_reads)


def _export_result(res: "StoredResult") -> dict:
    return {"id": res.id, "tool": res.tool, "args": res.args,
            "data_version": res.data_version, "sql": res.sql,
            "created_step": res.created_step, "digest": res.digest,
            "unit": res.unit, "meta": res.meta, "frame": _encode_frame(res.df)}


def _import_result(item: dict) -> "StoredResult":
    return StoredResult(id=item["id"], tool=item["tool"], args=dict(item.get("args") or {}),
                        data_version=item["data_version"], df=_decode_frame(item.get("frame") or {}),
                        sql=item.get("sql"), created_step=int(item.get("created_step") or 0),
                        digest=item.get("digest") or "", unit=item.get("unit") or "",
                        meta=dict(item.get("meta") or {}))


#: 检查点里的单元格编码：非有限浮点用哨兵字符串，避免写出 MySQL/JSON 不认的
#: ``NaN`` / ``Infinity`` 字面量（json.dumps 默认会写出去，读回来也不规范）。
_INF = {"inf": "Infinity", "-inf": "-Infinity"}


def _cell(x: Any) -> Any:
    """单个单元格 → 可 JSON 序列化且能精确还原的值。

    浮点交给 ``repr``（Python 默认最短往返表示），因此 float64 精确无损；
    这点很重要：``to_json`` 的 double_precision 上限只有 15 位有效数字，
    更高精度的值会被静默截断，恢复后 check_value 就对不上原值。

    容器（list/dict/tuple/set）递归编码，不能 ``str()`` 掉——``list_metrics``
    的 ``dims`` 列装的就是 list，字符串化会让恢复后的维度信息变成字面量文本。
    Python 原生 int/float/bool 必须显式处理：它们不是 ``np.integer``/``np.floating``
    的实例，漏掉会掉进最后的 ``str()`` 分支，把 ``1`` 存成 ``"1"``。
    """
    if x is None or x is pd.NaT:
        return None
    if isinstance(x, (bool, np.bool_)):     # 必须在 int 之前：bool 是 int 的子类
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        f = float(x)
        if f != f:                           # NaN
            return None
        if f == float("inf"):
            return _INF["inf"]
        if f == float("-inf"):
            return _INF["-inf"]
        return f
    if isinstance(x, pd.Timestamp):
        return x.isoformat()
    if isinstance(x, (np.str_, str)):
        return str(x)
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_cell(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _cell(v) for k, v in x.items()}
    if isinstance(x, (bytes, bytearray)):
        return x.decode("utf-8", "replace")
    if isinstance(x, np.ndarray):
        return [_cell(v) for v in x.tolist()]
    return str(x)


def _uncell(v: Any) -> Any:
    """_cell 的逆：哨兵还原为 inf，其余原样（null 由 pandas 复原为 NaN/None）。"""
    if v == _INF["inf"]:
        return float("inf")
    if v == _INF["-inf"]:
        return float("-inf")
    return v


def _encode_frame(df: pd.DataFrame) -> dict:
    """DataFrame → 可重建结构（唯一一条路径，不做分支退化）。

    刻意不用 ``to_json``：它的 double_precision 上限 15 位有效数字，float64
    最高需要 17 位，会静默截断。这里逐列取 Python 原生数值，交给 ``repr``
    序列化，往返精确；类型信息靠 dtypes 清单还原。

    **按列而不是按行**编码，且用位置名 ``__c{i}`` 承载数值：真实结果里确实存在
    重复列名（``list_metrics`` 就是），而行式编码会被 pandas 静默去重丢列
    （DataFrame columns are not unique, some columns will be omitted），
    列名重复时 ``df[c]`` 还会返回 DataFrame 而不是 Series。检查点格式在这里出错
    会连带丢掉整个检查点，所以位置名 + 列式是必须的，不是洁癖。

    已知限制：tuple 单元格会还原成 list（JSON 无法表示 tuple）。财报结果里
    没有 tuple 单元格；真出现时这是个类型漂移而非数据丢失，调用方按 list 处理即可。
    """
    raw_cols = list(df.columns)
    ncols = len(raw_cols)
    pos = [f"__c{i}" for i in range(ncols)]
    values = {pos[i]: [_cell(v) for v in df.iloc[:, i].tolist()] for i in range(ncols)}
    return {"kind": "columns", "orig_columns": [_colname(c) for c in raw_cols],
            "pos_names": pos, "n_rows": int(len(df)),
            "dtypes": [str(t) for t in df.dtypes],
            "values": values}


def _colname(c: Any) -> Any:
    """列名 → JSON 可表示。字符串原样；非字符串用 ``{"__repr__": ...}`` 标记。

    直接 ``str(c)`` 会把整数列名变成 ``"10"``，恢复后按名取列就取不到了——
    而 check_identities 之类的工具正是按列名读数据的。列名所在的数组位置是稳定的，
    所以这里可以用标记对象承载任意可 literal_eval 的名字，而不是丢信息。
    """
    return c if isinstance(c, str) else {"__repr__": repr(c)}


def _decolname(v: Any) -> Any:
    import ast
    if isinstance(v, dict) and set(v) == {"__repr__"}:
        try:
            return ast.literal_eval(v["__repr__"])      # literal_eval，不是 eval
        except (ValueError, SyntaxError, TypeError):
            return v["__repr__"]
    return v


def _decode_frame(blob: dict) -> pd.DataFrame:
    pos = list(blob.get("pos_names") or [])
    vals = blob.get("values") or {}
    if not pos:                       # 兼容早期 row 形态的负载
        rows = [[_uncell(v) for v in r] for r in (blob.get("records") or [])]
        return pd.DataFrame(rows, columns=blob.get("columns") or None)
    # 按位置名建表：位置名唯一，所以重复列名也能原样回来
    data = pd.DataFrame({p: [_uncell(v) for v in (vals.get(p) or [])] for p in pos},
                        columns=pos)
    dtypes = blob.get("dtypes") or []
    for i, p in enumerate(pos):
        t = dtypes[i] if i < len(dtypes) else None
        if not t:
            continue
        try:
            if str(data[p].dtype) != t:
                # 先 object 再 astype：否则 "2026-04-30" 会被 pandas 先推断成别的
                # 类型，之后再 astype('datetime64[ns]') 就失败了
                data[p] = data[p].astype(object).astype(t)
        except (TypeError, ValueError):
            pass                      # 类型还原失败保留原样，调用方仍拿到可读数据
    try:
        data.columns = [_decolname(c) for c in (blob.get("orig_columns") or pos)]
    except (ValueError, TypeError):
        data.columns = pos
    return data


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    return str(x)

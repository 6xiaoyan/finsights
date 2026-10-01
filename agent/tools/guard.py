"""P4.1：工具安全护栏（plan 6.4/6.8）——run_sql 只读校验、calc 的 AST 白名单求值。

禁止 eval/exec（G7）：calc 用 ast.parse + 手写节点求值，未引用的节点类型一律拒绝。
所有拒绝都以 CalcRejected/SqlRejected 异常给出，由工具层捕获转成错误文本，不回抛给模型。
"""
from __future__ import annotations

import ast
import operator
import re

import sqlglot
from sqlglot import exp

SQL_DIALECT = "duckdb"
MAX_SQL_ROWS = 200


class SqlRejected(Exception):
    pass


class CalcRejected(Exception):
    pass


_ALLOWED_SELECT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def validate_select(sql: str, max_rows: int = MAX_SQL_ROWS) -> str:
    """只允许单条 SELECT（含 WITH/UNION）；拒绝 INSERT/DROP/ATTACH/COPY/PRAGMA/多语句；自动加 LIMIT。"""
    try:
        statements = [s for s in sqlglot.parse(sql, read=SQL_DIALECT) if s is not None]
    except sqlglot.error.Error as e:
        raise SqlRejected(f"SQL 解析失败: {e}") from e
    if len(statements) != 1:
        raise SqlRejected(f"只允许单条语句，收到 {len(statements)} 条")
    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_SELECT_TYPES):
        raise SqlRejected(f"只允许 SELECT，收到 {type(stmt).__name__}")
    for node in stmt.walk():  # 子查询/CTE 里也不许藏写操作
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
                             exp.Alter, exp.Command)):
            raise SqlRejected(f"语句中包含不允许的操作 {type(node).__name__}")
    if stmt.args.get("limit") is None:
        stmt = stmt.limit(max_rows)
    return stmt.sql(dialect=SQL_DIALECT)


# ---------------- calc：AST 白名单 ----------------

_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_SAFE_FUNCS = {"abs": abs, "min": min, "max": max, "round": round}

_PERIOD_RE = re.compile(r"^FY?(\d{2,4})Q([1-4])$", re.IGNORECASE)
# r3.revenue[Lenovo,2024Q2] 里 2024Q2 不是合法 Python 标识符 → 解析前给裸的自然季度标签补引号
_BARE_CAL_RE = re.compile(r"\[\s*([^\[\],]+?)\s*,\s*(\d{4}Q[1-4])\s*\]")


def _normalize_refs(expression: str) -> str:
    return _BARE_CAL_RE.sub(lambda m: f"[{m.group(1)},'{m.group(2)}']", expression)


def _parse_period(label: str) -> tuple[int | None, int | None, str]:
    """返回 (fiscal_year, quarter, calendar_label)；FY24Q2/FY2024Q2 → 财年；2024Q2 → 自然季度。"""
    label = label.strip().upper()
    m = _PERIOD_RE.match(label)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return (y + 2000 if y < 100 else y, q, label)
    if re.match(r"^\d{4}Q[1-4]$", label):
        return None, None, label
    raise CalcRejected(f"期间标签无法解析: {label}")


def _lookup_ref(store, rid: str, account: str, company: str, period: str) -> float:
    res = store.get(rid)
    if res is None:
        raise CalcRejected(f"{rid} 不存在（先用数据工具查询）")
    df = res.df
    if account not in df.columns:
        raise CalcRejected(f"{rid} 中没有列 {account}，可用列: {list(df.columns)}")
    fy, q, cal = _parse_period(period)
    g = df
    if "company" in g.columns and company:
        g = g[g["company"] == company]
    if fy is not None and "fiscal_year" in g.columns:
        g = g[(g["fiscal_year"] == fy) & (g["fiscal_quarter"] == q)]
    elif "calendar_quarter" in g.columns:
        g = g[g["calendar_quarter"] == cal]
    vals = g[account].dropna() if account in g.columns else g[account]
    if vals.empty:
        raise CalcRejected(f"{rid}.{account}[{company},{period}] 没有数据")
    return float(vals.iloc[-1])


def _label_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise CalcRejected("引用参数必须是 公司/期间 标签")


def _ref_parts(node: ast.Subscript) -> tuple[str, str, ast.AST, ast.AST] | None:
    """识别 rN.account[X,Y] 形态；不是则返回 None。"""
    v = node.value
    if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
            and isinstance(node.slice, ast.Tuple) and len(node.slice.elts) == 2:
        return v.value.id, v.attr, node.slice.elts[0], node.slice.elts[1]
    return None


def _collect_refs(tree: ast.AST) -> list[str]:
    rids = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            parts = _ref_parts(node)
            if parts is None:
                if isinstance(node.slice, ast.Tuple):
                    raise CalcRejected("引用格式应为 rN.科目[公司,期间]")
                continue
            rids.append(parts[0])
    return sorted(set(rids))


def _eval(node: ast.AST, store) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, store)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS:
            raise CalcRejected(f"不允许的运算符 {type(node.op).__name__}")
        left, right = _eval(node.left, store), _eval(node.right, store)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise CalcRejected("指数过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY_OPS:
            raise CalcRejected("不允许的一元运算符")
        return _UNARY_OPS[type(node.op)](_eval(node.operand, store))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
            raise CalcRejected("只允许函数 abs/min/max/round")
        return float(_SAFE_FUNCS[node.func.id](*[_eval(a, store) for a in node.args]))
    if isinstance(node, ast.Subscript):
        parts = _ref_parts(node)
        if parts is None:
            raise CalcRejected("引用格式应为 rN.科目[公司,期间]")
        rid, account, comp_node, per_node = parts
        return _lookup_ref(store, rid, account, _label_of(comp_node), _label_of(per_node))
    raise CalcRejected(f"表达式中不允许的节点: {type(node).__name__}")


def calc_value(expression: str, store) -> tuple[float, list[str]]:
    """求值 rN 引用表达式；返回 (数值, 用到的 rid 列表)。非法输入抛 CalcRejected。"""
    try:
        tree = ast.parse(_normalize_refs(expression.strip()), mode="eval")
    except (SyntaxError, ValueError) as e:
        raise CalcRejected(f"表达式无法解析: {e}") from e
    used = _collect_refs(tree)
    value = _eval(tree, store)
    if value != value or value in (float("inf"), float("-inf")):
        raise CalcRejected("结果不是有限数值")
    return value, used

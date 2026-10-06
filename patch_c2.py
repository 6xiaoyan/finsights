# 补丁 P6-2：概念绑定 + 核验冲突修复。执行后删除。
import pathlib

# ============ 1) data.py：render 口径块 + _stored 计算元数据 ============
p = pathlib.Path('agent/tools/data.py')
s = p.read_text(encoding='utf-8')
old = '''    if len(df) > rows_shown or rows_shown == 0 and len(df) > 0:
        out += f"\\n（结果共 {len(df)} 行，此处只显示前 {rows_shown} 行；" \\
               f'完整结果可用 recall("{res.id}", offset={rows_shown}) 查看）'
    return out'''
new = '''    if len(df) > rows_shown or rows_shown == 0 and len(df) > 0:
        out += f"\\n（结果共 {len(df)} 行，此处只显示前 {rows_shown} 行；" \\
               f'完整结果可用 recall("{res.id}", offset={rows_shown}) 查看）'
    ids = list((res.meta or {}).get("concept_ids") or [])
    if ids:  # 概念口径块追加在截断提示之后，不被数据行吞掉（交接 §3.5）
        from agent import concepts
        out += "\\n【口径 " + concepts.version() + "】" + "；".join(concepts.brief_for(ids))
    dn = (res.meta or {}).get("data_nature")
    if dn:
        out += f"\\n【数据性质】{dn}"
    return out'''
assert old in s, 'render'
s = s.replace(old, new)

old = '''def _stored(tool: str, args: dict, df: pd.DataFrame, ctx: ToolContext, step: int,
            sql: str | None, unit: str = "") -> Outcome:
    res = ctx.store.put(tool, args, df, sql=sql, step=step, unit=unit)
    return Outcome(render(res, ctx.cfg), rid=res.id, ok=True, stored=res)'''
new = '''def _stored(tool: str, args: dict, df: pd.DataFrame, ctx: ToolContext, step: int,
            sql: str | None, unit: str = "") -> Outcome:
    from agent import concepts
    ids = concepts.concepts_for(tool, set(map(str, df.columns)) if df is not None else set())
    nature = None
    if df is not None and "synthetic" in df.columns:
        nature = "synthetic" if bool(df["synthetic"].astype(bool).all()) else "real/synthetic 混合"
    res = ctx.store.put(tool, args, df, sql=sql, step=step, unit=unit,
                        meta={**concepts.concept_meta(ids, tool), "data_nature": nature})
    return Outcome(render(res, ctx.cfg), rid=res.id, ok=True, stored=res)'''
assert old in s, '_stored'
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('1 data.py OK')

# ============ 2) result_store.py：meta 字段/put/dump + 舍入核对 ============
p = pathlib.Path('agent/result_store.py')
s = p.read_text(encoding='utf-8')
old = '''    digest: str = ""
    unit: str = ""
'''
new = '''    digest: str = ""
    unit: str = ""
    meta: dict = field(default_factory=dict)  # 概念字典绑定（交接 §3.4）
'''
assert old in s, 'StoredResult meta'
s = s.replace(old, new)
old = '''    def put(self, tool: str, args: dict, df: pd.DataFrame, sql: str | None = None,
            step: int = 0, unit: str = "") -> StoredResult:'''
new = '''    def put(self, tool: str, args: dict, df: pd.DataFrame, sql: str | None = None,
            step: int = 0, unit: str = "", meta: dict | None = None) -> StoredResult:'''
assert old in s, 'put sig'
s = s.replace(old, new)
# put 体内 StoredResult 构造补 meta —— 找 put 体内 return StoredResult
import re
m = re.search(r'(def put\(.*?return StoredResult\([^)]*?unit=unit,)', s, re.S)
assert m, 'put body'
s = s.replace(m.group(1), m.group(1) + "\n                             meta=dict(meta or {})", 1)
# check_value_rounded：按显示精度核对
old = '''    def find_value(self, rid: str, value: float, unit: str = "", tol: float = TOL) -> bool:
        return self.check_value(rid, value, unit, tol)[0]'''
new = '''    def find_value(self, rid: str, value: float, unit: str = "", tol: float = TOL) -> bool:
        return self.check_value(rid, value, unit, tol)[0]

    def check_value_rounded(self, rid: str, value: float, unit: str, decimals: int) -> tuple[bool, str]:
        """显示舍入核对（交接 §4.2）：stored 按显示小数位四舍五入后与 claim 相等则接受。

        只接受合理显示舍入；量级/单位/方向错误仍会被拒（round(12345,2)≠12.35）。
        """
        res = self._by_id.get(rid)
        if res is None:
            return False, f"rid {rid} 不存在"
        for c in res.candidates():
            for f in UNIT_FACTORS:
                v = c * f
                if round(v, decimals) == round(value, decimals):
                    return True, ""
        return False, f"rid {rid} 按 {decimals} 位小数舍入后无匹配"
'''
assert old in s, 'find_value anchor'
s = s.replace(old, new)
# dump 带 meta
old = '''                "created_step": res.created_step, "digest": res.digest, "unit": res.unit,'''
new = '''                "created_step": res.created_step, "digest": res.digest, "unit": res.unit,
                "meta": res.meta,'''
assert old in s, 'dump meta'
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('2 result_store OK')

# ============ 3) hooks.py：Markdown 行首序号仅排除序号本身 ============
p = pathlib.Path('agent/hooks.py')
s = p.read_text(encoding='utf-8')
old = '''def bare_numbers(text: str) -> list[str]:
    """提取文本中的"真数值"（排除年份/季度标签/序号/rid 引用后）。"""
    return _NUMBER_RE.findall(_EXCLUDE_RE.sub(" ", text))'''
new = '''def bare_numbers(text: str) -> list[str]:
    """提取文本中的"真数值"（排除年份/季度标签/序号/rid 引用后）。

    Markdown 行首列表序号（如 `2. `、`3） `）仅移除序号本身，其后的财务数字仍参与扫描（交接 §4.2）。
    """
    text = re.sub(r"(?m)^\\s*\\d{1,2}[.、)]\\s+", " ", text)
    return _NUMBER_RE.findall(_EXCLUDE_RE.sub(" ", text))'''
assert old in s, 'bare_numbers'
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('3 hooks OK')

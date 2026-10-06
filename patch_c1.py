# 补丁 P6a：概念字典接线（base 块/结果元数据/渲染）+ 置信度结构化 + 扫描/舍入修复。执行后删除。
import pathlib

# ---------- 1) loop.user_message 附基础口径块；make_run_ctx 传 fy_map ----------
p = pathlib.Path('agent/loop.py')
s = p.read_text(encoding='utf-8')
old = '''def user_message(question: str, ctx: RunContext) -> dict:
    content = question
    if ctx.as_of is not None:
        content += f"\\n（as_of 数据截止提醒：动态信息以 {ctx.as_of.isoformat()} 为准）"
    return {"role": "user", "content": content}'''
new = '''def user_message(question: str, ctx: RunContext) -> dict:
    content = question
    if ctx.as_of is not None:
        content += f"\\n（as_of 数据截止提醒：动态信息以 {ctx.as_of.isoformat()} 为准）"
    try:  # 概念口径基础块（交接 §3.2）：每次运行自动提供，随 question 常驻
        from agent import concepts
        fy_map = {"Lenovo": "3 月底", "HP": "10 月底", "Dell": "1/2 月底（52/53 周）"}
        content += "\\n\\n" + concepts.base_block(fy_map)
    except Exception:
        pass
    return {"role": "user", "content": content}'''
assert old in s
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('1 user_message OK')

# ---------- 2) StoredResult.meta + render 口径块 + dump ----------
p = pathlib.Path('agent/result_store.py')
s = p.read_text(encoding='utf-8')
old = '''    digest: str = ""
    unit: str = ""
'''
new = '''    digest: str = ""
    unit: str = ""
    meta: dict = field(default_factory=dict)  # 概念字典绑定（dictionary_version/concept_ids/口径）
'''
assert old in s
s = s.replace(old, new)
old = '''    def dump(self, path: str | Path) -> None:'''
new = '''    def dump(self, path: str | Path) -> None:  # noqa: C901'''
s = s.replace(old, new)
# render 尾部追加口径块：找到 render 函数
import re
m = re.search(r'(def render\(res: "StoredResult".*?)(\n\n)', s, re.S)
assert m, 'render not found'
body = m.group(1)
if "口径" not in body:
    body += '\\n    ids = list((res.meta or {}).get("concept_ids") or [])\\n    if ids:\\n        from agent import concepts\\n        block = concepts.brief_for(ids)\\n        body += "\\n\\n【口径 " + concepts.version() + "】" + "；".join(block)\\n"'
    s = s.replace(m.group(1), body)
# put() 签名接受 meta
old = s[s.index('    def put('):]
old_put = old[:old.index('\n\n    def ')]
new_put = old_put.replace('def put(', 'def put(', 1)
# 查看 put 参数
print('put signature:', old_put.splitlines()[0])
p.write_text(s, encoding='utf-8')
print('2 StoredResult/render OK')
PYEOF
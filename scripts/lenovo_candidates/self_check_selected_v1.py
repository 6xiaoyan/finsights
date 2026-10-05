# -*- coding: utf-8 -*-
"""selected_v1 自查器：结构、单位、引用完整性、DB/PDF 溯源、公式重算、query 逐字一致。

审核二轮加固（docs/lenovo_selected_v1_review.md §3.1–3.5）：
  §3.1 run_side_visible 既不得出现答案数字，也不得出现本题完整解题路线（FORBIDDEN_ROUTES）；
  §3.2 每个 calc 标 tolerance_scope=internal_selfcheck_only，评分另设 answer_numeric_check；
  §3.3 断言分 core/optional/method/internal 四层，须互斥且覆盖全部 calc；
  §3.5 DB 输入的 pdf_crossref 逐条锚定到"数字属于该页该行该栏"（exact_thousands 串+row_label 落页）。

用法：PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/lenovo_candidates/self_check_selected_v1.py
只读校验，不修改任何数据文件。
"""
import ast
import json
import re
import sys
import unicodedata
import duckdb

ROOT = "data/eval/lenovo_attribution_candidates"
SEL = f"{ROOT}/selected_v1/cases.jsonl"
ORIG = f"{ROOT}/candidates.jsonl"
PE = {1: lambda fy: f"{fy-1}-06-30", 2: lambda fy: f"{fy-1}-09-30",
      3: lambda fy: f"{fy-1}-12-31", 4: lambda fy: f"{fy}-03-31"}
EXPECTED = {
    "LV-FY2027Q1-INV-DIO-001": "联想 FY2027Q1 的库存增长是否反映周转恶化？请比较上年同期，量化变化，并区分现有数据能够支持与不能支持的解释。",
    "LV-FY2026Q4-GM-OPEX-001": "联想 FY2026 全年的经营利润增长由哪些可量化变化支撑？请比较上年，解释毛利率与经营利润不同向的原因，并说明能否判断改善具有持续性。",
    "LV-FY2026Q2-Q2VSH1-001": "联想 FY2026 上半年利润增长是否代表第二季度也在改善？请分开分析两个期间，并定位第二季度利润变化的主要环节。",
    "LV-FY2025Q3-OPEXRATIO-56BPS-001": "联想 FY2025Q3 的费用控制表现如何？请比较上年同期的费用金额与费用强度，说明现有数据能否证明成本削减或效率改善。",
    "LV-FY2026Q3-GM-DRIFT-001": "联想 FY2026 前九个月毛利率下滑主要集中在哪些季度？请量化季度表现及收入结构变化的影响，说明分析的口径与限制。",
    "LV-FY2027Q1-WC-CCC-001": "联想 FY2027Q1 营运资金扩张体现了怎样的资金占用变化？请比较上年同期，分析库存、应收和应付之间的关系，并说明能否据此判断现金压力。",
    "LV-FY2026Q1-GM-OI-DIVERGE-001": "联想 FY2026Q1 的经营利润改善质量如何？请比较上年同期，分析可量化的增长来源，并指出需要哪些额外数据判断改善的持续性。",
    "LV-FY2027Q1-OI-BRIDGE-001": "联想 FY2027Q1 的报表经营利润与调整后经营利润为何表现不同？请量化口径差异和同比变化，并说明这些变化对经营表现和现金的含义。",
    "LV-FY2027Q1-SEGM-CALIBER-001": "联想 FY2027Q1 的集团收入增长主要来自哪些业务？请量化贡献，解释增长率排序与金额贡献是否一致，并说明结构变化的分析边界。",
    "LV-FY2026Q2-ISG-LOSS-Q1CONCENTRATION-001": "联想 FY2026 上半年的 ISG 亏损变化集中在哪些季度？请比较两个季度与上年同期，判断现有数字是否支持逐季恶化的说法。",
}
PERIOD_TYPES = {"time_point", "single_quarter", "fiscal_year_sum", "half_year", "nine_month_sum"}
# review fix §3.1: run side must not carry the case's full solving route. These marker strings
# (bridge/decomposition formulas, differential steps, acceptance clauses, residual-zero clauses)
# were the route content now relocated to evaluator side. The kept S06 统一指标定义
# "选定三余额净占用 = Δ存货 + Δ应收 − Δ应付" uses Δ存货/Δ应收/Δ应付, none of these markers.
FORBIDDEN_ROUTES = ["Σ[w0", "ΔM", "同样接受", "交互项", "差分得到", "增量 = 毛利增量",
                    "增量=毛利增量", "Δ经营利润", "经营利润桥", "Σ分部贡献",
                    "贡献 = 分部收入同比变化", "贡献=分部收入同比变化", "残差=0", "残差 = 0"]

errors = []
def err(msg):
    errors.append(msg)

def num_from_raw(raw):
    """提取千美元原值串的第一个带括号数字 -> usd_mn"""
    m = re.search(r"\((-?[\d,]+)\)|(-?[\d,]+)", raw)
    tok = m.group(1) or m.group(2)
    neg = m.group(1) is not None or tok.startswith("-")
    v = float(tok.replace(",", "").lstrip("-"))
    return -v / 1000.0 if neg else v / 1000.0

ALLOWED = {ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load, ast.Constant,
           ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd}
def arith(formula):
    body = formula.replace("，", ",")
    if not re.fullmatch(r"[0-9+\-*/(). ,]+", body.replace(",", "")) and not re.fullmatch(r"[0-9+\-*/(). ]+", body):
        return None
    try:
        tree = ast.parse(body.replace(" ", ""), mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if type(node) not in ALLOWED:
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            return None
    try:
        return float(eval(compile(tree, "<f>", "eval"), {"__builtins__": {}}, {}))
    except ZeroDivisionError:
        return None

page_cache = {}
def page_text(stem, page):
    if stem not in page_cache:
        try:
            txt = open(f".cache/lenovo_pdf_text/{stem}.txt", encoding="utf-8").read()
        except FileNotFoundError:
            page_cache[stem] = None
            return None
        cur, d = None, {}
        for line in txt.splitlines():
            if line.startswith("<<<PAGE "):
                cur = int(line[8:-3]); d[cur] = []
            elif cur is not None:
                d[cur].append(line)
        page_cache[stem] = d
    if page_cache[stem] is None:
        return None
    return "\n".join(page_cache[stem].get(page, []))

def norm(s):
    return "".join(unicodedata.normalize("NFKC", s).split())

con = duckdb.connect("data/finsights.duckdb", read_only=True)
def db_value(acct, fy, q):
    r = con.execute("SELECT value FROM facts WHERE company_id='Lenovo' AND account_code=? "
                    "AND fiscal_year=? AND fiscal_quarter=? AND version='original'",
                    [acct, fy, q]).fetchall()
    return float(r[0][0]) if len(r) == 1 else None

recs = [json.loads(l) for l in open(SEL, encoding="utf-8")]
orig = {}
for l in open(ORIG, encoding="utf-8"):
    r = json.loads(l)
    orig[r["case_id"]] = r

assert len(recs) == 10, f"expect 10, got {len(recs)}"
sel_by_id = {}
for rec in recs:
    cid = rec["case_id"]
    sel_by_id[rec["selected_id"]] = cid
    o = orig.get(cid)
    if o is None:
        err(f"{cid}: not in original candidates")
        continue
    if rec.get("case_revision") != 2:
        err(f"{cid}: case_revision != 2 (review round-2 fixes applied)")
    if not (rec.get("revision") or {}).get("review_round2", {}).get("applied"):
        err(f"{cid}: revision.review_round2.applied missing")
    if rec.get("original_query") != o["query"]:
        err(f"{cid}: original_query != original candidate query")
    if rec.get("query") != EXPECTED[cid]:
        err(f"{cid}: business query not verbatim from rework doc")
    if rec["management_reference_evaluator_side"] != o.get("management_reference"):
        err(f"{cid}: management_reference not copied verbatim")
    # status discipline
    if cid.startswith(("LV-FY2027Q1-OI", "LV-FY2027Q1-SEGM", "LV-FY2026Q2-ISG")):
        if rec.get("status") != "needs_numeric_data":
            err(f"{cid}: R cases must stay needs_numeric_data")
    elif rec.get("status") not in ("reworked_pending_review", "needs_numeric_data"):
        err(f"{cid}: unexpected status {rec.get('status')}")
    ids = set()
    for ri in rec["required_inputs"]:
        iid = ri.get("input_id")
        if not iid or iid in ids:
            err(f"{cid}: duplicate/missing input_id {iid}")
        ids.add(iid)
        if ri.get("unit") != "usd_mn":
            err(f"{cid}:{iid}: unit != usd_mn")
        if ri.get("entity") != "Lenovo 联想":
            err(f"{cid}:{iid}: entity contamination")
        if ri.get("period_type") not in PERIOD_TYPES:
            err(f"{cid}:{iid}: bad period_type")
        if ri.get("value") is None:
            err(f"{cid}:{iid}: value missing")
        src = ri.get("source", {})
        if src.get("type") == "db":
            if ri["period_type"] == "fiscal_year_sum":
                s = sum(db_value(ri["metric"], ri["fiscal_year"], k) for k in (1, 2, 3, 4))
                if abs(s - ri["value"]) > 0.001:
                    err(f"{cid}:{iid}: fiscal_year_sum mismatch {ri['value']} vs {s}")
            else:
                v = db_value(ri["metric"], ri["fiscal_year"], ri["fiscal_quarter"])
                if v is None or abs(v - ri["value"]) > 0.001:
                    err(f"{cid}:{iid}: DB mismatch {ri['value']} vs {v}")
            cr = src.get("pdf_crossref")
            if not cr:
                err(f"{cid}:{iid}: DB input lacks pdf_crossref anchor")
            else:
                for fld in ("kind", "row_label", "value_column", "period_column", "note"):
                    if not cr.get(fld):
                        err(f"{cid}:{iid}: pdf_crossref missing field {fld}")
                if cr.get("kind") not in ("exact_thousands", "crosscheck_rounded_mn"):
                    err(f"{cid}:{iid}: pdf_crossref kind invalid: {cr.get('kind')}")
                pt = page_text(cr["pdf"][:-4], cr["pdf_page"])
                if pt is None:
                    err(f"{cid}:{iid}: crossref page missing {cr.get('pdf')} p{cr.get('pdf_page')}")
                else:
                    # review fix §3.5: not just "page exists" — the cited row and the exact
                    # value must be on that page (row/column/period verified at build time
                    # via .cache/candidates/anchor_check.py, 53/53 OK 2026-10-05).
                    if cr.get("kind") == "exact_thousands":
                        want = f"{round(ri['value'] * 1000):,}"
                        if want not in pt:
                            err(f"{cid}:{iid}: exact thousands {want} not on cited page {cr['pdf']} p{cr['pdf_page']}")
                    elif cr.get("kind") == "crosscheck_rounded_mn":
                        if abs(ri["value"] - round(ri["value"])) > 0.02:
                            err(f"{cid}:{iid}: crosscheck_rounded_mn value should be a rounded-millions magnitude check")
                        want = f"{int(round(ri['value'])):,}"
                        if want not in pt and f"{int(round(ri['value']))}" not in pt:
                            err(f"{cid}:{iid}: rounded millions {want} not on cited page")
                        if not cr.get("note") or "容差" not in cr.get("period_column", "") + cr.get("note", ""):
                            err(f"{cid}:{iid}: crosscheck_rounded_mn must state rounding tolerance in note/period_column")
                    if cr.get("row_label") and norm(cr["row_label"]) not in norm(pt):
                        err(f"{cid}:{iid}: row label '{cr['row_label']}' not on cited page {cr['pdf']} p{cr['pdf_page']}")
        elif src.get("type") == "pdf":
            raw = src.get("raw_value", "")
            nums = re.findall(r"\(?-?[\d,]+\)?(?:\s*千美元)?", raw)
            tok = re.search(r"\((-?[\d,]+)\)|(-?[\d,]+)", raw)
            if not tok:
                err(f"{cid}:{iid}: no number in raw_value")
            else:
                want = num_from_raw(raw)
                if abs(want - ri["value"]) > 0.0005:
                    err(f"{cid}:{iid}: value {ri['value']} vs raw {raw} -> {want}")
                stem = src["pdf"][:-4]
                pt = page_text(stem, src["pdf_page"])
                if pt is None:
                    err(f"{cid}:{iid}: no cached text for {src['pdf']}")
                else:
                    key = tok.group(0).replace(" ", "")
                    if norm(key.lstrip("(").rstrip(")") if not key.startswith("(") else key) not in norm(pt):
                        # try bare digits with/without parentheses
                        bare = tok.group(1) or tok.group(2)
                        if norm(bare) not in norm(pt):
                            err(f"{cid}:{iid}: raw number '{key}' not on page {src['pdf_page']} of {stem}")
            if src.get("db_match") is not None:
                v = db_value("operating_income", ri["fiscal_year"], ri["fiscal_quarter"])
                if v is None or abs(v - src["db_match"]["value"]) > 0.001:
                    err(f"{cid}:{iid}: db_match mismatch vs live DB")
        else:
            err(f"{cid}:{iid}: source.type missing/invalid")
    for c in rec["calculations"]:
        cidc = c.get("calculation_id")
        if not cidc or cidc in ids:
            err(f"{cid}: duplicate/missing calculation_id {cidc}")
        ids.add(cidc)
    for c in rec["calculations"]:
        for ref in c["inputs"]:
            if ref not in ids:
                err(f"{cid}: calc {c['calculation_id']} refs unknown id {ref}")
        if c.get("unit") is None:
            err(f"{cid}: calc {c['calculation_id']} missing unit")
        if c.get("result") is None:
            err(f"{cid}: calc {c['calculation_id']} result None")
        if c.get("tolerance_abs") is None and c.get("tolerance_pct") is None:
            err(f"{cid}: calc {c['calculation_id']} no tolerance")
        if c.get("tolerance_scope") != "internal_selfcheck_only":
            err(f"{cid}: calc {c['calculation_id']} tolerance_scope != internal_selfcheck_only (review fix 3.2)")
        val = arith(c["formula"])
        if val is not None:
            if abs(val - c["result"]) > c["tolerance_abs"] + 1e-9:
                err(f"{cid}: calc {c['calculation_id']} formula '{c['formula']}' evaluates {val} vs result {c['result']} (tol {c['tolerance_abs']})")
        else:
            if not re.search(r"[A-Za-z\u4e00-\u9fff]", c["formula"]):
                err(f"{cid}: calc {c['calculation_id']} pure-numeric formula failed to eval: {c['formula']}")
    for a in rec["scoring"].get("numeric_assertions", []):
        if a.startswith("calc:") and a[5:] not in {c["calculation_id"] for c in rec["calculations"]}:
            err(f"{cid}: numeric_assertion unknown calc {a}")
    # review fix §3.2/§3.3: layered assertions + separate answer-display check required
    sc = rec["scoring"]
    calc_ids = {c["calculation_id"] for c in rec["calculations"]}
    if "answer_numeric_check" not in sc:
        err(f"{cid}: scoring.answer_numeric_check missing (display tolerance separated from internal)")
    if "assertion_policy" not in sc:
        err(f"{cid}: scoring.assertion_policy missing")
    layers = {"core": [a[5:] if a.startswith("calc:") else a for a in sc.get("numeric_assertions", [])],
              "optional": sc.get("optional_support_assertions"),
              "method": sc.get("method_dependent_assertions"),
              "internal": sc.get("internal_selfcheck_only")}
    for k, v in layers.items():
        if v is None:
            err(f"{cid}: scoring layer '{k}' missing")
            continue
        if len(v) != len(set(v)):
            err(f"{cid}: scoring layer '{k}' has duplicates")
        for x in v:
            if x not in calc_ids:
                err(f"{cid}: layer '{k}' references unknown calc {x}")
    flat = sum((v for v in layers.values() if v is not None), [])
    if len(flat) != len(set(flat)):
        err(f"{cid}: assertion layers overlap (same calc in two layers)")
    if set(flat) != calc_ids:
        err(f"{cid}: assertion layers must cover all {len(calc_ids)} calcs, missing {sorted(calc_ids - set(flat))}")
    if "optional_assertions" in sc:
        err(f"{cid}: legacy scoring.optional_assertions should be replaced by optional_support_assertions")
    for layer in ("layer1_facts", "layer2_assumptions_boundaries", "layer3_followups"):
        if not rec["gold_evaluator_side"].get(layer):
            err(f"{cid}: gold missing {layer}")
    rv = json.dumps(rec["run_side_visible"], ensure_ascii=False)
    for forbidden_num in ["152.361", "1690.299", "1522.683", "631.046", "10.3", "56 个基点"]:
        if forbidden_num in rv:
            err(f"{cid}: run_side_visible leaks answer value {forbidden_num}")
    # review fix §3.1: answer numbers alone are not enough — the run side must not hand the
    # Agent this case's full solving route (bridge/decomposition formulas, differential steps,
    # acceptance clauses, residual-zero clauses). Routes were relocated to evaluator side at
    # build time (ROUTE_MARKERS); metric/period/统一指标定义 remain visible.
    for route in FORBIDDEN_ROUTES:
        if route in rv:
            err(f"{cid}: run_side_visible leaks solving route '{route}' (review fix 3.1)")
    if rec.get("diagnostic_query") is not None and cid != "LV-FY2025Q3-OPEXRATIO-56BPS-001":
        err(f"{cid}: unexpected diagnostic_query")
    if cid == "LV-FY2025Q3-OPEXRATIO-56BPS-001" and rec.get("diagnostic_query") != o["query"]:
        err(f"{cid}: diagnostic_query must be the original query verbatim")

# cross-case leakage fields
groups = {}
for rec in recs:
    groups.setdefault(rec["split_group"], []).append(rec["case_id"])
print("split_groups:", json.dumps(groups, ensure_ascii=False))
if not any(len(v) > 1 for v in groups.values()):
    err("split_group clustering expected overlapping cases")
con.close()

if errors:
    print(f"ERRORS={len(errors)}")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("selected_v1 self-check: errors=0  (10 cases, DB/PDF provenance, formulas, queries verified)")

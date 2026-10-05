# -*- coding: utf-8 -*-
"""自查第二轮：把 7 条记录中含省略号/跨表行的 evidence_spans 换成缓存逐字可匹配引用（幂等）。"""
import json
from pathlib import Path

CACHE = Path(__file__).resolve().parent

def load(stem):
    p = CACHE / f"{stem}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()], p

def save(stem, recs, p):
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")

def rec_of(recs, cid):
    for r in recs:
        if r["case_id"] == cid:
            return r
    raise AssertionError(f"missing {cid}")

def replace_span(r, old_prefix, new_spans):
    spans = r["management_reference"]["evidence_spans"]
    idx = None
    for i, sp in enumerate(spans):
        q = sp.get("quote", sp.get("span", ""))
        if q.startswith(old_prefix):
            idx = i
            break
    if idx is None:
        # 幂等：旧 span 已不在——若新 spans 全在则视为已应用
        for ns in new_spans:
            if not any(s.get("quote") == ns["quote"] and s.get("pdf_page") == ns["pdf_page"] for s in spans):
                raise AssertionError(f"{r['case_id']}: old span '{old_prefix[:18]}' gone but new not present")
        return
    if spans[idx].get("quote") == new_spans[0]["quote"] and len(new_spans) == 1:
        return
    spans[idx:idx + 1] = new_spans

def add_page(r, page):
    s = r["sources"][0]
    if page not in s["pdf_pages"]:
        s["pdf_pages"] = sorted(set(s["pdf_pages"]) | {page})
        s["printed_pages"] = s["pdf_pages"]

# ---- FY26Q2 ----
recs, p = load("FY26Q2")
r = rec_of(recs, "LV-FY2026Q2-Q2VSH1-001")
replace_span(r, "收入 20,452", [
    {"pdf_page": 8, "quote": "收入 20,452 17,850 15%"},
    {"pdf_page": 8, "quote": "經營溢利 643 651 (1)%"},
    {"pdf_page": 8, "quote": "公司權益持有人應佔溢利 340 359 (5)%"}])
replace_span(r, "集團錄得與認股權證相關的衍生金融負債公允值虧損", [
    {"pdf_page": 9, "quote": "集團錄得與認股權證相關的衍生金融負債公允值虧損 1.48 億美元 (二零二四 / 二五年：無)"}])
add_page(r, 9)
r = rec_of(recs, "LV-FY2026Q2-INTERIM-DIV-FLAT-001")
replace_span(r, "中期股息每股 8.5 港幣仙", [
    {"pdf_page": 2, "quote": "中期股息每股 8.5 港仙（二零二四 / 二五年：8.5 港仙），合共約 1.354 億美元（二零二四 / 二五年：約 1.355億美元）"}])
save("FY26Q2", recs, p)

# ---- FY26Q1 ----
recs, p = load("FY26Q1")
r = rec_of(recs, "LV-FY2026Q1-GM-OI-DIVERGE-001")
replace_span(r, "非香港財務報告準則的經營溢利", [
    {"pdf_page": 10, "quote": "非香港財務報告準則 631,046 497,341 412,216 389,403"}])
r = rec_of(recs, "LV-FY2026Q1-REPORTED-VS-ADJ-001")
replace_span(r, "呈報 784,809", [
    {"pdf_page": 10, "quote": "呈報 784,809 622,104 537,550 505,333"},
    {"pdf_page": 10, "quote": "非香港財務報告準則 631,046 497,341 412,216 389,403"}])
r = rec_of(recs, "LV-FY2026Q1-IDG-VOLUME-IDENT-001")
replace_span(r, "個人電腦業務以24.6%", [
    {"pdf_page": 2, "quote": "個人電腦業務以24.6%的全球個人電腦市場份額創下新高"},
    {"pdf_page": 2, "quote": "以24.6%的全球個人電腦市場份額創下新高，較去年同期提升1.7個百分點"}])
replace_span(r, "受個人電腦及智能手機的平均售價上升", [
    {"pdf_page": 2, "quote": "受個人電腦及智能手機的平均售價上升，以及高端及商業市場的強力推動，盈利能力保持在健康的歷史區間"}])
r = rec_of(recs, "LV-FY2026Q1-EPS-DILUTION-001")
replace_span(r, "基本 4.12", [
    {"pdf_page": 1, "quote": "基本 4.12美仙 1.99 美仙 2.13美仙"},
    {"pdf_page": 1, "quote": "攤薄 3.65美仙 1.92 美仙 1.73美仙"}])
save("FY26Q1", recs, p)

# ---- FY25Q4 ----
recs, p = load("FY25Q4")
r = rec_of(recs, "LV-FY2025Q4-Q4-COLLAPSE-VS-FY-001")
replace_span(r, "第四季度的權益持有人應佔溢利同比下降", [
    {"pdf_page": 1, "quote": "第四季度的權益持有人應佔溢利同比下降 64%，主要是由於非現金認股權證公允值虧損所致；排除非現金費用，非香港財務報告準則的權益持有人應佔溢利則上升25%，反映出強勁的營運表現"}])
replace_span(r, "與認股權證相關的衍生金融負債公允值虧損", [
    {"pdf_page": 10, "quote": "與認股權證相關的衍生金融負債公允值虧損 (118,275) -"},
    {"pdf_page": 10, "quote": "簽出認沽期權負債的重新計量收益 - 143,430"}])
save("FY25Q4", recs, p)

print("span fixups round2 OK")

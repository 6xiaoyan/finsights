# -*- coding: utf-8 -*-
"""自查后对 .cache/candidates/*.jsonl 的定点修正（幂等：已修则跳过，模式不匹配则报错）。

修正内容（仅真实缺陷，不含历史 writer 已具备的 tolerance_abs=0）：
- FY27Q1：三处 span 页码未列入 sources.pdf_pages（OI-BRIDGE p2 / WC-CCC p3 / INV-DIO p3）；
  INV-DIO 删除对 FY26Q1 公告的空页码引用（该输入实际来自 DB，不引该 PDF 文字）；
  SEGM-CALIBER/PROFIT-SEQ/FX-GEO/INV-DIO 四条 narrative 计算补 result 或 formula。
- FY26Q4：GM-OPEX/Q4ACCEL 补 result；INFINIDAT 把含省略号的 span 拆为可精确匹配的单行引用。
- FY26Q3：TAXFLIP 两条 span 改为缓存原文逐字可匹配形式（p14 行实际在 p13；p1 表行换行）；
  TAXFLIP/GM-DRIFT/RESTRUCT 补 result。
"""
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

def add_page(r, pdf_sub, page):
    for s in r["sources"]:
        if pdf_sub in s["pdf"]:
            if page not in s["pdf_pages"]:
                s["pdf_pages"] = sorted(set(s["pdf_pages"]) | {page})
                s["printed_pages"] = s["pdf_pages"]
            return
    raise AssertionError(f"{r['case_id']}: no source {pdf_sub}")

def set_calc(r, cid_calc, **kv):
    for c in r["calculations"]:
        if c["id"] == cid_calc:
            before = {k: c.get(k) for k in kv}
            if all(before[k] == kv[k] for k in kv):
                return  # already applied
            if any(v is not None and k in c for k, v in kv.items()):
                raise AssertionError(f"{r['case_id']}/{cid_calc}: conflict {kv}")
            c.update(kv)
            return
    raise AssertionError(f"{r['case_id']}: calc {cid_calc} not found")

def set_span(r, prefixes, new):
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    prefixes = list(prefixes) + [new["quote"][:12]]
    for sp in r["management_reference"]["evidence_spans"]:
        q = sp.get("quote", sp.get("span", ""))
        if any(q.startswith(p) for p in prefixes):
            if sp.get("pdf_page", sp.get("page")) == new["pdf_page"] and q == new["quote"]:
                return
            sp.clear(); sp.update(new)
            return
    raise AssertionError(f"{r['case_id']}: span not found")

# ---- FY27Q1 ----
recs, p = load("FY27Q1")
add_page(rec_of(recs, "LV-FY2027Q1-OI-BRIDGE-001"), "FY27Q1", 2)
add_page(rec_of(recs, "LV-FY2027Q1-WC-CCC-001"), "FY27Q1", 3)
r = rec_of(recs, "LV-FY2027Q1-INV-DIO-001")
add_page(r, "FY27Q1", 3)
before = len(r["sources"])
r["sources"] = [s for s in r["sources"] if "FY26Q1" not in s["pdf"]]
set_calc(r, "dio_delta", formula="dio_curr − dio_prior = 55.5587 − 47.2317")
r2 = rec_of(recs, "LV-FY2027Q1-SEGM-CALIBER-001")
set_calc(r2, "seg_contrib", result=[3646.180, 4219.929, 625.910, -379.122], unit="usd_mn")
set_calc(r2, "op_growth_rank", result=[862.458, 259.620, 196.001, -208.448], unit="usd_mn")
set_calc(rec_of(recs, "LV-FY2027Q1-PROFIT-SEQ-001"), "gp_margin", result=[14.7344, 16.5241, 1.7897])
set_calc(rec_of(recs, "LV-FY2027Q1-FX-GEO-001"), "rev_yoy_abs", formula="26,942.766 − 18,829.869 = 8,112.897")
save("FY27Q1", recs, p)

# ---- FY26Q4 ----
recs, p = load("FY26Q4")
set_calc(rec_of(recs, "LV-FY2026Q4-GM-OPEX-001"), "margin_check", result=[15.4192, 16.0655, -0.6463])
r = rec_of(recs, "LV-FY2026Q4-Q4ACCEL-001")
set_calc(r, "yoy_series", result=[26.8, 23.1, 16.7, 6.8, 0.2, -4.4, -24.1, -24.3, -23.9, -15.7,
                                  3.0, 9.5, 19.7, 23.9, 19.6, 22.8, 21.9, 14.6, 18.1, 27.1])
set_calc(r, "window_check", unit="判定",
         result="FY22Q1–FY26Q4 二十季窗口内 +27.1% 为最高；若窗口含 FY21Q4（+47.7%）则非最高")
set_span(rec_of(recs, "LV-FY2026Q4-INFINIDAT-001"), "業務合併之初始",
         {"pdf_page": 38, "quote": "業務合併之初始會計處理尚未完成。因此，由於估值程序仍在進行中，包括所收購可識別資產"})
save("FY26Q4", recs, p)

# ---- FY26Q3 ----
recs, p = load("FY26Q3")
r = rec_of(recs, "LV-FY2026Q3-TAXFLIP-001")
set_span(r, "一次性所得稅抵免", {"pdf_page": 13, "quote": "一次性所得稅抵免 - - (282,000) (282,000)"})
set_span(r, "調整後的公司權益持有人應佔溢利",
         {"pdf_page": 1, "quote": "調整後的公司權益持有人應佔溢利 589 1,490 435 1,163 36% 28%"})
set_calc(r, "attr_check", result=[546.0, 693.0, -21.2], unit="百万美元 / 百分比")
r = rec_of(recs, "LV-FY2026Q3-GM-DRIFT-001")
set_calc(r, "gm_q", result=[[16.57, 15.66, 15.74], [14.73, 15.39, 15.08]])
set_calc(r, "gm_yoy_q", result=[-1.84, -0.27, -0.63, -0.88])
set_calc(r, "weight_effect", result=[15.077, 15.067])
r = rec_of(recs, "LV-FY2026Q3-RESTRUCT-001")
set_calc(r, "split_check", result=[224.2, 285.0, -60.8])
set_calc(r, "cash_split", result=[236.7, 78.3])
save("FY26Q3", recs, p)

print("fixups applied OK")

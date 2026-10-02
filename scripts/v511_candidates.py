"""Q11 步骤3：把 V5.11 候选汇合成全字段清单（docs/evidence/V5.11.json）。

数据路径：读 docs/evidence/V5.11.csv（|z|>2.0，eval/generators/make_l3.py 生成）。
披露路径：本地公开披露语料（data/disclosures，仅联想繁中正文）按科目/事件关键词
定位，命中则并入同一候选（discovery_paths=data+disclosure）；披露侧新发现、
数据侧无异常的事件单独成条（discovery_paths=disclosure，factor_coverage 记
"现有因子未覆盖/抵消/滞后"，不强行制造对应异常）。

确定性脚本：只定位与摘录，不下结论。所有 review_status=pending_human(V5.12)，
正式 gold 必须人工独立确认（handoff_event_attribution.md 步骤3）。

运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/v511_candidates.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")

from agent.disclosure import load as load_corpus  # noqa: E402
from agent.disclosure import search as search_corpus  # noqa: E402
from agent.tools.db import connect  # noqa: E402

DB = "data/finsights.duckdb"
CSV_IN = Path("docs/evidence/V5.11.csv")
JSON_OUT = Path("docs/evidence/V5.11.json")
METHOD_VERSION = "v511-r3（双路径汇合，全字段 schema，handoff 步骤3）"

# 科目 → 披露检索关键词（联想语料为繁体正文）
ACCOUNT_QUERY = {
    "inventory": "存貨 庫存 渠道",
    "accounts_payable": "應付貿易賬款 應付票據 貨款",
    "accounts_receivable": "應收貿易賬款 應收票據 信貸風險",
}

# 披露驱动的定向扫描（事件类型提示，不是硬编码答案；handoff 步骤3 列表的代表词）
EVENT_SCAN = [
    ("channel_destocking", "需求疲弱 渠道 存貨 過剩"),
    ("impairment_restructuring", "減值 重組"),
    ("ma_divestiture", "收購 出售 分拆"),
    ("fx", "匯率 外幣"),
    ("supply_chain", "供應鏈 供應"),
    ("reclassification", "重列 重新分類 應收貿易賬款、租賃款"),
]

SCHEMA_NOTE = ("候选由脚本机械定位+摘录，review_status 全部 pending_human(V5.12)；"
               "数据候选不是原因 gold；披露陈述不等于已证实因果。"
               "披露扫描规则：仅取公告叙述段 p1–p8，score≥5 且引文含≥2个检索词（确定性、可复跑）。")

# 联想季度业绩公告的叙述段（业务回顾/展望/财务回顾）约在 p1–p8；财报表与附注页
# 出现事件词多为口径性套话，故披露扫描仅取该页段并要求引文命中 ≥2 个检索词。
MDA_MAX_PAGE = 8
MIN_SCORE = 5.0


def _n_terms(query: str, text: str) -> int:
    return sum(1 for t in query.split() if t in text)

LIMITS_DATA = ("z 基线样本仅 7–8 个同财季环比（|z| 理论上限≈2.47，Q11）；"
               "候选只说明“该期该科目变化异常”，原因须披露/人工佐证。")
LIMITS_DISC = ("来源为交易所公告正文（繁中），刊发日期取自文件名未逐份回验公告页；"
               "HP/Dell 无本地正文语料（SEC XBRL 不含叙述文），披露路径对公司覆盖不全。")


def period_bounds(con, company: str, fy: int, q: int) -> tuple[str, str]:
    # periods 表无 start 列；P1.7 修正后 days = 本期期末 − 上期期末，故 start = end − days + 1
    row = con.execute("SELECT period_end, days FROM periods WHERE company_id=? "
                      "AND fiscal_year=? AND fiscal_quarter=?", [company, fy, q]).fetchone()
    if not row:
        return ("", "")
    end = row[0]
    start = end - timedelta(days=int(row[1]) - 1)
    return (str(start), str(end))


def disclosure_hit(corpus, company: str, fyq: str, query: str) -> dict | None:
    hits = search_corpus(corpus, query, company=company, period=fyq, as_of=None, top_k=3)
    for h in hits:
        page = int(h["citation"].split("#p")[-1])
        if h["score"] >= MIN_SCORE and 2 <= page <= MDA_MAX_PAGE and _n_terms(query, h["text"]) >= 2:
            return h
    return None


def build() -> int:
    con = connect(DB)
    corpus = load_corpus("data/disclosures")
    idx = json.loads((Path("data/disclosures") / "index.json").read_text(encoding="utf-8"))
    pub = {(d["company"], f"FY{d['fiscal_year']}Q{d['fiscal_quarter']}"): d
           for d in idx["documents"]}

    candidates: list[dict] = []
    reviewed: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def new_id(kind: str) -> str:
        n = sum(1 for c in candidates if c["event_id"].startswith(kind)) + 1
        return f"{kind}{n:02d}"

    # ---- 数据路径候选（含披露并证）
    with CSV_IN.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        comp, fyq, acct = r["company_id"], r["period"], r["account"]
        z = float(r["z"])
        fy = int(fyq[2:6]); q = int(fyq[-1])
        pstart, pend = period_bounds(con, comp, fy, q)
        d = pub.get((comp, fyq))
        hit = disclosure_hit(corpus, comp, fyq, ACCOUNT_QUERY.get(acct, acct)) if comp == "Lenovo" else None
        paths = ["data"] + (["disclosure"] if hit else [])
        candidates.append({
            "event_id": new_id("d"),
            "company": comp, "fiscal_period": fyq,
            "period_start": pstart, "period_end": pend,
            "published_at": d["published_at"] if d else None,
            "discovery_paths": paths,
            "event_type": "unexplained_anomaly",
            "event_description": f"{acct} 环比变化相对历年同季度基线 z={z:+.3f}"
                                 f"（|z|>2.0），方向{'上升' if z > 0 else '下降'}",
            "selection_reason": f"数据路径：|z|={abs(z):.3f}>2.0（Q11 降阈值，基线上限≈2.47）",
            "affected_metrics": [acct],
            "formula_or_query": "z=(本期环比−同财季历年环比均值)/该分布σ（排除当前点；n≥3 且方差>0）",
            "factor_coverage": "已计算因子：环比 z；未计算：同比/分部/价格-销量拆分",
            "document_id": hit["document_id"] if hit else (d["document_id"] if d else None),
            "source_location": hit["citation"] if hit else None,
            "source_quote": hit["text"][:400] if hit else None,
            "competing_explanations": None,
            "missing_data": None if comp == "Lenovo" else "本地披露语料无此公司正文，无法核对管理层解释",
            "evidence_limits": LIMITS_DATA + (LIMITS_DISC if hit
                                              else " 该公司/期叙述段未检索到对应正文。"),
            "review_status": "pending_human(V5.12)",
            "method_version": METHOD_VERSION,
            "split": "dev" if comp == "Lenovo" else "holdout",
        })
        if hit:
            seen.add((comp, fyq, hit["citation"]))

    # ---- 披露路径定向扫描（联想；数据侧无异常也可入选）
    for comp in ["Lenovo"]:
        for fyq in ["FY2022Q3", "FY2023Q1", "FY2023Q2", "FY2023Q3", "FY2023Q4", "FY2024Q1"]:
            d = pub.get((comp, fyq))
            if not d:
                continue
            for etype, query in EVENT_SCAN:
                hits = [h for h in search_corpus(corpus, query, company=comp, period=fyq,
                                                 as_of=None, top_k=4)
                        if h["score"] >= MIN_SCORE and 2 <= int(h["citation"].split("#p")[-1]) <= MDA_MAX_PAGE
                        and _n_terms(query, h["text"]) >= 2]
                reviewed.append({"company": comp, "fiscal_period": fyq,
                                 "document_id": d["document_id"], "scan_query": query,
                                 "event_type": etype,
                                 "n_hits": len(hits),
                                 "result": "no_significant_event" if not hits
                                           else "; ".join(h["citation"] for h in hits)})
                for h in hits:
                    key = (comp, fyq, h["citation"])
                    dup = next((c for c in candidates
                                if c["company"] == comp and c["fiscal_period"] == fyq
                                and "disclosure" in c["discovery_paths"]
                                and c["source_location"] == h["citation"]), None)
                    if dup is not None:
                        dup.setdefault("disclosure_events", []).append(
                            {"event_type": etype, "scan_query": query})
                        continue
                    fy = int(fyq[2:6]); q = int(fyq[-1])
                    pstart, pend = period_bounds(con, comp, fy, q)
                    candidates.append({
                        "event_id": new_id("e"),
                        "company": comp, "fiscal_period": fyq,
                        "period_start": pstart, "period_end": pend,
                        "published_at": d["published_at"],
                        "discovery_paths": ["disclosure"],
                        "event_type": etype,
                        "event_description": f"披露正文命中事件词（{query}），未见对应 |z|>2.0 数据候选",
                        "selection_reason": "披露路径主动扫描；按 handoff 步骤3，无显著指标变化仍可入选",
                        "affected_metrics": [],
                        "formula_or_query": None,
                        "factor_coverage": "现有因子未覆盖/抵消/滞后（未生成对应 z 异常）",
                        "document_id": h["document_id"],
                        "source_location": h["citation"],
                        "source_quote": h["text"][:400],
                        "competing_explanations": None,
                        "missing_data": "未做数据侧对应异常验证（扫描顺序：先登记，后续核对）",
                        "evidence_limits": LIMITS_DISC,
                        "review_status": "pending_human(V5.12)",
                        "method_version": METHOD_VERSION,
                        "split": "dev",
                    })
                    seen.add(key)

    con.close()
    out = {"generated_at": date.today().isoformat(),
           "schema_note": SCHEMA_NOTE,
           "method_version": METHOD_VERSION,
           "field_order": ["event_id", "company", "fiscal_period", "period_start", "period_end",
                           "published_at", "discovery_paths", "event_type", "event_description",
                           "selection_reason", "affected_metrics", "formula_or_query",
                           "factor_coverage", "document_id", "source_location", "source_quote",
                           "competing_explanations", "missing_data", "evidence_limits",
                           "review_status", "method_version", "split"],
           "n_data_candidates": sum(1 for c in candidates if c["event_id"].startswith("d")),
           "n_disclosure_only": sum(1 for c in candidates if c["event_id"].startswith("e")),
           "reviewed_documents": reviewed,
           "candidates": candidates}
    JSON_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"V5.11.json：数据候选 {out['n_data_candidates']} 条"
          f"（并入披露 {sum(1 for c in candidates if len(c['discovery_paths']) == 2)} 条），"
          f"披露新发现 {out['n_disclosure_only']} 条；审阅扫描 {len(reviewed)} 条 → {JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())

# -*- coding: utf-8 -*-
"""联想归因候选集交付前自查（handoff §3/§6 步骤 6）。

校验项：
1. JSONL 可解析、case_id 全局唯一、总数在 45–55。
2. §3 必备字段齐全、枚举合法（question_origin / db_support.status）。
3. sources：每个 sha256/url 与 source_inventory.csv 逐字一致且为 64 位；
   候选的 fiscal_year/quarter/period_end/publication_date 与清单行一致；
   pdf_pages 非空且页数不超过清单 pdf_pages。
4. management_reference.evidence_spans：每条有 pdf_page+quote，页码在来源声明的
   pdf_pages 内；quote 经 NFKC 归一并去除空白后，能在对应 PDF 缓存文本的该页
   精确匹配（跨行缓存含换行，故去空白匹配）。
5. required_inputs / calculations 非空或注明；每条 calculation 有 formula、
   result（非 None）与 tolerance_abs（或 tolerance_pct）。
6. scorable_answer 四个列表齐全；ready 案例 must_include 非空；
   needs_numeric_data 案例 missing_fields 非空。
7. source_inventory：read=yes 当且仅当该文件出现在候选 sources 中，
   candidates 计数与候选中引用该文件的 case 数一致（按缓存 stem 计）。
"""
from __future__ import annotations

import csv
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DELIV = ROOT / "data/eval/lenovo_attribution_candidates"
TEXTS = ROOT / ".cache/lenovo_pdf_text"

ORIGINS = {"explicit_qa", "derived_from_management_discussion", "analyst_constructed"}
DB_STATUS = {"ready", "needs_numeric_data", "boundary_only", "reject"}
REQUIRED = ["case_id", "event_family_id", "question_origin", "company", "fiscal_year",
            "fiscal_quarter", "period_end", "publication_date", "query", "sources",
            "management_reference", "required_inputs", "calculations", "scorable_answer",
            "db_support", "tags", "difficulty_rationale", "review_status"]

errors: list[str] = []


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if not ch.isspace())


def load_pages(stem: str) -> dict[int, str]:
    raw = (TEXTS / f"{stem}.txt").read_text(encoding="utf-8")
    pages: dict[int, str] = {}
    cur = None
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("<<<PAGE ") and line.endswith(">>>"):
            if cur is not None:
                pages[cur] = "\n".join(buf)
            cur = int(line[8:-3])
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        pages[cur] = "\n".join(buf)
    return pages


def main() -> None:
    recs = [json.loads(l) for l in (DELIV / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"records={len(recs)}")
    if not (45 <= len(recs) <= 55):
        errors.append(f"total {len(recs)} outside 45-55")

    ids = [r["case_id"] for r in recs]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errors.append(f"duplicate case_id: {sorted(dup)}")

    with (DELIV / "source_inventory.csv").open(encoding="utf-8-sig") as f:
        inv = list(csv.DictReader(f))
    by_pdf = {row["file"]: row for row in inv}

    cited_cases: dict[str, set[str]] = defaultdict(set)   # pdf -> case_ids citing it
    stem_cases: dict[str, set[str]] = defaultdict(set)    # FYxxQy -> case_ids (file-derived)

    page_cache: dict[str, dict[int, str]] = {}

    for r in recs:
        cid = r.get("case_id", "?")
        for k in REQUIRED:
            if k not in r or r[k] in (None, ""):
                errors.append(f"{cid}: missing field {k}")
        if r.get("review_status") != "unreviewed":
            errors.append(f"{cid}: review_status={r.get('review_status')}")
        if r.get("company") != "Lenovo":
            errors.append(f"{cid}: company={r.get('company')}")
        if r.get("question_origin") not in ORIGINS:
            errors.append(f"{cid}: bad question_origin")
        dbs = r.get("db_support", {})
        if dbs.get("status") not in DB_STATUS:
            errors.append(f"{cid}: bad db_support.status")
        if not dbs.get("checked_at"):
            errors.append(f"{cid}: db_support.checked_at empty")
        if "missing_fields" not in dbs:
            errors.append(f"{cid}: db_support.missing_fields absent")
        if dbs.get("status") == "needs_numeric_data" and not dbs.get("missing_fields"):
            errors.append(f"{cid}: needs_numeric_data without missing_fields")

        sa = r.get("scorable_answer", {})
        for k in ("must_include", "acceptable_variants", "must_not_claim", "unknowns"):
            if k not in sa or not isinstance(sa[k], list):
                errors.append(f"{cid}: scorable_answer.{k} missing/not list")
        if dbs.get("status") == "ready" and not sa.get("must_include"):
            errors.append(f"{cid}: ready but empty must_include")

        # sources vs inventory
        claimed_pages: set[int] = set()
        for s in r.get("sources", []):
            row = by_pdf.get(s.get("pdf", ""))
            if row is None:
                errors.append(f"{cid}: unknown pdf {s.get('pdf')}")
                continue
            if s.get("sha256") != row["sha256_full"] or len(s.get("sha256", "")) != 64:
                errors.append(f"{cid}: sha256 mismatch for {s['pdf']}")
            if s.get("url") != row["url"]:
                errors.append(f"{cid}: url mismatch for {s['pdf']}")
            pp = s.get("pdf_pages") or []
            if not pp:
                errors.append(f"{cid}: empty pdf_pages for {s['pdf']}")
            max_page = int(row["pdf_pages"])
            if any(p > max_page for p in pp):
                errors.append(f"{cid}: page beyond {max_page} in {s['pdf']}")
            if s.get("printed_pages") != pp:
                errors.append(f"{cid}: printed!=pdf for {s['pdf']}")
            # 期间身份仅对本公告自身的来源行校验（跨公告引用不改记录期间）
            if row["publication_date"] == r.get("publication_date"):
                inv_fy = 2000 + int(row["fiscal_year"].replace("FY", ""))
                inv_q = int(row["fiscal_quarter"].replace("Q", ""))
                if r.get("fiscal_year") != inv_fy or r.get("fiscal_quarter") != inv_q:
                    errors.append(f"{cid}: fy/q mismatch vs inventory for {s['pdf']}")
                if r.get("period_end") != row["period_end"]:
                    errors.append(f"{cid}: period_end mismatch vs inventory for {s['pdf']}")
            claimed_pages.update(pp)
            cited_cases[s["pdf"]].add(cid)

        # spans match cached text
        mr = r.get("management_reference", {})
        if not mr.get("paraphrase"):
            errors.append(f"{cid}: management_reference.paraphrase empty")
        for sp in mr.get("evidence_spans", []):
            page = sp.get("pdf_page")
            quote = sp.get("quote", "")
            if not page or not quote:
                errors.append(f"{cid}: span missing pdf_page/quote")
                continue
            if page not in claimed_pages:
                errors.append(f"{cid}: span page {page} not in claimed pdf_pages")
            ok = False
            for src in r["sources"]:
                stem = src["pdf"][:-4]
                pages = page_cache.get(stem)
                if pages is None:
                    try:
                        pages = load_pages(stem)
                        page_cache[stem] = pages
                    except FileNotFoundError:
                        errors.append(f"{cid}: no cached text for {src['pdf']}")
                        continue
                if page in pages and norm(quote) in norm(pages[page]):
                    ok = True
                    break
            if not ok:
                errors.append(f"{cid}: span not found on page {page}: {quote[:30]}...")

        # calculations
        for c in r.get("calculations", []):
            if not c.get("formula"):
                errors.append(f"{cid}: calc {c.get('id')} no formula")
            if c.get("result") is None:
                errors.append(f"{cid}: calc {c.get('id')} result is None")
            if c.get("tolerance_abs") is None and c.get("tolerance_pct") is None:
                errors.append(f"{cid}: calc {c.get('id')} no tolerance")

    for r in recs:
        stem = f"FY{r['fiscal_year'] % 100}Q{r['fiscal_quarter']}"
        stem_cases[stem].add(r["case_id"])

    # inventory consistency
    for row in inv:
        pdf = row["file"]
        read = row["read"] == "yes"
        cited = pdf in cited_cases
        if read != cited:
            errors.append(f"inventory {pdf}: read={row['read']} but cited={cited}")
        stem = f"{row['fiscal_year']}{row['fiscal_quarter']}"
        n = int(row["candidates"] or 0)
        actual = len(stem_cases.get(stem, set()))
        if n != actual:
            errors.append(f"inventory {pdf}: candidates={n} but cases引用该期={actual}")

    print(f"errors={len(errors)}")
    for e in errors:
        print(" -", e)
    if not errors:
        fams = defaultdict(int)
        for r in recs:
            fams[r["event_family_id"]] += 1
        multi = {k: v for k, v in fams.items() if v > 1}
        print(f"OK  families={len(fams)} shared={multi}")


if __name__ == "__main__":
    main()

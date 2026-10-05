# -*- coding: utf-8 -*-
"""联想归因候选集 step 2：合并每份公告的抽取缓存为交付四件套之二/三，并回填源清单。

- 按 source_inventory.csv 的披露日期倒序读取 .cache/candidates/<stem>.jsonl；
- 用 meta.json 补齐 tags/difficulty_rationale，强制 review_status=unreviewed；
- 结构自查：必备字段、数值 result 与公式存在、sources 页码非空（允许显式空并注明）；
- 输出 candidates.jsonl、candidate_index.csv，并把每份公告的 read/candidates 写回
  source_inventory.csv（跳过原因保留）。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DELIV = ROOT / "data/eval/lenovo_attribution_candidates"
CACHE = ROOT / ".cache/candidates"

REQUIRED = ["case_id", "event_family_id", "question_origin", "company", "fiscal_year",
            "fiscal_quarter", "period_end", "publication_date", "query", "sources",
            "management_reference", "required_inputs", "calculations", "scorable_answer",
            "db_support", "tags", "difficulty_rationale", "review_status"]
ORIGINS = {"explicit_qa", "derived_from_management_discussion", "analyst_constructed"}
DB_STATUS = {"ready", "needs_numeric_data", "boundary_only", "reject"}


def main() -> None:
    meta = json.loads((CACHE / "meta.json").read_text(encoding="utf-8"))
    inv_path = DELIV / "source_inventory.csv"
    with inv_path.open(encoding="utf-8-sig") as f:
        inv = list(csv.DictReader(f))
        fields = list(inv[0].keys())
    by_pdf = {row["file"]: row for row in inv}

    records: list[dict] = []
    per_file: dict[str, int] = {}
    for row in inv:  # 已按披露日期倒序
        stem = f"{row['fiscal_year']}{row['fiscal_quarter']}"  # 缓存按 FYxxQy.jsonl 命名
        p = CACHE / f"{stem}.jsonl"
        if not p.exists():
            continue
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            m = meta.get(r["case_id"], {})
            r.setdefault("tags", m.get("tags", []))
            r.setdefault("difficulty_rationale", m.get("difficulty_rationale", ""))
            r["review_status"] = "unreviewed"
            # 早期 writer 的 span 键为 page/span，统一为 §3 交付形态 pdf_page/quote
            for sp in r.get("management_reference", {}).get("evidence_spans", []):
                if "page" in sp and "pdf_page" not in sp:
                    sp["pdf_page"] = sp.pop("page")
                if "span" in sp and "quote" not in sp:
                    sp["quote"] = sp.pop("span")
            # db_support 规范化：missing→missing_fields；checked_at 缺省用整理日期
            dbs = r.get("db_support", {})
            if "missing" in dbs and "missing_fields" not in dbs:
                dbs["missing_fields"] = dbs.pop("missing")
            dbs.setdefault("checked_at", "2026-10-05")
            dbs.setdefault("missing_fields", [])
            missing = [k for k in REQUIRED if k not in r or r[k] in (None, "")]
            assert not missing, (r["case_id"], missing)
            assert r["question_origin"] in ORIGINS, r["case_id"]
            assert r["db_support"]["status"] in DB_STATUS, r["case_id"]
            for s in r["sources"]:
                src = by_pdf.get(s["pdf"])
                assert src is not None, (r["case_id"], "unknown pdf", s["pdf"])
                assert s["sha256"] == src["sha256_full"], (r["case_id"], "sha mismatch", s["pdf"])
                assert s["url"] == src["url"], (r["case_id"], "url mismatch", s["pdf"])
                assert s["sha256"] and len(s["sha256"]) == 64, r["case_id"]
            for calc in r["calculations"]:
                assert calc.get("formula") or calc.get("result") is not None, r["case_id"]
            records.append(r)
            per_file[stem] = n + 1
            n += 1
    seen = set()
    for r in records:
        assert r["case_id"] not in seen, r["case_id"]
        seen.add(r["case_id"])

    with (DELIV / "candidates.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (DELIV / "candidate_index.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "publication_date_desc", "period", "company_fy_q", "query",
                    "event_family_id", "question_origin", "db_support", "source_pages", "tags"])
        for r in records:
            pages = ";".join(str(x) for s in r["sources"] for x in (s.get("pdf_pages") or ["-"]))
            w.writerow([r["case_id"], r["publication_date"], r["period_end"],
                        f"FY{r['fiscal_year']}Q{r['fiscal_quarter']}", r["query"],
                        r["event_family_id"], r["question_origin"], r["db_support"]["status"],
                        pages, "|".join(r["tags"])])

    for row in inv:
        stem = f"{row['fiscal_year']}{row['fiscal_quarter']}"
        if stem in per_file:
            row["read"] = "yes"
            row["candidates"] = str(per_file[stem])
    with inv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(inv)

    from collections import Counter
    print(f"total={len(records)}", Counter(r['db_support']['status'] for r in records),
          Counter(r['question_origin'] for r in records),
          dict(sorted(per_file.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()

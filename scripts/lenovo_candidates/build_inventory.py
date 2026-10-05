# -*- coding: utf-8 -*-
"""联想归因候选集 step 1：按披露日期倒序建源清单 + 完整 SHA256 + 逐页文本缓存。

对应 docs/lenovo_attribution_dataset_handoff.md §2/§6：
- manifest 的 sha256 是截断值，这里补算完整哈希并标记是否匹配前 16 位；
- 按真实披露日期倒序（不是文件名字典序）；
- 文本缓存输出到 .cache/（未提交），正式交付物只有四件套。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manual/lenovo_manifest.json"
RAW_DIR = ROOT / "data/raw/lenovo"
OUT_DIR = ROOT / "data/eval/lenovo_attribution_candidates"
CACHE_DIR = ROOT / ".cache/lenovo_pdf_text"

# 联想财年 3/31 截止：Q1→6/30, Q2→9/30, Q3→12/31, Q4→次年 3/31
_QEND = {1: (6, 30), 2: (9, 30), 3: (12, 31), 4: (3, 31)}


def period_end(fy: str, quarter: str) -> str:
    n = int(fy[2:])          # "FY27" -> 27；财年第 n 季结束于自然年 20xx
    q = int(quarter[1])      # "Q1" -> 1
    m, d = _QEND[q]
    year = 2000 + n - 1 if q <= 3 else 2000 + n
    return f"{year:04d}-{m:02d}-{d:02d}"


def sha256_full(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    recs = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for r in recs:
        pub = datetime.strptime(r["date"].split()[0], "%d/%m/%Y").date()
        p = RAW_DIR / r["file"]
        ok = p.exists()
        full = sha256_full(p) if ok else ""
        match = (full[:16] == r["sha256"]) if ok else None
        pages_txt, page_count, extract_ok = [], 0, False
        if ok:
            try:
                with pdfplumber.open(p) as pdf:
                    page_count = len(pdf.pages)
                    for i, pg in enumerate(pdf.pages, 1):
                        t = pg.extract_text() or ""
                        pages_txt.append(f"<<<PAGE {i}>>>\n{t}")
                    extract_ok = any(t.strip() for t in pages_txt)
            except Exception as e:  # 记录解析缺口，不捏造
                pages_txt = [f"<<<EXTRACT_ERROR>>> {e}"]
        if extract_ok:
            (CACHE_DIR / (p.stem + ".txt")).write_text("\n".join(pages_txt), encoding="utf-8")
        rows.append({
            "publication_date": pub.isoformat(),
            "file": r["file"],
            "fiscal_year": r["fy"],
            "fiscal_quarter": r["quarter"],
            "period_end": period_end(r["fy"], r["quarter"]),
            "url": r["url"],
            "sha256_full": full,
            "manifest_sha_prefix_ok": "" if match is None else str(match).lower(),
            "pdf_pages": page_count,
            "text_extracted": str(extract_ok).lower(),
            "read": "no",
            "candidates": "0",
            "skip_reason": "" if (ok and extract_ok) else ("missing_file" if not ok else "text_extract_failed"),
        })
    rows.sort(key=lambda x: x["publication_date"], reverse=True)
    hdr = list(rows[0].keys()) + ["note"]
    with (OUT_DIR / "source_inventory.csv").open("w", encoding="utf-8-sig", newline="") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=hdr, extrasaction="ignore")
        w.writeheader()
        for x in rows:
            x["note"] = ""
            w.writerow(x)
    print(f"inventory rows={len(rows)} newest={rows[0]['publication_date']} "
          f"hash_mismatch={sum(1 for x in rows if x['manifest_sha_prefix_ok']=='false')} "
          f"extract_fail={sum(1 for x in rows if x['text_extracted']!='true')}")


if __name__ == "__main__":
    main()

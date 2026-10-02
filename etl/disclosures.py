"""Q11 回答一步骤 2：从公开财报 PDF 构建披露索引与页级正文块 → data/disclosures/。

来源：data/raw/lenovo/FYqq_<DDMMYYYYhhmm>.pdf（联想季度业绩公告，公开材料）。
文件名内嵌公告日期；按交接要求记录 SHA256、提取方式与发布时间的推导依据。
HP/Dell：SEC submissions/companyfacts 只有数字与索引、没有 MD&A 正文（回答一步骤 2
明确不能视为已取得经营解释），其正文公告下载后补入库，本脚本暂不处理。

运行：.venv/Scripts/python -m etl.disclosures        （纯本地，无网络）
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "finsights.duckdb"
RAW_DIR = ROOT / "data" / "raw" / "lenovo"
OUT_DIR = ROOT / "data" / "disclosures"

# FY23Q3_170220231201.pdf → 财年季度 + 公告日 DDMMYYYY + hhmm
FNAME_RE = re.compile(r"^FY(\d{2})Q(\d)_(\d{2})(\d{2})(\d{4})\d{4}\.pdf$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _period_info(con, fy: int, q: int) -> dict:
    row = con.execute(
        "SELECT period_end, calendar_quarter, days FROM periods "
        "WHERE company_id='Lenovo' AND fiscal_year=? AND fiscal_quarter=?",
        [fy, q]).fetchone()
    if row is None:
        raise KeyError(f"periods 缺少 Lenovo FY{fy}Q{q}（先跑 P1 入库）")
    end, cal_q, days = row
    return {"period_end": str(end), "period_start": str(end - timedelta(days=int(days) - 1)),
            "calendar_quarter": cal_q, "days": days}


def build(out_dir: Path = OUT_DIR) -> dict:
    con = duckdb.connect(str(DB), read_only=True)
    docs, chunk_total = [], 0
    for pdf_path in sorted(RAW_DIR.glob("FY*Q*_*.pdf")):
        m = FNAME_RE.match(pdf_path.name)
        if not m:
            print(f"[skip] 文件名不含可核发布日期: {pdf_path.name}")
            continue
        fy, q = 2000 + int(m.group(1)), int(m.group(2))
        published = date(int(m.group(5)), int(m.group(4)), int(m.group(3)))
        doc_id = f"lenovo_FY{fy}Q{q}"
        pdf = pdfium.PdfDocument(str(pdf_path))
        chunks = []
        for i in range(len(pdf)):
            text = pdf[i].get_textpage().get_text_range()
            if len(text.strip()) >= 40:      # 纯封面/空白页不入检索库
                chunks.append({"chunk_id": f"{doc_id}#p{i + 1}", "page": i + 1,
                               "section": None, "printed_page": None, "text": text})
        pdf.close()
        (out_dir / "chunks").mkdir(parents=True, exist_ok=True)
        (out_dir / "chunks" / f"{doc_id}.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n",
            encoding="utf-8")
        rel = str(pdf_path.relative_to(ROOT)).replace("\\", "/")
        docs.append({"document_id": doc_id, "company": "Lenovo",
                     "fiscal_year": fy, "fiscal_quarter": q, **_period_info(con, fy, q),
                     "doc_type": "quarterly_results_announcement",
                     "published_at": published.isoformat(),
                     "published_at_basis": "filename（交易所刊发日期，未逐份回验公告页）",
                     "source_file": rel, "sha256": _sha256(pdf_path),
                     "extraction": "pypdfium2 get_textpage", "n_chunks": len(chunks)})
        chunk_total += len(chunks)
    con.close()
    index = {"generated_at": date.today().isoformat(),
             "note": "HP/Dell 披露正文尚未入库（SEC JSON 无正文）；检索/评测按 published_at≤as_of 过滤",
             "documents": docs}
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"documents": len(docs), "chunks": chunk_total, "out": str(out_dir)}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False))

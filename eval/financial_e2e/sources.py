"""官方来源索引：港交所公告标题、URL、发布日期 → PDF 页码锚点。

来源限定为 data/manual/lenovo_manifest.json（港交所 disclosed 公告清单）与
data/raw/lenovo/*.pdf（本地原文）。不引入投资者关系网站等额外渠道。
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.contracts import SourceRef

MANIFEST = Path("data/manual/lenovo_manifest.json")
RAW_DIR = Path("data/raw/lenovo")


def load_manifest(path: Path = MANIFEST) -> dict[tuple[int, int], dict]:
    """返回 {(fy, quarter): {title, url, date, file, sha256}}。fy 形如 2027 表示 FY2027。"""
    import re
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[tuple[int, int], dict] = {}
    for d in data:
        m = re.match(r"FY(\d{2})", d["fy"])
        q = int(str(d["quarter"]).lstrip("Qq"))
        if m:
            out[(2000 + int(m.group(1)), q)] = d
    return out


def write_sources_index(cases, path: str | Path = "eval/datasets/financial_real_v1/sources.json",
                        manifest: dict | None = None) -> Path:
    """把数据集用到的来源汇总成 sources.json（公告标题/URL/发布日期/pdf/sha256/页码）。"""
    import json
    man = manifest or load_manifest()
    by_url: dict[str, dict] = {}
    for c in cases:
        for s in c.sources:
            entry = by_url.setdefault(s.url, {"title": s.title, "url": s.url,
                                              "publish_date": s.publish_date,
                                              "periods": [], "cases": []})
            if s.period not in entry["periods"]:
                entry["periods"].append(s.period)
            if c.case_id not in entry["cases"]:
                entry["cases"].append(c.case_id)
    # 补 pdf 文件名与 sha256（按 URL 匹配 manifest）
    url_to_man = {d["url"]: d for d in man.values()}
    for url, e in by_url.items():
        m = url_to_man.get(url)
        if m:
            e["pdf"] = f"data/raw/lenovo/{m['file']}"
            e["sha256_16"] = m["sha256"]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(list(by_url.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def source_for(fy: int, quarter: int, page: int | None = None, *,
               period: str | None = None, table_row: str | None = None,
               table_col: str | None = None, note: str | None = None,
               manifest: dict | None = None) -> SourceRef:
    man = manifest or load_manifest()
    d = man[(fy, quarter)]
    # date 形如 "13/08/2026 12:01"
    date_part = d["date"].split(" ")[0]
    dd, mm, yyyy = date_part.split("/")
    return SourceRef(
        title=d["title"], url=d["url"], publish_date=f"{yyyy}-{mm}-{dd}",
        period=period or f"FY{fy}Q{quarter}", page=page, table_row=table_row,
        table_col=table_col, note=note,
    )

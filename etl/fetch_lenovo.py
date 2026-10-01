"""P1：从港交所披露易抓取联想季度业绩公告 PDF。

- 每个财年窗口（7/1 至次年 6/30）搜索全部公告，按标题正则筛出 4 类业绩公佈：
  第一季 → FYxxQ1，中期 → FYxxQ2，第三季 → FYxxQ3，全年 → FYxxQ4
- 下载中文版 PDF 到 data/raw/lenovo/，生成 manifest（含 URL、日期、sha256），manifest 副本存 data/manual/ 供追溯（V1.16）。
- 限速：每次请求间隔 >= 0.5s；重试 3 次。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from pathlib import Path

import httpx

STOCK_ID = 2325  # 聯想集團 00992（EDGAR 之外的港交所内部 id，prefix.do 查得）
BASE = "https://www1.hkexnews.hk"
SEARCH = BASE + "/search/titleSearchServlet.do"
OUT_DIR = Path("data/raw/lenovo")
MANUAL_DIR = Path("data/manual")

TITLE_RE = re.compile(r"(第一季|中期|第三季|全年)業績公佈")
QUARTER_MAP = {"第一季": "Q1", "中期": "Q2", "第三季": "Q3", "全年": "Q4"}

UA = {"User-Agent": "FinSights personal project (contact: 1348587884@qq.com)"}


def fetch_json(url: str, params: dict, retries: int = 3) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=UA, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — 网络错误统一退避重试
            last_err = e
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {url} ({last_err})")


def search_window(fy: int) -> list[dict]:
    params = {
        "sortDir": 0, "sortByOptions": "DateTime", "category": 0, "market": "SEHK",
        "stockId": STOCK_ID, "documentType": -1,
        "fromDate": f"{fy}0701", "toDate": f"{fy + 1}0630",
        "title": "", "searchType": 1, "t1code": -2, "t2Gcode": -2, "t2code": -2,
        "rowRange": 400, "lang": "ZH",
    }
    d = fetch_json(SEARCH, params)
    inner = d.get("result", "[]")
    if isinstance(inner, str):
        inner = json.loads(inner)
    return inner


def clean_title(t: str) -> str:
    return html.unescape(t or "").replace("\n", " ").strip()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for fy in range(2017, 2027):
        records = search_window(fy)
        time.sleep(0.5)
        hits = []
        for r in records:
            title = clean_title(r.get("TITLE"))
            m = TITLE_RE.search(title)
            if not m or not r.get("FILE_LINK"):
                continue
            hits.append((r, title, m.group(1)))
        # 每类每财年最多 1 份（业绩公告可能重发/更正，保留第一条并记录全部候选）
        seen: dict[str, dict] = {}
        for r, title, kind in hits:
            q = QUARTER_MAP[kind]
            if q not in seen:
                seen[q] = {"date": r.get("DATE_TIME"), "title": title, "url": BASE + r["FILE_LINK"]}
            else:
                print(f"  [提示] FY{str(fy + 1)[2:]}{q} 存在多份公告，保留 {seen[q]['date']}，忽略 {r.get('DATE_TIME')}: {title[:50]}")
        for q, info in sorted(seen.items()):
            fy_tag = f"FY{str(fy + 1)[2:]}"  # 财年 FY18 = 2017/18
            url = info["url"]
            # Windows 文件名不能含冒号：日期 "18/08/2017 07:59" → "180820170759"
            fname = f"{fy_tag}{q}_{re.sub(r'[^0-9]', '', info['date'])}.pdf"
            fpath = OUT_DIR / fname
            info.update({"fy": fy_tag, "quarter": q, "file": fname})
            if fpath.exists() and fpath.stat().st_size > 10_000:
                print(f"  已存在 {fname}")
            else:
                data = fetch_bytes(url)
                fpath.write_bytes(data)
                print(f"  下载 {fname} ({len(data) // 1024} KB)")
                time.sleep(0.5)
            info["sha256"] = hashlib.sha256(fpath.read_bytes()).hexdigest()[:16]
            manifest.append(info)
    manifest.sort(key=lambda x: (x["fy"], x["quarter"]))
    out = MANUAL_DIR / "lenovo_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共 {len(manifest)} 份公告，manifest → {out}")
    by_fy: dict[str, int] = {}
    for m in manifest:
        by_fy[m["fy"]] = by_fy.get(m["fy"], 0) + 1
    for fy, n in sorted(by_fy.items()):
        print(f"  {fy}: {n} 份")
    return 0


def fetch_bytes(url: str, retries: int = 3) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, headers=UA, timeout=120, follow_redirects=True)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2**attempt)
    raise RuntimeError(f"download failed: {url} ({last_err})")


if __name__ == "__main__":
    raise SystemExit(main())

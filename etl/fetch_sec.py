"""P1：从 SEC EDGAR 抓取 HP/Dell 的 companyfacts 与 submissions JSON。

- companyfacts：XBRL 全量事实，load_db.py 解析入库（plan 3.1）。
- submissions：每份 10-Q/10-K 的 reportDate → filingDate，check_disclosure_dates.py
  用它核对 available_date 是否等于公司自己那份申报日（V1.21 防泄露检查）。
- CIK 以 EDGAR 为准，不凭记忆：HP Inc. = 0000047217，Dell Technologies = 0001571996。
- SEC 要求 User-Agent 含联系邮箱，限速 <= 10 次/秒；重试 3 次指数退避。

用法：python -m etl.fetch_sec
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

CIKS = {"HP": "0000047217", "Dell": "0001571996"}
OUT_DIR = Path("data/raw/sec")
FACT = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBS = "https://data.sec.gov/submissions/CIK{cik}.json"

UA = {"User-Agent": "FinSights personal project (contact: 1348587884@qq.com)"}
MIN_INTERVAL_S = 0.15  # >= 10 req/s 的反向约束，实际每次请求远大于此


def _get(url: str, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, headers=UA, timeout=120, follow_redirects=True)
            r.raise_for_status()
            time.sleep(MIN_INTERVAL_S)
            return r.json()
        except Exception as e:  # noqa: BLE001 — 网络错误统一退避重试
            last = e
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {url} ({last})")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for company, cik in CIKS.items():
        facts = _get(FACT.format(cik=cik))
        fp = OUT_DIR / f"CIK{cik}.json"
        fp.write_text(json.dumps(facts), encoding="utf-8")
        n = len((facts.get("facts") or {}).get("us-gaap") or {})
        print(f"{company} companyfacts -> {fp}  ({fp.stat().st_size/1e6:.1f} MB, us-gaap tags={n})")

        subs = _get(SUBS.format(cik=cik))
        sp = OUT_DIR / f"submissions_CIK{cik}.json"
        sp.write_text(json.dumps(subs), encoding="utf-8")
        rec = (subs.get("filings") or {}).get("recent") or {}
        print(f"{company} submissions  -> {sp}  ({len(rec.get('accessionNumber') or [])} filings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
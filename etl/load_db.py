"""P1：构建基础库 data/finsights.duckdb（附录 B 表结构）。

数据源：
- Lenovo：data/manual/lenovo_*.csv（千美元，extract_lenovo.py 产物）+ manifest（披露日期）
- HP/Dell：data/raw/sec/CIK*.json（SEC companyfacts，USD）

规则（plan 3.2）：
- 单位统一百万美元（Lenovo /1000，SEC /1e6）
- 时间窗口：period_end >= 2017-01-01
- 版本：同 (account, 期间) 取最早 filed 为 original；更晚且值不同 → restated
- Q4 利润表倒算：Q4 = 全年 − Q1 − Q2 − Q3，is_derived=TRUE（资产负债表为时点数，不需倒算）
- SEC 财年/季度：以年报 duration 的期末日为财年锚（52/53 周容差），季度由月份差推导
- 残差吸收：residual_rules 中 total − Σknown → other_*（is_derived=FALSE，语义仅指 Q4 倒算，见 questions.md）
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import duckdb
import yaml

MAP_PATH = Path("etl/account_map.yaml")
MANIFEST = Path("data/manual/lenovo_manifest.json")
CSV_BS = Path("data/manual/lenovo_balance_sheet.csv")
CSV_IS = Path("data/manual/lenovo_income_statement.csv")
DB_PATH = Path("data/finsights.duckdb")
WINDOW_START = date(2017, 1, 1)

COMPANIES = [
    {"company_id": "Lenovo", "name": "联想集团", "fy_end_month": 3, "source": "hkex"},
    {"company_id": "HP", "name": "HP Inc.", "fy_end_month": 10, "source": "sec"},
    {"company_id": "Dell", "name": "Dell Technologies Inc.", "fy_end_month": 1, "source": "sec"},
]

# ---------------- 通用纯函数（tests 覆盖） ----------------


def select_versions(entries: list[dict]) -> list[dict]:
    """同 (tag, end, start) 多条报送：最早 filed = original；更晚且值不同 = restated；同值跳过。

    entries: [{val, filed, ...}]，返回 [{val, filed, version}]（按 filed 升序）。
    """
    out: list[dict] = []
    for e in sorted(entries, key=lambda x: x["filed"]):
        if not out:
            out.append({"val": e["val"], "filed": e["filed"], "version": "original"})
        elif e["val"] != out[-1]["val"]:
            out.append({"val": e["val"], "filed": e["filed"], "version": "restated"})
    return out


def fy_anchor_for(pe: date, anchors: list[date]) -> date | None:
    """period_end 所属财年锚：>= pe 且最近的财年末（Q1 距财年末 9 个月，容差取 370 天内）。

    最新财年的 Q1–Q3 还没有年报锚：用最后一个锚 +1 年作为隐含锚（52/53 周漂移 ±数日可接受）。
    """
    ext = set(anchors)
    if anchors:
        last = max(anchors)
        try:
            ext.add(last.replace(year=last.year + 1))
        except ValueError:
            pass
    future = [a for a in sorted(ext) if 0 <= (a - pe).days <= 370]
    return min(future) if future else None


def fiscal_quarter(pe: date, anchor: date) -> int:
    """季度 = 距上一财年末的天数 / 季度长度（91.3 天），四舍五入。

    用天数而非月差：52/53 周财年的期末日在月中漂移（如 Dell 8 月 1 日距 1 月 30 日
    实为 6 个月，月差公式会算出 7 导致季度错位）。
    """
    if abs((pe - anchor).days) <= 5:
        return 4
    prev = date(anchor.year - 1, anchor.month, min(anchor.day, 28))
    return max(1, round((pe - prev).days / (365.25 / 4)))


def quarter_dates(periods: dict[tuple[int, int], date | dict]) -> dict[tuple[int, int], tuple[date, date]]:
    """每期的 (期初, 期末)：期初 = 上一季度期末 + 1 天（Q1 = 上财年末 + 1 天）。"""
    pes = {k: (v["pe"] if isinstance(v, dict) else v) for k, v in periods.items()}
    out = {}
    for (fy, q), pe in pes.items():
        if q == 1:
            prev_pe = pes.get((fy - 1, 4))
        else:
            prev_pe = pes.get((fy, q - 1))
        if prev_pe is None:
            # 没有上一期（窗口边缘）：用期末减 91 天近似，仅影响 days 字段
            out[(fy, q)] = (pe - timedelta(days=91), pe)
        else:
            out[(fy, q)] = (prev_pe + timedelta(days=1), pe)
    return out


def derive_q4(quarters: dict[int, float], fy_total: float) -> float:
    """Q4 = 全年 − (Q1+Q2+Q3)。"""
    return fy_total - sum(quarters.get(q, 0.0) for q in (1, 2, 3))


# ---------------- Lenovo ----------------


def _parse_manifest_date(s: str) -> date:
    d, _ = s.split(" ")
    dd, mm, yy = d.split("/")
    return date(int(yy), int(mm), int(dd))


def load_lenovo(amap: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (facts, periods, is_fy_aggregates)。值单位：百万美元。"""
    manifest = {m["fy"] + m["quarter"]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    facts: list[dict] = []
    periods: list[dict] = []
    fy_agg: list[dict] = []
    # ---- 资产负债表（时点数）----
    subtotal_map = {"_subtotal_流動資產": "total_current_assets", "_subtotal_流動負債": "total_current_liabilities"}
    for r in csv.DictReader(CSV_BS.open(encoding="utf-8")):
        code = subtotal_map.get(r["account_code"], r["account_code"])
        if code.startswith("_subtotal_") or not r["value_k_usd"]:
            continue
        key = r["fy"] + r["quarter"]
        m = manifest[key]
        pe = date.fromisoformat(r["period_end"])
        fy = int(r["fy"][2:])
        facts.append({
            "company_id": "Lenovo", "fy": fy, "q": int(r["quarter"][1]), "account": code,
            "value": round(float(r["value_k_usd"]) / 1000, 3),
            "available_date": _parse_manifest_date(m["date"]), "version": "original",
            "source": "lenovo_pdf", "source_ref": f"{m['file']}", "is_derived": False,
        })
        periods.append({"fy": fy, "q": int(r["quarter"][1]), "pe": pe, "available": _parse_manifest_date(m["date"])})
    # 同期同科目求和（如 NCI 两个组成部分）
    merged: dict[tuple, dict] = {}
    for f in facts:
        k = (f["company_id"], f["fy"], f["q"], f["account"], f["version"])
        if k in merged:
            merged[k]["value"] = round(merged[k]["value"] + f["value"], 3)
        else:
            merged[k] = f
    facts = list(merged.values())
    # ---- 利润表 ----
    is_rows = list(csv.DictReader(CSV_IS.open(encoding="utf-8")))
    for r in is_rows:
        if r["period_type"] != "3m" or not r["value_k_usd"]:
            continue  # 12m 行单独用于倒算
        key = r["fy"] + r["quarter"]
        m = manifest[key]
        fy = int(r["fy"][2:])
        facts.append({
            "company_id": "Lenovo", "fy": fy, "q": int(r["quarter"][1]), "account": r["account_code"],
            "value": round(float(r["value_k_usd"]) / 1000, 3),
            "available_date": _parse_manifest_date(m["date"]), "version": "original",
            "source": "lenovo_pdf", "source_ref": m["file"], "is_derived": False,
        })
    # 12m 聚合 → Q4 倒算
    for r in is_rows:
        if r["period_type"] != "12m" or not r["value_k_usd"]:
            continue
        fy = int(r["fy"][2:])
        m = manifest[r["fy"] + "Q4"]
        q123 = {q: {} for q in (1, 2, 3)}
        for f in facts:
            if f["company_id"] == "Lenovo" and f["fy"] == fy and f["q"] in (1, 2, 3) and f["account"] == r["account_code"]:
                q123[f["q"]][f["account"]] = f["value"]
        q4 = derive_q4({q: q123[q].get(r["account_code"], 0.0) for q in (1, 2, 3)}, float(r["value_k_usd"]) / 1000)
        fy_agg.append({"fy": fy, "account": r["account_code"], "q4": round(q4, 3), "available": _parse_manifest_date(m["date"]), "ref": m["file"]})
    for a in fy_agg:
        facts.append({
            "company_id": "Lenovo", "fy": a["fy"], "q": 4, "account": a["account"], "value": a["q4"],
            "available_date": a["available"], "version": "original", "source": "lenovo_pdf",
            "source_ref": f"{a['ref']};derived=FY-Q1-Q2-Q3", "is_derived": True,
        })
    return facts, periods, fy_agg


# ---------------- SEC (HP/Dell) ----------------


def parse_companyfacts(raw: dict, amap: dict, company_id: str) -> dict:
    """解析一份 companyfacts：返回 {instants, durations, fallback}，均含版本选择。"""
    tag_map = amap["xbrl_tags"]
    fallback_map = amap.get("xbrl_fallback_tags", {})
    instants: dict[tuple, list] = {}
    durations: dict[tuple, list] = {}
    fallback: dict[tuple, list] = {}  # (account, tag, end) → versions
    gaap = raw["facts"].get("us-gaap", {})

    def usd_entries(tag: str) -> list[dict]:
        if tag not in gaap or "USD" not in gaap[tag].get("units", {}):
            return []
        return [e for e in gaap[tag]["units"]["USD"]
                if e.get("form") in ("10-Q", "10-K") and date.fromisoformat(e["end"]) >= WINDOW_START]

    for tag, account in tag_map.items():
        for e in usd_entries(tag):
            end = date.fromisoformat(e["end"])
            filed = date.fromisoformat(e["filed"])
            if "start" not in e:
                instants.setdefault((tag, account, end), []).append({"val": float(e["val"]), "filed": filed})
            else:
                start = date.fromisoformat(e["start"])
                durations.setdefault((tag, account, start, end), []).append({"val": float(e["val"]), "filed": filed})
    for account, tags in fallback_map.items():
        for tag in tags:
            for e in usd_entries(tag):
                fallback.setdefault((account, tag, date.fromisoformat(e["end"])), []).append(
                    {"val": float(e["val"]), "filed": date.fromisoformat(e["filed"])})
    instants = {k: select_versions(v) for k, v in instants.items()}
    durations = {k: select_versions(v) for k, v in durations.items()}
    fallback = {k: select_versions(v) for k, v in fallback.items()}
    return {"instants": instants, "durations": durations, "fallback": fallback}


def build_sec_periods(parsed: dict) -> dict[tuple[int, int], dict]:
    """从 instant 日期 + 年报 duration 锚推导 (fy, q) → {pe, available}。

    候选期末要求 >= 5 个科目支撑（真实资产负债表日有多科目，杂散 instant 只有 1 个）；
    同一 (fy, q) 冲突时取支撑最多、其次更早的日期。
    """
    anchors: set[date] = set()
    for (tag, account, start, end), versions in parsed["durations"].items():
        if 360 <= (end - start).days <= 371 and versions:
            anchors.add(end)
    support: Counter = Counter()
    for (_tag, _account, end) in parsed["instants"]:
        support[end] += 1
    candidates = sorted(end for end, n in support.items() if n >= 5)
    periods: dict[tuple[int, int], dict] = {}
    for pe in candidates:
        anchor = fy_anchor_for(pe, sorted(anchors))
        if anchor is None:
            continue
        fy = anchor.year
        q = fiscal_quarter(pe, anchor)
        if not 1 <= q <= 4:
            continue
        key = (fy, q)
        if key not in periods or (support[pe], -pe.toordinal()) > (support[periods[key]["pe"]], -periods[key]["pe"].toordinal()):
            periods[key] = {"pe": pe, "anchor": anchor}
    return periods


def _merge_facts(facts: list[dict], key_fields: tuple[str, ...] = ("company_id", "fy", "q", "account", "version", "_kind")) -> list[dict]:
    """多标签 → 同一科目的合并（求和），available_date 取最早，source_ref 逗号连接。"""
    merged: dict[tuple, dict] = {}
    for f in facts:
        k = tuple(f.get(x) for x in key_fields)
        if k not in merged:
            merged[k] = dict(f)
        else:
            m = merged[k]
            m["value"] = round(m["value"] + f["value"], 3)
            m["available_date"] = min(m["available_date"], f["available_date"])
            if f["source_ref"] not in m["source_ref"]:
                m["source_ref"] = m["source_ref"] + "," + f["source_ref"]
    return list(merged.values())


def load_sec(raw_path: Path, amap: dict, company_id: str) -> tuple[list[dict], list[dict]]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    parsed = parse_companyfacts(raw, amap, company_id)
    periods = build_sec_periods(parsed)
    qdates = quarter_dates(periods)
    facts: list[dict] = []
    for (tag, account, end), versions in parsed["instants"].items():
        keys = [k for k in periods if periods[k]["pe"] == end]
        if not keys:
            continue
        fy, q = keys[0]
        for v in versions:
            facts.append({
                "company_id": company_id, "fy": fy, "q": q, "account": account, "value": round(v["val"] / 1e6, 3),
                "available_date": v["filed"], "version": v["version"], "source": "sec_xbrl",
                "source_ref": f"{tag}@{end}", "is_derived": False,
            })
    for (tag, account, start, end), versions in parsed["durations"].items():
        keys = [k for k in periods if periods[k]["pe"] == end]
        if not keys:
            continue
        fy, q = keys[0]
        exp_start, exp_end = qdates[(fy, q)]
        days = (end - start).days
        if q != 4 and 80 <= days <= 100 and abs((start - exp_start).days) <= 10:
            kind = "quarter"
        elif 360 <= days <= 371:
            kind = "fy"
        else:
            continue  # YTD 或无法对齐的期间，跳过
        for v in versions:
            facts.append({
                "company_id": company_id, "fy": fy, "q": q, "account": account, "value": round(v["val"] / 1e6, 3),
                "available_date": v["filed"], "version": v["version"], "source": "sec_xbrl",
                "source_ref": f"{tag}@{start}~{end}", "is_derived": False,
                "_kind": kind,
            })
    # ---- 回退标签：主标签在该期缺失时启用（不与主标签求和）----
    for (account, tag, end), versions in parsed.get("fallback", {}).items():
        keys = [k for k in periods if periods[k]["pe"] == end]
        if not keys:
            continue
        fy, q = keys[0]
        has = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q and f["account"] == account]
        if has:
            continue
        for v in versions:
            facts.append({
                "company_id": company_id, "fy": fy, "q": q, "account": account, "value": round(v["val"] / 1e6, 3),
                "available_date": v["filed"], "version": v["version"], "source": "sec_xbrl",
                "source_ref": f"{tag}@{end}", "is_derived": False,
            })
    # ---- 多标签合并（求和）----
    facts = _merge_facts(facts)
    # Q4 倒算（仅 IS 科目）
    amap_is = {a["code"] for a in amap["accounts"] if a["statement"] == "IS"}
    for fy, q in sorted(periods):
        if q != 4:
            continue
        for account in amap_is:
            q123 = {}
            for qq in (1, 2, 3):
                vs = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == qq
                      and f["account"] == account and f["version"] == "original" and f.get("_kind") == "quarter"]
                if vs:
                    q123[qq] = vs[0]["value"]
            fys = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == 4
                   and f["account"] == account and f["version"] == "original" and f.get("_kind") == "fy"]
            if fys and len(q123) == 3:
                q4 = derive_q4(q123, fys[0]["value"])
                facts.append({
                    "company_id": company_id, "fy": fy, "q": 4, "account": account, "value": round(q4, 3),
                    "available_date": fys[0]["available_date"], "version": "original", "source": "sec_xbrl",
                    "source_ref": f"derived=FY-Q1-Q2-Q3:{account}", "is_derived": True, "_kind": "quarter",
                })
    # HP 没有 Liabilities 标签 → total_liabilities = total_assets − total_equity
    if company_id == "HP":
        for (fy, q), _info in periods.items():
            has = [f for f in facts if f["company_id"] == "HP" and f["fy"] == fy and f["q"] == q and f["account"] == "total_liabilities"]
            if has:
                continue
            ta = [f for f in facts if f["company_id"] == "HP" and f["fy"] == fy and f["q"] == q and f["account"] == "total_assets" and f["version"] == "original"]
            te = [f for f in facts if f["company_id"] == "HP" and f["fy"] == fy and f["q"] == q and f["account"] == "total_equity" and f["version"] == "original"]
            if ta and te:
                facts.append({
                    "company_id": "HP", "fy": fy, "q": q, "account": "total_liabilities",
                    "value": round(ta[0]["value"] - te[0]["value"], 3),
                    "available_date": ta[0]["available_date"], "version": "original", "source": "sec_xbrl",
                    "source_ref": "derived=total_assets-total_equity", "is_derived": False,
                })
    # ---- 残差吸收 ----
    for rule in amap["residual_rules"]:
        for (fy, q), _info in periods.items():
            def orig(acct):
                vs = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                      and f["account"] == acct and f["version"] == "original"]
                return vs[0]["value"] if vs else None
            total_fact = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                          and f["account"] == rule["total"] and f["version"] == "original"]
            if not total_fact:
                continue
            known_sum = 0.0
            for acct in rule["known"]:
                v = orig(acct)
                if v is not None:
                    known_sum += v
            resid = round(total_fact[0]["value"] - known_sum, 3)
            existing = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                        and f["account"] == rule["absorb"] and f["version"] == "original"]
            if existing:
                existing[0]["value"] = resid  # 直接映射到的 other_* 覆盖为残差，保证分项闭合
                existing[0]["available_date"] = total_fact[0]["available_date"]
                existing[0]["source_ref"] = f"residual:{rule['total']}"
            else:
                facts.append({
                    "company_id": company_id, "fy": fy, "q": q, "account": rule["absorb"], "value": resid,
                    "available_date": total_fact[0]["available_date"],
                    "version": "original", "source": "sec_xbrl", "source_ref": f"residual:{rule['total']}",
                    "is_derived": False,
                })
    # 去掉辅助字段
    # SEC 毛利派生：gross_profit = revenue − cogs（保证口径一致；见 account_map 注释）
    for (fy, q) in sorted(periods):
        rev_facts = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                     and f["account"] == "revenue" and f["version"] == "original" and f.get("_kind") != "fy"]
        cogs_facts = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                      and f["account"] == "cogs" and f["version"] == "original" and f.get("_kind") != "fy"]
        if rev_facts and cogs_facts:
            facts.append({
                "company_id": company_id, "fy": fy, "q": q, "account": "gross_profit",
                "value": round(rev_facts[0]["value"] - cogs_facts[0]["value"], 3),
                "available_date": rev_facts[0]["available_date"],
                "version": "original", "source": "sec_xbrl",
                "source_ref": f"derived=revenue-cogs:{rev_facts[0]['source_ref']},{cogs_facts[0]['source_ref']}",
                "is_derived": True,
            })
    # FY 聚合值只用于 Q4 倒算，不入库（否则 V1.10 的营收单位检查会被全年值击穿）
    facts = [f for f in facts if f.get("_kind") != "fy"]
    for f in facts:
        f.pop("_kind", None)
    period_rows = [
        {"fy": fy, "q": q, "pe": info["pe"], "available": date(1970, 1, 1)}
        for (fy, q), info in sorted(periods.items())
    ]
    return facts, period_rows


# ---------------- 建库 ----------------

SCHEMA = """
CREATE TABLE companies (company_id TEXT PRIMARY KEY, name TEXT, fy_end_month INT, source TEXT);
CREATE TABLE periods (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT,
  period_end DATE, calendar_quarter TEXT, days INT, PRIMARY KEY (company_id, fiscal_year, fiscal_quarter));
CREATE TABLE accounts (code TEXT PRIMARY KEY, name_zh TEXT, name_en TEXT, statement TEXT,
  parent_code TEXT, is_total BOOLEAN, sign INT);
CREATE TABLE facts (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  value DOUBLE, available_date DATE, version TEXT DEFAULT 'original',
  source TEXT, source_ref TEXT, is_derived BOOLEAN DEFAULT FALSE);
CREATE TABLE facts_detail (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  region TEXT, product_line TEXT, value DOUBLE, synthetic BOOLEAN DEFAULT TRUE);
CREATE TABLE filing_notes (company_id TEXT, fiscal_year INT, fiscal_quarter INT, note TEXT);
"""


def main() -> int:
    amap = _nfkc(yaml.safe_load(MAP_PATH.read_text(encoding="utf-8")))
    all_facts: list[dict] = []
    all_periods: dict[str, dict[tuple, dict]] = {}
    for c in COMPANIES:
        if c["company_id"] == "Lenovo":
            facts, prows, _ = load_lenovo(amap)
        else:
            pattern = {"HP": "CIK0000047217.json", "Dell": "CIK0001571996.json"}[c["company_id"]]
            facts, prows = load_sec(Path("data/raw/sec") / pattern, amap, c["company_id"])
        all_facts += facts
        all_periods[c["company_id"]] = {(p["fy"], p["q"]): p for p in prows}

    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))
    con.execute(SCHEMA)
    con.executemany("INSERT INTO companies VALUES (?,?,?,?)",
                    [(c["company_id"], c["name"], c["fy_end_month"], c["source"]) for c in COMPANIES])
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
                    [(a["code"], a["name_zh"], a["name_en"], a["statement"], a["parent_code"] or None, a["is_total"], a["sign"])
                     for a in amap["accounts"]])
    # periods：days 用 quarter_dates
    for cid, pd in all_periods.items():
        qd = quarter_dates({k: v["pe"] for k, v in pd.items()})
        for (fy, q), info in sorted(pd.items()):
            start, pe = qd[(fy, q)]
            cq = f"{pe.year}Q{(pe.month - 1) // 3 + 1}"
            con.execute("INSERT INTO periods VALUES (?,?,?,?,?,?)",
                        (cid, fy, q, pe, cq, (pe - start).days))
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (f["company_id"], f["fy"], f["q"], f["account"], f["value"], f["available_date"],
         f["version"], f["source"], f["source_ref"], f["is_derived"]) for f in all_facts])
    n = con.execute("SELECT company_id, COUNT(DISTINCT (fiscal_year, fiscal_quarter)) FROM periods GROUP BY 1").fetchall()
    for cid, cnt in n:
        print(f"{cid}: {cnt} 期")
    nf = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    print(f"facts 总行数: {nf}")
    con.close()
    return 0


def _nfkc(obj):
    if isinstance(obj, str):
        import unicodedata
        return unicodedata.normalize("NFKC", obj)
    if isinstance(obj, list):
        return [_nfkc(x) for x in obj]
    if isinstance(obj, dict):
        return {_nfkc(k): _nfkc(v) for k, v in obj.items()}
    return obj


if __name__ == "__main__":
    raise SystemExit(main())

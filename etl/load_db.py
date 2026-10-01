"""P1：构建基础库 data/finsights.duckdb（附录 B 表结构，含 derivation 字段）。

数据源：
- Lenovo：data/manual/lenovo_*.csv（千美元，extract_lenovo.py 产物）+ manifest（披露日期）
- HP/Dell：data/raw/sec/CIK*.json（SEC companyfacts，USD）
- 人工补录：data/manual/dell_patch.csv（Dell FY2024Q2/Q3 AR/AP）、
  dell_fy2017q4_is.csv（Dell FY2017Q4 revenue/cogs，9M-YTD 倒算，Q4-2 答复）、
  available_date_patch.csv（Dell FY2024Q4 披露日期修正）

规则（plan 3.2 + questions.md Q4/Q6/Q7 答复）：
- 单位统一百万美元（Lenovo /1000，SEC /1e6）；fiscal_year 统一 4 位数（联想 FY18 → 2018）
- 时间窗口：period_end >= 2017-01-01（限制入库期间；倒算的输入可读窗口外原始数据）
- 版本：同 (account, 期间) 取最早 filed 为 original；更晚且值不同 → restated
- Q4 利润表倒算：Q4 = 全年 − 9M YTD（Q4-2 答复，优先于"全年 − 三个单季"），derivation='q4_backout'，
  输入不齐则不生成该条（禁止按 0 补齐）
- days = 本期期末 − 上期期末（Q7-2；窗口边缘期用窗口外的真实期末日）
- derivation 语义（Q6-3）：NULL=直接报送；q4_backout / sum_of_parts / rev_minus_cogs / residual / manual_patch；
  is_derived = (derivation IS NOT NULL)
- HP total_liabilities = 负债分项之和（Q6-1：禁用 A−E 推导，否则恒等式检查对 HP 恒真）
- 残差吸收：residual_rules 中 total − Σknown → other_*，derivation='residual'；HP 跳过 total_liabilities 规则
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
PATCH_DIR = Path("data/manual")
DB_PATH = Path("data/finsights.duckdb")
WINDOW_START = date(2017, 1, 1)

COMPANIES = [
    {"company_id": "Lenovo", "name": "联想集团", "fy_end_month": 3, "source": "hkex"},
    {"company_id": "HP", "name": "HP Inc.", "fy_end_month": 10, "source": "sec"},
    {"company_id": "Dell", "name": "Dell Technologies Inc.", "fy_end_month": 1, "source": "sec"},
]

FILING_NOTES = [
    ("Lenovo", 2023, 1, "应付款列报变更：FY2023 起联想将「應付票據」并入「應付貿易賬款及票據」列报。"
                        "本库统一口径 accounts_payable = 贸易应付 + 票据（FY2018–2022 的應付票據亦计入），保证跨年可比（Q7-3）。"),
    ("Lenovo", 2026, 1, "应收款列报变更：FY2026 起报表行「應收貿易賬款、租賃款及票據」含租赁应收款，"
                        "为真实口径变化且无法从公开披露拆分，影响 accounts_receivable 与往年直接可比性（Q7-3）。"),
    ("Dell", 2019, 1, "Dell 自 FY2019 起采用 ASC 606（full retrospective）；FY2018 及以前的 original 值为原口径，"
                      "restated 版本为 606 重述值，跨年比较须注意会计准则断点。"),
]


def _fact(company_id, fy, q, account, value, available_date, version, source, source_ref, derivation=None, **extra):
    return {
        "company_id": company_id, "fy": fy, "q": q, "account": account,
        "value": value, "available_date": available_date, "version": version,
        "source": source, "source_ref": source_ref, "derivation": derivation, **extra,
    }


# ---------------- 通用纯函数（tests 覆盖） ----------------


def select_versions(entries: list[dict]) -> list[dict]:
    """同 (tag, 期间) 多条报送：最早 filed = original；更晚且值不同 = restated；同值跳过。"""
    out: list[dict] = []
    for e in sorted(entries, key=lambda x: x["filed"]):
        if not out:
            out.append({"val": e["val"], "filed": e["filed"], "version": "original"})
        elif e["val"] != out[-1]["val"]:
            out.append({"val": e["val"], "filed": e["filed"], "version": "restated"})
    return out


def fy_anchor_for(pe: date, anchors: list[date]) -> date | None:
    """period_end 所属财年锚：>= pe 且最近的财年末（容差 370 天）。

    最新财年的 Q1–Q3 还没有年报锚：用最后一个锚 +1 年作为隐含锚。
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
    """季度 = 距上一财年末的天数 / 季度长度（91.3 天）四舍五入（52/53 周财年用天数法）。"""
    if abs((pe - anchor).days) <= 5:
        return 4
    prev = date(anchor.year - 1, anchor.month, min(anchor.day, 28))
    return max(1, round((pe - prev).days / (365.25 / 4)))


def days_since_prev_pe(pe: date, prev_pe: date) -> int:
    """days = 本期期末 − 上期期末（Q7-2：含首尾日的会计惯例按期间长度计）。"""
    return (pe - prev_pe).days


def q4_backout(fy_total: float, ytd9: float) -> float:
    """Q4 = 全年 − 9M YTD（Q4-2 答复）。"""
    return fy_total - ytd9


def quarter_dates(periods: dict[tuple[int, int], date | dict]) -> dict[tuple[int, int], tuple[date, date]]:
    """每期的 (期初, 期末)：期初 = 上一季度期末 + 1 天（Q1 = 上财年末 + 1 天）。

    仅用于 SEC duration 与季度边界的对齐校验；days 字段改用 days_since_prev_pe（Q7-2）。
    """
    pes = {k: (v["pe"] if isinstance(v, dict) else v) for k, v in periods.items()}
    out = {}
    for (fy, q), pe in pes.items():
        prev_pe = pes.get((fy - 1, 4)) if q == 1 else pes.get((fy, q - 1))
        out[(fy, q)] = (pe - timedelta(days=91), pe) if prev_pe is None else (prev_pe + timedelta(days=1), pe)
    return out


# ---------------- Lenovo ----------------


def _parse_manifest_date(s: str) -> date:
    d, _ = s.split(" ")
    dd, mm, yy = d.split("/")
    return date(int(yy), int(mm), int(dd))


def _lenovo_fy(fy_tag: str) -> int:
    """FY18 → 2018（4 位数，Q7-1）。"""
    return 2000 + int(fy_tag[2:])


def load_lenovo(amap: dict) -> tuple[list[dict], list[dict]]:
    """返回 (facts, period_rows)。值单位：百万美元。"""
    manifest = {m["fy"] + m["quarter"]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    facts: list[dict] = []
    periods: dict[tuple[int, int], dict] = {}
    # ---- 资产负债表（时点数，直接报送）----
    subtotal_map = {"_subtotal_流動資產": "total_current_assets", "_subtotal_流動負債": "total_current_liabilities"}
    for r in csv.DictReader(CSV_BS.open(encoding="utf-8")):
        code = subtotal_map.get(r["account_code"], r["account_code"])
        if code.startswith("_subtotal_") or not r["value_k_usd"]:
            continue
        fy, q = _lenovo_fy(r["fy"]), int(r["quarter"][1])
        m = manifest[r["fy"] + r["quarter"]]
        facts.append(_fact("Lenovo", fy, q, code, round(float(r["value_k_usd"]) / 1000, 3),
                           _parse_manifest_date(m["date"]), "original", "lenovo_pdf", m["file"]))
        periods[(fy, q)] = {"pe": date.fromisoformat(r["period_end"]), "available": _parse_manifest_date(m["date"])}
    # 同期同科目求和（如 NCI = 其他非控制性權益 − 認沽期權 两条报送行）→ sum_of_parts
    merged: dict[tuple, dict] = {}
    for f in facts:
        k = (f["company_id"], f["fy"], f["q"], f["account"], f["version"])
        if k not in merged:
            merged[k] = f
        else:
            m0 = merged[k]
            m0["value"] = round(m0["value"] + f["value"], 3)
            m0["derivation"] = "sum_of_parts"
    facts = list(merged.values())
    # ---- 利润表：3m 直接报送；9m 为 Q4 倒算输入；12m − 9m → Q4（q4_backout）----
    is_rows = list(csv.DictReader(CSV_IS.open(encoding="utf-8")))
    ytd9: dict[tuple[int, str], dict] = {}
    for r in is_rows:
        if r["period_type"] not in ("3m", "9m") or not r["value_k_usd"]:
            continue
        fy, q = _lenovo_fy(r["fy"]), int(r["quarter"][1])
        m = manifest[r["fy"] + r["quarter"]]
        if r["period_type"] == "3m":
            facts.append(_fact("Lenovo", fy, q, r["account_code"], round(float(r["value_k_usd"]) / 1000, 3),
                               _parse_manifest_date(m["date"]), "original", "lenovo_pdf", m["file"]))
        else:
            ytd9[(fy, r["account_code"])] = {"value": float(r["value_k_usd"]) / 1000, "ref": m["file"]}
    for r in is_rows:
        if r["period_type"] != "12m" or not r["value_k_usd"]:
            continue
        fy = _lenovo_fy(r["fy"])
        m = manifest[r["fy"] + "Q4"]
        y = ytd9.get((fy, r["account_code"]))
        if y is None:
            print(f"  [警告] Lenovo FY{fy}Q4 {r['account_code']}: 缺 9M YTD，无法倒算，跳过")
            continue
        facts.append(_fact("Lenovo", fy, 4, r["account_code"], round(q4_backout(float(r["value_k_usd"]) / 1000, y["value"]), 3),
                           _parse_manifest_date(m["date"]), "original", "lenovo_pdf",
                           f"{m['file']};{y['ref']};q4=FY12m-9mYTD", "q4_backout"))
    # ---- days（Q7-2：本期期末 − 上期期末；Q1 用上财年末 3/31）----
    period_rows = []
    for (fy, q), info in sorted(periods.items()):
        prev_pe = date(fy - 1, 3, 31) if q == 1 else periods[(fy, q - 1)]["pe"]
        period_rows.append({"fy": fy, "q": q, "pe": info["pe"], "available": info["available"],
                            "days": days_since_prev_pe(info["pe"], prev_pe)})
    return facts, period_rows


# ---------------- SEC (HP/Dell) ----------------


def parse_companyfacts(raw: dict, amap: dict, company_id: str) -> dict:
    """解析 companyfacts：{instants, durations, fallback, ends_all, unmapped_by_end}。

    - durations 不做窗口过滤（9M YTD 的期末可在窗口外，仅作 Q4 倒算输入，不入库）
    - ends_all：全部 instant 期末日（支撑 ≥5，无窗口过滤），供 days 计算（Q7-2）
    - unmapped_by_end：未映射标签的 instant 值，供 V1.22 残差拆解
    """
    tag_map = dict(amap["xbrl_tags"])
    for extra in amap.get("xbrl_tags_per_company", {}).get(company_id, {}):
        tag_map[extra] = amap["xbrl_tags_per_company"][company_id][extra]
    fallback_map = amap.get("xbrl_fallback_tags", {})
    instants: dict[tuple, list] = {}
    durations: dict[tuple, list] = {}
    fallback: dict[tuple, list] = {}
    unmapped_by_end: dict[date, list[tuple[str, float]]] = {}
    gaap = raw["facts"].get("us-gaap", {})

    def usd_entries(tag: str) -> list[dict]:
        # 不做窗口过滤：事实只在匹配到窗口内期间时才入库（period_of），而
        # ends_all（Q7-2 days 计算）与 V1.22 残差拆解需要窗口外的真实期末日
        if tag not in gaap or "USD" not in gaap[tag].get("units", {}):
            return []
        return [e for e in gaap[tag]["units"]["USD"] if e.get("form") in ("10-Q", "10-K")]

    mapped_tags = set(tag_map) | {t for tags in fallback_map.values() for t in tags}
    for tag, entries in gaap.items():
        if tag in mapped_tags:
            continue
        for e in usd_entries(tag):
            if "start" in e:
                continue
            unmapped_by_end.setdefault(date.fromisoformat(e["end"]), []).append((tag, float(e["val"])))

    for tag, account in tag_map.items():
        for e in usd_entries(tag):
            end, filed = date.fromisoformat(e["end"]), date.fromisoformat(e["filed"])
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
    ends_support = Counter()
    for (tag, account, end) in instants:
        ends_support[end] += 1
    ends_all = sorted(e for e, n in ends_support.items() if n >= 5)
    return {"instants": instants, "durations": durations, "fallback": fallback,
            "ends_all": ends_all, "unmapped_by_end": unmapped_by_end}


def build_sec_periods(parsed: dict) -> dict[tuple[int, int], dict]:
    """从 instant 日期 + 年报 duration 锚推导 (fy, q) → {pe, anchor}。

    候选期末要求 >= 5 个科目支撑；同一 (fy, q) 冲突取支撑最多、其次更早。
    """
    anchors: set[date] = set()
    for (tag, account, start, end), versions in parsed["durations"].items():
        if 360 <= (end - start).days <= 371 and versions:
            anchors.add(end)
    support: Counter = Counter()
    for (_tag, _account, end) in parsed["instants"]:
        support[end] += 1
    candidates = sorted(end for end, n in support.items() if n >= 5 and end >= WINDOW_START)
    periods: dict[tuple[int, int], dict] = {}
    for pe in candidates:
        anchor = fy_anchor_for(pe, sorted(anchors))
        if anchor is None:
            continue
        fy, q = anchor.year, fiscal_quarter(pe, anchor)
        if not 1 <= q <= 4:
            continue
        key = (fy, q)
        if key not in periods or (support[pe], -pe.toordinal()) > (support[periods[key]["pe"]], -periods[key]["pe"].toordinal()):
            periods[key] = {"pe": pe, "anchor": anchor}
    return periods


def _merge_facts(facts: list[dict], key_fields: tuple[str, ...] = ("company_id", "fy", "q", "account", "version", "_kind")) -> list[dict]:
    """多标签 → 同一科目的合并（求和）：value 相加、available_date 取最早、source_ref 连接、derivation=sum_of_parts。"""
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
                m["derivation"] = "sum_of_parts"
    return list(merged.values())


def load_sec(raw_path: Path, amap: dict, company_id: str, manual_facts: list[dict]) -> tuple[list[dict], list[dict]]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    parsed = parse_companyfacts(raw, amap, company_id)
    periods = build_sec_periods(parsed)
    qdates = quarter_dates(periods)
    ends_all = parsed["ends_all"]
    facts: list[dict] = []

    def period_of(end: date) -> tuple[int, int] | None:
        keys = [k for k in periods if periods[k]["pe"] == end]
        return keys[0] if keys else None

    for (tag, account, end), versions in parsed["instants"].items():
        key = period_of(end)
        if key is None:
            continue
        fy, q = key
        for v in versions:
            facts.append(_fact(company_id, fy, q, account, round(v["val"] / 1e6, 3),
                               v["filed"], v["version"], "sec_xbrl", f"{tag}@{end}"))
    for (tag, account, start, end), versions in parsed["durations"].items():
        days = (end - start).days
        kind = "quarter" if 80 <= days <= 100 else ("ytd9" if 250 <= days <= 290 else ("fy" if 360 <= days <= 371 else None))
        if kind is None:
            continue
        key = period_of(end) if kind != "ytd9" else next((k for k in periods if periods[k]["pe"] == end and k[1] == 3), None)
        if key is None:
            continue
        fy, q = key
        if kind == "quarter":
            exp_start, _ = qdates[(fy, q)]
            if q == 4 or abs((start - exp_start).days) > 10:
                continue
        for v in versions:
            facts.append(_fact(company_id, fy, q, account, round(v["val"] / 1e6, 3),
                               v["filed"], v["version"], "sec_xbrl", f"{tag}@{start}~{end}", _kind=kind))
    # ---- 回退标签：主标签在该期缺失时启用（不与主标签求和）----
    for (account, tag, end), versions in parsed.get("fallback", {}).items():
        key = period_of(end)
        if key is None:
            continue
        fy, q = key
        if any(f["fy"] == fy and f["q"] == q and f["account"] == account for f in facts):
            continue
        for v in versions:
            facts.append(_fact(company_id, fy, q, account, round(v["val"] / 1e6, 3),
                               v["filed"], v["version"], "sec_xbrl", f"{tag}@{end}"))
    # ---- 人工补录（Q4 答复：必须在残差之前合入，否则缺口被残差吸收形成假异常）----
    facts += [f for f in manual_facts if f["company_id"] == company_id]
    # ---- 多标签合并（求和，derivation=sum_of_parts）----
    facts = _merge_facts(facts)

    def orig(acct, fy, q, kind=None):
        vs = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
              and f["account"] == acct and f["version"] == "original"
              and (kind is None or f.get("_kind") == kind)]
        return vs[0] if vs else None

    # ---- Q4 倒算：全年 − 9M YTD（Q4-2；输入不齐则跳过并告警，禁止按 0 补齐）----
    for (fy, q) in sorted(periods):
        if q != 4:
            continue
        pe_q3 = periods[(fy, 3)]["pe"] if (fy, 3) in periods else None
        # 只对报送过全年 duration 的科目倒算（GP 是派生值、人工补录科目已有 original，均自动跳过）
        fy_accounts = {f["account"] for f in facts if f["company_id"] == company_id and f.get("_kind") == "fy"
                       and f["version"] == "original" and f["fy"] == fy and f["q"] == 4}
        for account in sorted(fy_accounts):
            has_stored = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == 4
                          and f["account"] == account and f["version"] == "original" and "_kind" not in f]
            if has_stored:
                continue  # 已有人工补录（_kind 聚合值除外）
            fy_f = orig(account, fy, 4, kind="fy")
            ytd_f = next((f for f in facts if f["account"] == account and f["version"] == "original"
                          and f.get("_kind") == "ytd9" and pe_q3 is not None
                          and f["q"] == 3 and f["fy"] == fy), None)
            if fy_f is None or ytd_f is None:
                print(f"  [警告] {company_id} FY{fy}Q4 {account}: 缺全年或 9M YTD，无法倒算")
                continue
            facts.append(_fact(company_id, fy, 4, account, round(q4_backout(fy_f["value"], ytd_f["value"]), 3),
                               fy_f["available_date"], "original", "sec_xbrl",
                               f"q4=FY({fy_f['source_ref']})-9M({ytd_f['source_ref']})", "q4_backout", _kind="quarter"))
    # ---- SEC 毛利派生：gross_profit = revenue − cogs（Q6-2 同意；只用季度值/补录值，排除 FY/YTD9 聚合）----
    for (fy, q) in sorted(periods):
        rev_f = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                 and f["account"] == "revenue" and f["version"] == "original" and f.get("_kind") not in ("fy", "ytd9")]
        cogs_f = [f for f in facts if f["company_id"] == company_id and f["fy"] == fy and f["q"] == q
                  and f["account"] == "cogs" and f["version"] == "original" and f.get("_kind") not in ("fy", "ytd9")]
        if rev_f and cogs_f:
            facts.append(_fact(company_id, fy, q, "gross_profit", round(rev_f[0]["value"] - cogs_f[0]["value"], 3),
                               rev_f[0]["available_date"], "original", "sec_xbrl",
                               f"rev_minus_cogs:{rev_f[0]['source_ref']},{cogs_f[0]['source_ref']}", "rev_minus_cogs"))
    # ---- HP total_liabilities = 负债分项之和（Q6-1：禁用 A−E 推导）----
    if company_id == "HP":
        for (fy, q) in sorted(periods):
            # HP 的 "Other liabilities" 行已包含非流动递延收入/递延税/退休福利（FY17Q1 实测 866 差额），
            # 故分项不含 deferred_revenue_noncurrent（它作为子项保留，类似 NCI 之于 total_equity）
            parts = [orig(a, fy, q) for a in ("total_current_liabilities", "long_term_debt",
                                              "other_noncurrent_liabilities")]
            if any(p is None for p in parts):
                print(f"  [警告] HP FY{fy}Q{q}: 负债分项缺失，total_liabilities 无法分项加总")
                continue
            refs = ",".join(p["source_ref"] for p in parts)
            facts.append(_fact("HP", fy, q, "total_liabilities", round(sum(p["value"] for p in parts), 3),
                               max(p["available_date"] for p in parts), "original", "sec_xbrl",
                               f"sum_of_parts:{refs}", "sum_of_parts"))
    # ---- 残差吸收（HP 跳过 total_liabilities 规则：其 TL 为分项加总，规则会循环定义）----
    for rule in amap["residual_rules"]:
        if rule["total"] == "total_liabilities" and company_id == "HP":
            continue
        for (fy, q), _info in periods.items():
            total_fact = orig(rule["total"], fy, q)
            if total_fact is None:
                continue
            known_sum = 0.0
            for acct in rule["known"]:
                vf = orig(acct, fy, q)
                if vf is not None:
                    known_sum += vf["value"]
            resid = round(total_fact["value"] - known_sum, 3)
            existing = orig(rule["absorb"], fy, q)
            if existing is not None:
                existing["value"] = resid
                existing["available_date"] = total_fact["available_date"]
                existing["source_ref"] = f"residual:{rule['total']}"
                existing["derivation"] = "residual"
            else:
                facts.append(_fact(company_id, fy, q, rule["absorb"], resid,
                                   total_fact["available_date"], "original", "sec_xbrl",
                                   f"residual:{rule['total']}", "residual"))
    # ---- FY/YTD9 聚合值不入库 ----
    facts = [f for f in facts if f.get("_kind") not in ("fy", "ytd9")]
    for f in facts:
        f.pop("_kind", None)
    # ---- days（Q7-2）：本期期末 − 上期期末（上期可来自窗口外真实期末日）----
    period_rows = []
    for (fy, q), info in sorted(periods.items()):
        earlier = [e for e in ends_all if e < info["pe"]]
        prev_pe = max(earlier) if earlier else None
        days = days_since_prev_pe(info["pe"], prev_pe) if prev_pe else None
        period_rows.append({"fy": fy, "q": q, "pe": info["pe"], "available": date(1970, 1, 1), "days": days})
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
  source TEXT, source_ref TEXT, derivation TEXT, is_derived BOOLEAN DEFAULT FALSE);
CREATE TABLE facts_detail (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  region TEXT, product_line TEXT, value DOUBLE, synthetic BOOLEAN DEFAULT TRUE);
CREATE TABLE filing_notes (company_id TEXT, fiscal_year INT, fiscal_quarter INT, note TEXT);
"""


def _load_manual_facts(amap: dict) -> list[dict]:
    """人工补录（Q4 答复）：Dell FY2024Q2/Q3 AR/AP + Dell FY2017Q4 营收/成本（9M-YTD 倒算，数值经人工核对）。"""
    out: list[dict] = []
    for r in csv.DictReader((PATCH_DIR / "dell_patch.csv").open(encoding="utf-8")):
        out.append(_fact(r["company_id"], int(r["fiscal_year"]), int(r["fiscal_quarter"]), r["account_code"],
                         float(r["value"]), date.fromisoformat(r["available_date"]), "original",
                         "manual_edgar", r["source_ref"], "manual_patch"))
    for r in csv.DictReader((PATCH_DIR / "dell_fy2017q4_is.csv").open(encoding="utf-8")):
        out.append(_fact(r["company_id"], int(r["fiscal_year"]), int(r["fiscal_quarter"]), r["account_code"],
                         float(r["value"]), date.fromisoformat(r["available_date"]), "original",
                         "manual_edgar", r["source_ref"], "q4_backout"))
    return out


def main() -> int:
    amap = _nfkc(yaml.safe_load(MAP_PATH.read_text(encoding="utf-8")))
    all_facts: list[dict] = []
    all_periods: dict[str, dict[tuple, dict]] = {}
    manual_facts = _load_manual_facts(amap)
    for c in COMPANIES:
        if c["company_id"] == "Lenovo":
            facts, prows = load_lenovo(amap)
        else:
            pattern = {"HP": "CIK0000047217.json", "Dell": "CIK0001571996.json"}[c["company_id"]]
            facts, prows = load_sec(Path("data/raw/sec") / pattern, amap, c["company_id"], manual_facts)
        all_facts += facts
        all_periods[c["company_id"]] = {(p["fy"], p["q"]): p for p in prows}
    # ---- 披露日期修正（Q4 答复：数值不变仅改日期）----
    for r in csv.DictReader((PATCH_DIR / "available_date_patch.csv").open(encoding="utf-8")):
        new_d = date.fromisoformat(r["available_date"])
        for f in all_facts:
            if (f["company_id"] == r["company_id"] and f["fy"] == int(r["fiscal_year"])
                    and f["q"] == int(r["fiscal_quarter"]) and f["account"] == r["account_code"]
                    and f["version"] == "original" and f["available_date"] != new_d):
                f["available_date"] = new_d
                f["source_ref"] += f";date_patch:{r['reason'][:48]}"

    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))
    con.execute(SCHEMA)
    con.executemany("INSERT INTO companies VALUES (?,?,?,?)",
                    [(c["company_id"], c["name"], c["fy_end_month"], c["source"]) for c in COMPANIES])
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)",
                    [(a["code"], a["name_zh"], a["name_en"], a["statement"], a["parent_code"] or None, a["is_total"], a["sign"])
                     for a in amap["accounts"]])
    for cid, pd in all_periods.items():
        for (fy, q), info in sorted(pd.items()):
            pe = info["pe"]
            cq = f"{pe.year}Q{(pe.month - 1) // 3 + 1}"
            con.execute("INSERT INTO periods VALUES (?,?,?,?,?,?)",
                        (cid, fy, q, pe, cq, info.get("days")))
    con.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (f["company_id"], f["fy"], f["q"], f["account"], f["value"], f["available_date"],
         f["version"], f["source"], f["source_ref"], f.get("derivation"),
         f["derivation"] is not None) for f in all_facts])
    con.executemany("INSERT INTO filing_notes VALUES (?,?,?,?)", FILING_NOTES)
    for cid, cnt in con.execute("SELECT company_id, COUNT(*) FROM periods GROUP BY 1 ORDER BY 1").fetchall():
        print(f"{cid}: {cnt} 期")
    print(f"facts 总行数: {con.execute('SELECT COUNT(*) FROM facts').fetchone()[0]}")
    print("derivation 分布:", dict(con.execute(
        "SELECT COALESCE(derivation,'reported'), COUNT(*) FROM facts GROUP BY 1").fetchall()))
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

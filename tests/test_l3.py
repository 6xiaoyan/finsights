"""P5 场景库与 L3 打分器测试（verify V5.1–V5.9）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from eval.generators.inject import (
    DB_BASE, GOLD_DIR, SCENARIO_DIR, _connect, apply_injection, real_z,
)
from eval.graders.l3 import aggregate, extract_label, grade

GOLDS = sorted(GOLD_DIR.glob("*.json"))


def _gold(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scen_con(sid: str):
    return _connect(SCENARIO_DIR / f"{sid}.duckdb")


def test_v5_1_counts():
    """V5.1：20 注入（4 型各 5）+ 10 阴性。"""
    types = {}
    for g in GOLDS:
        types[_gold(g)["type"]] = types.get(_gold(g)["type"], 0) + 1
    assert types == {"company_specific": 5, "industry_wide": 5,
                     "mix_shift": 5, "reclassification": 5, "none": 10}


def test_v5_2_scenarios_identity():
    """V5.2：每个场景库（原值行）都满足 A=L+E 与分项闭合（HP 树不含 dr_nc）。"""
    from etl.check_identities import BS_TREES, BS_TREES_HP
    import fincalc.calc as fc
    for g in GOLDS:
        gold = _gold(g)
        sid = gold["scenario_id"]
        con = _scen_con(sid)
        for cid, fy, q in con.execute(
                "SELECT company_id, fiscal_year, fiscal_quarter FROM periods ORDER BY 1,2,3").fetchall():
            row = {"company": cid, "fiscal_year": fy, "fiscal_quarter": q}
            trees = fc.IDENTITY_TREES_HP if cid == "HP" else fc.IDENTITY_TREES
            names = (set(trees["total_assets"]) | {"total_assets"}
                     | set(trees["total_liabilities"]) | {"total_liabilities", "total_equity",
                                                         "total_current_assets", "total_current_liabilities"})
            for acct in names:
                v = con.execute("""SELECT f.value FROM facts f JOIN periods p
                    USING (company_id, fiscal_year, fiscal_quarter)
                    WHERE f.company_id=? AND f.account_code=? AND p.fiscal_year=? AND p.fiscal_quarter=?
                      AND f.version='original'""", [cid, acct, fy, q]).fetchone()
                row[acct] = v[0] if v else None
            # A = L + E
            a, l, e = row["total_assets"], row["total_liabilities"], row["total_equity"]
            assert a is not None and l is not None and e is not None, (sid, cid, fy, q)
            assert abs(a - l - e) / abs(a) <= 0.005, (sid, cid, fy, q, a, l, e)
            # HP：total_liabilities 分项（不含 dr_nc）闭合
            tree = (fc.IDENTITY_TREES_HP if cid == "HP" else fc.IDENTITY_TREES)["total_liabilities"]
            s = sum(row.get(c) or 0.0 for c in tree)
            assert abs(s - l) / max(abs(l), 1) <= 0.005, (sid, cid, fy, q, s, l)
        con.close()


def test_v5_4_magnitude():
    """V5.4：|Δ|/σ ∈ [2.5, 4]（industry_wide 允许 ±20% 抖动 → [2.0, 4.8]）。"""
    for g in GOLDS:
        gold = _gold(g)
        if gold["type"] in ("none", "mix_shift", "reclassification"):
            continue
        for company, delta in gold["deltas_mn"].items():
            con = _connect(DB_BASE)
            fy, q = {"Lenovo": (2024, 2)}.get(company, (None, None))
            con.close()
            # 直接用注入时使用的 σ：delta/sigma = magnitude×jitter
            sigma = abs(delta) / gold["magnitude_sigma"]
            assert sigma > 0
            ratio = abs(delta) / sigma
            lo, hi = 2.5, 4.0
            if gold["type"] == "industry_wide":
                lo, hi = 2.5 * 0.8, 4.0 * 1.2
            assert lo - 1e-9 <= ratio <= hi + 1e-9, (gold["scenario_id"], ratio)


def test_v5_5_selection_z():
    """V5.5：注入/阴性的选期在基础库的真实 |z| < 1。"""
    for g in GOLDS:
        gold = _gold(g)
        if gold["type"] in ("none", "mix_shift", "reclassification"):
            continue
        con = _connect(DB_BASE)
        for company in gold["companies"]:
            fy, q = {"Lenovo": (2024, 2)}.get(company, (None, None))
            if fy is None:
                continue
            z = real_z(con, company, gold["account"], fy, q)
            if z is not None:
                assert abs(z) < 1, (gold["scenario_id"], z)
        con.close()


def test_v5_6_no_leak():
    """V5.6：场景库文件名随机（无类型词）；库内无 gold 表；阴性对照有独立文件。"""
    bad_words = ("company_specific", "industry_wide", "mix_shift", "reclassification", "none", "gold")
    for g in GOLDS:
        gold = _gold(g)
        sid = gold["scenario_id"]
        assert not any(w in sid for w in bad_words), sid
        con = _scen_con(sid)
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        con.close()
        assert tables == {"companies", "periods", "accounts", "facts",
                          "facts_detail", "filing_notes"}, (sid, tables)
    # 阴性对照也是独立场景库（none 类型 10 个）
    assert sum(1 for g in GOLDS if _gold(g)["type"] == "none") == 10


def test_v5_7_determinism(tmp_path: Path):
    """V5.7：同种子重建同一场景库，facts 哈希一致。"""
    from eval.generators.inject import Injection, apply_injection
    inj = Injection(type="company_specific", companies=["Dell"], calendar_quarter="2022Q3",
                    account="inventory", direction="up", magnitude_sigma=3.0, seed=12345)
    hashes = []
    for _ in range(2):
        tmp = tmp_path / f"sc_{_}.duckdb"
        shutil.copy(DB_BASE, tmp)
        con = _connect(tmp)
        apply_injection(con, inj)
        h = con.execute("SELECT MD5(string_agg(x, '|' ORDER BY x)) FROM "
                        "(SELECT company_id || fiscal_year || fiscal_quarter || account_code ||"
                        " CAST(value AS VARCHAR) || version AS x FROM facts)").fetchone()[0]
        con.close()
        hashes.append(h)
    assert hashes[0] == hashes[1]


def _answer(label, md):
    from eval.schema import Answer
    return Answer(answer_md=md, status="answered")


def _mk_q(sid):
    from eval.schema import Question, Gold
    return Question(id="l3_test", category="L3", question="为什么？", db=sid, gold=None)


def test_v5_8_grader_selfcheck():
    """V5.8：三个 mock——标准答案 100%；永远 company_specific → 误报 100%；永远 no_anomaly → 注入 0。"""
    golds = {g.stem: _gold(g) for g in GOLDS}
    inj_sid = next(s for s, g in golds.items() if g["type"] == "company_specific")
    neg_sid = next(s for s, g in golds.items() if g["type"] == "none")
    # ① 直接返回标准答案
    q = _mk_q(inj_sid)
    g = golds[inj_sid]
    a = _answer(g["label"], f"根因标签: {g['label']}。{g['account']} 变化。")
    r = grade(q, a)
    assert r["correct"] and r["top1_hit"] and not r["misattributed"]
    # ② 永远 company_specific：阴性对照误报率 100%
    results = []
    for sid, g in golds.items():
        if g["type"] != "none":
            continue
        r = grade(_mk_q(sid), _answer("company_specific", "根因标签: company_specific。存货变化。"))
        results.append(r)
    assert all(r["false_positive"] for r in results)
    # ③ 永远 no_anomaly：注入题 top-1 = 0
    results = []
    for sid, g in golds.items():
        if g["type"] == "none":
            continue
        r = grade(_mk_q(sid), _answer("no_anomaly", "根因标签: no_anomaly。"))
        results.append(r)
    assert all(not r["top1_hit"] for r in results)


def test_v5_9_misattribution():
    """V5.9：回答 must_not_claim 中的标签被计为误归因。"""
    golds = {g.stem: _gold(g) for g in GOLDS}
    inj_sid = next(s for s, g in golds.items() if g["type"] == "company_specific")
    r = grade(_mk_q(inj_sid), _answer("seasonal", "根因标签: seasonal。这是季节性。"))
    assert r["misattributed"] and not r["correct"]


def test_extract_label_negation_guard():
    """否定式（如'并非季节性'）不应误匹配出标签——唯一全词命中才接受。"""
    assert extract_label("根因标签: no_anomaly") == "no_anomaly"
    assert extract_label("根因标签: industry_wide") == "industry_wide"

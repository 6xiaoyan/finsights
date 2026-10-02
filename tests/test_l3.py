"""P5 场景库与 L3 打分器测试（verify V5.1–V5.9）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from eval.generators.inject import (
    DB_BASE, GOLD_DIR, SCENARIO_DIR, _connect, _fy_of, _sigma_of,
    apply_injection, real_z,
)
from eval.graders.l3 import aggregate, extract_label, grade

GOLDS = sorted(GOLD_DIR.glob("*.json"))


def _gold(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scen_con(sid: str):
    return _connect(SCENARIO_DIR / f"{sid}.duckdb")


# ---------------- 2026-10-02 修复回归：_fy_of 必须以 periods 表为准
# （旧硬编码映射对 Dell 全部、Lenovo q≥2 错位一季度，live V5.10 l3_002/l3_003 三轮全错的根因）

def test_fy_of_matches_periods_table():
    con = _connect(DB_BASE)
    try:
        for cid, fy, q, cq in con.execute(
                "SELECT company_id, fiscal_year, fiscal_quarter, calendar_quarter FROM periods").fetchall():
            assert _fy_of(con, cq, cid) == (fy, q), (cid, cq, fy, q)
    finally:
        con.close()


def _series(con, company, account):
    return {(r[0], r[1]): r[2] for r in con.execute(
        """SELECT p.fiscal_year, p.fiscal_quarter, f.value FROM facts f
           JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
           WHERE f.company_id=? AND f.account_code=? AND f.version='original'""",
        [company, account]).fetchall()}


def test_injection_lands_on_selected_calendar_quarter():
    """每笔 company_specific / industry_wide 注入：主科目恰好只在该自然季度对应的财季变化，
    变化量 = gold deltas_mn（其余期间 0 变化 → 落期正确性由构造得证）。"""
    base = _connect(DB_BASE)
    try:
        for g in GOLDS:
            gold = _gold(g)
            if gold["type"] not in ("company_specific", "industry_wide"):
                continue
            con = _scen_con(gold["scenario_id"])
            try:
                for company, delta in gold["deltas_mn"].items():
                    fy, q = _fy_of(base, gold["calendar_quarter"], company)
                    s_base = _series(base, company, gold["account"])
                    s_scen = _series(con, company, gold["account"])
                    assert set(s_base) == set(s_scen)
                    changed = [(y, qq) for (y, qq) in s_base
                               if abs(s_scen[(y, qq)] - s_base[(y, qq)]) > 0.005]
                    assert changed == [(fy, q)], (gold["scenario_id"], company, changed)
                    assert abs(s_scen[(fy, q)] - s_base[(fy, q)] - delta) < 0.01, \
                        (gold["scenario_id"], company, fy, q)
            finally:
                con.close()
    finally:
        base.close()


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
    """V5.4：|Δ|/σ ∈ [2.5, 4]（industry_wide 允许 ±20% 抖动 → [2.0, 4.8]）。
    2026-10-02 增强：旧版 σ 由 Δ 自身反除（恒真式，无证明力），且 mix_shift/reclassification
    整类被跳过（恰好掩盖了 reclass σ=0、全类零注入的缺陷）；现五类全覆盖，σ 从基础库独立重算。"""
    con = _connect(DB_BASE)
    try:
        for g in GOLDS:
            gold = _gold(g)
            if gold["type"] == "none":
                continue
            t = gold["type"]
            if t == "mix_shift":
                src = "cash" if gold["account"] == "inventory" else "accounts_receivable"
                _, cq = _fy_of(con, gold["calendar_quarter"], gold["companies"][0])
                checks = [(gold["companies"][0], v, _sigma_of(con, gold["companies"][0], src, cq))
                          for v in gold["deltas_mn"].values()]
            elif t == "reclassification":
                cid = gold["companies"][0]
                _, cq = _fy_of(con, gold["calendar_quarter"], cid)
                sigma = _sigma_of(con, cid, gold["account"], cq)
                checks = [(cid, v, sigma) for v in gold["deltas_mn"].values()]
            else:
                checks = [(cid, v, _sigma_of(con, cid, gold["account"],
                                             _fy_of(con, gold["calendar_quarter"], cid)[1]))
                          for cid, v in gold["deltas_mn"].items()]
            lo, hi = (2.0, 4.8) if t == "industry_wide" else (2.5, 4.0)
            for company, delta, sigma in checks:
                assert sigma > 0, (gold["scenario_id"], company, t)
                ratio = abs(delta) / sigma
                assert lo - 0.01 <= ratio <= hi + 0.01, (gold["scenario_id"], t, ratio)
    finally:
        con.close()


# ---------------- 题面-数据一致性（2026-10-02 新增：旧 bank 有 9/10 配对题题面方向与数据相反）

QUESTIONS = [json.loads(l) for l in open("eval/datasets/l3.jsonl", encoding="utf-8")]


def test_questions_direction_matches_footprint():
    base = _connect(DB_BASE)
    try:
        for q in QUESTIONS:
            gold = _gold(Path(GOLD_DIR) / f"{q['db']}.json")
            cid = gold["companies"][0]
            fy, fq = _fy_of(base, gold["calendar_quarter"], cid)
            con = _scen_con(gold["scenario_id"])
            try:
                v = _series(con, cid, gold["account"])[(fy, fq)]
                b = _series(base, cid, gold["account"])[(fy, fq)]
            finally:
                con.close()
            word = "上升" if ("上升" in q["question"]) else "下降"
            if gold["type"] == "none":
                assert word == "上升", q["id"]      # 错误前提（阴性）固定"上升"
            else:
                assert (word == "上升") == (v > b), (q["id"], gold["type"], word, v, b)
    finally:
        base.close()


def test_pair_types_ask_paired_accounts():
    """mix_shift / reclassification 只问有定义子科目对的科目（应付账款无配对，旧版静默转走应收）。"""
    for g in GOLDS:
        gold = _gold(g)
        if gold["type"] in ("mix_shift", "reclassification"):
            assert gold["account"] in ("inventory", "accounts_receivable"), (gold["scenario_id"],)


def test_reclassification_persistent_footprint():
    """reclassification：自 t 期起被问科目**每个后续期间**都等额变化，t 之前零变化（持续、不衰减）。"""
    base = _connect(DB_BASE)
    try:
        for g in GOLDS:
            gold = _gold(g)
            if gold["type"] != "reclassification":
                continue
            cid = gold["companies"][0]
            fy, fq = _fy_of(base, gold["calendar_quarter"], cid)
            con = _scen_con(gold["scenario_id"])
            try:
                s_b = _series(base, cid, gold["account"])
                s_c = _series(con, cid, gold["account"])
                deltas = set(gold["deltas_mn"].values())
                assert len(deltas) == 1 and 0 not in deltas, (gold["scenario_id"], deltas)
                d0 = next(iter(deltas))
                for (y, qq), v0 in s_b.items():
                    moved = s_c[(y, qq)] - v0
                    if (y, qq) >= (fy, fq):
                        assert abs(moved - d0) < 0.01, (gold["scenario_id"], y, qq, moved)
                    else:
                        assert abs(moved) < 0.005, (gold["scenario_id"], y, qq, moved)
                notes = con.execute("SELECT note FROM filing_notes WHERE company_id=?"
                                    " AND fiscal_year=? AND fiscal_quarter=?",
                                    [cid, fy, fq]).fetchall()
                assert any("重分类" in r[0] for r in notes), gold["scenario_id"]
            finally:
                con.close()
    finally:
        base.close()


def test_no_new_negative_balances():
    """注入不得把原本科目转成负余额（旧 bank 有应收被转到 −34 的题）。"""
    base = _connect(DB_BASE)
    try:
        neg_base = base.execute("""SELECT count(*) FROM facts f
            JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
            WHERE f.version='original' AND f.value < 0
              AND p.fiscal_year BETWEEN 2018 AND 2025""").fetchone()[0]
        for g in GOLDS:
            gold = _gold(g)
            con = _scen_con(gold["scenario_id"])
            try:
                neg = con.execute("""SELECT count(*) FROM facts f
                    JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
                    WHERE f.version='original' AND f.value < 0
                      AND p.fiscal_year BETWEEN 2018 AND 2025""").fetchone()[0]
            finally:
                con.close()
            assert neg <= neg_base, (gold["scenario_id"], neg, neg_base)
    finally:
        base.close()


def test_v5_5_selection_z():
    """V5.5：注入/阴性的选期在基础库的真实 |z| < 1。
    2026-10-02 修复：旧版对 Lenovo 检查硬编码 FY2024Q2（与实际注入期无关）、HP/Dell 直接跳过、
    阴性未纳入；现按 periods 表映射出真实注入期逐公司检查（阴性对照与文档口径一致）。"""
    con = _connect(DB_BASE)
    try:
        for g in GOLDS:
            gold = _gold(g)
            if gold["type"] in ("mix_shift", "reclassification"):
                continue
            for company in gold["companies"]:
                fy, q = _fy_of(con, gold["calendar_quarter"], company)
                z = real_z(con, company, gold["account"], fy, q)
                assert z is not None and abs(z) < 1, (gold["scenario_id"], company, fy, q, z)
    finally:
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

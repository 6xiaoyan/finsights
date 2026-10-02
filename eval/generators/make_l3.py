"""P5.1/P5.3：构建 L3 评测集——30 个场景库（20 注入 + 10 阴性对照）+ l3.jsonl + V5.11 候选清单。

用法：`.venv/Scripts/python -m eval.generators.make_l3`（seed 固定，V5.7 可复现）。

选期规则（V5.5）：注入与阴性对照的真实 |z| < 1；industry_wide 要求三家在同一年度/财季都 |z| < 1。
场景库：data/scenarios/<sid>.duckdb（文件名随机、不含类型词，V5.6）；
标准答案：eval/datasets/l3_gold/<sid>.json（agent 永远不可读，G6 范围）。
"""
from __future__ import annotations

import csv
import json
import random
import shutil
from pathlib import Path

import duckdb

from eval.generators.inject import (
    DB_BASE, GOLD_DIR, INJECTABLE, SCENARIO_DIR, Z_SELECT_MAX, Injection,
    _connect, _fy_of, apply_injection, new_scenario_id, real_z,
)

SEED = 20261002
COMPANIES = ["Lenovo", "HP", "Dell"]
N_PER_TYPE = 5          # 4 种注入类型 × 5
N_NEGATIVE = 10
ACCOUNT_ZH = {"inventory": "存货", "accounts_receivable": "应收账款", "accounts_payable": "应付账款"}
MUST_NOT = {
    "company_specific": ["seasonal", "industry_wide"],
    "industry_wide": ["company_specific"],
    "mix_shift": ["industry_wide"],
    "reclassification": ["company_specific", "industry_wide"],
    "none": ["company_specific", "industry_wide", "mix_shift", "reclassification"],
}
PHRASES = [
    "为什么{company}{period}的{acct}明显{dir}？请给出根因标签。",
    "{company}{period}{acct}出现了明显的{dir}，原因是什么？给出根因标签。",
    "请分析{company}{period}{acct}显著{dir}的根因（输出根因标签）。",
]


def _all_periods(con) -> list[tuple[str, int, int, str]]:
    return con.execute("""SELECT p.company_id, p.fiscal_year, p.fiscal_quarter, p.calendar_quarter
        FROM periods p WHERE p.fiscal_year BETWEEN 2018 AND 2025
        ORDER BY p.company_id, p.period_end""").fetchall()


def _z_table(con) -> dict[tuple[str, str, int, int], float]:
    out = {}
    for cid, fy, q, cq in _all_periods(con):
        for acct in INJECTABLE:
            z = real_z(con, cid, acct, fy, q)
            if z is not None:
                out[(cid, acct, fy, q)] = z
    return out


def build(seed: int = SEED) -> int:
    rng = random.Random(seed)
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    ztab = _z_table(duckdb.connect(str(DB_BASE), read_only=True))

    # 候选期（V5.5：真实 |z| < 1）
    def ok(cid, acct, fy, q):
        z = ztab.get((cid, acct, fy, q))
        return z is not None and abs(z) < Z_SELECT_MAX

    # industry_wide：按自然季度聚合三家（各自财季映射到同一自然季度），要求三家 |z| < 1
    cal_of: dict[tuple, str] = {}
    per_cq: dict[str, dict[str, tuple[int, int]]] = {}
    con0 = duckdb.connect(str(DB_BASE), read_only=True)
    for cid, fy, q, cq in _all_periods(con0):
        cal_of[(cid, fy, q)] = cq
        per_cq.setdefault(cq, {})[cid] = (fy, q)
    con0.close()
    iw_candidates: dict[str, list[tuple[str, str]]] = {}
    for acct in INJECTABLE:
        for cq in sorted(per_cq):
            fmap = per_cq[cq]
            if len(fmap) != 3 or not cq[:4].isdigit() or not (2018 <= int(cq[:4]) <= 2025):
                continue
            if all(ok(cid, acct, fmap[cid][0], fmap[cid][1]) for cid in COMPANIES):
                iw_candidates.setdefault(acct, []).append((fmap["Lenovo"][0], fmap["Lenovo"][1], cq))

    plans: list[tuple[str, Injection]] = []
    used: set[tuple] = set()

    def take_iw(acct: str) -> Injection | None:
        pool = iw_candidates.get(acct, [])
        rng.shuffle(pool)
        for (y, qq, cq) in pool:
            key = (acct, y, qq)
            if key in used:
                continue
            used.add(key)
            return Injection(type="industry_wide", companies=list(COMPANIES),
                             calendar_quarter=cq, account=acct, direction="up",
                             magnitude_sigma=rng.uniform(2.5, 4.0), seed=rng.randrange(1 << 30))
        return None

    def take_single(inj_type: str, acct: str, company: str) -> Injection | None:
        pool = [(y, qq) for (cid, a, y, qq) in sorted(ztab)
                if cid == company and a == acct and ok(cid, acct, y, qq)
                and (acct, y, qq) not in used]
        rng.shuffle(pool)
        if not pool:
            return None
        y, qq = pool[0]
        used.add((acct, y, qq))
        cq = cal_of[(company, y, qq)]
        return Injection(type=inj_type, companies=[company], calendar_quarter=cq,
                         account=acct, direction=rng.choice(["up", "down"]),
                         magnitude_sigma=rng.uniform(2.5, 4.0), seed=rng.randrange(1 << 30))

    # 4 类型 × 5 题；industry_wide 全部三条腿，其余单公司轮转
    for i in range(N_PER_TYPE):
        for inj_type in ("company_specific", "industry_wide", "mix_shift", "reclassification"):
            for attempt in range(60):
                if inj_type == "industry_wide":
                    acct = rng.choice(list(INJECTABLE))
                    inj = take_iw(acct)
                else:
                    acct = rng.choice(list(INJECTABLE))
                    company = COMPANIES[(i + attempt) % 3]
                    inj = take_single(inj_type, acct, company)
                if inj is not None:
                    plans.append((inj_type, inj))
                    break
            else:
                print(f"  [警告] {inj_type} 第 {i + 1} 题候选不足")
    # 10 阴性对照：单公司、真实 |z| < 1 的期间（问法与注入题相同——错误前提）
    for i in range(N_NEGATIVE):
        company = COMPANIES[i % 3]
        acct = list(INJECTABLE)[i % 3]
        for attempt in range(60):
            inj = take_single("none", acct, company)
            if inj is not None:
                plans.append(("none", inj))
                break
        else:
            print(f"  [警告] 阴性对照 第 {i + 1} 题候选不足")

    questions: list[dict] = []
    for idx, (inj_type, inj) in enumerate(plans):
        sid = new_scenario_id(rng)
        scen_path = SCENARIO_DIR / f"{sid}.duckdb"
        shutil.copy(DB_BASE, scen_path)
        con = _connect(scen_path)
        gold = apply_injection(con, inj)
        con.close()
        gold.update({"scenario_id": sid, "seed": inj.seed,
                     "magnitude_sigma": round(inj.magnitude_sigma, 3),
                     "must_not_claim": MUST_NOT[inj.type]})
        (GOLD_DIR / f"{sid}.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        company = inj.companies[0]
        fy, q = _fy_of(inj.calendar_quarter, company)
        period = f"FY{str(fy)[2:]}Q{q}"
        phrase = PHRASES[idx % len(PHRASES)]
        direction = "上升" if (inj.direction == "up" or inj.type in ("none", "reclassification", "mix_shift")) else "下降"
        if inj.type == "none":
            direction = "上升"
        question = phrase.format(company=company, period=period,
                                 acct=ACCOUNT_ZH[inj.account], dir=direction)
        questions.append({
            "id": f"l3_{idx + 1:03d}", "category": "L3", "question": question,
            "db": sid, "as_of": None,
            "gold": None, "gold_sql": None, "gold_method": None,
            "tags": [inj.account, company.lower(), inj_type if inj_type != "none" else "negative"],
        })
    with open("eval/datasets/l3.jsonl", "w", encoding="utf-8") as f:
        for qd in questions:
            f.write(json.dumps(qd, ensure_ascii=False) + "\n")
    _v511_candidates(ztab)
    n_types: dict[str, int] = {}
    for inj_type, _ in plans:
        n_types[inj_type] = n_types.get(inj_type, 0) + 1
    print(f"场景库 {len(plans)} 个 → {SCENARIO_DIR}；l3.jsonl {len(questions)} 题；gold → {GOLD_DIR}")
    print("类型分布:", dict(sorted(n_types.items())))
    return 0


def _v511_candidates(ztab: dict) -> int:
    """V5.11：真实 |z| > 2.0 的期间清单（真实事件候选，供人工结合 MD&A 标注）。

    注：plan 原阈值 2.5 在 n<=8 的同季度样本下数学上不可达（z 上限约为 (n-1)/sqrt(n) = 2.47），
    已按 2.0 生成并登记 questions.md Q11（Q11 待人工确认阈值或改用稳健排序法）。
    """
    out = Path("docs/evidence/V5.11.csv")
    rows = [[cid, f"FY{fy}Q{q}", acct, f"{z:.3f}"]
            for (cid, acct, fy, q), z in sorted(ztab.items()) if abs(z) > 2.0]
    header = "company_id,period,account,z"
    body = chr(10).join(",".join(r) for r in rows)
    out.write_text(header + chr(10) + body + chr(10), encoding="utf-8")
    print(f"V5.11 真实事件候选(|z|>2.0): {len(rows)} 条 -> {out}")
    return 0

def _z_table_val(cid, acct, fy, q):
    from eval.generators.inject import real_z, _connect
    con = _connect(DB_BASE)
    z = real_z(con, cid, acct, fy, q)
    con.close()
    return z if z is not None else 0.0


if __name__ == "__main__":
    raise SystemExit(build())

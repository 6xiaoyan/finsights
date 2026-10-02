"""P5.1/P5.3：构建 L3 评测集——30 个场景库（20 注入 + 10 阴性对照）+ l3.jsonl + V5.11 候选清单。

用法：`.venv/Scripts/python -m eval.generators.make_l3`（seed 固定，V5.7 可复现）。

选期规则（V5.5）：注入与阴性对照的真实 |z| < 1；industry_wide 要求三家在同一年度/财季都 |z| < 1。
场景库：data/scenarios/<sid>.duckdb（文件名随机、不含类型词，V5.6）；
标准答案：eval/datasets/l3_gold/<sid>.json（agent 永远不可读，G6 范围）。

2026-10-02 修复（live V5.10 取证发现的三处 harness 缺陷，全部在出题/注入侧）：
1. 财季映射：_fy_of 改以 periods 表为准（旧硬编码对 Dell 全部、Lenovo q≥2 错一季度）；
2. 科目-题面一致：mix_shift/reclassification 只在有定义子科目对的科目上出题
   （inventory / accounts_receivable；应付账款没有配对，旧版静默用了应收对→问的科目没动），
   reclassification 方向固定 up（附注语义"其他流动资产重分类计入 X"只能增加 X）；
   题面"上升/下降"按被问科目的**实际移动方向**生成，不再一律写"上升"；
3. 可行性过滤（计划期）：注入后各受影响科目余额 ≥ 原值 20%（重分类另要求 other_current_assets
   全窗口 ≥ 1.25|Δ|），杜绝负余额假数（旧 l3_011 应收被转到 −34）。
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import duckdb

from eval.generators.inject import (
    COUNTERPART, DB_BASE, GOLD_DIR, INJECTABLE, JITTER, SCENARIO_DIR, Z_SELECT_MAX,
    Injection, _connect, _fy_of, _sigma_of, apply_injection, new_scenario_id, real_z,
)

SEED = 20261002
COMPANIES = ["Lenovo", "HP", "Dell"]
N_PER_TYPE = 5          # 4 种注入类型 × 5
N_NEGATIVE = 10
PAIR_ACCOUNTS = ("inventory", "accounts_receivable")   # mix_shift / reclassification 可用科目
ACCOUNT_ZH = {"inventory": "存货", "accounts_receivable": "应收账款", "accounts_payable": "应付账款"}
HEADROOM = 0.2          # 注入后余额至少保留原值比例
RECLASS_OCA_MARGIN = 1.25   # other_current_assets 全窗口余额 ≥ 1.25×|Δ|
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


def _value_table(con) -> dict[tuple[str, str, int, int], float]:
    accts = set(INJECTABLE) | {"cash", "other_current_assets"}
    rows = con.execute("""SELECT f.company_id, f.account_code, p.fiscal_year, p.fiscal_quarter, f.value
        FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
        WHERE f.version='original'""").fetchall()
    return {(r[0], r[1], r[2], r[3]): float(r[4]) for r in rows if r[1] in accts}


def _qoq_delta(con, company: str, acct: str, fy: int, q: int) -> float | None:
    """基础口径环比变化量（original 行，q1 对上年 q4）；缺任一值返回 None。"""
    def val(y: int, qq: int):
        r = con.execute("""SELECT value FROM facts JOIN periods USING (company_id, fiscal_year, fiscal_quarter)
            WHERE company_id=? AND account_code=? AND fiscal_year=? AND fiscal_quarter=?
              AND version='original'""", [company, acct, y, qq]).fetchone()
        return float(r[0]) if r and r[0] is not None else None
    prev = val(fy - 1, 4) if q == 1 else val(fy, q - 1)
    cur = val(fy, q)
    return None if prev is None or cur is None else cur - prev


class Feasibility:
    """计划期可行性：用基础库的 σ 与余额预判注入落点是否会破坏数据合理性（负余额/零注入）。"""

    def __init__(self, con, vals: dict, cal_of: dict):
        self.vals = vals
        self.cal_of = cal_of          # (company, fy, q) -> calendar_quarter
        self.sigma: dict[tuple[str, str, int], float] = {}
        for cid in COMPANIES:
            for acct in set(INJECTABLE) | {"cash"}:
                for q in (1, 2, 3, 4):
                    self.sigma[(cid, acct, q)] = _sigma_of(con, cid, acct, q)
        self.oca_min: dict[tuple[str, int, int], float] = {}

    def period_of(self, inj: Injection, cid: str) -> tuple[int, int]:
        for (c, fy, q), cq in self.cal_of.items():
            if c == cid and cq == inj.calendar_quarter:
                return (fy, q)
        raise KeyError((cid, inj.calendar_quarter))

    def _oca_min_from(self, cid, fy, q) -> float:
        key = (cid, fy, q)
        if key not in self.oca_min:
            self.oca_min[key] = min(v for (c, a, y, qq), v in self.vals.items()
                                    if c == cid and a == "other_current_assets"
                                    and (y, qq) >= (fy, q))
        return self.oca_min[key]

    def ok(self, inj: Injection) -> bool:
        sign = 1.0 if inj.direction == "up" else -1.0
        if inj.type in ("company_specific", "industry_wide"):
            jitter = 1 + JITTER if inj.type == "industry_wide" else 1.0
            for cid in inj.companies:
                fy, q = self.period_of(inj, cid)
                acct = inj.account
                s = self.sigma[(cid, acct, q)]
                if s <= 0:
                    return False
                delta = sign * inj.magnitude_sigma * s * jitter
                v = self.vals[(cid, acct, fy, q)]
                if v + delta < HEADROOM * v:
                    return False
                cp, csign = COUNTERPART[acct]
                pv = self.vals[(cid, cp, fy, q)]
                if pv + csign * delta < HEADROOM * pv:
                    return False
            return True
        if inj.type == "mix_shift":
            cid = inj.companies[0]
            fy, q = self.period_of(inj, cid)
            src = "cash" if inj.account == "inventory" else "accounts_receivable"
            s = self.sigma[(cid, src, q)]
            if s <= 0:
                return False
            delta = sign * inj.magnitude_sigma * s
            v = self.vals[(cid, src, fy, q)]
            return v - delta >= HEADROOM * v
        if inj.type == "reclassification":
            cid = inj.companies[0]
            fy, q = self.period_of(inj, cid)
            s = self.sigma[(cid, inj.account, q)]
            if s <= 0:
                return False
            return self._oca_min_from(cid, fy, q) >= RECLASS_OCA_MARGIN * inj.magnitude_sigma * s
        return True    # none


def build(seed: int = SEED) -> int:
    rng = random.Random(seed)
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    bcon = duckdb.connect(str(DB_BASE), read_only=True)
    ztab = _z_table(bcon)
    vals = _value_table(bcon)
    # (company, fy, q) -> 自然季度
    cal_of: dict[tuple[str, int, int], str] = {}
    for cid, fy, q, cq in _all_periods(bcon):
        cal_of[(cid, fy, q)] = cq
    feas = Feasibility(bcon, vals, cal_of)

    def ok(cid, acct, fy, q):
        z = ztab.get((cid, acct, fy, q))
        return z is not None and abs(z) < Z_SELECT_MAX

    # industry_wide：按自然季度聚合三家，要求三家 |z| < 1
    per_cq: dict[str, dict[str, tuple[int, int]]] = {}
    for cid, fy, q, cq in _all_periods(bcon):
        per_cq.setdefault(cq, {})[cid] = (fy, q)
    iw_candidates: dict[str, list[tuple[int, int, str]]] = {}
    for acct in INJECTABLE:
        for cq in sorted(per_cq):
            fmap = per_cq[cq]
            if len(fmap) != 3 or not cq[:4].isdigit() or not (2018 <= int(cq[:4]) <= 2025):
                continue
            if all(ok(cid, acct, fmap[cid][0], fmap[cid][1]) for cid in COMPANIES):
                iw_candidates.setdefault(acct, []).append(
                    (fmap["Lenovo"][0], fmap["Lenovo"][1], cq))

    plans: list[tuple[str, Injection]] = []
    used: set[tuple] = set()

    def mk(inj_type, companies, cq, acct, direction):
        return Injection(type=inj_type, companies=companies, calendar_quarter=cq,
                         account=acct, direction=direction,
                         magnitude_sigma=rng.uniform(2.5, 4.0), seed=rng.randrange(1 << 30))

    def take_iw(acct: str) -> Injection | None:
        pool = iw_candidates.get(acct, [])
        rng.shuffle(pool)
        for (y, qq, cq) in pool:
            key = (acct, y, qq)
            if key in used:
                continue
            inj = mk("industry_wide", list(COMPANIES), cq, acct, "up")
            if feas.ok(inj):
                used.add(key)
                return inj
        return None

    def take_single(inj_type: str, acct: str, company: str) -> Injection | None:
        pool = [(y, qq) for (cid, a, y, qq) in sorted(ztab)
                if cid == company and a == acct and ok(cid, acct, y, qq)]
        rng.shuffle(pool)
        for (y, qq) in pool:
            if (acct, y, qq) in used:
                continue
            direction = "up" if inj_type == "reclassification" else rng.choice(["up", "down"])
            cq = cal_of[(company, y, qq)]
            inj = mk(inj_type, [company], cq, acct, direction)
            if inj_type == "none" or feas.ok(inj):
                used.add((acct, y, qq))
                return inj
        return None

    # 4 类型 × 5 题；industry_wide 全部三条腿，其余单公司轮转
    for i in range(N_PER_TYPE):
        for inj_type in ("company_specific", "industry_wide", "mix_shift", "reclassification"):
            accts = PAIR_ACCOUNTS if inj_type in ("mix_shift", "reclassification") else INJECTABLE
            for attempt in range(60):
                acct = rng.choice(list(accts))
                if inj_type == "industry_wide":
                    inj = take_iw(acct)
                else:
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
        company = inj.companies[0]
        fy, q = _fy_of(con, inj.calendar_quarter, company)
        none_delta = _qoq_delta(con, company, inj.account, fy, q) if inj_type == "none" else None
        con.close()
        gold.update({"scenario_id": sid, "seed": inj.seed,
                     "magnitude_sigma": round(inj.magnitude_sigma, 3),
                     "must_not_claim": MUST_NOT[inj.type]})
        (GOLD_DIR / f"{sid}.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        period = f"FY{str(fy)[2:]}Q{q}"
        phrase = PHRASES[idx % len(PHRASES)]
        # 题面方向 = 被问科目的实际移动方向；negative 前提必须为真（2026-10-02 修复：
        # 旧版固定"上升"与基础数据方向矛盾，l3_022/023 live 取证），reclass 附注恒为转入上移。
        if inj_type == "mix_shift":
            asked_is_src = inj.account == "accounts_receivable"   # AR 对里被问科目是转出方
            ask_up = (inj.direction == "down") if asked_is_src else (inj.direction == "up")
        elif inj_type == "reclassification":
            ask_up = True
        elif inj_type == "none":
            ask_up = True if none_delta is None else none_delta >= 0
        else:
            ask_up = inj.direction == "up"
        question = phrase.format(company=company, period=period,
                                 acct=ACCOUNT_ZH[inj.account], dir="上升" if ask_up else "下降")
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
    bcon.close()
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


if __name__ == "__main__":
    raise SystemExit(build())

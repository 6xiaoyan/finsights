"""P2.1：合成明细维度（plan 4.1）——营收/存货/应收账款 按 地区 × 产品线 拆分 → facts_detail。

- 份额：Dirichlet 生成初始份额，随时间平滑漂移（相邻季度单格份额变化 < 3 个百分点，V2.2），固定随机种子（V2.3）。
- 硬约束：每期每科目的明细加总 = 总额，误差为 0（V2.1）——最后一格用 总额 − 其余格 得到。
- 只覆盖真实报送了总额的期间（facts.original），明细值 = 总额 × 份额，synthetic=TRUE（V2.4）。
- 粒度：仅季度（PRD 待定问题，Q4-未拍板前不做月度）。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np

DB_PATH = Path("data/finsights.duckdb")
SEED = 20261001
REGIONS = ["Americas", "EMEA", "APAC"]
PRODUCT_LINES = ["PC", "Infra", "Other"]
ACCOUNTS = ["revenue", "inventory", "accounts_receivable"]
MAX_DRIFT = 0.03  # 相邻季度单格份额变化上限（百分点）


def synth_shares(n_periods: int, n_cells: int, rng: np.random.Generator) -> np.ndarray:
    """(n_periods, n_cells) 份额矩阵：Dirichlet 初始 + 平滑漂移，单格相邻变化 < 3pp。"""
    shares = np.zeros((n_periods, n_cells))
    cur = rng.dirichlet(np.full(n_cells, 6.0))  # 初始份额（较集中，模拟真实结构）
    shares[0] = cur
    target = rng.dirichlet(np.full(n_cells, 6.0))
    since_retarget = 0
    for t in range(1, n_periods):
        if since_retarget >= 8:
            target = rng.dirichlet(np.full(n_cells, 6.0))
            since_retarget = 0
        step = 0.18 * (target - cur)
        # 归一化可能放大单格变化：折半步长直到"净变化（归一后）"仍 ≤ MAX_DRIFT
        for _ in range(8):
            nxt = np.clip(cur + step, 1e-4, None)
            nxt = nxt / nxt.sum()
            if np.abs(nxt - cur).max() <= MAX_DRIFT:
                break
            step = step / 2
        cur = nxt
        shares[t] = cur
        since_retarget += 1
    return shares


def main() -> int:
    con = duckdb.connect(str(DB_PATH))
    con.execute("DELETE FROM facts_detail")
    rng = np.random.default_rng(SEED)
    cells = [f"{r}|{p}" for r in REGIONS for p in PRODUCT_LINES]  # 9 格
    n_cells = len(cells)
    total_inserted = 0
    for account in ACCOUNTS:
        periods = con.execute("""
            SELECT p.company_id, p.fiscal_year, p.fiscal_quarter, f.value
            FROM facts f JOIN periods p USING (company_id, fiscal_year, fiscal_quarter)
            WHERE f.account_code = ? AND f.version = 'original'
            ORDER BY p.company_id, p.period_end""", [account]).fetchall()
        by_company: dict[str, list] = {}
        for cid, fy, q, value in periods:
            by_company.setdefault(cid, []).append((fy, q, value))
        for cid, plist in sorted(by_company.items()):
            shares = synth_shares(len(plist), n_cells, rng)
            for (fy, q, value), sh in zip(plist, shares):
                if not value:
                    continue
                raw = value * sh
                vals = [round(float(x), 3) for x in raw[:-1]]
                vals.append(round(value - sum(vals), 3))  # 最后一格吸收舍入，保证加总精确
                for cell, v in zip(cells, vals):
                    region, pl = cell.split("|")
                    con.execute("INSERT INTO facts_detail VALUES (?,?,?,?,?,?,?,TRUE)",
                                (cid, fy, q, account, region, pl, v))
                total_inserted += n_cells
    con.execute("CREATE OR REPLACE VIEW facts_detail_summary AS SELECT 1")
    con.execute("DROP VIEW IF EXISTS facts_detail_summary")
    print(f"facts_detail 行数: {total_inserted}（3 科目 × 3 公司 × ~37 期 × 9 格）")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

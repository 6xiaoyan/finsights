# -*- coding: utf-8 -*-
"""selected_v1 返工前数字复算：全部读 live DB（Lenovo/original），并与返工文档目标值比对。"""
import duckdb

con = duckdb.connect("data/finsights.duckdb", read_only=True)
PE = {1: ("{y-1}-06-30",), 2: ("{y-1}-09-30",), 3: ("{y-1}-12-31",), 4: ("{y}-03-31",)}

def fact(acct, fy, q, version="original"):
    end = {1: (fy - 1, 6, 30), 2: (fy - 1, 9, 30), 3: (fy - 1, 12, 31), 4: (fy, 3, 31)}[q]
    r = con.execute(
        "SELECT value FROM facts WHERE company_id='Lenovo' AND account_code=? AND fiscal_year=? "
        "AND fiscal_quarter=? AND version=?", [acct, fy, q, version]).fetchall()
    assert len(r) == 1, (acct, fy, q, version, r)
    return float(r[0][0])

def check(label, got, want, tol=0.005):
    ok = abs(got - want) <= tol
    print(f"{'OK ' if ok else 'BAD'} {label}: got={got:.6f} want={want}")
    assert ok, label

# ---- S01: FY2027Q1 vs FY2026Q1 DIO（平均余额，D=91）----
inv = lambda fy, q: fact("inventory", fy, q)
cogs = lambda fy, q: fact("cogs", fy, q)
i_26q1, i_27q1 = inv(2026, 1), inv(2027, 1)
i_25q4, i_26q4 = inv(2025, 4), inv(2026, 4)
c_26q1, c_27q1 = cogs(2026, 1), cogs(2027, 1)
avg0, avg1 = (i_25q4 + i_26q1) / 2, (i_26q4 + i_27q1) / 2
dio0, dio1 = avg0 / c_26q1 * 91, avg1 / c_27q1 * 91
print(f"S01 balances: inv 2025-03-31={i_25q4} 2025-06-30={i_26q1} 2026-03-31={i_26q4} 2026-06-30={i_27q1}")
print(f"S01 cogs: FY26Q1={c_26q1} FY27Q1={c_27q1}")
check("S01 dio_prior", dio0, 47.2317); check("S01 dio_curr", dio1, 55.5587)
check("S01 dio_delta", dio1 - dio0, 8.327)
print(f"S01 growth: closing inv {i_27q1/i_26q1-1:.4%}; avg inv {avg1/avg0-1:.4%}; cogs {c_27q1/c_26q1-1:.4%}")

# ---- S02: FY2026 vs FY2025 全年（四季度求和）----
S = lambda a, fy: sum(fact(a, fy, q) for q in (1, 2, 3, 4))
gp25, gp26, oi25, oi26, rev25, rev26 = (S("gross_profit", 2025), S("gross_profit", 2026),
                                        S("operating_income", 2025), S("operating_income", 2026),
                                        S("revenue", 2025), S("revenue", 2026))
ne25, ne26 = gp25 - oi25, gp26 - oi26
check("S02 rev25", rev25, 69076.968); check("S02 rev26", rev26, 83074.555)
check("S02 gp25", gp25, 11097.610); check("S02 gp26", gp26, 12809.422)
check("S02 dGP", gp26 - gp25, 1711.812); check("S02 dNE", ne26 - ne25, 614.222)
check("S02 dOI", oi26 - oi25, 1097.590)
print(f"S02 bridge tie: dGP-dNE={gp26-gp25-(ne26-ne25):.3f} vs dOI={oi26-oi25:.3f}")
print(f"S02 margin GM {gp25/rev25:.4%} -> {gp26/rev26:.4%}; OI margin {oi25/rev25:.4%} -> {oi26/rev26:.4%}")
print(f"S02 Q4=annual-9M sanity: FY26Q4 gp={fact('gross_profit',2026,4)} 9M={S('gross_profit',2026)-fact('gross_profit',2026,4):.3f}")

# ---- S03: FY2026 Q1/Q2 vs FY2025 Q1/Q2 分层 ----
for q in (1, 2):
    print(f"S03 Q{q}: rev {fact('revenue',2025,q)}->{fact('revenue',2026,q)}; "
          f"gp {fact('gross_profit',2025,q)}->{fact('gross_profit',2026,q)}; "
          f"oi {fact('operating_income',2025,q)}->{fact('operating_income',2026,q)}; "
          f"ni {fact('net_income',2025,q)}->{fact('net_income',2026,q)}")
dgp2 = fact("gross_profit", 2026, 2) - fact("gross_profit", 2025, 2)
dne2 = (fact("gross_profit", 2026, 2) - fact("operating_income", 2026, 2)) - \
       (fact("gross_profit", 2025, 2) - fact("operating_income", 2025, 2))
doi2 = fact("operating_income", 2026, 2) - fact("operating_income", 2025, 2)
check("S03 Q2 dGP", dgp2, 350.881); check("S03 Q2 dNE", dne2, 358.559); check("S03 Q2 dOI", doi2, -7.678)
dni1 = fact("net_income", 2026, 1) - fact("net_income", 2025, 1)
dni2 = fact("net_income", 2026, 2) - fact("net_income", 2025, 2)
h1_25 = fact("net_income", 2025, 1) + fact("net_income", 2025, 2)
h1_26 = fact("net_income", 2026, 1) + fact("net_income", 2026, 2)
print(f"S03 NI: dQ1={dni1:.3f} dQ2={dni2:.3f} sum={dni1+dni2:.3f} vs dH1={h1_26-h1_25:.3f}; H1 yoy {h1_26/h1_25-1:.4%}")

# ---- S04: FY2025Q3 vs FY2024Q3 费用金额与强度 ----
rev24q3, rev25q3 = fact("revenue", 2024, 3), fact("revenue", 2025, 3)
gp_, oi_ = ("gross_profit", 24, "operating_income"), None
ne24 = fact("gross_profit", 2024, 3) - fact("operating_income", 2024, 3)
ne25 = fact("gross_profit", 2025, 3) - fact("operating_income", 2025, 3)
check("S04 ne prior", ne24, 1988.454); check("S04 ne curr", ne25, 2271.687)
print(f"S04 rev {rev24q3}->{rev25q3} (+{rev25q3/rev24q3-1:.4%}); NE +{ne25/ne24-1:.4%}; "
      f"ratio {ne24/rev24q3:.4%} -> {ne25/rev25q3:.4%}; delta bps {(ne25/rev25q3-ne24/rev24q3)*10000:.2f}")

# ---- S05: 9M 加权毛利率与贡献分解 ----
r25 = [fact("revenue", 2025, q) for q in (1, 2, 3)]; g25 = [fact("gross_profit", 2025, q) for q in (1, 2, 3)]
r26 = [fact("revenue", 2026, q) for q in (1, 2, 3)]; g26 = [fact("gross_profit", 2026, q) for q in (1, 2, 3)]
check("S05 gp25q1", g25[0], 2559.849); check("S05 gp25q2", g25[1], 2795.750); check("S05 gp25q3", g25[2], 2959.391)
R25, R26 = sum(r25), sum(r26)
w25, w26 = [x / R25 for x in r25], [x / R26 for x in r26]
m25, m26 = [g / r for g, r in zip(g25, r25)], [g / r for g, r in zip(g26, r26)]
M25, M26 = sum(g25) / R25, sum(g26) / R26
print(f"S05 margins FY25 {[f'{x:.4%}' for x in m25]}; FY26 {[f'{x:.4%}' for x in m26]}")
check("S05 w25Q1", w25[0] * 100, 29.6526, 0.0001); check("S05 w26Q1", w26[0] * 100, 30.6245, 0.0001)
check("S05 M25", M25 * 100, 15.9617, 0.0001); check("S05 M26", M26 * 100, 15.0767, 0.0001)
check("S05 dM", (M26 - M25) * 100, -0.8849, 0.0001)
rate = sum(w25[i] * (m26[i] - m25[i]) for i in range(3))
mix = sum((w26[i] - w25[i]) * m26[i] for i in range(3))
print(f"S05 decomp: rate={rate*100:.4f}pp mix={mix*100:.4f}pp tie_sum={(rate+mix)*100:.4f}pp vs dM={(M26-M25)*100:.4f}pp")
check("S05 tie", (rate + mix) * 100, (M26 - M25) * 100, 0.0001)
per = [(rate_i * 100, mix_i * 100) for rate_i, mix_i in
       zip((w25[i] * (m26[i] - m25[i]) for i in range(3)), ((w26[i] - w25[i]) * m26[i] for i in range(3)))]
print("S05 per-quarter (rate,mix) pp:", [(f"{a:.4f}", f"{b:.4f}") for a, b in per])
print(f"S05 FY26 three-quarter simple mean: {sum(m26)/3:.4%}")

# ---- S06: 三余额净占用 FY2027Q1 vs FY2026Q1 ----
def tri(fy, q):
    return fact("inventory", fy, q), fact("accounts_receivable", fy, q), fact("accounts_payable", fy, q)
i0, a0, p0 = tri(2026, 1); i1, a1, p1 = tri(2027, 1)
d_inv, d_ar, d_ap = i1 - i0, a1 - a0, p1 - p0
net = d_inv + d_ar - d_ap
# 审核文档/原 gold 称 +5280.727，但其公式串（dInv=7020.312, dAR=8470.213）
# 与自身 required_inputs 余额（8742.635→15741.947；10796.573→19265.786）不符。
# 以 PDF/DB 余额逐字重算为准，差额 22.000 登记为原 gold 算术缺陷。
check("S06 dInv", d_inv, 6999.312); check("S06 dAR", d_ar, 8469.213); check("S06 dAP", d_ap, 10209.798)
check("S06 net usage (recomputed)", net, 5258.727)
print(f"S06 NOTE: review-doc 5280.727 minus recomputed {net:.3f} = {5280.727-net:.3f} (orig gold dInv +21.000, dAR +1.000)")
print(f"S06 balances FY26Q1 {(i0,a0,p0)} FY27Q1 {(i1,a1,p1)}")
rev0, rev1, c0, c1 = fact("revenue", 2026, 1), fact("revenue", 2027, 1), c_26q1, c_27q1
# 平均余额口径（统一评测定义，与原 gold 公式一致）：DSO/DIO/DPO = 平均余额/单季流量×91
a_25q4, a_26q4 = fact("accounts_receivable", 2025, 4), fact("accounts_receivable", 2026, 4)
p_25q4, p_26q4 = fact("accounts_payable", 2025, 4), fact("accounts_payable", 2026, 4)
dso0, dso1 = (a_25q4+a0)/2/rev0*91, (a_26q4+a1)/2/rev1*91
dpo0, dpo1 = (p_25q4+p0)/2/c0*91, (p_26q4+p1)/2/c1*91
ccc0, ccc1 = dso0+dio0-dpo0, dso1+dio1-dpo1
check("S06 DSO avg prior", dso0, 51.4765); check("S06 DSO avg curr", dso1, 57.0271)
check("S06 DPO avg prior", dpo0, 74.1548); check("S06 DPO avg curr", dpo1, 88.2743)
check("S06 CCC avg prior", ccc0, 24.5533); check("S06 CCC avg curr", ccc1, 24.3115)
print(f"S06 AVG(days): DSO {dso0:.4f}->{dso1:.4f} DIO {dio0:.4f}->{dio1:.4f} DPO {dpo0:.4f}->{dpo1:.4f} CCC {ccc0:.4f}->{ccc1:.4f} (d={ccc1-ccc0:+.4f})")
dso_pt0, dso_pt1 = a0/rev0*91, a1/rev1*91
dio_pt0, dio_pt1 = i0/c0*91, i1/c1*91
dpo_pt0, dpo_pt1 = p0/c0*91, p1/c1*91
ccc_pt0, ccc_pt1 = dso_pt0+dio_pt0-dpo_pt0, dso_pt1+dio_pt1-dpo_pt1
print(f"S06 PT(days): DSO {dso_pt0:.3f}->{dso_pt1:.3f} DIO {dio_pt0:.3f}->{dio_pt1:.3f} "
      f"DPO {dpo_pt0:.3f}->{dpo_pt1:.3f} CCC {ccc_pt0:.3f}->{ccc_pt1:.3f} d={ccc_pt1-ccc_pt0:+.3f}")
cash0 = fact("cash", 2026, 1, "original"); 
try: cash1 = fact("cash", 2027, 1, "original")
except Exception as e: cash1 = None; print("S06 cash FY27Q1 missing:", e)
if cash1 is not None:
    print(f"S06 cash {cash0}->{cash1} (+{cash1-cash0:.3f})")

# ---- S07: FY2026Q1 vs FY2025Q1 利润桥 ----
dgp = fact("gross_profit", 2026, 1) - fact("gross_profit", 2025, 1)
dne = (fact("gross_profit", 2026, 1) - fact("operating_income", 2026, 1)) - \
      (fact("gross_profit", 2025, 1) - fact("operating_income", 2025, 1))
doi = fact("operating_income", 2026, 1) - fact("operating_income", 2025, 1)
check("S07 dGP", dgp, 214.628); check("S07 dNE(下降)", dne, -75.712); check("S07 dOI", doi, 290.340)
print(f"S07 tie: dGP-dNE={dgp-dne:.3f} vs dOI={doi:.3f}")
print(f"S07 levels: gp {fact('gross_profit',2025,1)}->{fact('gross_profit',2026,1)}; "
      f"oi {fact('operating_income',2025,1)}->{fact('operating_income',2026,1)}; "
      f"rev {fact('revenue',2025,1)}->{fact('revenue',2026,1)}; "
      f"gm {fact('gross_profit',2025,1)/fact('revenue',2025,1):.4%}->{fact('gross_profit',2026,1)/fact('revenue',2026,1):.4%}; "
      f"opm {fact('operating_income',2025,1)/fact('revenue',2025,1):.4%}->{fact('operating_income',2026,1)/fact('revenue',2026,1):.4%}")

# ---- R01/R02/R03 内部勾稽（PDF 精确数千美元→百万，桥项来自已核 span）----
bc = 19.202 - 250.083 + 16.524 + 1.240 + 22.229 + 1690.299 + 23.272
bp = 784.809 - 20.893 + 16.458 + 3.033 - 152.361
check("R01 bridge_curr", bc, 1522.683, 0.001); check("R01 bridge_prior", bp, 631.046, 0.001)
d_adj = 1522.683 - 631.046
d_rep = 19.202 - 784.809
T_c = 1522.683 - 19.202   # 本期调节合计
T_p = 631.046 - 784.809   # 上年调节合计
print(f"R01 yoy: dReported={d_rep:.3f} dAdjusted={d_adj:.3f} dAdjItems={T_c-T_p:.3f} "
      f"warrant swing={-(1690.299+152.361):.3f}")
check("R01 yoy tie", d_adj - (T_c - T_p), d_rep, 0.001)
seg_curr = 17105.518 + 8510.078 + 2883.628 - 1556.458
seg_prior = 13459.338 + 4290.149 + 2257.718 - 1177.336
check("R02 seg tie curr", seg_curr, fact("revenue", 2027, 1), 0.001)
check("R02 seg tie prior", seg_prior, fact("revenue", 2026, 1), 0.001)
check("R02 seg sum delta", 3646.180 + 4219.929 + 625.910 - 379.122, seg_curr - seg_prior, 0.001)
check("R02 vs DB rev delta", seg_curr - seg_prior, fact("revenue", 2027, 1) - fact("revenue", 2026, 1), 0.001)
check("R03 Q1 prior", -73.002 - (-35.728), -37.274, 0.001)
check("R03 Q1 curr", -117.555 - (-32.035), -85.520, 0.001)
print("ALL VERIFIED")

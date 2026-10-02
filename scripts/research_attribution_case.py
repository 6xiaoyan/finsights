"""Read-only exploration of one downloaded filing and existing quarterly facts."""
from pathlib import Path
import hashlib
import json
import math

import pandas as pd

import duckdb
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/evidence/attribution_case"
OUT.mkdir(parents=True, exist_ok=True)
path = ROOT / "data/raw/lenovo/FY23Q3_170220231201.pdf"
pdf = pdfium.PdfDocument(str(path))
texts = []
for index in range(len(pdf)):
    page = pdf[index]
    textpage = page.get_textpage()
    texts.append(f"\n=== PDF PAGE {index + 1} ===\n" + textpage.get_text_range())
    textpage.close()
    page.close()
pdf.close()
(OUT / "lenovo_FY23Q3_text.txt").write_text("\n".join(texts), encoding="utf-8")
con = duckdb.connect(str(ROOT / "data/finsights.duckdb"), read_only=True)
sql = """SELECT p.company_id, p.fiscal_year, p.fiscal_quarter, p.period_end,
p.calendar_quarter, p.days, f.account_code, f.value, f.available_date, f.source_ref
FROM facts f JOIN periods p USING(company_id,fiscal_year,fiscal_quarter)
WHERE f.version='original' AND p.period_end BETWEEN '2019-01-01' AND '2024-12-31'
AND f.account_code IN ('inventory','revenue','cogs','gross_profit','accounts_receivable',
'accounts_payable','cash') ORDER BY p.company_id,p.period_end,f.account_code"""
df = con.execute(sql).df()
df.to_csv(OUT / "quarterly_inputs.csv", index=False, encoding="utf-8")
wide = df.pivot(index=["company_id","fiscal_year","fiscal_quarter","period_end","calendar_quarter","days"], columns="account_code",values="value").reset_index()
for _, indices in wide.groupby("company_id").groups.items():
    indices = list(indices)
    for metric in ("inventory", "revenue", "cogs", "accounts_receivable", "accounts_payable"):
        values = wide.loc[indices,metric]
        wide.loc[indices, metric + "_qoq_pct"] = values.pct_change(fill_method=None).to_numpy() * 100
        wide.loc[indices, metric + "_yoy_pct"] = values.pct_change(4,fill_method=None).to_numpy() * 100
    wide.loc[indices,"dio"] = (((wide.loc[indices,"inventory"] + wide.loc[indices,"inventory"].shift(1)) / 2) / wide.loc[indices,"cogs"] * wide.loc[indices,"days"]).to_numpy()
    wide.loc[indices,"dso"] = (((wide.loc[indices,"accounts_receivable"] + wide.loc[indices,"accounts_receivable"].shift(1)) / 2) / wide.loc[indices,"revenue"] * wide.loc[indices,"days"]).to_numpy()
    wide.loc[indices,"gross_margin_pct"] = (wide.loc[indices,"gross_profit"] / wide.loc[indices,"revenue"] * 100).to_numpy()
wide.to_csv(OUT / "quarterly_factors.csv",index=False,encoding="utf-8")
(OUT / "provenance.txt").write_text(f"Filing: {path}\nPDF SHA256: {hashlib.sha256(path.read_bytes()).hexdigest()}\nDatabase SHA256: {hashlib.sha256((ROOT / 'data/finsights.duckdb').read_bytes()).hexdigest()}\nSQL:\n{sql}\nFormula: QoQ=V_t/V_(t-1)-1; YoY=V_t/V_(t-4)-1; DIO=mean(I_t,I_(t-1))/COGS_t*days_t; DSO=mean(AR_t,AR_(t-1))/Revenue_t*days_t\nCurrent observations are exploratory, not a blind validation.\n",encoding="utf-8")
con.close()
print("Extracted",len(texts),"pages and",len(df),"facts to",OUT)
print(wide[(wide.company_id == "Lenovo") & (wide.period_end.between("2022-06-01", "2023-12-31"))][["fiscal_year","fiscal_quarter","period_end","revenue","inventory","revenue_yoy_pct","inventory_yoy_pct","inventory_qoq_pct","dio","gross_margin_pct"]].to_string(index=False))

# The following branch rules were recorded in docs/attribution_method_draft.md
# before inspecting peer results. They identify patterns, not proven causes.
wide["dio_yoy_delta"] = wide.groupby("company_id")["dio"].diff(4)
def branch(row):
    vals = [row.get(k) for k in ("revenue_yoy_pct", "inventory_yoy_pct", "dio_yoy_delta")]
    if any(pd.isna(v) for v in vals):
        return "insufficient_data"
    rev, inv, dio = vals
    if rev < 0 and inv > 0 and dio > 0:
        return "inventory_pressure_candidate"
    if rev < 0 and inv < 0 and dio >= 0:
        return "inventory_reduction_without_turnover_improvement"
    if rev < 0 and inv < 0 and dio < 0:
        return "inventory_reduction_with_turnover_improvement"
    if rev > 0 and inv > 0 and dio <= 0:
        return "expansion_compatible"
    return "mixed_or_unclassified"
wide["pattern"] = wide.apply(branch,axis=1)
cols = ["company_id", "fiscal_year", "fiscal_quarter", "period_end", "calendar_quarter", "revenue_yoy_pct", "inventory_yoy_pct", "inventory_qoq_pct", "dio", "dio_yoy_delta", "pattern"]
chosen = wide[(wide.company_id.isin(["Dell","HP"])) & wide.period_end.between("2022-01-01","2023-12-31")]
chosen[cols].to_csv(OUT / "peer_replay.csv",index=False,encoding="utf-8")
print("PEER NUMERICAL REPLAY (no independently labelled narrative gold)")
print(chosen[cols].to_string(index=False,float_format=lambda v:f"{v:.3f}"))
lenovo = wide[(wide.company_id == "Lenovo") & (wide.fiscal_year == 2023) & (wide.fiscal_quarter == 3)]
print("LENOVO DIO YOY:",lenovo[cols].to_string(index=False,float_format=lambda v:f"{v:.3f}"))
segments = [
    {"segment": "IDG", "current_usd_mn":11585.682, "prior_usd_mn":17609.684},
    {"segment": "ISG", "current_usd_mn":2855.147, "prior_usd_mn":1928.783},
    {"segment": "SSG", "current_usd_mn":1836.435, "prior_usd_mn":1497.621},
    {"segment": "eliminations", "current_usd_mn":-1010.658, "prior_usd_mn":-909.556},
]
for s in segments:
    s.update(company="Lenovo", fiscal_year=2023,fiscal_quarter=3,source_pdf=path.name,page=11)
    s["delta_usd_mn"] = s["current_usd_mn"]-s["prior_usd_mn"]
    s["contribution_pct"] = s["delta_usd_mn"]/(15266.606-20126.532)*100
    s["yoy_pct"] = (s["current_usd_mn"]/s["prior_usd_mn"]-1)*100
pd.DataFrame(segments).to_csv(OUT / "lenovo_real_segments.csv",index=False,encoding="utf-8")
print("SEGMENT DELTA SUM:",sum(s["delta_usd_mn"] for s in segments))

lv = wide[wide.company_id == "Lenovo"].copy()
lv["inventory_avg"] = (lv.inventory + lv.inventory.shift(1))/2
current = lv[(lv.fiscal_year == 2023) & (lv.fiscal_quarter == 3)].iloc[0]
prior = lv[(lv.fiscal_year == 2022) & (lv.fiscal_quarter == 3)].iloc[0]
decomp = {"current_dio":float(current.dio),"prior_dio":float(prior.dio),
    "current_inventory_avg":float(current.inventory_avg),"prior_inventory_avg":float(prior.inventory_avg),
    "current_cogs":float(current.cogs),"prior_cogs":float(prior.cogs),
    "inventory_log_term":math.log(current.inventory_avg/prior.inventory_avg),
    "cogs_log_term":-math.log(current.cogs/prior.cogs),
    "days_log_term":math.log(current.days/prior.days),
    "dio_log_change":math.log(current.dio/prior.dio)}
decomp["identity_residual"] = sum(decomp[k] for k in ("inventory_log_term","cogs_log_term","days_log_term"))-decomp["dio_log_change"]
(OUT / "lenovo_dio_decomposition.json").write_text(json.dumps(decomp,indent=2),encoding="utf-8")
print("DIO DECOMPOSITION:",json.dumps(decomp))

"""Independent, offline review requested by the user (no model/API calls)."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
report = {"date": "2026-10-02", "reviewer": "Codex", "db_sha256": hashlib.sha256((ROOT / "data/finsights.duckdb").read_bytes()).hexdigest()}
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
report["env_tracked"] = ".env" in tracked
untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
keys = []
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" not in line or line.lstrip().startswith("#"):
        continue
    name, value = line.split("=", 1)
    value = value.strip().strip("\"'")
    if re.search(r"KEY|TOKEN|SECRET", name, re.I) and value:
        keys.append(value)
pattern = re.compile(r"sk-[A-Za-z0-9]{16,}|api_key\s*=\s*['\"][^'\"]+")
allowlist_path = ROOT / "docs/evidence/G4_example_allowlist.json"
allowlist = json.loads(allowlist_path.read_text(encoding="utf-8")) if allowlist_path.exists() else []
hits = []
actual = []
for name in sorted(set(filter(None, tracked + untracked))):
    path = ROOT / name
    if not path.is_file():
        continue
    raw = path.read_bytes()
    if any(key.encode() in raw for key in keys):
        actual.append(name)
    if b"\0" in raw:
        continue
    for n, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        for match in pattern.finditer(line):
            digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
            exempt = {"path": name, "line_sha256": digest} in allowlist
            hits.append({"path": name, "line": n, "line_sha256": digest, "registered_example": exempt})
report["actual_key_match_paths"] = actual
report["generic_hits_redacted"] = hits
con = duckdb.connect(str(ROOT / "data/finsights.duckdb"), read_only=True)
sql = """SELECT account_code, value FROM facts
WHERE company_id='HP' AND fiscal_year=2024 AND fiscal_quarter=3 AND version='original'
AND account_code IN ('total_liabilities','total_current_liabilities','long_term_debt',
'other_noncurrent_liabilities','deferred_revenue_noncurrent') ORDER BY 1"""
values = dict(con.execute(sql).fetchall())
report["hp_identity"] = {"sql": sql, "values": values,
    "sum_without_nested_deferred_revenue": sum(values[k] for k in ("total_current_liabilities", "long_term_debt", "other_noncurrent_liabilities")),
    "sum_with_nested_deferred_revenue": sum(values[k] for k in ("total_current_liabilities", "long_term_debt", "other_noncurrent_liabilities", "deferred_revenue_noncurrent"))}
report["v447_traces"] = []
for name in ("l2_0003_3.jsonl", "l1_0002_1.jsonl", "l1_0001_1.jsonl"):
    events = [json.loads(line) for line in (ROOT / "runs/20261001-215744" / name).read_text(encoding="utf-8").splitlines() if line]
    final = next(e for e in events if e.get("type") == "final")
    result = next(e for e in events if e.get("type") == "result")
    report["v447_traces"].append({"file": name, "final": final, "result": result})
con.close()
path = ROOT / "docs/evidence/review_20261002.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"env_tracked": report["env_tracked"], "actual_key_match_count": len(actual),
    "generic_hit_count": len(hits), "unregistered_hit_count": sum(not h["registered_example"] for h in hits),
    "hp_identity": report["hp_identity"], "evidence": str(path)}, ensure_ascii=False))
raise SystemExit(1 if report["env_tracked"] or actual or any(not h["registered_example"] for h in hits) else 0)

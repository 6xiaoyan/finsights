"""第二轮基线（handoff_concept_dictionary_verifier.md §6）：S01–S07 各跑一次。

用法：.venv/Scripts/python scripts/live/round2_selected_v1.py
- 输入：data/eval/lenovo_attribution_candidates/selected_v1/cases.jsonl（revision 2，S01–S07）
- 输出：runs/selected_v1_dev_round2/<case_id>/ 五件套 + manifest.tsv
- 与第一轮一致：默认 config（max_steps=20）、同一模型与限流；不覆写第一轮目录
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(".")
CASES = Path("data/eval/lenovo_attribution_candidates/selected_v1/cases.jsonl")
OUT = Path("runs/selected_v1_dev_round2")
PY = str(Path(".venv/Scripts/python.exe"))


def main() -> int:
    cases = [json.loads(ln) for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    sel = [c for c in cases if str(c.get("selected_id", "")) in
           (f"S0{i}" for i in range(1, 8))]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for c in sel:
        case_id = c["selected_id"]
        before = {d.name for d in OUT.parent.glob("cli-*")}
        t0 = time.monotonic()
        proc = subprocess.run(
            [PY, "-m", "agent.cli", "--question", c["query"], "--max-steps", "20",
             "--out-root", "runs"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8",
                 "NO_PROXY": "api.agnes-ai.cn"})
        new = [d.name for d in OUT.parent.glob("cli-*") if d.name not in before]
        pkg = ""
        if new:
            src = OUT.parent / new[-1]
            dst = OUT / case_id
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
            pkg = str(dst)
        qj = (dst / "question.json") if pkg else None
        status = verified = ""
        if qj and Path(qj).exists():
            qd = json.loads(Path(qj).read_text(encoding="utf-8"))
            meta = json.loads((dst / "run_meta.json").read_text(encoding="utf-8"))
            status, verified = meta["status"], meta["verified"]
            manifest.append([case_id, str(proc.returncode), status, str(verified),
                             pkg, f"{time.monotonic() - t0:.0f}s"])
        else:
            manifest.append([case_id, str(proc.returncode), "no-package", "", "",
                             f"{time.monotonic() - t0:.0f}s"])
        print("\t".join(manifest[-1]))
        time.sleep(5)  # 免费档 RPM 间隔
    (OUT / "manifest.tsv").write_text(
        "case_id\texit\tstatus\tverified\tpackage\twall\n" +
        "\n".join("\t".join(r) for r in manifest) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

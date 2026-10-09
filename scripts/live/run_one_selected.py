"""跑 selected_v1 的单题（single_agent / multi_agent），从 cases.jsonl 读题面。

为什么不用命令行直接传中文：PowerShell 的参数编码会把题面弄坏，而题面必须与
cases.jsonl 逐字一致（否则不是同一道题，结果不可比）。

用法：
  .venv\\Scripts\\python scripts\\live/run_one_selected.py S01 single_agent 100
  .venv\\Scripts\\python scripts\\live/run_one_selected.py S01 multi_agent 100
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CASES = Path("data/eval/lenovo_attribution_candidates/selected_v1/cases.jsonl")
PY = str(Path(".venv/Scripts/python.exe"))


def main() -> int:
    sid = sys.argv[1] if len(sys.argv) > 1 else "S01"
    mode = sys.argv[2] if len(sys.argv) > 2 else "single_agent"
    steps = sys.argv[3] if len(sys.argv) > 3 else "100"
    out_root = sys.argv[4] if len(sys.argv) > 4 else f"runs/ds_{mode}_{sid}"

    rows = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    case = next(r for r in rows if r["selected_id"] == sid)
    q = case["query"]
    print(f"case={sid}  mode={mode}  max_steps={steps}")
    print(f"题面（逐字来自 cases.jsonl revision {case.get('case_revision')}）：")
    print("  " + q)

    out_root_p = Path(out_root)
    out_root_p.mkdir(parents=True, exist_ok=True)
    before = {d.name for d in Path("runs").glob("cli-*")}

    cmd = [PY, "-m", "agent.cli", "--question", q, "--max-steps", steps,
           "--agent-mode", mode, "--out-root", "runs"]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8",
                               "NO_PROXY": "api.deepseek.com"})
    wall = time.monotonic() - t0
    new = [d.name for d in Path("runs").glob("cli-*") if d.name not in before]

    dst = out_root_p
    if new:
        src = Path("runs") / new[-1]
        if dst.exists():
            import shutil
            shutil.rmtree(dst)
        import shutil
        shutil.move(str(src), str(dst))

    meta_p = dst / "run_meta.json"
    if meta_p.exists():
        m = json.loads(meta_p.read_text(encoding="utf-8"))
        u = m.get("usage") or {}
        print("\n--- 结果 ---")
        print(f"status={m['status']}  verified={m['verified']}  exit={proc.returncode}")
        print(f"steps={u.get('steps')}  prompt={u.get('prompt_tokens')}  "
              f"completion={u.get('completion_tokens')}  latency={u.get('latency_s')}s  "
              f"sql_errors={u.get('sql_errors')}")
        print(f"model={m.get('model')}  commit={m.get('commit')}  dirty={m.get('dirty')}")
        print(f"rate_limit_rpm={m.get('budget', {}).get('rate_limit_rpm')}  "
              f"max_wall_s={m.get('budget', {}).get('max_wall_s')}")
        if m.get("multi_agent"):
            ma = m["multi_agent"]
            print(f"multi: {json.dumps(ma.get('consumption'), ensure_ascii=False)}")
            for k in ("final_answer_rejects",):
                if k in ma:
                    print(f"  {k}={ma[k]}")
        print(f"包: {dst}")
    else:
        print("\n!! 没有生成 run_meta.json（运行可能在导出前就异常终止）")
        print("stderr 尾部:", (proc.stderr or "")[-1200:])

    print(f"\n墙钟 {wall:.0f}s | exit={proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
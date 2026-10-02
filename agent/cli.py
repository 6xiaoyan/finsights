"""MVP 单问题命令行入口（docs/mvp_handoff_qwen.md §3.1）。

自由文本问题 → v1 agent 取数/计算/检索披露 → 核验 → 结论与运行包落盘。
不新建主循环：复用 agent.loop 与 agent.v1.Paced；限速复用 eval.runner.RateLimiter，
单问题入口与评测共用同一 RPM 配额语义，不绕过免费额度。

--as-of：走 etl.snapshot.make_snapshot 的时点快照库（与 runner 的 as_of 题同一路径），
仅向 prompt 传日期不构成时点隔离；未提供则明确使用当前库，不称历史回测。

用法：
  .venv\\Scripts\\python -m agent.cli --question "联想 FY2023Q3 存货下降是否意味着周转改善？"
  .venv\\Scripts\\python -m agent.cli --question "..." --as-of 2024-06-30

退出码：0=answered 且核验通过；2=核验未通过；3=clarify/refuse（预算拒答等）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from agent.llm import LLMClient
from agent.loop import make_run_ctx, run as loop_run
from agent.v1 import Paced
from eval.runner import RateLimiter, _git_commit

DB_PATH = Path("data/finsights.duckdb")
SECRET_RE = re.compile(r"key|token|secret|password|quota", re.I)
MAX_EXPORT_ROWS = 2000  # 导出防御上限：正常工具返回远小于此值，超出记 truncated


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if SECRET_RE.search(str(k)) else _sanitize(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _export_package(out_dir: Path, question: str, as_of, db_path: Path, client,
                    cfg_path: str, ctx, out, info, run_id: str) -> dict:
    """运行包（§3.2）：question/answer/trace/results/run_meta 五件套，人工可查。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from agent.tools.db import connect
        con = connect(db_path)
        try:
            rng = con.execute(
                "SELECT company_id, min(period_end), max(period_end) FROM periods"
                " GROUP BY 1 ORDER BY 1").fetchall()
        finally:
            con.close()
        available = {c: {"from": str(a), "to": str(b)} for c, a, b in rng}
    except Exception:
        available = None
    (out_dir / "question.json").write_text(json.dumps({
        "run_id": run_id, "question": question, "as_of": as_of.isoformat() if as_of else None,
        "db": str(db_path), "available_range": available,
        "note": ("时点快照：数据/披露已按 available_date 过滤" if as_of
                 else "未提供 --as-of：使用当前库，非历史回测"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [json.dumps({"type": "meta", "run_id": run_id, "question": question,
                         "ts": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False)]
    lines += [json.dumps(ev, ensure_ascii=False) for ev in info["trace"]]
    lines.append(json.dumps({"type": "result", "status": out.answer.status,
                             "verified": out.verified}, ensure_ascii=False))
    (out_dir / "trace.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows_truncated = False
    results = []
    for res in ctx.store.all():
        df = res.df
        rec = {"id": res.id, "tool": res.tool, "args": res.args, "sql": res.sql,
               "unit": res.unit, "data_version": res.data_version,
               "created_step": res.created_step, "digest": res.digest[:200]}
        if len(df) > MAX_EXPORT_ROWS:
            rows_truncated = True
            rec["truncated"] = len(df) - MAX_EXPORT_ROWS
            df = df.head(MAX_EXPORT_ROWS)
        rec["rows"] = json.loads(df.to_json(orient="records"))
        results.append(rec)
    (out_dir / "results.json").write_text(json.dumps(
        {"results": results, "any_truncated": rows_truncated},
        ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        import yaml
        cfg_safe = _sanitize(yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8")))
    except Exception:
        cfg_safe = None
    idx = Path("data/disclosures/index.json")
    meta = {
        "run_id": run_id, "created": datetime.now().isoformat(timespec="seconds"),
        "agent": "v1", "model": client.model, "config": cfg_safe,
        "commit": _git_commit(), "dirty": _is_dirty(),
        "db": str(db_path), "db_sha256_12": _file_hash(db_path) if db_path.exists() else None,
        "data_version": ctx.store.data_version,
        "disclosure_index": {"path": str(idx),
                             "sha256_12": _file_hash(idx) if idx.exists() else None},
        "budget": {"max_steps": ctx.budget.max_steps, "max_tokens": ctx.budget.max_tokens,
                   "max_wall_s": ctx.budget.max_wall_s},
        "usage": {k: info.get(k) for k in ("steps", "prompt_tokens", "completion_tokens",
                                           "latency_s", "sql_errors", "refuse_reason")},
        "verified": out.verified, "status": out.answer.status,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8")

    ans = [f"# {question}\n", f"- status: {out.answer.status}",
           f"- 核验: {'通过（仅指现有数字/引用规则通过，不代表经营因果结论被证明）' if out.verified else '未通过'}",
           f"- run_id: {run_id}  输出目录: {out_dir}\n", out.answer.answer_md, "\n"]
    if out.answer.claims:
        ans.append("\n## 引用\n")
        for c in out.answer.claims:
            ans.append(f"- {c.text}  [{c.value} {c.unit} → {c.ref}]\n")
    (out_dir / "answer.md").write_text("".join(ans), encoding="utf-8")
    return meta


def _is_dirty() -> bool:
    """git 工作区状态探测放在 eval 侧实现（agent/ 禁起进程，G7），这里只转调。"""
    from eval.runner import git_dirty  # noqa: PLC0415
    return git_dirty()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.cli")
    ap.add_argument("--question", required=True)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD；走时点快照库")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max-rpm", type=float, default=None)
    ap.add_argument("--out-root", default="runs")
    args = ap.parse_args(argv)

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    db_path = Path(args.db)
    if as_of is not None:
        from etl.snapshot import make_snapshot
        db_path = make_snapshot(as_of)

    with open(args.config, encoding="utf-8") as f:
        import yaml
        default_rpm = float((yaml.safe_load(f).get("llm") or {}).get("rate_limit_rpm", 18))
    limiter = RateLimiter(args.max_rpm or default_rpm)

    client = LLMClient(config_path=args.config)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    ctx = make_run_ctx(str(db_path), Paced(client, limiter), run_id=f"cli-{run_id}",
                       as_of=as_of, config_path=args.config)
    out = loop_run(args.question, ctx)

    info = dict(out.stats)
    info["trace"] = list(ctx.trace.events)
    info["sql_errors"] = sum(1 for e in ctx.trace.events
                             if e.get("type") == "tool_result"
                             and e.get("tool") == "run_sql" and not e.get("ok"))
    info.setdefault("refuse_reason", None)
    out_dir = Path(args.out_root) / f"cli-{run_id}"
    _export_package(out_dir, args.question, as_of, db_path, client, args.config,
                    ctx, out, info, run_id)

    print(out.answer.answer_md)
    print("\n—— 运行包已保存 ——")
    print(f"status={out.answer.status} | 核验={'通过' if out.verified else '未通过'}"
          f" | run_id={run_id} | 输出={out_dir}")
    if out.answer.status == "refuse":
        print("预算拒答：上方结论不可用（refuse）。", file=sys.stderr)
        return 3
    if out.answer.status == "clarify":
        print("需要澄清：未给出最终结论。", file=sys.stderr)
        return 3
    if not out.verified:
        print("核验未通过：数字/引用规则不符，结论按未核验处理，不得视为正常验证成功。")
        print("核验未通过（详见 run_meta.json 与 trace.jsonl）。", file=sys.stderr)
        return 2
    print("核验通过：仅指现有数字/引用规则通过，不代表经营因果结论已被证明。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

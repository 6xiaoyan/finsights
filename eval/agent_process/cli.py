"""`python -m eval.agent_process` 命令行入口。

子命令：adapt / metrics / roles / queries / report / replay。
默认全离线（`--judge fake`）：本阶段不调用真实 API，模拟判定的结论一律标
`pipeline_check`，不是真实质量评分。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.agent_process.adapt import FIELD_COMPAT, load_process_package
from eval.agent_process.metrics import compute_metrics
from eval.agent_process.query import diagnose_queries
from eval.agent_process.report import (build_record, case_report_md, compat_table_md,
                                       summary_report_md, write_reports)
from eval.agent_process.roles import reviewer as reviewer_role
from eval.agent_process.roles import planner as planner_role
from eval.agent_process.roles import worker as worker_role
from eval.agent_process.roles.common import SAMPLES_DIR, load_samples, make_judge

DEFAULT_OUT = Path("eval/reports/process")
ROLES = ("planner", "worker", "reviewer")


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


class CliError(Exception):
    """可预期的使用错误：打印一行、返回非零码，不抛栈。"""


def _pkg(args):
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise CliError(f"运行目录不存在：{run_dir}")
    return load_process_package(run_dir, getattr(args, "case_id", "") or "")


def _load_gold(path: str | None) -> list[dict] | None:
    """可选参考点（评测侧 gold）。未提供时 Planner 覆盖映射如实记 unknown。"""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise CliError(f"gold 文件不存在：{p}")
    text = p.read_text(encoding="utf-8-sig").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def cmd_adapt(args) -> int:
    pkg = _pkg(args)
    out = Path(args.out) if args.out else DEFAULT_OUT
    _write(out / "adapt.json", pkg.model_dump(mode="json"))
    _write(out / "compat_table.md", compat_table_md())
    print(f"adapt: {pkg.run_id} status={pkg.final_status} "
          f"nodes={len(pkg.plans[-1].nodes) if pkg.plans else 0} "
          f"role_calls={len(pkg.role_calls)} query_calls={len(pkg.query_records)}")
    print(f"  unknown_fields={pkg.unknown_fields or '（无）'}")
    print(f"  compat entries={len(FIELD_COMPAT)} -> {out / 'compat_table.md'}")
    return 0


def cmd_metrics(args) -> int:
    pkg = _pkg(args)
    out = Path(args.out) if args.out else DEFAULT_OUT
    m = compute_metrics(pkg)
    _write(out / "metrics.json", m)
    print(f"metrics: tool_calls={m['tool_calls']['total']} "
          f"roles={m['role_calls']['by_role']} tokens={m['tokens']['source']} "
          f"discrepancies={len(m['discrepancies'])}")
    return 0


def cmd_queries(args) -> int:
    pkg = _pkg(args)
    out = Path(args.out) if args.out else DEFAULT_OUT
    q = diagnose_queries(pkg)
    _write(out / "queries.json", q)
    print(f"queries: calls={q['calls']} by_reason={q['by_reason']}")
    return 0


def _roles_samples(args, judge, judge_is_live: bool) -> dict:
    samples_dir = Path(args.samples) if args.samples else SAMPLES_DIR
    wanted = ROLES if args.role == "all" else (args.role,)
    result: dict = {}
    for role in wanted:
        path = samples_dir / f"{role}.jsonl"
        if not path.is_file():
            result[role] = {"samples": [], "error": f"样例文件不存在：{path}"}
            continue
        samples = load_samples(path)
        if role == "reviewer":
            items = [reviewer_role.evaluate_sample(s) for s in samples]
        elif role == "planner":
            items = [planner_role.evaluate_sample(s, judge, judge_is_live=judge_is_live)
                     for s in samples]
        else:
            items = [worker_role.evaluate_sample(s, judge, judge_is_live=judge_is_live)
                     for s in samples]
        result[role] = {"samples": items, "count": len(items),
                        **_sample_agreement(role, items)}
    return result


def _sample_agreement(role: str, items: list[dict]) -> dict:
    """复现情况汇总：程序结论与样例标注是否一致。

    不一致必须显式报出——尤其 Worker 的 intent_error 只能由 Judge 判 contradicted 得出，
    离线模拟判必然降级成 unknown；这属于「本阶段拿不到」，不是评测器判错。
    """
    if role == "worker":
        ok = [i for i in items if i.get("cause_matches_annotation")]
        return {"agreement": len(ok), "total": len(items),
                "requires_live_judge": [i["sample_id"] for i in items
                                        if i.get("requires_live_judge")
                                        and not i.get("cause_matches_annotation")],
                "mismatched": [i["sample_id"] for i in items
                               if not i.get("cause_matches_annotation")]}
    if role == "planner":
        return {"with_coverage_mapping": sum(1 for i in items
                                             if i["coverage"]["status"] == "judged"),
                "total": len(items)}
    defined = [i for i in items if i["precision"] is not None]
    return {"with_precision": len(defined), "total": len(items),
            "mean_precision": (sum(i["precision"] for i in defined) / len(defined)
                               if defined else None)}


def cmd_roles(args) -> int:
    pkg = _pkg(args)
    out = Path(args.out) if args.out else DEFAULT_OUT
    judge_is_live = args.judge == "live"
    judge = make_judge(args.judge) if judge_is_live else make_judge(
        args.judge, point_fn=lambda q, p, f, a: {
            "verdict": "unknown", "reason": "离线模式未做模型判定（pipeline_check）"})
    wanted = ROLES if args.role == "all" else (args.role,)
    gold = _load_gold(getattr(args, "gold", None))

    if args.mode in ("trajectory", "both"):
        points = None
        if gold:
            pts = {g.get("case_id"): g.get("scoring_points")
                   for g in gold if isinstance(g, dict)}
            points = pts.get(pkg.case_id) or []
        roles_out: dict = {}
        if "planner" in wanted:
            roles_out["planner"] = planner_role.evaluate_package(
                pkg, judge=judge, points=points, judge_is_live=judge_is_live)
        if "worker" in wanted:
            roles_out["worker"] = worker_role.evaluate_package(
                pkg, judge=judge, judge_is_live=judge_is_live)
        if "reviewer" in wanted:
            roles_out["reviewer"] = reviewer_role.evaluate_package(
                pkg, judge=judge, judge_is_live=judge_is_live)
        _write(out / "roles_trajectory.json", {
            "mode": "trajectory", "run_id": pkg.run_id, "case_id": pkg.case_id,
            "judge": args.judge, "gold_provided": bool(gold),
            # 明确标注：模拟判定不是真实质量评分
            "is_real_quality_score": judge_is_live,
            "note": ("真实模型判定" if judge_is_live else
                     "模拟/离线判定：只验证评测流程，不是真实质量评分"),
            "roles": roles_out,
        })
        print(f"roles(trajectory): {list(roles_out)} judge={args.judge} "
              f"real_quality={judge_is_live}")

    if args.mode in ("samples", "both"):
        samples_out = _roles_samples(args, judge, judge_is_live)
        for role, payload in samples_out.items():
            _write(out / f"roles_samples_{role}.json", {
                "mode": "samples", "role": role, "judge": args.judge,
                "is_real_quality_score": judge_is_live,
                "not_real_quality_score": not judge_is_live,
                "note": ("真实模型判定" if judge_is_live else
                         "固定样例 + 模拟判定：只验证评测流程，不是真实质量评分"),
                **payload,
            })
        print(f"roles(samples): { {k: v.get('count', 0) for k, v in samples_out.items()} }")
    return 0


def _load_e2e(path: str | None) -> list[dict] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise CliError(f"e2e 结果文件不存在：{p}")
    text = p.read_text(encoding="utf-8-sig").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _build(pkg, e2e_results=None) -> dict:
    return build_record(pkg, metrics=compute_metrics(pkg),
                        roles={"planner": planner_role.evaluate_package(pkg),
                               "worker": worker_role.evaluate_package(pkg),
                               "reviewer": reviewer_role.evaluate_package(pkg)},
                        queries=diagnose_queries(pkg), e2e_results=e2e_results)


def cmd_report(args) -> int:
    pkg = _pkg(args)
    e2e_results = _load_e2e(getattr(args, "e2e_results", None))
    rec = _build(pkg, e2e_results)
    e2e = rec.get("e2e")
    md = case_report_md(pkg, metrics=rec["process"]["metrics"],
                        roles=rec["process"]["roles"], queries=rec["process"]["queries"],
                        e2e=e2e)
    out = Path(args.out) if args.out else DEFAULT_OUT / "report.md"
    _write(out, md)
    print(f"report: {out}（e2e={'已关联' if e2e else '未提供'}）")
    return 0


def cmd_replay(args) -> int:
    """固定包回放：一次跑完 adapt → metrics → queries → roles → report。"""
    out = Path(args.out_dir) if args.out_dir else DEFAULT_OUT

    def _ns(**over):
        v = dict(vars(args))
        v.update(over)
        return argparse.Namespace(**v)

    for fn in (cmd_adapt, cmd_metrics, cmd_queries):
        if fn(_ns(out=str(out))):
            return 1

    if cmd_roles(_ns(out=str(out), mode="both", role="all", judge=args.judge,
                     samples=None, gold=getattr(args, "gold", None))):
        return 1

    pkg = _pkg(args)
    e2e_results = _load_e2e(getattr(args, "e2e_results", None))
    rec = _build(pkg, e2e_results)
    e2e = rec.get("e2e")
    _write(out / "report.md",
           case_report_md(pkg, metrics=rec["process"]["metrics"],
                          roles=rec["process"]["roles"],
                          queries=rec["process"]["queries"], e2e=e2e))
    write_reports(out, [rec], run_ids=[pkg.run_id], dataset_name=None)
    print(f"replay: 全部产物在 {out}/")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m eval.agent_process",
                                description="运行包适配 / 运行监控 / 角色独立评测 / 报告")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--run-dir", required=True, help="运行包目录")
        sp.add_argument("--case-id", default="", help="评测侧绑定的 case_id")
        sp.add_argument("--out", default=None, help=f"输出目录（默认 {DEFAULT_OUT}）")

    sp = sub.add_parser("adapt", help="运行包 → 规范化对象 + 字段兼容表")
    common(sp)
    sp.set_defaults(func=cmd_adapt)

    sp = sub.add_parser("metrics", help="运行监控统计")
    common(sp)
    sp.set_defaults(func=cmd_metrics)

    sp = sub.add_parser("queries", help="微观查询链路诊断")
    common(sp)
    sp.set_defaults(func=cmd_queries)

    sp = sub.add_parser("roles", help="Planner/Worker/Reviewer 独立评测")
    common(sp)
    sp.add_argument("--mode", choices=("trajectory", "samples", "both"), default="both")
    sp.add_argument("--role", choices=(*ROLES, "all"), default="all")
    sp.add_argument("--samples", default=None, help=f"固定样例目录（默认 {SAMPLES_DIR}）")
    sp.add_argument("--judge", choices=("fake", "live"), default="fake",
                    help="fake=离线（默认，不调用真实 API）；live=真实模型判定")
    sp.add_argument("--gold", default=None,
                    help="评测侧参考点（JSON/JSONL），提供后 Planner 覆盖映射才会判 judged")
    sp.set_defaults(func=cmd_roles)

    sp = sub.add_parser("report", help="逐题报告")
    common(sp)
    sp.add_argument("--e2e-results", default=None, help="A 的 E2E results.jsonl（可选）")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("replay", help="固定包回放：一次跑完全部评测")
    common(sp)
    sp.add_argument("--out-dir", default=None, help=f"输出目录（默认 {DEFAULT_OUT}）")
    sp.add_argument("--e2e-results", default=None)
    sp.add_argument("--judge", choices=("fake", "live"), default="fake")
    sp.add_argument("--gold", default=None)
    sp.set_defaults(func=cmd_replay)
    return p


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv[1:])
    try:
        return args.func(args)
    except CliError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""E2E 评测 CLI（PRD §6.3：三个真实可运行入口 + 报告/审核工具）。

子命令：
  validate        校验数据集结构、来源与审核状态
  generate-points 生成得分点候选（强制 draft，不自动批准）
  score-run       对已有运行包评分（只读原包，不重新请求 Agent）
  run-dataset     按固定数据集调用当前 Agent（agent.cli 子进程）并评分
  report          由 results.jsonl 生成汇总报告
  review          人工/独立审核者设置审核状态，写 review_log.md（非自动）

诚实边界：score-run / run-dataset 的评分需要 judge；默认用 LLM judge（需 API key），
`--judge-fake` 仅用于离线自检，输出会明确标注 judge=callable，不冒充真实评分。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.financial_e2e.adapter import load_run_artifact, run_question
from eval.financial_e2e.generate import apply_candidate, generate_points
from eval.financial_e2e.grader import GRADER_VERSION, grade_case
from eval.financial_e2e.judge import CallableJudge, LLMJudge
from eval.financial_e2e.report import summary_report_md, write_case_report
from eval.financial_e2e.schema import CaseRecord, load_dataset, save_dataset

DEFAULT_CASES = Path("eval/datasets/financial_real_v1/cases.jsonl")


def _find_case(cases: list[CaseRecord], case_id: str) -> CaseRecord:
    for c in cases:
        if c.case_id == case_id:
            return c
    raise SystemExit(f"数据集中不存在 case_id={case_id}")


def _judge(args):
    if getattr(args, "judge_fake", False):
        return CallableJudge(point_fn=lambda q, p, f, a: "unknown")
    return LLMJudge()


def _gate_review(cases, allow_draft: bool) -> None:
    """正式评分默认要求每题 approved；未审核题不得进入正式评分（复审问题 1）。"""
    drafts = [c.case_id for c in cases if c.review_status != "approved"]
    if drafts and not allow_draft:
        raise SystemExit(
            f"拒绝评分：{len(drafts)} 题未 approved（正式评分需人工/独立审核）：{drafts[:8]}"
            "…；校准/离线可用 --allow-draft，并在报告中标注结果非正式。")


def cmd_validate(args) -> int:
    cases = load_dataset(args.cases)
    errors: list[str] = []
    seen: set[str] = set()
    approved = 0
    blocked = 0
    for c in cases:
        if c.case_id in seen:
            errors.append(f"重复 case_id: {c.case_id}")
        seen.add(c.case_id)
        if c.review_status == "approved":
            approved += 1
            if not c.sources:
                errors.append(f"{c.case_id}: approved 但无 sources")
            if not c.scoring_points:
                errors.append(f"{c.case_id}: approved 但无 scoring_points")
        if c.blocked:
            blocked += 1
            if not c.blocked_reason:
                errors.append(f"{c.case_id}: blocked 但未记录 blocked_reason")
        if c.question_type == "forecast" and not c.as_of:
            errors.append(f"{c.case_id}: forecast 题必须指定 as_of")
    if args.require_approved:
        # 不只是"至少有一道 approved"：本次校验范围内的可评分题必须全部 approved
        not_approved = [c.case_id for c in cases
                        if c.review_status != "approved" and not c.blocked]
        if not_approved:
            errors.append(f"{len(not_approved)} 题未 approved，不能进入正式评分：{not_approved}")
    print(f"cases={len(cases)} approved={approved} blocked={blocked}")
    by_type: dict[str, int] = {}
    by_split: dict[str, int] = {}
    for c in cases:
        by_type[c.question_type] = by_type.get(c.question_type, 0) + 1
        by_split[c.split] = by_split.get(c.split, 0) + 1
    print("题型分布:", by_type)
    print("开发/留出:", by_split)
    if errors:
        print("校验失败：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("校验通过。")
    return 0


def cmd_verify_sources(args) -> int:
    """核验来源：每个数值断言应能在该题引用的某个来源页面上找到（千美元形态）。

    不是完整审计：只检查"数值是否出现在引用页"，不代替人工核对行列与期间。
    """
    from eval.financial_e2e.pageindex import thousands
    cases = load_dataset(args.cases)
    reported_miss: list[str] = []
    derived = 0
    reported = 0
    for c in cases:
        for a in c.numeric_assertions:
            if _is_derived(a.metric, a.formula):
                derived += 1               # 派生值（增速/比率/天数）由已披露项计算，不要求逐字命中
                continue
            reported += 1
            if not any(_source_contains(s, thousands(a.value)) for s in c.sources):
                reported_miss.append(
                    f"{c.case_id}: {a.metric} {a.period} 值 {a.value} 未在任何来源页命中")
    print(f"已披露项 {reported} 条命中 {reported - len(reported_miss)} 条；派生值 {derived} 条（由已披露项计算）")
    for p in reported_miss:
        print(f"  - {p}")
    if reported_miss and args.strict:
        return 1
    return 0


_DERIVED_SUFFIXES = ("_pct", "_delta", "_growth", "_days", "_ratio", "_margin", "margin")


def _is_derived(metric: str, formula: str | None = None) -> bool:
    """增速/比率/天数/差额/由累计推出的单季等派生值不需要在公告原文逐字出现。"""
    return (metric.endswith(_DERIVED_SUFFIXES) or metric == "operating_expense"
            or metric.endswith("expense") or bool(formula))


def _source_contains(source, needle: str) -> bool:
    """在 SourceRef 指向的 PDF 页面上查找 needle（按 URL→本地文件名映射）。"""
    from eval.financial_e2e.pageindex import find_pages, page_texts
    from eval.financial_e2e.sources import load_manifest
    man = load_manifest()
    url_to_file = {d["url"]: d["file"] for d in man.values()}
    fname = url_to_file.get(source.url)
    if not fname:
        return False
    if source.page:
        pages = page_texts(str(Path("data/raw/lenovo") / fname))
        if 1 <= source.page <= len(pages):
            return needle in pages[source.page - 1]
    return bool(find_pages(fname, needle))


def cmd_generate_points(args) -> int:
    cases = load_dataset(args.cases)
    keep = set(args.ids.split(",")) if args.ids else None
    if getattr(args, "judge_fake", False):
        print("--judge-fake：跳过真实生成（离线自检），不写入数据集。", file=sys.stderr)
        return 0
    client = LLMJudge().client
    updated = 0
    out_cases: list[CaseRecord] = []
    for c in cases:
        if c.review_status == "approved":
            out_cases.append(c)  # 已批准题不覆盖
            continue
        if keep and c.case_id not in keep:
            out_cases.append(c)
            continue
        points, prov = generate_points(c, client)
        out_cases.append(apply_candidate(c, points, prov))
        updated += 1
        print(f"{c.case_id}: 生成 {len(points)} 个候选点（draft）")
    if args.write:
        save_dataset(out_cases, args.cases)
        print(f"已写入 {args.cases}（{updated} 题更新为 draft）")
    else:
        print(f"（--write 未指定，仅预览；{updated} 题将更新）")
    return 0


def _load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _score_one(case: CaseRecord, run_dir: Path, judge, results_jsonl: Path,
               report_dir: Path, dataset_path: Path, formal: bool = True) -> dict:
    art = load_run_artifact(run_dir, case.case_id)
    answer_text = art.final_answer if art.final_status == "answered" else None
    grading_text = answer_text  # 只有最终提交答案才进入正式评分（PRD §6.1）
    result = grade_case(case, grading_text or "", judge, run_id=art.run_id)
    from eval.runner import _git_commit
    rec = {"case_id": case.case_id, "run_id": art.run_id,
           "final_status": art.final_status, "final_answer": answer_text,
           "dataset": str(dataset_path), "dataset_revision": case.case_revision,
           "code_commit": _git_commit(), "grader_version": GRADER_VERSION, "formal": formal,
           "judge": {"name": getattr(judge, "name", "unknown"),
                     "model": getattr(judge, "model", None)},
           "result": {
               "points": [g.model_dump() for g in result["points"]],
               "whole": result["whole"].model_dump(),
               "numeric": result["numeric"],
               "forecast": result["forecast"],
               "aggregate": result["aggregate"],
           }}
    report_dir.mkdir(parents=True, exist_ok=True)
    write_case_report(report_dir, case, result, art.run_id, art.final_status, answer_text)
    with results_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def cmd_score_run(args) -> int:
    cases = load_dataset(args.cases)
    case = _find_case(cases, args.case_id)
    _gate_review([case], args.allow_draft)
    judge = _judge(args)
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        raise SystemExit(f"运行包目录不存在: {run_dir}")
    results_jsonl = Path(args.results) if args.results else Path(args.out) / "results.jsonl"
    results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    rec = _score_one(case, run_dir, judge, results_jsonl,
                     Path(args.out) / "cases", Path(args.cases),
                     formal=not args.allow_draft)
    a = rec["result"]["aggregate"]
    print(f"{case.case_id}: status={rec['final_status']} delivered={a['delivered']} "
          f"必答命中={a['required_hit_rate']:.2f} "
          f"missing={a['required_missing']} contradicted={a['required_contradicted']} "
          f"unknown={a['required_unknown']}")
    print(f"逐题报告：{Path(args.out) / 'cases' / (case.case_id + '.md')}")
    print(f"结果追加：{results_jsonl}")
    return 0


def _run_agent_cli(case: CaseRecord, out_root: Path, args) -> Path | None:
    cmd = [sys.executable, "-m", "agent.cli", "--question", case.question,
           "--agent-mode", args.agent_mode, "--query-backend", args.query_backend,
           "--out-root", str(out_root)]
    if args.config:
        cmd += ["--config", args.config]
    if case.as_of:
        cmd += ["--as-of", case.as_of]
    before = {p.name for p in out_root.iterdir()} if out_root.exists() else set()
    print(f"[run-dataset] {case.case_id}: {' '.join(cmd[:6])} ...")
    proc = subprocess.run(cmd)
    after = {p.name for p in out_root.iterdir()} if out_root.exists() else set()
    new = sorted(after - before)
    if not new:
        print(f"[run-dataset] {case.case_id}: 未产生新运行包（退出码 {proc.returncode}）",
              file=sys.stderr)
        return None
    return out_root / new[-1]


def cmd_run_dataset(args) -> int:
    cases = load_dataset(args.cases)
    if args.ids:
        keep = set(args.ids.split(","))
        cases = [c for c in cases if c.case_id in keep]
    if args.max:
        cases = cases[:args.max]
    # 正式评分默认要求 approved；blocked 题保留在结果（记 blocked，不运行 Agent）
    _gate_review([c for c in cases if not c.blocked], args.allow_draft)
    if args.dry_run:
        for c in cases:
            tag = "BLOCKED" if c.blocked else c.split
            print(f"[dry-run] {tag} {c.case_id} as_of={c.as_of} q={c.question[:40]}...")
        return 0
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    judge = _judge(args)
    results_jsonl = Path(args.out) / "results.jsonl"
    if results_jsonl.exists() and args.reset:
        results_jsonl.unlink()
    formal = not args.allow_draft
    records = []
    for c in cases:
        if c.blocked:
            rec = _record_unscored(c, "blocked", judge, results_jsonl, Path(args.cases),
                                   reason=c.blocked_reason, formal=formal)
            records.append(rec)
            print(f"{c.case_id}: blocked（{c.blocked_reason}），记入分母但不运行")
            continue
        run_dir = _run_agent_cli(c, out_root, args)
        if run_dir is None:
            rec = _record_unscored(c, "run_missing", judge, results_jsonl, Path(args.cases),
                                   reason="Agent 未产生运行包", formal=formal)
            records.append(rec)
            print(f"{c.case_id}: 运行包缺失，记入分母（不伪造事件）", file=sys.stderr)
            continue
        rec = _score_one(c, run_dir, judge, results_jsonl, Path(args.out) / "cases",
                         Path(args.cases), formal=formal)
        records.append(rec)
    scored = sum(1 for r in records if r["final_status"] not in ("blocked", "run_missing"))
    print(f"共 {len(records)} 题入分母（已评分 {scored}）；结果：{results_jsonl}")
    return 0


def _record_unscored(case, status: str, judge, results_jsonl: Path, dataset_path: Path,
                     reason: str | None, formal: bool = True) -> dict:
    """未运行/被阻塞的题也写入结果，保证汇总分母完整（复审问题 3）。"""
    result = grade_case(case, "", judge, run_id="")
    from eval.runner import _git_commit
    rec = {"case_id": case.case_id, "run_id": "", "final_status": status,
           "final_answer": None, "dataset": str(dataset_path),
           "dataset_revision": case.case_revision, "code_commit": _git_commit(),
           "grader_version": GRADER_VERSION, "formal": formal,
           "judge": {"name": getattr(judge, "name", "unknown"),
                     "model": getattr(judge, "model", None)},
           "unscored_reason": reason,
           "result": {"points": [g.model_dump() for g in result["points"]],
                      "whole": result["whole"].model_dump(),
                      "numeric": result["numeric"], "forecast": result["forecast"],
                      "aggregate": result["aggregate"]}}
    results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with results_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def cmd_report(args) -> int:
    all_cases = load_dataset(args.cases)
    if args.ids:
        keep = set(args.ids.split(","))
        all_cases = [c for c in all_cases if c.case_id in keep]
    by_id = {c.case_id: c for c in all_cases}
    results = {r["case_id"]: r for r in _load_results(Path(args.results))}
    items = []
    for case in all_cases:                       # 遍历数据集，缺结果的题也计入分母
        rec = results.get(case.case_id)
        if rec is None:
            items.append({"case": case, "result": None, "run_id": "",
                          "final_status": "not_run"})
        else:
            items.append({"case": case, "result": rec["result"],
                          "run_id": rec.get("run_id", ""),
                          "final_status": rec.get("final_status", "unknown"),
                          "formal": rec.get("formal")})
    run_ids = sorted({r["run_id"] for r in results.values() if r.get("run_id")})
    judges = {(r.get("judge") or {}).get("name") for r in results.values()}
    judges.discard(None)
    judge_name = judges.pop() if len(judges) == 1 else ("mixed/部分缺记录" if judges else "n/a")
    judge_model = args.judge_model
    if judge_model is None:
        models = {(r.get("judge") or {}).get("model") for r in results.values()}
        models.discard(None)
        judge_model = models.pop() if len(models) == 1 else None
    md = summary_report_md(items, args.dataset_name, judge_name, judge_model, run_ids)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"汇总报告：{out}（{len(items)} 题，其中未运行 {sum(1 for i in items if i['final_status']=='not_run')} 题）")
    return 0


def cmd_review(args) -> int:
    """人工/独立审核者操作；不自动批准自己生成的 gold。"""
    cases = load_dataset(args.cases)
    case = _find_case(cases, args.case_id)
    if args.status == "approved" and not args.reviewer:
        raise SystemExit("approved 需要 --reviewer（人工/独立审核者），不得自动批准")
    updated = case.model_copy(update={"review_status": args.status})
    out = [updated if c.case_id == args.case_id else c for c in cases]
    save_dataset(out, args.cases)
    log = Path(args.cases).parent / "review_log.md"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = (f"- {ts}｜{case.case_id}｜{case.review_status}→{args.status}"
            f"｜reviewer={args.reviewer or '—'}｜note={args.note or '—'}\n")
    with log.open("a", encoding="utf-8") as f:
        f.write(line)
    print(f"{case.case_id}: review_status={args.status}（记录到 {log}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m eval.financial_e2e")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="校验数据集")
    v.add_argument("--cases", default=str(DEFAULT_CASES))
    v.add_argument("--require-approved", action="store_true")
    v.set_defaults(func=cmd_validate)

    vs = sub.add_parser("verify-sources", help="核验数值断言能在来源页命中")
    vs.add_argument("--cases", default=str(DEFAULT_CASES))
    vs.add_argument("--strict", action="store_true", help="有未命中时返回码 1")
    vs.set_defaults(func=cmd_verify_sources)

    g = sub.add_parser("generate-points", help="生成得分点候选（draft）")
    g.add_argument("--cases", default=str(DEFAULT_CASES))
    g.add_argument("--ids", default=None)
    g.add_argument("--write", action="store_true")
    g.add_argument("--judge-fake", action="store_true", help="离线自检，不写数据集")
    g.set_defaults(func=cmd_generate_points)

    s = sub.add_parser("score-run", help="对已有运行包评分")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--case-id", required=True)
    s.add_argument("--cases", default=str(DEFAULT_CASES))
    s.add_argument("--out", default="eval/reports/e2e")
    s.add_argument("--results", default=None)
    s.add_argument("--judge-fake", action="store_true")
    s.add_argument("--allow-draft", action="store_true",
                   help="允许对未 approved 题评分（校准/离线；结果非正式）")
    s.set_defaults(func=cmd_score_run)

    r = sub.add_parser("run-dataset", help="按固定数据集调用当前 Agent 并评分")
    r.add_argument("--cases", default=str(DEFAULT_CASES))
    r.add_argument("--out-root", default="runs")
    r.add_argument("--out", default="eval/reports/e2e")
    r.add_argument("--ids", default=None)
    r.add_argument("--max", type=int, default=None)
    r.add_argument("--agent-mode", default="multi_agent")
    r.add_argument("--query-backend", default="text2sql")
    r.add_argument("--config", default=None)
    r.add_argument("--reset", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--judge-fake", action="store_true")
    r.add_argument("--allow-draft", action="store_true",
                   help="允许对未 approved 题运行评分（校准/离线；结果非正式）")
    r.set_defaults(func=cmd_run_dataset)

    p = sub.add_parser("report", help="生成汇总报告")
    p.add_argument("--results", required=True)
    p.add_argument("--cases", default=str(DEFAULT_CASES))
    p.add_argument("--ids", default=None, help="只汇总这些题（缺结果的题仍计入分母）")
    p.add_argument("--out", default="eval/reports/e2e_summary.md")
    p.add_argument("--dataset-name", default="financial_real_v1")
    p.add_argument("--judge-model", default=None)
    p.set_defaults(func=cmd_report)

    rv = sub.add_parser("review", help="人工/独立审核（非自动批准）")
    rv.add_argument("--cases", default=str(DEFAULT_CASES))
    rv.add_argument("--case-id", required=True)
    rv.add_argument("--status", choices=["draft", "approved", "blocked"], required=True)
    rv.add_argument("--reviewer", default=None)
    rv.add_argument("--note", default=None)
    rv.set_defaults(func=cmd_review)
    return ap


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

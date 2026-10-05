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
import time
from datetime import date, datetime
from pathlib import Path

from agent.llm import LLMClient
from agent.loop import make_run_ctx, run as loop_run
from agent.tools import data as tool_data
from agent.tools.base import DESCRIPTIONS
from agent.v1 import Paced
from eval.runner import RateLimiter, _git_commit

DB_PATH = Path("data/finsights.duckdb")
# 第一阶段（PRD 3.2）：披露正文/附注检索不对被测路径暴露——落实到工具暴露层，非仅 prompt
DISABLED_TOOLS = ["get_filing_notes", "search_disclosure"]
COMPANY_ALIASES = [("联想", "Lenovo"), ("lenovo", "Lenovo"), ("惠普", "HP"), ("hp", "HP"),
                   ("戴尔", "Dell"), ("dell", "Dell")]
COMPANIES_ALIASES_VALUES = ["Lenovo", "HP", "Dell"]
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
        "parsed_scope": getattr(ctx, "_parsed_scope", None),
        "initial_packet_rule": getattr(ctx, "_initial_rule", None),
        "tools_disabled": DISABLED_TOOLS,
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
               "created_step": res.created_step, "digest": res.digest[:200],
               "initial_packet": res.created_step == 0}
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
        "tools_disabled": DISABLED_TOOLS,
        "dev_model_note": "开发执行模型与项目运行模型（client.model）是不同配置；详见交付说明",
        "usage": {k: info.get(k) for k in ("steps", "prompt_tokens", "completion_tokens",
                                           "latency_s", "sql_errors", "refuse_reason")},
        "verified": out.verified, "status": out.answer.status,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                           encoding="utf-8")

    ans = [f"# {question}\n", f"- status: {out.answer.status}\n",
           f"- 核验: {'通过（仅指现有数字/引用规则通过，不代表经营因果结论被证明）' if out.verified else '未通过'}\n",
           f"- run_id: {run_id}  输出目录: {out_dir}\n\n", out.answer.answer_md, "\n"]
    if out.answer.claims:
        ans.append("\n## 引用\n")
        for c in out.answer.claims:
            ans.append(f"- {c.text}  [{c.value} {c.unit} → {c.ref}]\n")
    (out_dir / "answer.md").write_text("".join(ans), encoding="utf-8")

    # ---- review.md（PRD 3.4：人工可读审阅摘要，三视角待人工判断栏默认"未评审"）----
    _initial_rule = (cfg_safe or {}).get("initial_packet", {}) if cfg_safe else {}
    timeline = [e for e in info["trace"] if e.get("type") in ("assistant", "tool_result")]
    rev = [f"# 审阅摘要（run={run_id}）", "",
           "## 范围与输入", "",
           f"- 原始 query：{question}",
           f"- as_of：{as_of.isoformat() if as_of else '未提供（当前库，非历史回测）'}",
           f"- 初始数据包规则：metrics={_initial_rule.get('metrics')}，quarters_back={_initial_rule.get('quarters_back')}"
           f"（详细规则与解析范围见 question.json）",
           f"- 数据库：{db_path}（sha256_12={meta['db_sha256_12']}，data_version={meta['data_version']}）",
           f"- 披露检索：本路径禁用（工具暴露层排除 {DISABLED_TOOLS}）", "",
           "## 最终答案位置", "",
           f"- answer.md（status={out.answer.status}，verified={out.verified}）；"
           f"claims {len(out.answer.claims)} 条 → results.json 按 rid 关联", "",
           "## 工具执行时间线", "", "| # | 工具 | step | 摘要 |", "|---|---|---|---|"]
    for i, e in enumerate(timeline, 1):
        kind = e.get("type")
        if kind == "assistant":
            calls = ",".join(tc.get("name", "?") for tc in e.get("tool_calls") or []) or "（文本）"
            rev.append(f"| {i} | model | {e.get('step', '')} | {calls} |")
        elif kind == "tool_result":
            rev.append(f"| {i} | {e.get('tool')} | {e.get('step', '')} | "
                       f"{'ok' if e.get('ok') else 'error'} rid={e.get('result_id', '')} |")
    rev += ["", "## 错误与终止", "",
            f"- 终止状态：{out.answer.status}；verified={out.verified}；"
            f"refuse_reason={info.get('refuse_reason')}",
            f"- SQL 报错：{info.get('sql_errors')} 次；LLM 重试事件见 trace.jsonl（type=llm_retry）",
            "- 运行中出错也保存已获得的轨迹与状态（本包即出错时的产物）" if out.answer.status == "error" else "", "",
            "## 资源统计", "",
            f"- steps={info.get('steps')}，prompt_tokens={info.get('prompt_tokens')}，"
            f"completion_tokens={info.get('completion_tokens')}，latency_s={info.get('latency_s')}s，"
            f"cost_usd={'未知（未获得计费信息）' if not info.get('cost_usd') else info.get('cost_usd')}", "",
            "## 三视角待人工判断（默认未评审，不自动标 PASS）", "",
            "| 视角 | 待人工回答 | 评审 |", "|---|---|---|",
            "| 端到端质量 | 是否回答了问题？数字/分析/结论是否有依据？是否超出数据支持范围？ | 未评审 |",
            "| 取数准确性 | 公司/期间/科目/单位/版本是否正确？是否遗漏必要条件？ | 未评审 |",
            "| 运行可靠性 | 是否完成或诚实终止？出错后如何恢复？预算花在哪里？ | 未评审 |", "",
            "## 已知边界", "",
            "- facts_detail 中合成明细已标记 synthetic=TRUE；无真实业务分部数据时 Agent 应说明缺口，"
            "合成明细不代表真实经营事实。",
            "- 数字核验通过仅指现有数字/引用规则通过，不代表经营归因正确。"]
    (out_dir / "review.md").write_text("\n".join(rev) + "\n", encoding="utf-8")
    return meta


def parse_scope(question: str) -> dict:
    """PRD 3.2：系统映射用户明确给出的公司/期间；解析不到的字段置 None（由 agent 澄清，不擅自定）。"""
    company = next((cid for pat, cid in COMPANY_ALIASES if pat in question.lower()), None)
    period = None
    m = re.search(r"FY(\d{2,4})Q(\d)", question, re.I)
    if m:
        fy = int(m.group(1))
        period = f"FY{fy if fy > 100 else fy + 2000 - 2000}Q{m.group(2)}" if fy > 100 else f"FY{fy}Q{m.group(2)}"
        if fy <= 100:
            period = f"FY{fy}Q{m.group(2)}"
    else:
        m = re.search(r"(\d{4})\s*年?[第]?\s*([一二三四1-4])\s*季度", question)
        if m:
            qmap = {"一": 1, "二": 2, "三": 3, "四": 4}
            qq = qmap[m.group(2)] if m.group(2) in qmap else int(m.group(2))
            period = f"{m.group(1)}Q{qq}"
    return {"company": company, "period": period,
            "raw": {"company_in_query": company is not None, "period_in_query": period is not None}}


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
    ap.add_argument("--max-steps", type=int, default=None,
                    help="覆盖 config 的 agent.max_steps；预算会记录进 run_meta.json")
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
    import yaml as _yaml
    cfg_full = _yaml.safe_load(open(args.config, encoding="utf-8"))
    rule = cfg_full.get("initial_packet") or {}
    parsed = parse_scope(args.question)
    # 第一阶段（PRD 3.2）：披露正文/附注检索在工具暴露层禁用
    enabled_tools = sorted(set(DESCRIPTIONS) - set(DISABLED_TOOLS))
    ctx = make_run_ctx(str(db_path), Paced(client, limiter), run_id=f"cli-{run_id}",
                       as_of=as_of, config_path=args.config, enabled_tools=enabled_tools,
                       max_steps=args.max_steps)

    # ---- 初始数据包（固定规则，step=0，先于 Agent 自主取数）----
    pk_metrics = list(rule.get("metrics") or [])
    pk_n = int(rule.get("quarters_back") or 8)
    pk_companies = [parsed["company"]] if parsed["company"] else list(COMPANIES_ALIASES_VALUES)
    packet_args = {"metrics": pk_metrics, "companies": pk_companies, "last_n": pk_n}
    if parsed["period"]:
        fy = int(parsed["period"].split("Q")[0].replace("FY", ""))
        qq = int(parsed["period"].split("Q")[1])
        packet_args["period_to"] = f"FY{fy}Q{qq}" if parsed["period"].startswith("FY") else parsed["period"]
    ctx._parsed_scope = parsed
    ctx._initial_rule = rule
    packet = tool_data.execute("query_metric", packet_args, ctx.tool_ctx, step=0)
    if not packet.ok:
        print(f"初始数据包执行失败：{packet.text}", file=sys.stderr)
    else:  # 初始包的执行事件也进 trace（PRD 3.4：审阅证据不只在 results.json）
        ctx.trace.event(type="tool_result", step=0, tool="query_metric",
                        ok=True, result_id=packet.rid, initial_packet=True)

    out = None
    try:
        out = loop_run(args.question, ctx)
    except Exception as e:  # PRD 3.4：运行中出错也保存已获得的轨迹和状态
        info_err = {"trace": list(ctx.trace.events), "steps": ctx.stats.get("steps", 0),
                    "prompt_tokens": ctx.stats.get("prompt_tokens", 0),
                    "completion_tokens": ctx.stats.get("completion_tokens", 0),
                    "latency_s": round(time.time() - ctx.stats.get("t_start", 0), 2),
                    "sql_errors": 0, "refuse_reason": f"运行异常: {type(e).__name__}: {e}"}
        from eval.schema import Answer
        out_err = type("Out", (), {"answer": Answer(answer_md=f"运行异常终止：{e}", status="refuse"),
                                   "verified": False, "stats": ctx.stats})()
        _export_package(Path(args.out_root) / f"cli-{run_id}", args.question, as_of, db_path,
                        client, args.config, ctx, out_err, info_err, run_id)
        print(f"运行异常（已保存已获得的轨迹）：{e}", file=sys.stderr)
        return 4

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

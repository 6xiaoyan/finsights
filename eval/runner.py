"""P3.4：评测 runner（plan 5.4）。

用法：`python -m eval.runner --agent v0 --datasets l1,l2 --trials 3 --concurrency 4`
- 每题跑 N 次；每次运行的 trace 存 `runs/<run_id>/<qid>_<trial>.jsonl`（每条消息/工具调用一行）。
- 记录：是否正确、步数、工具调用次数、SQL 报错次数、token 用量、延迟、成本（list 价，推广期实际为 0）。
- 输出 `eval/reports/<run_id>.md`（每类均值 ± 标准差 + 过程指标），更新 `eval/reports/leaderboard.md`。
- 限速：agnes 免费档约 20 RPM，客户端令牌桶默认 18。
- 真实 LLM 运行属于 live 检查（verify 0.5）：离线测试用注入的 mock agent 调用 run_suite()。
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from openai import RateLimitError

from eval.graders import get_grader
from eval.schema import Answer, Question

RUNS = Path("runs")
REPORTS = Path("eval/reports")
DATASETS = Path("eval/datasets")

AgentFn = Callable[[str], tuple[Answer, dict]]  # (question_text) -> (Answer, info)


def load_questions(datasets: str) -> list[Question]:
    out: list[Question] = []
    for cat in datasets.split(","):
        path = DATASETS / f"{cat.strip().lower()}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(Question.model_validate_json(line))
    return out


class RateLimiter:
    """令牌桶：max_rpm 每分钟请求数，线程安全。"""

    def __init__(self, max_rpm: float):
        self.interval = 60.0 / max_rpm
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._next = max(self._next, now)
            wake = self._next
            self._next += self.interval
        time.sleep(max(0.0, wake - now))


def make_agent(name: str, limiter: RateLimiter) -> AgentFn:
    if name == "v0":
        from agent.llm import LLMClient
        from agent.v0 import run as run_v0
        client = LLMClient()
        return lambda q: run_v0(q, llm=client, limiter=limiter)
    if name == "v1":
        from agent.llm import LLMClient
        from agent.v1 import run as run_v1
        client = LLMClient()
        return lambda q: run_v1(q, llm=client, limiter=limiter)
    raise KeyError(f"未知 agent: {name}（可用: v0, v1；更后面的版本在对应阶段加入）")


RATE_RETRY_MAX = 3        # 429 耗尽 llm 内部重试后，整个 trial 重做的最大次数
RATE_RETRY_WAIT_S = 180   # 重做前等待秒数 × 次数（免费档配额按分钟窗口恢复）


def _call_with_rate_retry(agent_fn: AgentFn, question: str) -> tuple[Answer, dict]:
    """429 属于配额窗口问题，等待后整 trial 重做；其他异常原样抛出（由 one() 记为 error）。"""
    for attempt in range(RATE_RETRY_MAX + 1):
        try:
            return agent_fn(question)
        except RateLimitError:
            if attempt == RATE_RETRY_MAX:
                raise
            time.sleep(RATE_RETRY_WAIT_S * (attempt + 1))
    raise AssertionError("unreachable")


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _pricing() -> tuple[float, float]:
    import yaml
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p = (cfg.get("llm") or {}).get("pricing_usd_per_mtok") or {}
    return float(p.get("input", 0.0)), float(p.get("output", 0.0))


def run_suite(questions: list[Question], agent_fn: AgentFn, trials: int, run_id: str,
              agent_name: str, runs_root: Path = RUNS, concurrency: int = 1,
              reports_root: Path | None = None) -> dict:
    """执行 trials × questions。agent_fn 由调用方给出（live 用 make_agent，离线测试注入 mock）。"""
    reports_root = Path(reports_root) if reports_root else REPORTS
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pin, pout = _pricing()
    records: list[dict] = []
    lock = threading.Lock()

    def one(q: Question, trial: int) -> None:
        t0 = time.time()
        rec: dict
        answer: Answer | None = None
        info: dict = {}
        try:
            answer, info = _call_with_rate_retry(agent_fn, q.question)
            grade = get_grader(q.category)(q, answer)
            latency = info.get("latency_s", round(time.time() - t0, 2))
            cost = (info.get("prompt_tokens", 0) * pin + info.get("completion_tokens", 0) * pout) / 1e6
            rec = {"qid": q.id, "category": q.category, "trial": trial,
                   "correct": bool(grade["correct"]), "status": answer.status,
                   "steps": info.get("steps", 0), "tool_calls": info.get("tool_calls", 0),
                   "sql_errors": info.get("sql_errors", 0),
                   "prompt_tokens": info.get("prompt_tokens", 0),
                   "completion_tokens": info.get("completion_tokens", 0),
                   "cost_usd": round(cost, 6), "latency_s": latency,
                   "detail": grade["detail"], "answer_md": answer.answer_md[:500]}
        except Exception as e:  # 单次 trial 失败（如限速重试耗尽）不拖垮整个 run
            rec = {"qid": q.id, "category": q.category, "trial": trial,
                   "correct": False, "status": "error", "steps": 0, "tool_calls": 0,
                   "sql_errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                   "cost_usd": 0.0, "latency_s": round(time.time() - t0, 2),
                   "detail": f"运行异常: {type(e).__name__}: {e}", "answer_md": ""}
            info = {"trace": [{"type": "error", "error": str(e)[:500]}]}
        trace = [json.dumps({"qid": q.id, "trial": trial, "type": "meta", "agent": agent_name,
                             "question": q.question, "ts": datetime.now().isoformat(timespec="seconds")},
                            ensure_ascii=False)]
        trace += [json.dumps({"qid": q.id, "trial": trial, **ev}, ensure_ascii=False)
                  for ev in info.get("trace", [])]
        trace.append(json.dumps({"qid": q.id, "trial": trial, "type": "result", **rec},
                                ensure_ascii=False))
        (run_dir / f"{q.id}_{trial}.jsonl").write_text("\n".join(trace) + "\n", encoding="utf-8")
        with lock:
            records.append(rec)

    jobs = [(q, t) for t in range(1, trials + 1) for q in questions]
    if concurrency <= 1:
        for q, t in jobs:
            one(q, t)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(lambda j: one(*j), jobs))

    records.sort(key=lambda r: (r["qid"], r["trial"]))
    summary = {"run_id": run_id, "agent": agent_name, "commit": _git_commit(),
               "created": datetime.now().isoformat(timespec="seconds"),
               "pricing_usd_per_mtok": {"input": pin, "output": pout},
               "trials": trials, "records": records}
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    write_report(summary, reports_root)
    update_leaderboard(summary, reports_root)
    return summary


def category_stats(summary: dict) -> dict[str, dict]:
    """按类别：每次 trial 的正确率 → 均值 ± 样本标准差；过程指标均值。"""
    out: dict[str, dict] = {}
    recs = summary["records"]
    for cat in sorted({r["category"] for r in recs}):
        per_trial: dict[int, list[bool]] = {}
        for r in recs:
            if r["category"] == cat:
                per_trial.setdefault(r["trial"], []).append(r["correct"])
        accs = [sum(v) / len(v) for v in per_trial.values()]
        rows = [r for r in recs if r["category"] == cat]
        out[cat] = {
            "n_questions": len(rows) / max(1, summary["trials"]),
            "acc_mean": statistics.mean(accs), "acc_std": statistics.stdev(accs) if len(accs) > 1 else 0.0,
            "steps": statistics.mean(r["steps"] for r in rows),
            "tool_calls": statistics.mean(r["tool_calls"] for r in rows),
            "sql_errors": statistics.mean(r["sql_errors"] for r in rows),
            "tokens": statistics.mean(r["prompt_tokens"] + r["completion_tokens"] for r in rows),
            "latency_s": statistics.mean(r["latency_s"] for r in rows),
            "cost_usd": sum(r["cost_usd"] for r in rows),
        }
    return out


def write_report(summary: dict, reports_root: Path = REPORTS) -> Path:
    st = category_stats(summary)
    lines = [f"# 评测报告 {summary['run_id']}（agent={summary['agent']}，commit={summary['commit']}，"
             f"trials={summary['trials']}）", "",
             "成本按 list 价计算（推广期实际为 $0）。", "",
             "| 类别 | 题数 | 准确率（均值±标准差） | 平均步数 | 工具调用 | SQL 报错 | 平均 token | 平均延迟(s) | 总成本($) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cat, s in st.items():
        lines.append(f"| {cat} | {s['n_questions']:.0f} | {s['acc_mean']:.2f} ± {s['acc_std']:.2f} "
                     f"| {s['steps']:.1f} | {s['tool_calls']:.1f} | {s['sql_errors']:.1f} "
                     f"| {s['tokens']:.0f} | {s['latency_s']:.1f} | {s['cost_usd']:.4f} |")
    wrong = [r for r in summary["records"] if not r["correct"]]
    lines += ["", f"## 失败明细（{len(wrong)} 条 trial 记录）"]
    for r in wrong:
        lines.append(f"- {r['qid']} t{r['trial']}: {r['detail']}｜答案摘录: {r['answer_md'][:80]}")
    reports_root.mkdir(parents=True, exist_ok=True)
    path = reports_root / f"{summary['run_id']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def update_leaderboard(summary: dict, reports_root: Path = REPORTS) -> None:
    reports_root.mkdir(parents=True, exist_ok=True)
    path = reports_root / "leaderboard.md"
    st = category_stats(summary)
    row = [f"| {summary['agent']} | " + " | ".join(
        f"{s['acc_mean']:.2f} ± {s['acc_std']:.2f}" for s in st.values()) +
        f" | {summary['run_id']} | {summary['commit']} |"]
    if not path.exists():
        cats = " | ".join(st.keys())
        sep = "|---" * (len(st) + 3) + "|"
        path.write_text(f"# Leaderboard\n\n| agent | {cats} | run_id | commit |\n{sep}\n",
                        encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.startswith(f"| {summary['agent']} |")]
    lines += row
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _default_rpm() -> float:
    import yaml
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    return float((cfg.get("llm") or {}).get("rate_limit_rpm", 18))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--datasets", default="l1,l2")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-rpm", type=float, default=None)
    args = ap.parse_args(argv[1:])

    questions = load_questions(args.datasets)
    limiter = RateLimiter(args.max_rpm or _default_rpm())
    agent_fn = make_agent(args.agent, limiter)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = run_suite(questions, agent_fn, args.trials, run_id, args.agent,
                        concurrency=args.concurrency)
    print(f"run_id={run_id} 记录={len(summary['records'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))

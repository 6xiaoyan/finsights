"""看板实验索引（dashboard_design.md §6.1）：扫描 runs/ 生成实验清单与逐 query 行。

- cli-* 目录 = 单题实验（source="cli"）；含 summary.json 的目录 = 评测实验（source=数据集）。
- 计划清单：评测实验从 eval/datasets/*.jsonl 的题目 id 生成；单题实验计划数=1；
  旧实验没有计划清单时 planned=None（显示"计划总数未知"）。
- 只读小型元数据与 trace 事件序列；选中后才加载完整 trace/results（app 层负责）。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dashboard.rules import classify, summarize

RUNS = Path("runs")
INDEX = RUNS / "index.json"
DATASETS = Path("eval/datasets")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _trace_events(run_dir: Path, run_id: str = None) -> list[dict]:
    """兼容两种布局：单题包 trace.jsonl；评测 run 的 <qid>_<trial>.jsonl。"""
    events: list[dict] = []
    p = run_dir / "trace.jsonl"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(ln))
            except Exception:
                pass
        return events
    for f in sorted(run_dir.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(ln))
            except Exception:
                pass
    return events


def _planned_ids(source: str) -> list[str] | None:
    ids: list[str] = []
    for cat in source:
        f = DATASETS / f"{cat.lower()}.jsonl"
        if not f.exists():
            return None
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                ids.append(json.loads(ln).get("id"))
    return ids or None


def _cli_experiment(d: Path) -> dict:
    qj = _read_json(d / "question.json")
    meta = _read_json(d / "run_meta.json")
    events = _trace_events(d)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    files = {name: (d / name).exists() for name in
             ("question.json", "answer.md", "trace.jsonl", "results.json", "run_meta.json")}
    record = None
    status = result.get("status") if result else ("refuse" if meta and meta.get("status") == "refuse" else None)
    if status:
        record = {"status": status, "correct": result.get("correct")} if result else None
    qid = d.name.replace("cli-", "Q")
    flags = classify(record, events, meta, files)
    label, is_red = summarize(flags)
    if not result and status is None:
        label, is_red = "中性：未完成/记录不完整", False
    return {
        "experiment_id": d.name, "name": qj.get("question", d.name)[:40] if qj else d.name,
        "source": "cli 单题", "created": (qj or {}).get("run_id"),
        "commit": (meta or {}).get("commit"), "planned_total": 1,
        "query_id": qid, "trial": None, "question": (qj or {}).get("question", ""),
        "package_path": str(d), "status": status or "未完成",
        "verified": (result or {}).get("verified", (meta or {}).get("verified")),
        "latency_s": ((meta or {}).get("usage") or {}).get("latency_s"),
        "prompt_tokens": ((meta or {}).get("usage") or {}).get("prompt_tokens"),
        "completion_tokens": ((meta or {}).get("usage") or {}).get("completion_tokens"),
        "flags": flags, "flag_label": label, "is_red": is_red,
    }


def _eval_experiment(d: Path) -> dict | None:
    summary = _read_json(d / "summary.json")
    if summary is None:
        return None
    records = summary.get("records") or []
    by_file: dict[str, dict] = {}
    for f in sorted(d.glob("*_*.jsonl")):
        ev = _read_json_lines(f)
        if ev:
            by_file[f.stem] = ev
    source = ",".join(sorted({r.get("qid", "").split("_")[0] for r in records})) or "unknown"
    planned = _planned_ids(source)
    rows = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[r["qid"]].append(r)
    events_by_key: dict[str, list[dict]] = defaultdict(list)
    for stem, ev in by_file.items():
        events_by_key[ev[0].get("qid", stem)] = ev
    for qid, trials in sorted(grouped.items()):
        for r in trials:
            key = f"{qid}_{r['trial']}"
            ev = events_by_key.get(qid, [])
            flags = classify(r, ev, None, {})
            label, is_red = summarize(flags)
            rows.append({
                "experiment_id": d.name, "name": f"评测 {summary.get('agent')} ({source})",
                "source": f"评测({summary.get('agent')},{source})",
                "created": summary.get("created"), "commit": summary.get("commit"),
                "planned_total": len(planned) if planned is not None else None,
                "query_id": qid, "trial": r["trial"],
                "question": _question_of(qid),
                "package_path": str(d / f"{key}.jsonl"), "status": r.get("status"),
                "verified": None, "latency_s": r.get("latency_s"),
                "prompt_tokens": r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"),
                "correct": r.get("correct"), "detail": r.get("detail", ""),
                "flags": flags, "flag_label": label, "is_red": is_red,
            })
    return {"experiment_id": d.name, "name": f"评测 {summary.get('agent')} ({source})",
            "source": f"评测({summary.get('agent')},{source})",
            "created": summary.get("created"), "commit": summary.get("commit"),
            "planned_total": len(planned) if planned is not None else None,
            "rows": rows}


def _read_json_lines(f: Path) -> list[dict]:
    out = []
    for ln in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


_Q_CACHE: dict[str, str] | None = None


def _question_of(qid: str) -> str:
    """qid → 题面（从 datasets 读取，缓存）。评测行的 question 字段。"""
    global _Q_CACHE
    if _Q_CACHE is None:
        _Q_CACHE = {}
        for f in DATASETS.glob("*.jsonl"):
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    q = json.loads(ln)
                    _Q_CACHE[q["id"]] = q.get("question", "")
    return _Q_CACHE.get(qid, "")


def build_index(refresh: bool = False) -> list[dict]:
    if INDEX.exists() and not refresh:
        data = _read_json(INDEX)
        if data:
            return data["experiments"]
    experiments = []
    for d in sorted(RUNS.iterdir(), key=lambda x: x.name, reverse=True):
        if not d.is_dir():
            continue
        try:
            if d.name.startswith("cli-"):
                experiments.append(_cli_experiment(d))
            elif (d / "summary.json").exists():
                exp = _eval_experiment(d)
                if exp:
                    experiments.append(exp)
            else:
                # 旧运行/不完整包：只登记已知条目，计划总数未知
                events = _trace_events(d)
                experiments.append({
                    "experiment_id": d.name, "name": d.name, "source": "未知（无 summary）",
                    "created": None, "commit": None, "planned_total": None,
                    "query_id": d.name, "trial": None, "question": "",
                    "package_path": str(d), "status": "未知",
                    "verified": None, "latency_s": None, "prompt_tokens": None,
                    "completion_tokens": None, "flags": [], "flag_label": "计划总数未知",
                    "is_red": False, "rows": []})
        except Exception as e:
            experiments.append({"experiment_id": d.name, "name": d.name,
                                "source": "解析失败", "created": None, "commit": None,
                                "planned_total": None, "query_id": d.name, "trial": None,
                                "question": f"索引异常: {e}", "package_path": str(d),
                                "status": "未知", "verified": None, "latency_s": None,
                                "prompt_tokens": None, "completion_tokens": None,
                                "flags": [], "flag_label": "记录不完整（索引异常）",
                                "is_red": True, "rows": []})
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps({"generated": datetime.now().astimezone().isoformat(timespec="seconds"),
                                 "experiments": experiments}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return experiments


def experiment_rows(exp: dict) -> list[dict]:
    """实验的逐 query 行（cli 实验即单行；评测实验为 rows）。"""
    if "rows" in exp:
        return exp["rows"]
    return [{k: exp[k] for k in ("experiment_id", "query_id", "trial", "question",
                                 "package_path", "status", "verified", "flag_label",
                                 "is_red", "latency_s", "prompt_tokens", "completion_tokens",
                                 "flags")}]


def mark_reviewed(experiment_id: str, query_id: str, trial, perspective: dict) -> None:
    """人工审阅结论单独存放（refresh 不覆盖）：docs/evidence/review_marks.json。"""
    path = Path("docs/evidence/review_marks.json")
    data = _read_json(path) or {"marks": []}
    data["marks"] = [m for m in data["marks"]
                     if not (m["experiment_id"] == experiment_id and m["query_id"] == query_id
                             and m.get("trial") == trial)]
    data["marks"].append({"experiment_id": experiment_id, "query_id": query_id,
                          "trial": trial, **perspective,
                          "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def reviewed_marks() -> dict[str, dict]:
    path = Path("docs/evidence/review_marks.json")
    data = _read_json(path) or {"marks": []}
    return {f"{m['experiment_id']}|{m['query_id']}|{m.get('trial')}": m for m in data["marks"]}

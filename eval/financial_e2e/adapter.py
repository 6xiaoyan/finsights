"""运行包适配：把 agent/cli.py 落盘目录读成公共协议对象。

复用现有 trace.jsonl / run_meta.json / answer.md / results.json / analysis/，
不改原运行包、不重新请求被测 Agent（PRD §6.3：重评分只读原包）。

关键约定（PRD §2 / §6.1 / §9.3）：
- 只有 status=answered 才算"最终提交答案"；refuse/clarify/error 的最终答案字段为 null，
  但原始 answer.md 文本保留在 process_objects["answer_md_raw"] 供诊断评分。
- 节点 completed 或 Reviewer accepted 都不改写最终交付状态（cli-20261016-134035 即此例）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from eval.contracts import Claim, RunArtifact, TraceEvent

_CLAIM_RE = re.compile(r"^-\s+(?P<text>.+?)\s+\[(?P<value>-?[\d.]+)\s+(?P<unit>\S+)\s+→\s+(?P<ref>[^\]]*)\]\s*$")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def parse_claims(answer_md: str) -> list[Claim]:
    claims: list[Claim] = []
    for line in answer_md.splitlines():
        m = _CLAIM_RE.match(line.strip())
        if m:
            try:
                val = float(m.group("value"))
            except ValueError:
                continue
            claims.append(Claim(text=m.group("text").strip(), value=val,
                                unit=m.group("unit").strip(), ref=m.group("ref").strip()))
    return claims


def answer_body(answer_md: str) -> str:
    """去掉 cli.py 生成的头部与引用/评估尾巴，只留模型答案正文（诊断评分更干净）。"""
    text = answer_md
    # 头部：`# {question}` + `- status:` 等行 + 空行
    text = re.sub(r"^#\s.*?\n(?:-.*\n)*\n?", "", text, count=1)
    # 尾巴：`## 引用` / `## 模型主观评估` / `- 概念字典版本` 起截断
    for marker in ("\n## 引用", "\n## 模型主观评估", "\n- 概念字典版本"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def load_run_artifact(run_dir: str | Path, case_id: str) -> RunArtifact:
    run_dir = Path(run_dir)
    meta = _read_json(run_dir / "run_meta.json")
    answer_path = run_dir / "answer.md"
    answer_md = answer_path.read_text(encoding="utf-8") if answer_path.exists() else ""
    status = str(meta.get("status") or "unknown")
    committed = status == "answered"
    return RunArtifact(
        run_id=str(meta.get("run_id") or run_dir.name),
        case_id=case_id,
        run_dir=run_dir,
        final_status=status,
        final_answer=answer_body(answer_md) if committed and answer_md else None,
        claims=parse_claims(answer_md),
        candidate_refs=[],
        version=str(meta.get("commit") or "unknown"),
        process_objects={
            "answer_md_raw": answer_md,
            "verified": meta.get("verified"),
            "agent_mode": meta.get("agent_mode"),
            "query_backend": meta.get("query_backend"),
            "semantic_review_status": (meta.get("multi_agent") or {}).get("semantic_review_status"),
            "task_coverage_status": (meta.get("multi_agent") or {}).get("task_coverage_status"),
            "stop_reason": (meta.get("multi_agent") or {}).get("stop_reason"),
            "refuse_reason": (meta.get("usage") or {}).get("refuse_reason"),
        },
        usage=dict(meta.get("usage") or {}),
    )


def load_trace_events(run_dir: str | Path) -> list[TraceEvent]:
    """把 trace.jsonl 每行转成 TraceEvent；缺失字段用 None，不填 0 冒充已测量。"""
    run_dir = Path(run_dir)
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    events: list[TraceEvent] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") == "meta":
            continue
        rid = e.get("result_id") or e.get("rid")
        events.append(TraceEvent(
            event_id=e.get("call_id") or e.get("agent_call_id") or f"{run_dir.name}#{i}",
            run_id=run_dir.name,
            event_type=str(e.get("type") or "unknown"),
            status=("ok" if e.get("ok") else "error") if e.get("ok") is not None else None,
            parent_event_id=e.get("parent_call_id"),
            node_id=e.get("node_id"),
            plan_revision=e.get("plan_revision"),
            role=e.get("role"),
            role_call_id=e.get("agent_call_id"),
            tool_call_id=e.get("call_id"),
            query_trace_id=None,
            result_ids=[rid] if rid else [],
            artifact_id=e.get("artifact_id"),
            artifact_version=e.get("artifact_version"),
        ))
    return events


def run_question(run_dir: str | Path) -> str | None:
    q = _read_json(Path(run_dir) / "question.json")
    return q.get("question")

"""V4.47 材料生成（HUMAN 批阅用）：固定种子抽 3 条 v1 trace，把答案中每个数字追溯到 SQL/数据行。

用法：.venv/Scripts/python scripts/export_v447_evidence.py <run_id> [seed=42]
输出：docs/evidence/V4.47.md —— 每条 claim：数值 → rid → 工具与参数 → 编译 SQL → 重放结果中的命中行。
本脚本只生成材料，V4.47 的 PASS 由人工批阅给出（verify.md 0.3：HUMAN 检查不得自评通过）。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")

from agent.loop import make_run_ctx  # noqa: E402
from agent.tools import data as tool_data  # noqa: E402

DB = "data/finsights.duckdb"
OUT = Path("docs/evidence/V4.47.md")


def load_trace(path: Path) -> dict:
    events = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    meta = next(e for e in events if e.get("type") == "meta")
    result = next(e for e in events if e.get("type") == "result")
    return {"file": path.name, "meta": meta, "result": result, "events": events}


def calls_by_id(events: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in events:
        if e.get("type") == "assistant":
            for tc in e.get("tool_calls") or []:
                out[tc["id"]] = tc
    return out


def replay(tool: str, args: dict) -> tuple[str, list[str]]:
    """用同参数在当前库重放工具，返回 (SQL, 命中数字说明前的结果行预览)。"""
    ctx = make_run_ctx(DB, None)
    out = tool_data.execute(tool, args, ctx.tool_ctx)
    if not out.ok or out.stored is None:
        return "", [f"重放失败：{out.text[:200]}"]
    sql = out.stored.sql or ""
    rows = [f"rid={out.stored.id} digest: {out.stored.digest}"]
    df = out.stored.df
    rows.append("```")
    rows.append(df.head(12).to_string(index=False))
    rows.append("```")
    return sql, rows


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    run_id = argv[1]
    seed = int(argv[2]) if len(argv) > 2 else 42
    files = sorted(p for p in Path("runs", run_id).glob("*.jsonl"))
    chosen = random.Random(seed).sample(files, min(3, len(files)))
    lines = [f"# V4.47 证据链追溯材料（run={run_id}，seed={seed}，抽查 {len(chosen)} 条）",
             "",
             "> 生成命令：`python scripts/export_v447_evidence.py " + run_id + f" {seed}`。",
             "> 每条 claim 的数值按 run 内 rid 定位工具与参数，再在当前库同参数重放，展示编译 SQL 与结果行。",
             "> HUMAN 批阅点：数字→SQL→结论的链条是否完整可信。"]
    for f in chosen:
        t = load_trace(f)
        lines += ["", "---", "", f"## {t['result']['qid']} trial{t['result']['trial']}"
                  f"（{t['meta']['agent']}，file={t['file']}）", "",
                  f"**问题**：{t['meta']['question']}", "",
                  f"**答案摘录**：{t['result']['answer_md'][:300]}", "",
                  f"**判定**：correct={t['result']['correct']}，steps={t['result']['steps']}，"
                  f"tool_calls={t['result']['tool_calls']}"]
        final = next((e for e in t["events"] if e.get("type") == "final"), None)
        claims = (final or {}).get("claims") or []
        tr_by_rid = {e["result_id"]: e for e in t["events"]
                     if e.get("type") == "tool_result" and e.get("result_id")}
        tc_by_id = calls_by_id(t["events"])
        if not claims:
            lines += ["", "（本次答案没有带 rid 的数值 claim）"]
            continue
        lines += ["", "| # | 主张 | 数值 | 单位 | rid | 工具 | 参数 |",
                  "|---|---|---|---|---|---|---|"]
        detail: list[str] = ["", "### 逐条追溯", ""]
        for i, c in enumerate(claims, 1):
            tr = tr_by_rid.get(c.get("ref", ""))
            call = tc_by_id.get(tr.get("call_id"), {}) if tr else {}
            tool = call.get("name", "?")
            try:
                args = json.loads(call.get("arguments") or "{}")
            except Exception:
                args = {}
            lines.append(f"| {i} | {c.get('text', '')[:24]} | {c.get('value')} "
                         f"| {c.get('unit')} | {c.get('ref')} | {tool} | "
                         f"`{json.dumps(args, ensure_ascii=False)[:120]}` |")
            if tool == "final_answer" or not tr:
                detail.append(f"- claim {i}（{c.get('value')}，{c.get('ref')}）：run 内未找到产出该 rid 的工具调用，链条断裂，需人工确认")
                continue
            sql, rows = replay(tool, args)
            detail.append(f"- claim {i}：数值 **{c.get('value')}**（{c.get('unit')}）← "
                          f"**{c.get('ref')}** ← `{tool}`")
            if sql:
                detail.append(f"  - 编译 SQL：`{sql[:400]}`")
            detail += ["  - " + r for r in rows]
        lines += detail
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出 {OUT}（{len(chosen)} 条 trace）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""V4.47 材料生成（HUMAN 批阅用）：固定种子抽 3 条 v1 trace，把答案中每个数字追溯到 SQL/数据行。

用法：.venv/Scripts/python scripts/export_v447_evidence.py <run_id> [seed=42]
输出：docs/evidence/V4.47.md（整份覆盖式重生成，不挑选通过项）。

V4.47 批阅后增强（review_20261002.md 步骤 4）：
- 完整正文（不截断）、全部 claims、核验结果（final.verified 与逐条打回记录）；
- 原 run 证据与当前库重放**明确分区**：原 run 段给出该 rid 的工具/参数/模型所见结果；
  重放段 rid 一律加 `replay:` 前缀，不得冒充原 run 的 rid；
- 完整 SQL（不截断）与完整输入/输出行；派生工具附公式说明；data_version（当前库）。
- 本脚本只生成材料，V4.47 的 PASS 由人工批阅给出（verify.md 0.3）。
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

# 派生工具的公式说明（V4.47 批阅：派生公式要写进证据）
FORMULAS = {
    "working_capital": "DIO=平均存货/COGS×days；DSO=平均应收/收入×days；DPO=平均应付/COGS×days；CCC=DIO+DSO−DPO（平均余额=(本期+上期)/2）",
    "check_identities": "A=L+E；total_assets/total_current_assets/total_current_liabilities/total_liabilities 分项加总（HP 的 total_liabilities 分项不含非流动递延收入）",
    "variance": "同比/环比差异与贡献度分解（contribution）",
    "calc": "AST 白名单算术（表达式即公式）",
    "seasonal_check": "历年同季度环比分布的 z 分数与分位",
    "peer_compare": "同一自然季度各公司环比变化与排名",
}


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


def original_result_block(tr: dict) -> list[str]:
    """原 run 证据：模型当时看到的结果（不截断）。"""
    lines = ["", "#### 原 run 证据（模型当时所见，未截断）", "", "```"]
    content = tr.get("content") or tr.get("text") or ""
    lines.append(str(content))
    lines.append("```")
    if tr.get("sql"):
        lines += [f"- 原 run SQL：`{tr['sql']}`"]
    return lines


def replay_block(tool: str, args: dict) -> list[str]:
    """当前库重放：rid 加 replay: 前缀；完整 SQL 与完整输出行（不截断）。"""
    ctx = make_run_ctx(DB, None)
    out = tool_data.execute(tool, args, ctx.tool_ctx)
    if not out.ok or out.stored is None:
        return [f"- 重放失败：{out.text}"]
    stored = out.stored
    lines = [f"- 重放 rid：**replay:{stored.id}**（当前库，data_version={stored.data_version}）"]
    if stored.sql:
        lines += [f"- 完整编译 SQL：", "", "```sql", stored.sql, "```"]
    if tool in FORMULAS:
        lines.append(f"- 派生公式：{FORMULAS[tool]}")
    rows = [f"replay:{stored.id} digest: {stored.digest}", "```"]
    rows.append(stored.df.to_string(index=False))  # 完整输出行，不截断
    rows.append("```")
    lines += rows
    return lines


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
             "> 原 run 证据与当前库重放分区展示；重放 rid 加 `replay:` 前缀，不冒充原 run rid。",
             "> HUMAN 批阅点：数字→SQL→结论的链条是否完整可信（V4.15/V4.47 批阅标准见 review_20261002.md）。"]
    for f in chosen:
        t = load_trace(f)
        final = next((e for e in t["events"] if e.get("type") == "final"), None)
        result = t["result"]
        lines += ["", "---", "", f"## {result['qid']} trial{result['trial']}"
                  f"（{t['meta']['agent']}，file={t['file']}）", "",
                  f"**问题**：{t['meta']['question']}", "",
                  "**完整正文（answer_md，未截断）**：", "",
                  "```markdown", result.get("answer_md") or "", "```", "",
                  f"**核验结果**：verified={result.get('verified')}，correct={result['correct']}，"
                  f"steps={result['steps']}，tool_calls={result['tool_calls']}"]
        # 全部 claims
        claims = (final or {}).get("claims") or []
        lines += ["", "### 全部 claims（未截断）", "",
                  "| # | 主张 | 数值 | 单位 | rid |", "|---|---|---|---|---|"]
        for i, c in enumerate(claims, 1):
            lines.append(f"| {i} | {c.get('text', '')} | {c.get('value')} | {c.get('unit')} | {c.get('ref')} |")
        if not claims:
            lines += ["（本次答案没有带 rid 的数值 claim）"]
        # 打回记录
        rejects = [e for e in t["events"] if e.get("type") == "verifier_reject"]
        if rejects:
            lines += ["", "### verifier 打回记录", ""]
            for r in rejects:
                lines.append(f"- {r}")
        # 逐条追溯
        tr_by_rid = {e["result_id"]: e for e in t["events"]
                     if e.get("type") == "tool_result" and e.get("result_id")}
        tc_by_id = calls_by_id(t["events"])
        detail: list[str] = ["", "### 逐条追溯", ""]
        for i, c in enumerate(claims, 1):
            ref = c.get("ref", "")
            tr = tr_by_rid.get(ref)
            call = tc_by_id.get(tr.get("call_id"), {}) if tr else {}
            tool = call.get("name", "?")
            try:
                args = json.loads(call.get("arguments") or "{}")
            except Exception:
                args = {}
            detail.append(f"- claim {i}：数值 **{c.get('value')}**（{c.get('unit')}）← "
                          f"原 run rid **{ref}** ← `{tool}`，参数 `{json.dumps(args, ensure_ascii=False)}`")
            if tool == "final_answer" or not tr:
                detail.append("  - run 内未找到产出该 rid 的工具调用，链条断裂，需人工确认")
                continue
            detail += ["  " + ln for ln in original_result_block(tr)]
            detail.append("  #### 当前库重放")
            detail += ["  " + ln for ln in replay_block(tool, args)]
        lines += detail
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写出 {OUT}（{len(chosen)} 条 trace，未截断）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

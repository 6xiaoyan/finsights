"""P5.3 L3 打分器（plan 7.3，确定性）：top-1 命中 / 误归因 / 误报率 / 证据完整性。

- gold 从 eval/datasets/l3_gold/<scenario_id>.json 读取（Question.gold 置空，V5.6 隔离）。
- 标签提取：正文必须含一行「根因标签: <label>」（attribution skill 输出约定），
  并在 6 个枚举标签中做精确词匹配（防"非季节性"这类否定式误匹配：只认全词）。
- top-1 = label 与 account（中文名出现在正文）都命中。
- 误归因 = 说出 must_not_claim 中的标签。
- 证据完整性 = trace 中是否调用了 seasonal_check 与 peer_compare（过程指标）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from eval.schema import Answer, Question

GOLD_DIR = Path("eval/datasets/l3_gold")
LABELS = ("no_anomaly", "seasonal", "industry_wide", "company_specific", "mix_shift", "reclassification")
LABEL_RE = re.compile(r"根因标签[:：]\s*(\w+)")


def load_gold(question: Question) -> dict:
    sid = question.db
    return json.loads((GOLD_DIR / f"{sid}.json").read_text(encoding="utf-8"))


def extract_label(text: str) -> str | None:
    m = LABEL_RE.search(text)
    if m and m.group(1) in LABELS:
        return m.group(1)
    hits = [lb for lb in LABELS if re.search(rf"(?<![\w-]){lb}(?![\w-])", text)]
    return hits[0] if len(hits) == 1 else None


def extract_account(text: str, gold: dict) -> bool:
    from eval.generators.inject import name_of
    return name_of(gold["account"]) in text or gold["account"] in text


def grade(question: Question, answer: Answer, trace: list | None = None) -> dict:
    gold = load_gold(question)
    if answer.status != "answered":
        return {"correct": False, "top1_hit": False, "misattributed": False,
                "false_positive": False, "evidence": {}, "detail": f"status={answer.status}"}
    label = extract_label(answer.answer_md)
    account_hit = bool(label) and extract_account(answer.answer_md, gold)
    top1_hit = bool(label) and label == gold["label"] and account_hit
    misattributed = bool(label) and label in gold.get("must_not_claim", [])
    false_positive = gold["label"] == "no_anomaly" and bool(label) and label != "no_anomaly"
    tools_called = set()
    for e in trace or []:
        if e.get("type") == "assistant":
            for tc in e.get("tool_calls") or []:
                tools_called.add(tc.get("name"))
    evidence = {"seasonal_check": "seasonal_check" in tools_called,
                "peer_compare": "peer_compare" in tools_called}
    correct = top1_hit and not misattributed
    return {"correct": correct, "top1_hit": top1_hit, "misattributed": misattributed,
            "false_positive": false_positive, "evidence": evidence,
            "detail": f"label={label} gold={gold['label']} account_hit={account_hit}"}


def aggregate(results: list[dict], n_injected: int, n_negative: int) -> dict:
    """V5.10 汇总：top-1、top-3（占位：单标签体系下等于 top-1）、误归因率、误报率、证据完整性。"""
    n = max(len(results), 1)
    inj = [r for r in results if not r.get("false_positive") and r.get("_is_negative") is not True]
    return {
        "top1": sum(r["top1_hit"] for r in results) / n,
        "misattributed_rate": sum(r["misattributed"] for r in results) / n,
        "false_positive_rate": sum(r["false_positive"] for r in results) / n,
        "evidence_seasonal": sum(r["evidence"].get("seasonal_check", False) for r in results) / n,
        "evidence_peer": sum(r["evidence"].get("peer_compare", False) for r in results) / n,
        "n": len(results), "n_injected": n_injected, "n_negative": n_negative,
    }

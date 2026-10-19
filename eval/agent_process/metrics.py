"""运行监控统计（PRD §7.2）。

口径要点：
- 工具调用按 `(parent_call_id or "main", call_id)` 去重，避免主循环转发与子循环重复累计。
- 跳过计入 `skipped`，**不计成功**；`ok` 缺失归 `unknown_status`，不塞进 ok/error。
- 缺 usage 记 `None` + `source="missing"`，**不填 0**。
- 子 Agent token 是 run_meta 总量的子集，**不相加**，只并列展示。
- 原包自报值与重算值并列，不一致时进 `discrepancies`。
- 只描述程序运行状态，不表示取数业务正确（PRD §7.2）。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from eval.agent_process.adapt import _read_trace_lines
from eval.agent_process.schema import ProcessPackage

METRICS_VERSION = "process-metrics-v1"

# `execute()` 把一切异常转成 "工具错误: {类型}: {消息}"（agent/tools/data.py）
_TOOL_ERROR_RE = re.compile(r"工具错误[:：]\s*([A-Za-z_][A-Za-z0-9_]*)")

_REVIEWER_EVIDENCE_TOOLS = ("recall", "read_analysis_object", "check_identities")


def _tool_calls(trace: list[dict]) -> dict:
    seen: set[tuple[str, str]] = set()
    by_tool: dict[str, Counter] = {}
    exceptions: Counter = Counter()
    total = ok = error = unknown = skipped = dupes = 0

    for ev in trace:
        etype = ev.get("type")
        tool = ev.get("tool") or ""
        if etype == "tool_skipped":
            # 跳过是程序主动不调用，既不是成功也不是失败
            skipped += 1
            by_tool.setdefault(tool, Counter())["skipped"] += 1
            continue
        if etype != "tool_result":
            continue
        # 去重键优先用 call_id：它是模型调用级的唯一 id。若用 (parent, call_id)，
        # 一张丢了 parent_call_id 的转发副本就会被重复计数（PRD §7.2 要求防转发重复）。
        cid = ev.get("call_id") or ""
        key = ("call", cid) if cid else ("nokey", f"{ev.get('tool')}|{ev.get('rid')}")
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        total += 1
        bucket = by_tool.setdefault(tool, Counter())
        flag = ev.get("ok")
        if flag is True:
            ok += 1
            bucket["ok"] += 1
        elif flag is False:
            error += 1
            bucket["error"] += 1
            m = _TOOL_ERROR_RE.search(ev.get("result_head") or "")
            if m:
                exceptions[m.group(1)] += 1
        else:
            unknown += 1
            bucket["unknown_status"] += 1

    return {
        "total": total + skipped,
        "tool_result_events": total,
        "ok": ok, "error": error, "unknown_status": unknown, "skipped": skipped,
        "deduped_duplicates": dupes,
        "by_tool": {k: dict(v) for k, v in sorted(by_tool.items())},
        "exception_types": dict(sorted(exceptions.items())),
    }


def _queries(pkg: ProcessPackage) -> dict:
    """查询调用计数：按**调用**计，rid 只作结果引用（同计划会复用 rid）。"""
    seen_rids: set[str] = set()
    reuse = 0
    by_status: Counter = Counter()
    for rec in pkg.query_records:
        by_status[rec.status or "unknown"] += 1
        rids = set(rec.result_ids)
        if rids & seen_rids:
            reuse += 1
        seen_rids |= rids
    return {
        "calls": len(pkg.query_records),
        "unique_result_ids": len(seen_rids),
        "rid_reuse_calls": reuse,
        "by_status": dict(sorted(by_status.items())),
        "source": ("run_package" if any(r.source == "run_package"
                                        for r in pkg.query_records)
                   else ("derived" if pkg.query_records else "none")),
    }


def _role_calls(pkg: ProcessPackage) -> dict:
    by_role: Counter = Counter()
    latency: dict[str, int] = {}
    missing_end = 0
    for c in pkg.role_calls:
        by_role[c.role] += 1
        if c.latency_ms is not None:
            latency[c.agent_call_id] = c.latency_ms
        elif c.ok is None and c.child_steps is None:
            missing_end += 1
    return {
        "total": len(pkg.role_calls),
        "by_role": dict(sorted(by_role.items())),
        "pairs_missing_end": missing_end,
    }


def _rounds(pkg: ProcessPackage, trace: list[dict]) -> dict:
    multi = (pkg.run_meta.get("multi_agent") or {})
    usage = pkg.usage or {}
    complete = all(c.child_steps is not None for c in pkg.role_calls)
    child = sum(c.child_steps or 0 for c in pkg.role_calls)
    return {
        "main_assistant_events": sum(1 for e in trace if e.get("type") == "assistant"),
        "main_steps_reported": usage.get("steps"),
        "subagent_child_steps": child,
        "subagent_child_steps_reported": multi.get("subagent_child_steps"),
        # 有子调用没有 end 时，求和只是下界——必须标明，不能让人读成精确值
        "child_steps_complete": complete,
    }


def _extraction(pkg: ProcessPackage) -> dict:
    attempts = sum(r.attempts or 0 for r in pkg.query_records)
    retries = 0
    for r in pkg.query_records:
        retries += sum(1 for e in r.extractor_events
                       if isinstance(e, dict) and e.get("type") == "extractor_call"
                       and (e.get("attempt") or 1) > 1)
    have_detail = any(r.extractor_events for r in pkg.query_records)
    return {
        "calls_reported": pkg.run_meta.get("extraction_calls"),
        "attempts_seen": attempts if have_detail else None,
        "format_retries_seen": retries if have_detail else None,
        "available": have_detail,
        "prompt_version": pkg.run_meta.get("extraction_prompt_version"),
    }


def _result_reads(trace: list[dict]) -> dict:
    counts: Counter = Counter()
    reviewer = 0
    for ev in trace:
        if ev.get("type") != "tool_result":
            continue
        tool = ev.get("tool") or ""
        if tool == "recall":
            counts["recall_calls"] += 1
        elif tool == "read_analysis_object":
            counts["read_analysis_object_calls"] += 1
        if ev.get("role") == "reviewer" and tool in _REVIEWER_EVIDENCE_TOOLS:
            reviewer += 1
    counts["reviewer_evidence_reads"] = reviewer
    return {"recall_calls": counts["recall_calls"],
            "read_analysis_object_calls": counts["read_analysis_object_calls"],
            "reviewer_evidence_reads": reviewer}


def _retries(trace: list[dict]) -> dict:
    c = Counter(e.get("type") for e in trace)
    return {"llm_retry": c.get("llm_retry", 0),
            "stop_reject": c.get("stop_reject", 0),
            "protocol_correction": c.get("protocol_correction", 0)}


def _latency(pkg: ProcessPackage) -> dict:
    by_call: dict[str, int] = {}
    missing: list[str] = []
    for c in pkg.role_calls:
        if c.latency_ms is not None:
            by_call[c.agent_call_id] = c.latency_ms
        else:
            missing.append(c.agent_call_id)
    return {"roles_total": sum(by_call.values()), "by_role_call": by_call,
            # 有子调用没跑完就没有耗时：总计是下界，不是「0 秒」
            "complete": not missing, "missing_latency_calls": missing}


def _tokens(pkg: ProcessPackage) -> dict:
    usage = pkg.usage or {}
    has_in = "prompt_tokens" in usage
    has_out = "completion_tokens" in usage
    sub_complete = all(c.prompt_tokens is not None and c.completion_tokens is not None
                       for c in pkg.role_calls)
    sub_p = sum(c.prompt_tokens or 0 for c in pkg.role_calls)
    sub_c = sum(c.completion_tokens or 0 for c in pkg.role_calls)
    if has_in and has_out:
        source = "run_meta"
    elif has_in or has_out:
        source = "run_meta_partial"      # 只报了一项，不能当作完整用量
    else:
        source = "missing"
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "source": source,
        "subagent_prompt_tokens": sub_p,
        "subagent_completion_tokens": sub_c,
        "subagent_complete": sub_complete,
        "notes": (["run_meta.usage 是整次运行的用量；子 Agent token 是其中的子集，不相加"]
                  if source == "run_meta"
                  else ([f"run_meta.usage 只记录了部分用量（source={source}），"
                         "缺失项记未知（不填 0）"] if source == "run_meta_partial"
                        else ["运行包未记录 usage，token 记未知（不填 0）"])),
    }


def _cost(pkg: ProcessPackage, tokens: dict) -> float | None:
    """费用是**估算**（按 config 里的单价），无单价或 token 未知时为 None。"""
    pricing = (((pkg.run_meta.get("config") or {}).get("llm") or {})
               .get("pricing_usd_per_mtok") or {})
    p_in, p_out = pricing.get("input"), pricing.get("output")
    t_in, t_out = tokens["prompt_tokens"], tokens["completion_tokens"]
    if p_in is None or p_out is None or t_in is None or t_out is None:
        return None
    return t_in / 1e6 * p_in + t_out / 1e6 * p_out


def _discrepancies(pkg: ProcessPackage, tool_calls: dict, role_calls: dict,
                   rounds: dict, queries: dict, extraction: dict) -> tuple[list[dict], list[str]]:
    """原包自报值与重算值不一致时同时报告并标记差异（PRD §7.2）。

    **只比较同量纲的计数**：`extraction_calls` 是提取模型**请求**次数，
    一次工具调用在格式修复时会发多次请求，所以它既不能跟工具调用次数比，
    也不能直接跟查询调用数比 —— 有逐次明细时比 `attempts_seen`，没有就不比。
    """
    multi = (pkg.run_meta.get("multi_agent") or {})
    pairs = [
        ("subagent_calls", multi.get("subagent_calls"), role_calls["total"]),
        ("subagent_child_steps", multi.get("subagent_child_steps"),
         rounds["subagent_child_steps"]),
    ]
    out: list[dict] = []
    notes: list[str] = []
    for field, reported, recomputed in pairs:
        if reported is None:
            continue
        if reported != recomputed:
            out.append({"field": field, "reported": reported, "recomputed": recomputed})

    reported_extraction = pkg.run_meta.get("extraction_calls")
    if reported_extraction is None:
        pass
    elif extraction["attempts_seen"] is None:
        # 历史包没有提取明细：不可比，只说明，不报差异
        notes.append(f"extraction_calls 自报 {reported_extraction} 次，但该包没有逐次提取明细，"
                     "无法重算，不参与差异比较")
    elif reported_extraction != extraction["attempts_seen"]:
        out.append({"field": "extraction_calls", "reported": reported_extraction,
                    "recomputed": extraction["attempts_seen"]})

    reported_roles = multi.get("subagent_role_calls")
    if isinstance(reported_roles, dict) and reported_roles != role_calls["by_role"]:
        out.append({"field": "subagent_role_calls", "reported": reported_roles,
                    "recomputed": role_calls["by_role"]})
    return out, notes


def compute_metrics(pkg: ProcessPackage) -> dict:
    """从运行包算运行监控指标。trace 从 `pkg.run_dir` 现读（包内不重存整份事件）。"""
    trace = _read_trace_lines(Path(pkg.run_dir))

    tool_calls = _tool_calls(trace)
    role_calls = _role_calls(pkg)
    rounds = _rounds(pkg, trace)
    queries = _queries(pkg)
    tokens = _tokens(pkg)
    extraction = _extraction(pkg)
    discrepancies, discrepancy_notes = _discrepancies(
        pkg, tool_calls, role_calls, rounds, queries, extraction)

    notes = ["工具调用成功只描述程序运行状态，不表示取数业务正确（PRD §7.2）",
             "metrics 是运行状态，与质量评分分列展示，不合并为总分",
             *discrepancy_notes]
    if role_calls["pairs_missing_end"]:
        notes.append(f"{role_calls['pairs_missing_end']} 个子调用没有结束事件"
                     "（崩溃/超时/被中断）：耗时、子循环步数与子 Agent token 只是**下界**，"
                     "不是精确值")

    return {
        "metrics_version": METRICS_VERSION,
        "tool_calls": tool_calls,
        "exception_types": tool_calls["exception_types"],
        "retries": _retries(trace),
        "role_calls": role_calls,
        "rounds": rounds,
        "extraction": extraction,
        "queries": queries,
        "result_reads": _result_reads(trace),
        "latency_ms": _latency(pkg),
        "tokens": tokens,
        "cost_usd_estimate": _cost(pkg, tokens),
        "cost_note": "按 config.llm.pricing_usd_per_mtok 估算，非实际账单",
        "denominators": {
            "tool_calls_total": tool_calls["total"],
            # 必须恒等于 total，否则说明有状态被漏计或重复计
            "tool_calls_ok_plus_error_plus_skipped_plus_unknown":
                (tool_calls["ok"] + tool_calls["error"]
                 + tool_calls["skipped"] + tool_calls["unknown_status"]),
            "role_calls_total": role_calls["total"],
            "produced_by_calls": queries["calls"],
        },
        "discrepancies": discrepancies,
        "notes": notes,
    }

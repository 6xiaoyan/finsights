"""看板自动标红规则（dashboard_design.md §2.2）。

只按当前证据识别：没有 gold 或评分记录，不判断业务归因/SQL 语义/经营解释正确。
每条标记带 rule_id、原因与证据定位；人工确认与自动识别是两个独立字段。
"""
from __future__ import annotations


def _flag(rule_id: str, severity: str, reason: str, evidence: str) -> dict:
    return {"rule_id": rule_id, "severity": severity, "reason": reason, "evidence": evidence}


def classify(record: dict | None, trace_events: list[dict],
             meta: dict | None, files: dict[str, bool]) -> list[dict]:
    """对单个 query 的运行记录打标。

    record: eval summary 中的记录（可能 None）；trace_events: 该 query 的 trace 事件；
    meta: run_meta.json（可能 None）；files: {文件名: 是否存在且可解析}。
    返回 flags 列表（可能为空 = 未发现自动失败，业务质量仍待人工）。
    """
    flags: list[dict] = []
    result = next((e for e in reversed(trace_events) if e.get("type") == "result"), None)
    refuse_reason = str((meta or {}).get("usage", {}).get("refuse_reason")
                        or (result or {}).get("refuse_reason") or "")
    status = (result or {}).get("status") or (meta or {}).get("status")
    verified = (result or {}).get("verified")
    if verified is None:
        verified = (meta or {}).get("verified")

    # R1 运行异常：有明确记录的终止错误
    err_events = [e for e in trace_events if e.get("type") == "error"]
    if status == "error" or refuse_reason.startswith("运行异常") or err_events:
        detail = refuse_reason or (err_events[-1].get("error", "") if err_events else "")
        flags.append(_flag("R1", "红", "运行异常", detail[:200]))

    # R2 预算耗尽：步数/token/时间预算终止（诚实拒答，标注"未完成分析"）
    if any(k in refuse_reason for k in ("步数预算耗尽", "token 预算耗尽", "时间预算耗尽")):
        flags.append(_flag("R2", "红", "预算耗尽（未完成分析）", refuse_reason[:200]))

    # R3 最终核验失败：answered 但 verified=false
    if status == "answered" and verified is False:
        feedback = [e.get("feedback", "") for e in trace_events if e.get("type") == "stop_reject"]
        flags.append(_flag("R3", "红", "最终核验失败",
                           feedback[-1][:200] if feedback else "verified=false"))

    # R4 评分失败：有明确评分记录且 correct=false（评分来源/规则版本随记录）
    if record is not None and "correct" in record and record.get("status") not in ("error",):
        if record["correct"] is False:
            flags.append(_flag("R4", "红", "评分失败",
                               str(record.get("detail", ""))[:200]))

    # R6 记录不完整：任务已确认结束但必需文件缺失/JSON 损坏/引用指向不存在的结果
    ended = status in ("answered", "refuse", "clarify")
    missing = [name for name, ok in files.items() if not ok]
    if ended and missing:
        flags.append(_flag("R6", "红", "记录不完整", f"缺失/损坏: {','.join(missing)}"))

    # W1 过程警告：核验打回 / 工具失败 / API 重试后恢复 / 结果截断
    rejects = [e for e in trace_events if e.get("type") == "stop_reject"]
    if rejects:
        flags.append(_flag("W1", "黄", f"核验打回 {len(rejects)} 次后恢复",
                           str(rejects[-1].get("feedback", ""))[:200]))
    tool_errs = [e for e in trace_events if e.get("type") == "tool_result" and e.get("ok") is False]
    if tool_errs:
        flags.append(_flag("W1", "黄", f"工具失败 {len(tool_errs)} 次",
                           str(tool_errs[-1].get("tool", ""))))
    retries = [e for e in trace_events if e.get("type") == "llm_retry"]
    if retries:
        flags.append(_flag("W1", "黄", f"API 重试 {len(retries)} 次后恢复",
                           str(retries[-1].get("error", ""))[:120]))
    if (meta or {}).get("any_truncated") or any("truncated" in str(e) for e in trace_events):
        flags.append(_flag("W1", "黄", "结果导出截断", "见 results.json any_truncated"))

    # W2 待人工判断：clarify/refuse 且无已知期望（预算耗尽已归 R2）
    if status in ("clarify", "refuse") and not any(f["rule_id"] in ("R1", "R2") for f in flags):
        flags.append(_flag("W2", "黄", "待人工判断（澄清/拒答无已知期望）",
                           refuse_reason[:200] or status))
    return flags


def summarize(flags: list[dict]) -> tuple[str, bool]:
    """(显示文本, 是否红色 bad case)。多个原因显示多个标签；红黄并存以红计。"""
    reds = [f for f in flags if f["severity"] == "红"]
    yellows = [f for f in flags if f["severity"] == "黄"]
    if reds:
        return "🔴 " + "；".join(f["reason"] for f in reds), True
    if yellows:
        return "🟡 " + "；".join(f["reason"] for f in yellows), False
    return "中性：未发现自动失败", False

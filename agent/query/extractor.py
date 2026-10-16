"""提取器：一次模型调用（格式错误至多修复一次）+ 严格结构/目录校验（PRD §5.1/§5.2）。

业务歧义与目录外值不重试；只有"格式/协议错误"才修复一次。
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from agent.query.prompt import PROMPT_VERSION, build_extractor_prompt
from agent.query.schema import ExtractionResult, QueryIssue, QueryPlan, classify_issues

_FORMAT_CORRECTION = ("上面的输出不是合法 JSON 或不符合协议。请只输出一个符合协议的 JSON 对象："
                      "顶层恰好 status/issues/plan 三个键；status 只能是 "
                      "ok|needs_clarification|unknown_value|unsupported；status=ok 时给出 plan，"
                      "否则 plan 必须是 null。不要包含其他文字。")


def _normalize(raw, catalog) -> ExtractionResult:
    """把模型原始 JSON 变成受校验的 ExtractionResult；非法结构抛 ValueError。

    status != ok 时强制 plan=null（PRD §5.2：非 ok 禁止执行 SQL）。
    """
    if not isinstance(raw, dict):
        raise ValueError("顶层不是 JSON 对象")
    status = str(raw.get("status") or "").strip()
    if status not in ("ok", "needs_clarification", "unknown_value", "unsupported"):
        raise ValueError(f"未知 status: {status!r}")
    issues_raw = raw.get("issues") or []
    plan_raw = raw.get("plan")
    if status == "ok":
        if plan_raw is None:
            raise ValueError("status=ok 却没有 plan")
        if issues_raw:
            raise ValueError("status=ok 却带 issues")
        return ExtractionResult(status="ok", issues=[],
                                plan=QueryPlan.model_validate(plan_raw))
    norm: list[QueryIssue] = []
    for it in issues_raw:
        if isinstance(it, dict):
            norm.append(QueryIssue.model_validate(it))
        else:
            norm.append(QueryIssue(code=str(it), message=""))
    if not norm:
        raise ValueError(f"status={status} 却没有 issues")
    return ExtractionResult(status=status, issues=norm, plan=None)


def extract_plan(question: str, node_task: str, catalog, llm, *,
                 ctx=None, node_id: str | None = None) -> tuple[ExtractionResult, list[dict]]:
    """调用提取模型并校验，返回 (ExtractionResult, 事件列表)。不抛异常。"""
    events: list[dict] = []
    messages = [
        {"role": "system", "content": build_extractor_prompt(catalog)},
        {"role": "user", "content": json.dumps(
            {"question": question, "node_task": node_task}, ensure_ascii=False)},
    ]
    result: ExtractionResult | None = None
    for attempt in (1, 2):
        resp = llm.chat(messages, tools=None, role="agent")
        stats = getattr(ctx, "stats", None) if ctx is not None else None
        if isinstance(stats, dict):
            stats["extraction_calls"] = stats.get("extraction_calls", 0) + 1
        usage = getattr(resp, "usage", None) or {}
        ev = {"type": "extractor_call", "attempt": attempt, "node_id": node_id,
              "status": "parsed", "call_id": getattr(resp, "id", None),
              "prompt_version": PROMPT_VERSION,
              "prompt_tokens": usage.get("prompt_tokens"),
              "completion_tokens": usage.get("completion_tokens")}
        content = resp.content or ""
        try:
            from agent.subagents import _extract_json   # 复用既有 JSON 提取
            raw = _extract_json(content)
            if raw is None:
                raise ValueError("输出不是合法 JSON")
            result = _normalize(raw, catalog)
            events.append(ev)
            break
        except (ValueError, ValidationError) as e:
            ev["status"] = "invalid"
            ev["error"] = str(e)[:200]
            events.append(ev)
            if attempt == 1:
                messages.append({"role": "assistant", "content": content[:500]})
                messages.append({"role": "user", "content": _FORMAT_CORRECTION})
                continue
            return ExtractionResult(
                status="unsupported",
                issues=[QueryIssue(code="extractor_protocol",
                                   message=f"提取输出无法解析/不符合协议: {e}")]), events

    assert result is not None
    if result.status == "ok" and result.plan is not None:
        issues = result.plan.validate_against(catalog)
        if issues:
            status = classify_issues(issues)
            events.append({"type": "extractor_validate", "status": status,
                           "codes": [i.code for i in issues]})
            result = ExtractionResult(status=status, issues=issues, plan=None)
    events.append({"type": "extractor_result", "status": result.status})
    return result, events

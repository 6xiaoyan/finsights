"""微观查询链路诊断（PRD §7.3）。

分别检查：提取器是否忠实表达了 Worker 的查询、执行结果是否符合已确认的 QueryPlan、
以及每次调用落在哪个原因码上。多写/少写查询次数**不自动**加减分——只给计数，
与质量判断并列展示。

只诊断，不评分：这里没有也不该有 score/quality 字段。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from eval.agent_process.adapt import _read_json
from eval.agent_process.schema import ProcessPackage, QueryRecord

QUERY_DIAG_VERSION = "process-query-v1"

#: 原因码表。PRD §7.3 要求「目录外 / 歧义澄清 / 无数据 / 解析失败 / 执行失败」各不相同；
#: unsupported（不支持的组合）与 failed_unclassified（失败但类别未记录）各自独立，
#: 不塌进 unknown —— 塌了就看不出到底卡在哪一环。
REASON_CODES: dict[str, str] = {
    "ok": "执行成功并产出结果",
    "catalog_miss": "目录外取值（未知指标/公司/期间/分组/筛选值）",
    "ambiguous": "问题与节点任务冲突或多值无法定，需澄清",
    "no_data": "请求的范围没有数据（空结果不等于 0）",
    "parse_fail": "提取器协议/格式失败，未产出可执行计划",
    "exec_fail": "计划已生成但执行失败（SQL/连接等）",
    "unsupported": "当前能力不支持的组合（如衍生指标 + 明细维度）",
    "failed_unclassified": "调用失败，但历史包未记录失败类别",
    "unknown": "状态与结果都缺失，无法判定",
}

_PARSE_CODES = ("extractor_protocol",)
_NO_DATA_CODES = ("empty_filter", "no_data")


def classify_query(rec: QueryRecord) -> str:
    """把一次调用归到原因码。"""
    codes = set(rec.issue_codes or [])
    if codes & set(_PARSE_CODES):
        return "parse_fail"
    status = rec.status
    if status == "ok":
        return "ok"
    if status == "empty":
        return "no_data"
    if status == "needs_clarification":
        return "ambiguous"
    if status == "unknown_value":
        return "catalog_miss"
    if status == "unsupported":
        return "unsupported"
    if status == "error":
        return "exec_fail"
    if codes & set(_NO_DATA_CODES):
        return "no_data"
    if rec.call_ok is False:
        return "failed_unclassified"
    return "unknown"


def _dedup(seq) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x is not None and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def plan_conformance(rec: QueryRecord, rows: list[dict] | None) -> dict:
    """检查返回结果是否符合已确认的 QueryPlan（期间集合、列）。

    `rows` 来自 `results.json`（完整结果），不是事件里的截断预览。
    """
    plan = rec.plan or {}
    requested = list(plan.get("periods") or [])
    group_by = list(plan.get("group_by") or [])
    rows = rows or []

    if not plan or not rows:
        return {"periods_requested": requested, "periods_returned": [],
                "period_violations": [], "columns_present": [], "columns_missing": [],
                "status": "unknown"}

    columns = _dedup(rows[0].keys()) if isinstance(rows[0], dict) else []
    returned = _dedup(r.get("period") for r in rows if isinstance(r, dict))
    violations = [p for p in returned if p not in requested]
    missing = [c for c in group_by if c not in columns]
    return {
        "periods_requested": requested,
        "periods_returned": returned,
        "period_violations": violations,
        "columns_present": columns,
        "columns_missing": missing,
        "status": "ok" if not violations and not missing else "mismatch",
    }


def _rows_by_rid(run_dir: Path) -> dict[str, list[dict]]:
    results = _read_json(run_dir / "results.json") or {}
    out: dict[str, list[dict]] = {}
    for rec in (results.get("results") or []):
        if rec.get("id"):
            out[rec["id"]] = list(rec.get("rows") or [])
    return out


def diagnose_queries(pkg: ProcessPackage) -> dict:
    """逐次调用给出原因码与计划符合性；并诚实标注覆盖范围。"""
    rows_by_rid = _rows_by_rid(Path(pkg.run_dir))

    by_reason: Counter = Counter()
    records: list[dict] = []
    for rec in pkg.query_records:
        reason = classify_query(rec)
        by_reason[reason] += 1
        rows: list[dict] = []
        for rid in rec.result_ids:
            rows += rows_by_rid.get(rid, [])
        conf = plan_conformance(rec, rows)
        records.append({
            "query_trace_id": rec.query_trace_id,
            "call_index": rec.call_index,
            "agent_call_id": rec.agent_call_id,
            "node_id": rec.node_id,
            "role": rec.role,
            "question": rec.question,
            "status": rec.status,
            "call_ok": rec.call_ok,
            "reason": reason,
            "issue_codes": list(rec.issue_codes),
            "result_ids": list(rec.result_ids),
            "source": rec.source,
            "conformance": conf,
            "derivation_notes": list(rec.derivation_notes),
        })

    has_pkg = any(r.source == "run_package" for r in pkg.query_records)
    notes: list[str] = []
    if pkg.query_records and not has_pkg:
        notes.append("查询记录派生自 tool_result/results.json，缺少提取明细与调用级 id；"
                     "派生记录只覆盖成功路径，失败调用在历史包中无记录，分母不完整")
    if not pkg.query_records:
        notes.append("该运行包没有 query_financial_data 调用记录（可能是 metric 后端口径）")
    notes.append("查询次数只作展示，多写或少写都不自动改变质量判断（PRD §7.3）")

    return {
        "version": QUERY_DIAG_VERSION,
        "reason_codes": REASON_CODES,
        "calls": len(pkg.query_records),
        "by_reason": dict(sorted(by_reason.items())),
        "records": records,
        "coverage": {
            "has_run_package_records": has_pkg,
            "derived_calls": sum(1 for r in pkg.query_records if r.source == "derived"),
            "unlinked_calls": sum(1 for r in pkg.query_records
                                  if r.linkage == "unknown"),
        },
        "notes": notes,
    }

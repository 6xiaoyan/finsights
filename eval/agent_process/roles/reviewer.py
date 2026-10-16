"""Reviewer 独立评测（PRD §8.3）。

固定样例：已标注真实问题 + 无错误样例，覆盖错期间/单位错误/遗漏/无依据归因/
错误引用/合理替代算法/证据不足；有标注时算 precision/recall 并显示计数。

真实轨迹：把审查前对象、Reviewer 意见、审查后对象关联起来，看被指出的问题是否修复、
是否新引入错误。无法关联版本时记 unknown —— **不伪造改进率**。

Reviewer 的 accepted 是被测输出，不是标准答案。
"""
from __future__ import annotations

from typing import Any

from eval.agent_process.roles.common import evidence_basis
from eval.agent_process.schema import ProcessPackage

REVIEW_CATEGORIES = ("wrong_period", "unit_error", "omission",
                     "unsupported_attribution", "wrong_citation",
                     "alternative_algorithm_ok", "insufficient_evidence")

REVIEW_DIMENSIONS = ("problem_detection", "misses", "false_positives",
                     "severity", "evidence_basis", "actionability",
                     "fix_linkage")

_SEVERITY_ORDER = ("must_fix", "limitation", "suggestion")


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _key(item: dict) -> tuple[str, str]:
    return (item.get("category") or "", _norm(item.get("statement")))


def score_against_annotations(detected: list[dict], annotated: list[dict]) -> dict:
    """按 (category, statement) 比对检出与标注，算 precision/recall。

    分母为 0 时对应指标记 None —— 没有可检出项时 recall 无定义，
    一个都没检出时 precision 无定义；两者都不该填 0 冒充"算过"。
    """
    ann = {_key(a): a for a in annotated}
    det = {_key(d): d for d in detected}

    tp = [k for k in det if k in ann]
    fp = [k for k in det if k not in ann]
    fn = [k for k in ann if k not in det]

    precision = len(tp) / len(det) if det else None
    recall = len(tp) / len(ann) if ann else None
    return {
        "true_positive": len(tp),
        "false_positive": len(fp),
        "false_negative": len(fn),
        "precision": precision,
        "recall": recall,
        "matched": [det[k] for k in tp],
        "unexpected": [det[k] for k in fp],
        "missed": [ann[k] for k in fn],
    }


def evaluate_sample(sample: dict) -> dict:
    """固定样例评分。

    `detected` 是**记录下来的 Reviewer 输出**（本阶段离线，不跑真实 Reviewer），
    `annotated_issues` 是评测侧标注。所以：precision/recall 是程序实测（measured），
    而"检出本身"只是流程复现（pipeline_check），标注是 human_reviewed。
    """
    detected = list(sample.get("detected") or [])
    annotated = list(sample.get("annotated_issues") or [])
    scored = score_against_annotations(detected, annotated)

    severities: dict[str, int] = {}
    for d in detected:
        sev = d.get("severity") or "unspecified"
        severities[sev] = severities.get(sev, 0) + 1

    return {
        "sample_id": sample.get("sample_id"),
        "question": sample.get("question"),
        "target": sample.get("target") or {},
        **{k: scored[k] for k in ("true_positive", "false_positive", "false_negative",
                                 "precision", "recall")},
        "matched": scored["matched"],
        "unexpected": scored["unexpected"],
        "missed": scored["missed"],
        "severities": severities,
        "is_clean_sample": not annotated,
        "dimensions": list(REVIEW_DIMENSIONS),
        "evidence_basis": {
            "problem_detection": evidence_basis(False, False),   # 检出是记录值，不是本次判定
            "misses": evidence_basis(True, False),
            "false_positives": evidence_basis(True, False),
            "severity": evidence_basis(True, False),
            "evidence_basis": evidence_basis(False, False),
            "actionability": evidence_basis(False, False),
            "fix_linkage": evidence_basis(False, False),
            "detection": "pipeline_check",
            "annotation": "human_reviewed",
            "precision_recall": "measured",
        },
        "annotation": sample.get("annotations") or {},
    }


def _artifact_index(pkg: ProcessPackage) -> dict[str, Any]:
    return {a.artifact_id: a for a in pkg.artifacts}


def _next_version(pkg: ProcessPackage, target_id: str) -> tuple[str | None, str | None]:
    """找被审对象的同节点下一版产物。"""
    idx = _artifact_index(pkg)
    cur = idx.get(target_id)
    if cur is None:
        return None, None
    later = [a for a in pkg.artifacts
             if a.node_id == cur.node_id
             and (a.plan_revision or 0, a.attempt or 0)
             > (cur.plan_revision or 0, cur.attempt or 0)]
    if not later:
        return None, None
    nxt = min(later, key=lambda a: (a.plan_revision or 0, a.attempt or 0))
    return nxt.version, nxt.artifact_id


def link_review_rounds(pkg: ProcessPackage) -> dict:
    """审查前对象 → 意见 → 审查后对象；关联不上就记 unlinkable。"""
    rounds: list[dict] = []
    unlinkable: list[dict] = []

    for rv in pkg.reviews:
        findings = list(rv.findings or [])
        nxt_version, nxt_id = (None, None)
        if rv.target_type == "artifact":
            nxt_version, nxt_id = _next_version(pkg, rv.target_id)
        elif rv.target_type == "plan":
            nxt_version = str(pkg.latest_revision) if pkg.latest_revision else None

        fixed, not_fixed, declined = [], [], []
        for f in findings:
            fid = f.get("finding_id")
            status = (f.get("response_status") or "open").lower()
            if status == "addressed":
                fixed.append(fid)
            elif status == "declined":
                declined.append(fid)
            else:
                not_fixed.append(fid)

        rnd = {
            "review_id": rv.review_id,
            "target_type": rv.target_type,
            "target_id": rv.target_id,
            "target_version": rv.target_version,
            "verdict": rv.verdict,
            "finding_count": len(findings),
            "must_fix": sum(1 for f in findings if f.get("severity") == "must_fix"),
            "next_version": nxt_version,
            "next_artifact_id": nxt_id,
            "fixed": fixed,
            "not_fixed": not_fixed,
            "declined": declined,
            "findings": findings,
        }
        rounds.append(rnd)
        if rv.target_type == "artifact" and nxt_version is None:
            unlinkable.append({"review_id": rv.review_id, "target_id": rv.target_id,
                               "reason": "该节点没有更晚版本的产物，无法判断是否修复"})

    # 同一问题多轮重复提出：去重但保留轮次
    grouped: dict[tuple[str, str], dict] = {}
    for idx, rnd in enumerate(rounds, start=1):
        for f in rnd["findings"]:
            k = _key(f)
            entry = grouped.setdefault(k, {
                "category": f.get("category"), "statement": f.get("statement"),
                "severity": f.get("severity"), "rounds": [], "review_ids": [],
                "occurrences": 0,
            })
            entry["occurrences"] += 1
            if idx not in entry["rounds"]:
                entry["rounds"].append(idx)
            if rnd["review_id"] not in entry["review_ids"]:
                entry["review_ids"].append(rnd["review_id"])

    notes = [
        "Reviewer 的 accepted 是被测输出，不是标准答案；本评测不据此判定问题已修复",
        "修改前后变好只是该轨迹的观察结果，不宣称 Reviewer 带来了能力提升",
    ]
    if unlinkable:
        notes.append(f"{len(unlinkable)} 条审查无法关联到审查后版本："
                     "无法判断是否修复，不伪造改进率")

    return {
        "rounds": rounds,
        "deduped_findings": sorted(grouped.values(),
                                   key=lambda e: (-e["occurrences"], e["category"] or "")),
        "unlinkable": unlinkable,
        "notes": notes,
    }


def evaluate_package(pkg: ProcessPackage, *, judge: Any = None,
                     judge_is_live: bool = False) -> dict:
    linked = link_review_rounds(pkg)
    findings = [f for rv in pkg.reviews for f in (rv.findings or [])]
    verdicts: dict[str, int] = {}
    for rv in pkg.reviews:
        verdicts[rv.verdict] = verdicts.get(rv.verdict, 0) + 1

    return {
        **linked,
        "counts": {
            "reviews": len(pkg.reviews),
            "findings": len(findings),
            "must_fix": sum(1 for f in findings if f.get("severity") == "must_fix"),
            "limitation": sum(1 for f in findings if f.get("severity") == "limitation"),
            "suggestion": sum(1 for f in findings if f.get("severity") == "suggestion"),
            "verdicts": dict(sorted(verdicts.items())),
            "unresolved_must_fix": sum(
                1 for rv in pkg.reviews for u in (rv.unresolved_items or [])),
        },
        "categories": list(REVIEW_CATEGORIES),
        "dimensions": list(REVIEW_DIMENSIONS),
        "evidence_basis": {
            "problem_detection": evidence_basis(False, False),
            "misses": evidence_basis(True, False),
            "false_positives": evidence_basis(True, False),
            "severity": evidence_basis(True, False),
            "evidence_basis": evidence_basis(True, False),
            "actionability": evidence_basis(False, False),
            "fix_linkage": evidence_basis(True, False),
            "verdict": evidence_basis(True, False),
        },
    }

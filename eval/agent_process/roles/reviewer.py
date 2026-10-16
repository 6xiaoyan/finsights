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
                     "review_round_linkage", "fix_verification")

_SEVERITY_ORDER = ("must_fix", "limitation", "suggestion")

#: 用于判断「新版本内容是否变了」的字段——必须是 ArtifactView 上真实存在的字段。
_CONTENT_FIELDS = ("facts", "calculations", "evidence_refs", "summary", "limitations",
                   "required_missing_inputs", "optional_missing_inputs",
                   "completion_report")


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
            "review_round_linkage": evidence_basis(False, False),
            "fix_verification": evidence_basis(False, False),
            "detection": "pipeline_check",
            "annotation": "human_reviewed",
            "precision_recall": "measured",
        },
        "annotation": sample.get("annotations") or {},
    }


def _artifact_index(pkg: ProcessPackage) -> dict[str, Any]:
    return {a.artifact_id: a for a in pkg.artifacts}


_CONTENT_FIELDS = ("facts", "calculations", "evidence_refs", "summary", "limitations",
                   "required_missing_inputs", "optional_missing_inputs",
                   "completion_report", "interpretations", "hypotheses")


_CONTENT_FIELDS = ("facts", "calculations", "evidence_refs", "summary", "limitations",
                   "required_missing_inputs", "optional_missing_inputs",
                   "completion_report")


def _content_changed(before: Any, after: Any) -> bool | None:
    """新版本的内容相对被审版本是否变了。可测；「是否修好」不可测。"""
    if before is None or after is None:
        return None
    return any(getattr(before, f) != getattr(after, f) for f in _CONTENT_FIELDS)


def _next_artifact(pkg: ProcessPackage, target_id: str) -> tuple[str | None, str | None, bool | None]:
    """找被审产物的同节点下一版：返回（版本, artifact_id, 内容是否变化）。"""
    idx = _artifact_index(pkg)
    cur = idx.get(target_id)
    if cur is None:
        return None, None, None
    later = [a for a in pkg.artifacts
             if a.node_id == cur.node_id
             and (a.plan_revision or 0, a.attempt or 0)
             > (cur.plan_revision or 0, cur.attempt or 0)]
    if not later:
        return None, None, None
    nxt = min(later, key=lambda a: (a.plan_revision or 0, a.attempt or 0))
    return nxt.version, nxt.artifact_id, _content_changed(cur, nxt)


def _next_plan_revision(pkg: ProcessPackage, target_version: str) -> str | None:
    """计划审查：只有确实存在**更晚**的修订时才算有后续版本。

    不能拿 `latest_revision` 当"下一个版本"——审查 rev1 而 latest 也是 1 时，
    那等于凭空造出一个后续版本（本模块的 whole point 就是不伪造）。
    """
    try:
        reviewed = int(str(target_version))
    except (TypeError, ValueError):
        return None
    later = [p.revision for p in pkg.plans if p.revision > reviewed]
    return str(min(later)) if later else None


def link_review_rounds(pkg: ProcessPackage) -> dict:
    """审查前对象 → 意见 → 审查后对象。

    能程序判定的只有「有没有新版本」「新版本内容是否变了」。
    「被指出的问题是否修好」需要语义比对，本模块**不做**，记 `fix_verified=None`。

    特别地：`ReviewFinding.response_status` 的默认值是 `open`，而当前 agent
    **从不写入** `addressed`/`declined`（全仓 grep 只有声明与读取）。所以真实包里它
    恒为默认值、不含信息 —— 不能拿它断言「未修复」。
    """
    rounds: list[dict] = []
    unlinkable: list[dict] = []

    for rv in pkg.reviews:
        findings = list(rv.findings or [])
        nxt_version = nxt_id = None
        changed: bool | None = None
        if rv.target_type == "artifact":
            nxt_version, nxt_id, changed = _next_artifact(pkg, rv.target_id)
        elif rv.target_type == "plan":
            nxt_version = _next_plan_revision(pkg, rv.target_version)

        declared = {f.get("finding_id"): (f.get("response_status") or "open")
                    for f in findings}
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
            # 可测：新版本内容是否变了。「是否修好」见 fix_verified。
            "next_content_changed": changed,
            "fix_verified": None,
            # 产物**声明**的处理状态，如实转述；默认值不构成证据
            "declared_response": declared,
            "response_status_is_default": all(v == "open" for v in declared.values()),
            "findings": findings,
        }
        rounds.append(rnd)
        if rv.target_type in ("artifact", "plan") and nxt_version is None:
            unlinkable.append({"review_id": rv.review_id, "target_id": rv.target_id,
                               "reason": "没有更晚版本的对象，无法判断是否修复"})

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
        "「是否修好」未做前后语义比对，一律记 fix_verified=null；"
        "只报告有无新版本、新版本内容是否变化",
        "`response_status` 由产物声明；当前 agent 从不写 addressed/declined，"
        "真实包里它恒为默认值 open，**不能当作「未修复」的证据**",
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
        # 轨迹路径**没有标注**、也没算 precision/recall（那在固定样例路径里）。
        # 所以检出/漏检/误报在轨迹上无从判定，只能是 unknown —— 不能标 measured。
        "basis_scope": "trajectory",
        "evidence_basis": {
            "problem_detection": evidence_basis(False, False),
            "misses": evidence_basis(False, False),
            "false_positives": evidence_basis(False, False),
            "severity": evidence_basis(True, False),
            "evidence_basis": evidence_basis(False, False),
            "actionability": evidence_basis(False, False),
            # 关联本身是实测的（找到更晚版本、比对内容）；是否修好不是
            "review_round_linkage": evidence_basis(True, False),
            "fix_verification": evidence_basis(False, False),
            "verdict": evidence_basis(True, False),
        },
        "fix_linkage_scope": "仅「是否存在更晚版本 + 内容是否变化」两项为实测；"
                             "「问题是否被修好」未核验，fix_verified 恒为 null",
    }

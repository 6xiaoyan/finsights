"""Planner 独立评测（PRD §8.1）。

可程序判定的（节点数、依赖合法性、重复任务、范围偏移）走 measured；
覆盖映射需要评测侧参考点，有 gold 且有 Judge 时判 judged，否则**如实记 unknown**。

计划覆盖与最终答案覆盖分开：本模块不读 final_answer / answer_candidate，
不把后续 Worker 补救算成原计划已覆盖。
"""
from __future__ import annotations

import json
import re
from typing import Any

from eval.agent_process.roles.common import evidence_basis
from eval.agent_process.schema import PlanView, ProcessPackage

PLANNER_DIMENSIONS = ("executability", "dependency_soundness", "coverage",
                      "duplication", "scope_drift")

_PERIOD_RE = re.compile(r"FY\d{4}Q[1-4]")


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    """DFS 三色法：只看依赖边是否成环。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}

    def visit(node: str) -> bool:
        color[node] = GREY
        for dep in graph.get(node, []):
            if color.get(dep) == GREY:
                return True
            if color.get(dep) == WHITE and visit(dep):
                return True
        color[node] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in list(graph))


def evaluate_plan_measured(plan: PlanView, contract: dict | None = None) -> dict:
    """纯程序判定：不需要参考点，也不涉及语义判断。"""
    nodes = list(plan.nodes)
    ids = {n.node_id for n in nodes}

    goals: dict[str, list[str]] = {}
    for n in nodes:
        goals.setdefault(_norm(n.goal), []).append(n.node_id)
    duplicates = [v for v in goals.values() if len(v) > 1 and v[0]]

    graph: dict[str, list[str]] = {}
    dangling: list[str] = []
    self_deps: list[str] = []
    for n in nodes:
        deps: list[str] = []
        for d in n.depends_on:
            if d == n.node_id:
                self_deps.append(n.node_id)
            elif d not in ids:
                dangling.append(f"{n.node_id}->{d}")
            else:
                deps.append(d)
        graph[n.node_id] = deps

    period = (contract or {}).get("period")
    drift: list[str] = []
    if period:
        for n in nodes:
            mentioned = set(_PERIOD_RE.findall(n.goal or ""))
            if mentioned and any(p != period for p in mentioned):
                drift.append(n.node_id)

    return {
        "revision": plan.revision,
        "node_count": len(nodes),
        "required_node_count": sum(1 for n in nodes if n.required_for_answer),
        "duplicate_goals": duplicates,
        "dangling_deps": dangling,
        "self_deps": self_deps,
        "cycle": _has_cycle(graph),
        "scope_drift": drift,
        "completion_criteria_empty": [n.node_id for n in nodes
                                      if not (n.completion_criteria or "").strip()],
    }


def _plan_text(plan: PlanView) -> str:
    return json.dumps([{"node_id": n.node_id, "goal": n.goal,
                        "completion_criteria": n.completion_criteria,
                        "depends_on": n.depends_on,
                        "required_for_answer": n.required_for_answer}
                       for n in plan.nodes], ensure_ascii=False, indent=1)


def _map_coverage(question: str, points: list[dict], plan: PlanView,
                  judge: Any) -> list[dict]:
    plan_text = _plan_text(plan)
    out: list[dict] = []
    for p in points:
        res = judge.score_point(question, p, "", plan_text) or {}
        out.append({
            "point_id": p.get("point_id"),
            "atomic_judgment": p.get("atomic_judgment"),
            "required": p.get("required", "required"),
            "verdict": res.get("verdict", "unknown"),
            # 显式覆盖 / 合理隐含 / 未覆盖由 verdict 映射；证据理由原样保留
            "coverage": {"met": "explicit", "partial": "implicit",
                         "missing": "uncovered"}.get(res.get("verdict"), "unknown"),
            "reason": res.get("reason", ""),
        })
    return out


def evaluate_package(pkg: ProcessPackage, *, judge: Any = None,
                     points: list[dict] | None = None,
                     judge_is_live: bool = False,
                     gold_unbound: bool = False) -> dict:
    plans = list(pkg.plans)
    latest = plans[-1] if plans else None
    measured = evaluate_plan_measured(latest, pkg.contract) if latest else {}

    judged = bool(points) and latest is not None and judge is not None
    if judged:
        question = (pkg.contract or {}).get("query") or ""
        coverage = {"status": "judged", "reason": "",
                    "mapping": _map_coverage(question, list(points), latest, judge)}
    elif not latest:
        coverage = {"status": "unknown", "mapping": [],
                    "reason": "该运行包没有计划对象"}
    elif gold_unbound:
        # 给了参考点却绑不上：理由必须是真实的那一个，不能笼统说「gold 未就绪」
        coverage = {"status": "unknown", "mapping": [],
                    "reason": "已提供评测侧参考点，但未传入 --case-id，"
                              "无法把 gold 绑定到本运行包"}
    else:
        coverage = {"status": "unknown", "mapping": [],
                    "reason": "未提供评测侧参考点（gold 未就绪），不推断覆盖情况"}

    basis = evidence_basis(True, False)
    notes = ["计划覆盖与最终答案覆盖分开：本评测不读最终答案，"
             "Worker 后续补救不计入原计划覆盖",
             "节点数不是越小越好；这里只报告数量，不给规模分"]
    if measured.get("duplicate_goals"):
        notes.append("存在归一化后重复的节点目标，属重复任务而非更细拆分")

    return {
        "plan_revisions": len(plans),
        "nodes_by_revision": {p.revision: [n.node_id for n in p.nodes] for p in plans},
        "measured": measured,
        "coverage": coverage,
        "dimensions": list(PLANNER_DIMENSIONS),
        "evidence_basis": {
            "executability": basis,
            "dependency_soundness": basis,
            "duplication": basis,
            "scope_drift": basis,
            "coverage": evidence_basis(False, judged, judge_is_live=judge_is_live),
        },
        "notes": notes,
    }


def evaluate_sample(sample: dict, judge: Any, *, judge_is_live: bool = False) -> dict:
    """固定样例评测：同一套 measured + 覆盖映射，输入是样例自带的计划与参考点。"""
    from eval.agent_process.schema import PlanNodeView

    plan_src = sample.get("plan") or {}
    plan = PlanView(
        revision=plan_src.get("revision", 1),
        nodes=[PlanNodeView(**{k: v for k, v in n.items()
                               if k in PlanNodeView.model_fields})
               for n in (plan_src.get("nodes") or [])],
    )
    measured = evaluate_plan_measured(plan, sample.get("contract"))
    points = sample.get("scoring_points") or []
    mapping = _map_coverage(sample.get("question", ""), points, plan, judge) if points else []
    return {
        "sample_id": sample.get("sample_id"),
        "question": sample.get("question"),
        "measured": measured,
        "coverage": {"status": "judged" if mapping else "unknown", "mapping": mapping,
                     "reason": "" if mapping else "样例未带参考点"},
        "dimensions": list(PLANNER_DIMENSIONS),
        "evidence_basis": {
            "executability": evidence_basis(True, False),
            "dependency_soundness": evidence_basis(True, False),
            "duplication": evidence_basis(True, False),
            "scope_drift": evidence_basis(True, False),
            "coverage": evidence_basis(False, bool(mapping), judge_is_live=judge_is_live),
        },
        # 样例自带标注原样带出，供人工比对；不由评测器自行判定是否一致
        "annotation": sample.get("annotations") or {},
    }

"""eval.agent_process：运行包适配、微观埋点、运行监控、角色独立评测与报告。

依据 docs/prd-agent-evaluation.md §4/§7/§8/§9 与
docs/superpowers/specs/2026-10-16-eval-process-design.md。

CLI：`python -m eval.agent_process <adapt|metrics|roles|queries|report|replay>`
"""
from eval.agent_process.adapt import FIELD_COMPAT, artifact_version, load_process_package
from eval.agent_process.schema import (ArtifactView, PlanNodeView, PlanView,
                                       ProcessPackage, QueryRecord, ReviewView,
                                       RoleCall)

__all__ = [
    "load_process_package", "artifact_version", "FIELD_COMPAT",
    "ProcessPackage", "PlanView", "PlanNodeView", "ArtifactView", "ReviewView",
    "RoleCall", "QueryRecord",
]

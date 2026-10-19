"""eval.agent_process：运行包适配、微观埋点、运行监控、角色独立评测与报告。

依据 docs/prd-agent-evaluation.md §4/§7/§8/§9 与
docs/superpowers/specs/2026-10-16-eval-process-design.md。

CLI：`python -m eval.agent_process <adapt|metrics|roles|queries|report|replay>`

注意：本模块**不调用真实 API**。`roles` 默认用离线模拟 Judge，其结论一律标
`pipeline_check`（只验证评测流程），不得当作真实质量评分。
"""
from eval.agent_process.adapt import FIELD_COMPAT, artifact_version, load_process_package
from eval.agent_process.metrics import METRICS_VERSION, compute_metrics
from eval.agent_process.query import (QUERY_DIAG_VERSION, REASON_CODES, classify_query,
                                      diagnose_queries, plan_conformance)
from eval.agent_process.report import (REPORT_VERSION, build_record, case_report_md,
                                       compat_table_md, join_e2e, summary_report_md,
                                       write_reports)
from eval.agent_process.schema import (ArtifactView, PlanNodeView, PlanView,
                                       ProcessPackage, QueryRecord, ReviewView,
                                       RoleCall)

__all__ = [
    # 适配
    "load_process_package", "artifact_version", "FIELD_COMPAT",
    # 类型
    "ProcessPackage", "PlanView", "PlanNodeView", "ArtifactView", "ReviewView",
    "RoleCall", "QueryRecord",
    # 运行监控
    "compute_metrics", "METRICS_VERSION",
    # 查询诊断
    "diagnose_queries", "classify_query", "plan_conformance", "REASON_CODES",
    "QUERY_DIAG_VERSION",
    # 报告
    "build_record", "case_report_md", "summary_report_md", "compat_table_md",
    "join_e2e", "write_reports", "REPORT_VERSION",
]

"""公共协议：E2E 评测与数据集工具的数据对象定义。

依据 docs/prd-agent-evaluation.md §4.1 定义。
A、B 两路开发者从同一提交开始开发，公共协议先落到本目录。
"""
from eval.contracts.case import (CaseInput, CaseGold, RunArtifact, TraceEvent,
                                 GradeResult, SourceRef, ScoringPoint,
                                 NumericAssertion, Claim)

__all__ = [
    "CaseInput", "CaseGold", "RunArtifact", "TraceEvent", "GradeResult",
    "SourceRef", "ScoringPoint", "NumericAssertion", "Claim",
]

"""查询计划 v1 与工具返回协议（PRD §5.2/§5.3）。严格校验：拒绝未知字段与非法组合。

设计取舍：枚举字段（operation/field/operator/by/direction/group_by）用 str 承载，
由 `QueryPlan.validate_against()` 统一给出结构化 issue（而不是在模型构造时抛 ValidationError），
以便把"目录外值/不支持组合"映射到 PRD 的 `unknown_value`/`unsupported` 状态。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_FISCAL_RE = re.compile(r"^FY(\d{4})Q([1-4])$")
_CALENDAR_RE = re.compile(r"^(\d{4})Q([1-4])$")

_GROUP_BY_VALUES = ("company", "region", "product_line")
_DETAIL_DIMS = ("region", "product_line")
_OPERATIONS = ("difference", "change_rate")
_FILTER_FIELDS = ("region", "product_line")
_FILTER_OPERATORS = ("in", "not_in")
_SORT_BY = ("value", "difference", "change_rate")


class QueryIssue(BaseModel):
    code: str
    message: str = ""
    model_config = {"extra": "forbid"}


class Comparison(BaseModel):
    current: str
    baseline: str
    operation: str  # difference | change_rate
    model_config = {"extra": "forbid"}


class FilterClause(BaseModel):
    field: str    # region | product_line
    operator: str  # in | not_in
    values: list[str] = Field(min_length=1)
    model_config = {"extra": "forbid"}


class SortSpec(BaseModel):
    by: str                       # value | difference | change_rate
    metric: str
    current: str | None = None
    baseline: str | None = None
    direction: str = "desc"       # asc | desc
    absolute: bool = False
    top_n: int | None = Field(default=None, ge=1)
    model_config = {"extra": "forbid"}


class QueryPlan(BaseModel):
    metrics: list[str] = Field(min_length=1)
    companies: list[str] = Field(min_length=1)
    period_basis: str = "fiscal"          # fiscal | calendar
    periods: list[str] = Field(min_length=1)
    group_by: list[str] = []
    filters: list[FilterClause] = []
    comparisons: list[Comparison] = []
    sort: SortSpec | None = None
    model_config = {"extra": "forbid"}

    # ---- 语义/目录校验 ----
    def validate_against(self, catalog) -> list[QueryIssue]:
        issues: list[QueryIssue] = []
        fiscal = self.period_basis == "fiscal"

        resolved_metrics: list[str] = []
        for m in self.metrics:
            r = catalog.resolve_metric(m)
            if r is None:
                issues.append(QueryIssue(code="unknown_metric", message=f"目录中不存在指标「{m}」"))
            else:
                resolved_metrics.append(r)

        resolved_companies: list[str] = []
        for c in self.companies:
            r = catalog.resolve_company(c)
            if r is None:
                issues.append(QueryIssue(code="unknown_company", message=f"目录中不存在公司「{c}」"))
            else:
                resolved_companies.append(r)

        # 期间格式与存在性
        period_format_bad = False
        for p in self.periods:
            if not _period_matches(p, fiscal):
                issues.append(QueryIssue(
                    code="period_format",
                    message=f"期间「{p}」与口径 {self.period_basis}（FY2024Q2 / 2024Q2）不符"))
                period_format_bad = True
        if not period_format_bad:
            known: set = set()
            for c in resolved_companies:
                known |= (catalog.periods_fiscal.get(c, set()) if fiscal
                          else catalog.periods_calendar.get(c, set()))
            for p in self.periods:
                if _period_key(p, fiscal) not in known:
                    issues.append(QueryIssue(code="unknown_period",
                                             message=f"目录中不存在期间「{p}」"))

        # 分组维度
        for g in self.group_by:
            if g not in _GROUP_BY_VALUES:
                issues.append(QueryIssue(code="unknown_group_by", message=f"不支持的分组「{g}」"))

        # 明细组合：衍生指标或不支持明细的 base 指标 → unsupported
        wants_detail = bool(set(self.group_by) & set(_DETAIL_DIMS)) or any(
            f.field in _DETAIL_DIMS for f in self.filters)
        if wants_detail:
            for m in resolved_metrics:
                if catalog.metric_type(m) == "derived":
                    issues.append(QueryIssue(
                        code="unsupported_detail",
                        message=f"衍生指标「{m}」不支持 region/product_line 明细"))
                    break
            else:
                for m in resolved_metrics:
                    if m not in catalog.detail_metrics:
                        issues.append(QueryIssue(
                            code="unsupported_detail",
                            message=f"指标「{m}」没有 region/product_line 明细数据"))
                        break

        # 筛选
        by_field: dict[str, set[str]] = {}
        for f in self.filters:
            if f.field not in _FILTER_FIELDS:
                issues.append(QueryIssue(code="unknown_filter_field",
                                         message=f"不支持的筛选字段「{f.field}」"))
                continue
            if f.operator not in _FILTER_OPERATORS:
                issues.append(QueryIssue(code="unsupported_filter_logic",
                                         message=f"不支持的筛选算子「{f.operator}」"))
                continue
            by_field.setdefault(f.field, set()).add(f.operator)
            legal = catalog.regions if f.field == "region" else catalog.product_lines
            for v in f.values:
                if v not in legal:
                    issues.append(QueryIssue(
                        code="unknown_filter_value",
                        message=f"{f.field} 目录中不存在值「{v}」"))
        for field, ops in by_field.items():
            if "in" in ops and "not_in" in ops:
                issues.append(QueryIssue(
                    code="unsupported_filter_logic",
                    message=f"{field} 同时出现 in 与 not_in，无法确定语义"))

        # 比较
        for cmp in self.comparisons:
            if cmp.operation not in _OPERATIONS:
                issues.append(QueryIssue(code="comparison_operation",
                                         message=f"不支持的比较运算「{cmp.operation}」"))
            fmt_ok = True
            for v in (cmp.current, cmp.baseline):
                if not _period_matches(v, fiscal):
                    fmt_ok = False
            if not fmt_ok:
                issues.append(QueryIssue(code="comparison_format",
                                         message="比较期间与口径格式不符"))
                continue
            if cmp.baseline not in self.periods:
                issues.append(QueryIssue(code="baseline_not_in_periods",
                                         message=f"基期「{cmp.baseline}」不在 periods 中"))
            if cmp.current not in self.periods:
                issues.append(QueryIssue(code="current_not_in_periods",
                                         message=f"当前期「{cmp.current}」不在 periods 中"))

        # 排序
        if self.sort is not None:
            s = self.sort
            if s.by not in _SORT_BY:
                issues.append(QueryIssue(code="sort_format", message=f"不支持的排序依据「{s.by}」"))
            elif s.by == "value":
                if s.current is not None or s.baseline is not None:
                    issues.append(QueryIssue(
                        code="sort_value_baseline",
                        message="按原值排序时不应指定 current/baseline"))
            else:
                declared = any(c.current == s.current and c.baseline == s.baseline
                               and c.operation == s.by for c in self.comparisons)
                if not declared:
                    issues.append(QueryIssue(
                        code="sort_relation_undeclared",
                        message=f"排序依据 {s.by}({s.current} vs {s.baseline}) 未在 comparisons 中声明"))
            if s.direction not in ("asc", "desc"):
                issues.append(QueryIssue(code="sort_format", message=f"不支持的排序方向「{s.direction}」"))
            if catalog.resolve_metric(s.metric) is None:
                issues.append(QueryIssue(code="unknown_metric",
                                         message=f"排序指标「{s.metric}」不存在"))
        return issues


class ExtractionResult(BaseModel):
    status: Literal["ok", "needs_clarification", "unknown_value", "unsupported"]
    issues: list[QueryIssue] = []
    plan: QueryPlan | None = None
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _invariant(self):
        if self.status == "ok":
            if self.plan is None:
                raise ValueError("status=ok 必须给出 plan")
            if self.issues:
                raise ValueError("status=ok 时 issues 必须为空")
        else:
            if self.plan is not None:
                raise ValueError(f"status={self.status} 时 plan 必须为 null（禁止执行 SQL）")
            if not self.issues:
                raise ValueError(f"status={self.status} 时 issues 不能为空")
        return self


class QueryToolResult(BaseModel):
    status: Literal["ok", "empty", "needs_clarification", "unknown_value",
                    "unsupported", "error"]
    issues: list[QueryIssue] = []
    plan: QueryPlan | None = None
    result_ids: list[str] = []
    columns: list[str] = []
    rows: list[dict] = []
    total_rows: int = 0
    truncated: bool = False
    provenance: dict = {}
    model_config = {"extra": "forbid"}


# ---- 期间工具 ----

def _period_matches(p: str, fiscal: bool) -> bool:
    return bool((_FISCAL_RE if fiscal else _CALENDAR_RE).match(p or ""))


def _period_key(p: str, fiscal: bool):
    if fiscal:
        m = _FISCAL_RE.match(p or "")
        return (int(m.group(1)), int(m.group(2))) if m else None
    m = _CALENDAR_RE.match(p or "")
    return f"{int(m.group(1))}Q{int(m.group(2))}" if m else None


_UNKNOWN_CODES = {"unknown_metric", "unknown_company", "unknown_period",
                  "unknown_group_by", "unknown_filter_field", "unknown_filter_value"}
_UNSUPPORTED_CODES = {"unsupported_detail", "unsupported_filter_logic", "unsupported_unit",
                      "unsupported_task", "unsupported_time_grain", "unsupported_calculation"}


def classify_issues(issues: list[QueryIssue]) -> str:
    """把校验 issue 归类到提取状态（unsupported 优先于 unknown_value，PRD §5.2）。"""
    codes = {i.code for i in issues}
    if not codes:
        return "ok"
    if codes & _UNSUPPORTED_CODES:
        return "unsupported"
    if codes & _UNKNOWN_CODES:
        return "unknown_value"
    return "unsupported"

"""业务目录：启动时用只读元信息生成"模型可用的合法取值"（PRD §6）。

目录只描述**结构**（有哪些公司/指标/单位/期间/明细维度），不保证每家公司每期都有值；
实际数据缺失仍在查询后检查。目录带版本号，供 references/provenance 记录。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from agent.tools.db import query_df

METRICS_PATH = Path("semantic/metrics.yaml")

#: 公司别名（中文名/常见写法 → 规范 company_id）。只做明确别名，不做模糊匹配。
COMPANY_ALIASES: dict[str, str] = {
    "联想": "Lenovo", "联想集团": "Lenovo", "lenovo": "Lenovo",
    "惠普": "HP", "hp": "HP", "hp inc.": "HP", "hp inc": "HP",
    "戴尔": "Dell", "dell": "Dell", "dell technologies": "Dell",
    "dell technologies inc.": "Dell",
}


class BusinessCatalog:
    """合法公司/指标/单位/期间/明细维度的只读目录。"""

    def __init__(self, *, companies: list[str], company_aliases: dict[str, str],
                 metrics: dict[str, dict], metric_aliases: dict[str, str],
                 periods_fiscal: dict[str, set[tuple[int, int]]],
                 periods_calendar: dict[str, set[str]],
                 regions: list[str], product_lines: list[str],
                 detail_metrics: set[str]) -> None:
        self.companies = companies
        self.company_aliases = company_aliases
        self.metrics = metrics
        self.metric_aliases = metric_aliases
        self.periods_fiscal = periods_fiscal
        self.periods_calendar = periods_calendar
        self.regions = regions
        self.product_lines = product_lines
        self.detail_metrics = detail_metrics

    # ---- 构建 ----
    @classmethod
    def build(cls, db_path: str, metrics_path: Path = METRICS_PATH) -> "BusinessCatalog":
        raw = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))["metrics"]
        metrics = {m["name"]: dict(m) for m in raw}

        comp_df = query_df("SELECT company_id, name FROM companies ORDER BY company_id", db_path)
        companies = [str(r.company_id) for r in comp_df.itertuples(index=False)]
        company_aliases = {"lenovo": "Lenovo", "hp": "HP", "dell": "Dell"}
        for r in comp_df.itertuples(index=False):
            cid = str(r.company_id)
            company_aliases[cid.lower()] = cid
            if r.name:
                company_aliases[str(r.name)] = cid
                company_aliases[str(r.name).lower()] = cid
        for alias, cid in COMPANY_ALIASES.items():
            if cid in companies:
                company_aliases.setdefault(alias, cid)

        metric_aliases: dict[str, str] = {}
        for name, m in metrics.items():
            metric_aliases[name] = name
            metric_aliases[name.lower()] = name
            lbl = m.get("label_zh")
            if lbl:
                metric_aliases[str(lbl)] = name

        periods_fiscal: dict[str, set[tuple[int, int]]] = {}
        periods_calendar: dict[str, set[str]] = {}
        p_df = query_df("SELECT company_id, fiscal_year, fiscal_quarter, calendar_quarter "
                        "FROM periods", db_path)
        for r in p_df.itertuples(index=False):
            cid = str(r.company_id)
            periods_fiscal.setdefault(cid, set()).add((int(r.fiscal_year), int(r.fiscal_quarter)))
            if r.calendar_quarter:
                periods_calendar.setdefault(cid, set()).add(str(r.calendar_quarter))

        regions = [str(x) for x in query_df(
            "SELECT DISTINCT region FROM facts_detail WHERE region IS NOT NULL "
            "ORDER BY region", db_path)["region"]]
        product_lines = [str(x) for x in query_df(
            "SELECT DISTINCT product_line FROM facts_detail WHERE product_line IS NOT NULL "
            "ORDER BY product_line", db_path)["product_line"]]

        detail_metrics = {name for name, m in metrics.items()
                          if {"region", "product_line"} & set(m.get("dims") or [])}

        return cls(companies=companies, company_aliases=company_aliases, metrics=metrics,
                   metric_aliases=metric_aliases, periods_fiscal=periods_fiscal,
                   periods_calendar=periods_calendar, regions=regions,
                   product_lines=product_lines, detail_metrics=detail_metrics)

    # ---- 查询 ----
    def resolve_metric(self, name: str) -> str | None:
        if not name:
            return None
        return self.metric_aliases.get(name) or self.metric_aliases.get(str(name).lower())

    def resolve_company(self, name: str) -> str | None:
        if not name:
            return None
        if name in self.companies:
            return name
        return self.company_aliases.get(name) or self.company_aliases.get(str(name).lower())

    def metric_unit(self, name: str) -> str:
        return str(self.metrics[name].get("unit", ""))

    def metric_type(self, name: str) -> str:
        return str(self.metrics[name].get("type", "base"))

    def catalog_version(self) -> str:
        payload = {
            "companies": self.companies,
            "periods_fiscal": {c: sorted(v) for c, v in sorted(self.periods_fiscal.items())},
            "periods_calendar": {c: sorted(v) for c, v in sorted(self.periods_calendar.items())},
            "metrics": {n: {"type": m.get("type"), "unit": m.get("unit"),
                            "dims": sorted(m.get("dims") or []), "label_zh": m.get("label_zh")}
                        for n, m in sorted(self.metrics.items())},
            "regions": self.regions,
            "product_lines": self.product_lines,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

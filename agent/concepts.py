"""概念字典加载器与自动推送（交接 handoff_concept_dictionary_verifier.md §3）。

- config/concepts.yaml 为唯一来源（版本控制下），系统提示、工具说明、结果渲染共用。
- concepts_for(tool, args, fields)：按工具名/指标名/返回字段确定映射，相关概念随工具结果返回。
- base_block()：每次运行自动提供的精简基础块（≤1000 中文字符），财年映射由公司配置生成。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONCEPTS_PATH = Path("config/concepts.yaml")

# 概念 → 精简一句话（进模型可见的口径块，控制预算）
BRIEF = {
    "yoy": "同比=对上年同一期间；环比≠同比",
    "qoq": "环比=对上一连续财季（Q1 的上季为上财年 Q4）",
    "avg_balance": "平均余额=(期初+期末)/2；单期调用工具已自动前补上季",
    "dio": "DIO=平均存货/单季COGS×实际天数（平均存货在分子）",
    "dso": "DSO=平均应收/单季收入×实际天数",
    "dpo": "DPO=平均应付/单季COGS×实际天数（近似口径）",
    "ccc": "CCC=DIO+DSO−DPO，不单独证明现金流因果",
    "margin": "利润率=相应利润/同期间收入（比率≠金额）",
    "pct_vs_pp": "15%→16% 是 +1pp，相对变化 +6.67%（百分比≠百分点）",
    "quarter_vs_cum": "单季≠累计（Q2≠H1）；余额类无累计",
    "z_hist": "z/分位=当前环比 vs 历年同财季环比（标 n）；异常≠根因",
    "peer": "同行比较=同自然季度环比；截止日不同，同向≠同因",
}

# 各工具默认绑定的概念（§3.3 表）
TOOL_CONCEPTS = {
    "variance": ["yoy", "qoq", "pct_vs_pp"],
    "peer_compare": ["qoq", "peer"],
    "seasonal_check": ["qoq", "z_hist"],
    "working_capital": ["avg_balance", "dio", "dso", "dpo", "ccc"],
}


@lru_cache(maxsize=1)
def load() -> dict:
    data = yaml.safe_load(CONCEPTS_PATH.read_text(encoding="utf-8"))
    return {"version": data["version"],
            "concepts": {c["id"]: c for c in data["concepts"]}}


def version() -> str:
    return str(load()["version"])


def concepts_for(tool: str, fields: set[str] | None = None) -> list[str]:
    """确定性映射：工具名 + 返回字段 → 绑定的概念 id 列表（去重、保序）。"""
    ids = list(TOOL_CONCEPTS.get(tool, []))
    if tool == "query_metric":
        ids.append("pct_vs_pp")
        if fields and any(f in ("gross_profit", "revenue", "operating_income") for f in fields):
            ids.append("margin")
        if fields and any(f in ("inventory", "accounts_receivable", "accounts_payable",
                                "cash", "total_assets") for f in fields):
            ids.append("quarter_vs_cum")
    return sorted(set(ids))


def base_block(fy_map: dict[str, str] | None = None) -> str:
    """基础口径块（每次运行自动提供，≤1000 字符）。fy_map: 公司 → 财年截止描述。"""
    lines = [
        f"【概念口径 v{version()}】以下定义与本 run 所有工具结果绑定，以工具返回的口径 metadata 为准：",
    ]
    for cid in ("yoy", "qoq", "pct_vs_pp", "avg_balance", "dio", "dso", "dpo",
                "ccc", "quarter_vs_cum", "z_hist", "peer"):
        lines.append(f"- {BRIEF[cid]}")
    if fy_map:
        lines.append("- 财年映射：" + "；".join(f"{k} 财年止 {v}" for k, v in fy_map.items()))
    lines.append("- 数据边界：facts_detail 合成明细 synthetic=TRUE，不代表真实分部事实；"
                 "来源未确认时标 unknown。")
    return "\n".join(lines)[:1000]


def brief_for(ids: list[str]) -> list[str]:
    """概念 id → 一句话列表（进工具结果的口径块）。"""
    return [f"{cid}: {BRIEF.get(cid, load()['concepts'].get(cid, {}).get('definition', ''))}"
            for cid in ids]


def concept_meta(ids: list[str], tool: str) -> dict:
    """写进 ResultStore.metadata 的字典信息（V4.47 导出与 trace 用）。"""
    return {"dictionary_version": version(), "tool": tool, "concept_ids": list(ids)}

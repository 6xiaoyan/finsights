"""P4.1：工具定义（plan 6.4）——pydantic 参数模型 + JSON schema + "何时使用"描述。

约束：
- 任何工具参数中都不允许出现 db/as_of（运行时固定，模型不可改，V4.5）。
- 描述必须写清"何时使用/何时不用"（V4.4）。
- todo_write/final_answer 在 P4.2 注册（本文件 LOOP_ARGS_MODELS）；load_skill 在 P4.3，forecast 在 P6。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from eval.schema import Claim
from semantic.compiler import MetricRequest


class RunSqlArgs(BaseModel):
    sql: str = Field(description="单条只读 SELECT 语句；其他语句一律被拒绝")


class CalcArgs(BaseModel):
    expression: str = Field(
        description="算术表达式，引用结果 store：如 r3.inventory[Lenovo,FY24Q2] / "
                    "r3.inventory[Lenovo,FY23Q2] - 1。只允许 + - * / % ** 和 abs/min/max/round")
    refs: list[str] = Field(default_factory=list, description="用到的 rid 列表（可留空，自动识别）")


class VarianceArgs(BaseModel):
    metric: str = Field(description="指标名（见 list_metrics）")
    company: str = Field(description="公司：Lenovo/HP/Dell")
    period: str = Field(description="目标期间，财年写法 FY2024Q2 或 FY24Q2")
    basis: Literal["yoy", "qoq", "both"] = Field(default="both", description="同比/环比/两者")


class SeasonalCheckArgs(BaseModel):
    metric: str
    company: str
    period: str = Field(description="目标期间（财年写法），与历年同季度比较")


class PeerCompareArgs(BaseModel):
    metric: str
    calendar_quarter: str = Field(description="自然季度，如 2024Q3（三家公司同一自然季度对比）")


class WorkingCapitalArgs(BaseModel):
    company: str
    periods: list[str] = Field(
        description="期间列表，如 [\"FY24Q1\",\"FY24Q2\"]；也接受区间单项 [\"FY24Q1-FY25Q4\"]")


class CheckIdentitiesArgs(BaseModel):
    company: str
    periods: list[str] = Field(description="同 working_capital 的 periods")


class GetFilingNotesArgs(BaseModel):
    company: str
    period: str = Field(description="财报附注所属期间（财年写法 FY24Q1）")


class RecallArgs(BaseModel):
    result_id: str
    offset: int = Field(default=0, description="从第几行开始取回完整结果")


class SearchDisclosureArgs(BaseModel):
    query: str = Field(description="关键词短语（中文或英文），如 渠道库存 / destocking / 重组")
    company: str | None = Field(default=None, description="限定公司 Lenovo/HP/Dell；不限定则三家")
    period: str | None = Field(default=None, description="限定披露所属财年季度（FY24Q3 写法）")
    top_k: int = Field(default=5, description="返回页数（1–10）")


class EmptyArgs(BaseModel):
    """list_metrics 无参数。"""


# ---- 主循环工具（不进 Result Store）：plan 6.4 的终止/状态类 ----

class TodoItem(BaseModel):
    content: str = Field(description="任务描述")
    status: Literal["pending", "in_progress", "done"] = "pending"


class TodoWriteArgs(BaseModel):
    items: list[TodoItem] = Field(description="完整的新计划列表（整体替换旧列表）")


class FinalAnswerArgs(BaseModel):
    answer_md: str = Field(description="中文结论（Markdown）。正文中出现的每个数字都必须有一条对应 claim；"
                                       "数字保留足够精度（照抄工具有效位），舍入超过 0.5% 会被打回")
    claims: list[Claim] = Field(default_factory=list,
                                description="与正文数字一一对应：text=正文中的数字上下文，value/unit/ref(result_id)。"
                                            "只 claim 正文出现的数字；置信度等元数字不需要数据 claim")
    status: Literal["answered", "clarify", "refuse"] = "answered"


class LoadSkillArgs(BaseModel):
    name: str = Field(description="skill 名称（见 system prompt 的可用 skills 列表）")


# 描述统一包含"何时使用"（V4.4 关键词测试）
DESCRIPTIONS: dict[str, str] = {
    "list_metrics": "返回指标目录（名称、中文名、类型、单位、可用维度）。何时使用：开始分析前、"
                    "或不确定指标名/单位时。",
    "query_metric": "语义层取数首选工具：按指标/公司/期间取数，SQL 由代码编译生成。何时使用："
                    "任何标准取数需求。period_from/period_to 用 FY24Q1（财年）或 2024Q1（自然季度），"
                    "period_basis=calendar 时按自然季度。",
    "run_sql": "兜底工具：直接执行只读 SELECT（自动 LIMIT 200）。何时使用：只在 query_metric "
               "无法表达所需查询时使用，优先用 query_metric。地区/产品线等明细分组同样属于 "
               "query_metric 的 group_by 能力，不要手写 facts_detail 的 SQL；也不要查询 "
               "variance_results 之类不存在的表——分析工具的结果用 recall 取回。",
    "calc": "安全算术：对已存储结果做四则运算，结果进入 store 并获得新 rid。何时使用：答案中任何"
            "需要派生计算（占比、变化率、差值）的数字都必须用本工具，禁止心算。refs 里给出表达式"
            "用到的 rid 列表。",
    "variance": "同比/环比 + 有明细维度时的贡献度分解（contribution 列已含各分项贡献，"
                "无需再用 calc 逐项重算）。何时使用：回答'某指标为什么变'第一步（量化变化）；"
                "比较两期变化并定位来源。",
    "seasonal_check": "当前变化在历年同季度变化中的分位和 z-score。何时使用：归因时判断"
                      "变化是否属于季节性正常范围（在区间内应回答 no_anomaly）。",
    "peer_compare": "三家公司同一自然季度的对比（值与环比）。何时使用：判断变化是否为行业普遍现象。",
    "working_capital": "DSO/DIO/DPO/CCC（平均余额口径，周转天数=平均余额/流量×实际天数；"
                       "工具自动向前多取一期保证上期余额可用）。"
                       "何时使用：营运资本类问题；口径由代码统一，不要自己另算。",
    "check_identities": "勾稽校验：A=L+E 及各科目分项和。何时使用：怀疑数据口径问题、或归因第④步"
                        "检查对手科目时。",
    "get_filing_notes": "报表附注/口径变更说明。何时使用：归因第⑤步，检查变化是否由口径重分类导致。",
    "recall": "从 Result Store 取回某结果的完整行（不重新查库）。何时使用：上下文里该结果被压缩成"
              "存根、需要看 50 行之后的数据时。",
    "search_disclosure": "检索公开披露正文（业绩公告 MD&A/附注，按页返回原文引用+发布日期）。"
                         "何时使用：需要核对管理层自己如何解释变化（归因第⑤步、事件证据）；"
                         "只返回数据截止日（as_of）前已发布的披露。何时不用：取数字（用 query_metric）；"
                         "没检索到就不要编造披露原文，如实说未找到。",
    "todo_write": "写入或更新任务计划（整体替换）。何时使用：复杂问题第一步拆任务；"
                  "推进中更新完成状态。何时不用：一步就能答完的简单问题。",
    "final_answer": "给出最终答案并结束。何时使用：所有数字都已由数据工具查出或经 calc 计算、"
                    "并且每个数字都在 claims 里引用了 result_id。何时不用：还没查到数据。"
                    "status=clarify 时 answer_md 写澄清问题；无法回答用 refuse。",
    "load_skill": "读取一个 skill 的全文（步骤/判断规则/输出要求）。何时使用：问题命中某个"
                  "skill 的描述场景时，先加载再按其流程做。何时不用：简单取数不需要流程指导。",
}

# 产生 rid 并进入 store 的工具（L3 可压缩白名单，plan 6.6.3）
STORED_TOOLS = ["list_metrics", "query_metric", "run_sql", "calc", "variance",
                "seasonal_check", "peer_compare", "working_capital", "check_identities",
                "get_filing_notes", "search_disclosure"]
READ_ONLY_TOOLS = ["list_metrics", "recall", "query_metric", "variance", "seasonal_check",
                   "peer_compare", "working_capital", "check_identities", "get_filing_notes",
                   "calc", "run_sql", "search_disclosure"]  # 主循环可并发执行（V4.12）

ARGS_MODELS: dict[str, type[BaseModel]] = {
    "list_metrics": EmptyArgs,
    "query_metric": MetricRequest,
    "run_sql": RunSqlArgs,
    "calc": CalcArgs,
    "variance": VarianceArgs,
    "seasonal_check": SeasonalCheckArgs,
    "peer_compare": PeerCompareArgs,
    "working_capital": WorkingCapitalArgs,
    "check_identities": CheckIdentitiesArgs,
    "get_filing_notes": GetFilingNotesArgs,
    "recall": RecallArgs,
    "search_disclosure": SearchDisclosureArgs,
}


# 主循环特有工具（loop.py 直接处理，不走 data.execute）
LOOP_ARGS_MODELS: dict[str, type[BaseModel]] = {
    "todo_write": TodoWriteArgs,
    "final_answer": FinalAnswerArgs,
    "load_skill": LoadSkillArgs,
}


def tool_schemas() -> list[dict]:
    """数据类工具的 OpenAI function-calling 声明（从 pydantic 自动生成，V4.4）。"""
    out = []
    for name, model in ARGS_MODELS.items():
        schema = model.model_json_schema()
        schema.pop("$defs", None)
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": schema,
            },
        })
    return out


def all_tool_schemas() -> list[dict]:
    """主循环用的完整工具声明：数据类 + todo_write + final_answer + load_skill（forecast 在 P6）。"""
    out = tool_schemas()
    for name, model in LOOP_ARGS_MODELS.items():
        schema = model.model_json_schema()
        schema.pop("$defs", None)
        out.append({"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name], "parameters": schema}})
    return out


def all_args_models() -> dict[str, type[BaseModel]]:
    return {**ARGS_MODELS, **LOOP_ARGS_MODELS}


def validate_args(name: str, args: dict) -> BaseModel:
    """参数校验失败返回的异常消息会作为工具错误文本回给模型。"""
    model = ARGS_MODELS.get(name)
    if model is None:
        raise KeyError(f"未知工具: {name}（可用: {', '.join(ARGS_MODELS)}）")
    return model(**args)

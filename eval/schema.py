"""P3：评测数据格式（plan 5.1 / 5.3）——题目与答案的 pydantic 模型。

agent 代码可以 import 本模块（只有格式，无 gold 内容）；gold 只存在于 eval/datasets/*.jsonl，
agent 与 v0 运行时绝不读取（G6）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

Category = Literal["L1", "L2", "L3", "L4", "L5", "L6"]


class Gold(BaseModel):
    value: float
    unit: str                 # usd_mn | usd_100mn | days | pct | ratio
    tolerance: float = 0.005  # 相对容差


class Question(BaseModel):
    id: str
    category: Category
    question: str
    db: str = "base"                       # base 或场景 id（P5）
    as_of: str | None = None               # P6 使用
    gold: Gold | None = None               # L3 的 gold 在 eval/datasets/l3_gold/<db>.json（此处置空）
    gold_sql: str | None = None            # 标准答案 SQL（L1；L2 可为输入取数 SQL）
    gold_method: dict | None = None        # L2：{"fn": "working_capital.ccc", "args": {...}}，V3.2 复算依据
    tags: list[str] = []

    @field_validator("gold_sql")
    @classmethod
    def select_only(cls, v):
        if v is not None:
            head = v.strip().upper()
            if not head.startswith("SELECT") and "WITH" not in head[:20]:
                raise ValueError("gold_sql 必须是只读查询")
        return v


class Claim(BaseModel):
    text: str
    value: float
    unit: str
    ref: str = ""


class Answer(BaseModel):
    answer_md: str
    claims: list[Claim] = []
    status: Literal["answered", "clarify", "refuse"]

"""P3.3：v0 基线 agent（plan 5.5）——最简 text2sql。

只有 run_sql 与 final_answer 两个工具；无语义层、无 skills、无护栏、无压缩。
它就是 PRD 6.8 版本对比表的 v0 行，**不要优化它**。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field

from agent.llm import LLMClient
from eval.schema import Answer, Claim

DB_PATH = Path("data/finsights.duckdb")
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "v0.md"
MAX_ROWS = 200


class RunSqlArgs(BaseModel):
    sql: str = Field(description="单条只读 SQL（SELECT/WITH）。v0 唯一的取数方式。")


class FinalAnswerArgs(BaseModel):
    answer_md: str = Field(description="中文结论，含单位")
    claims: list[Claim] = Field(default_factory=list,
                                description="每个结论数字一条：text/value/unit")
    status: Literal["answered", "clarify", "refuse"] = "answered"


def _tool(name: str, model: type[BaseModel], desc: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": model.model_json_schema()}}


TOOLS = [
    _tool("run_sql", RunSqlArgs, "执行只读 SQL 查数据库。什么时候用：需要任何数据时。什么时候不用：没有 SQL 可查的常识。"),
    _tool("final_answer", FinalAnswerArgs, "给出最终答案并结束。什么时候用：数字已经全部查到并算好。什么时候不用：还没查到数据。"),
]
TOOL_MODELS = {"run_sql": RunSqlArgs, "final_answer": FinalAnswerArgs}


def _execute(tc, con: duckdb.DuckDBPyConnection, stats: dict) -> tuple[str, Answer | None]:
    try:
        args = TOOL_MODELS[tc.name](**json.loads(tc.arguments))
    except KeyError:
        return f"未知工具: {tc.name}", None
    except Exception as e:  # 参数错误以文本返回，循环继续
        return f"参数错误: {type(e).__name__}: {e}", None
    if tc.name == "final_answer":
        return "ok", Answer(answer_md=args.answer_md, claims=args.claims, status=args.status)
    try:
        df = con.execute(args.sql).df()
    except Exception as e:
        stats["sql_errors"] += 1
        return f"SQL 错误: {type(e).__name__}: {e}", None
    n = len(df)
    text = df.head(MAX_ROWS).to_string(index=False)
    if n > MAX_ROWS:
        text += f"\n（截断：共 {n} 行，仅显示前 {MAX_ROWS} 行）"
    return text, None


def run(question: str, llm: LLMClient | None = None, db_path: Path = DB_PATH,
        max_steps: int = 20, limiter=None) -> tuple[Answer, dict]:
    """跑一道题。返回 (Answer, {trace, steps, tool_calls, sql_errors, tokens, latency_s})。"""
    llm = llm or LLMClient()
    con = duckdb.connect(str(db_path), read_only=True)
    messages = [{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                {"role": "user", "content": question}]
    trace: list[dict] = []
    stats = {"steps": 0, "tool_calls": 0, "sql_errors": 0,
             "prompt_tokens": 0, "completion_tokens": 0}
    t_start = time.time()
    final: Answer | None = None
    nudge = 0
    try:
        for step in range(max_steps):
            stats["steps"] = step + 1
            if limiter:
                limiter.wait()
            t0 = time.time()
            res = llm.chat(messages, tools=TOOLS)
            stats["prompt_tokens"] += res.usage.get("prompt_tokens", 0)
            stats["completion_tokens"] += res.usage.get("completion_tokens", 0)
            trace.append({"type": "assistant", "step": step, "content": res.content,
                          "tool_calls": [{"name": t.name, "arguments": t.arguments} for t in res.tool_calls],
                          "usage": res.usage, "latency_ms": int((time.time() - t0) * 1000)})
            messages.append(res.message)
            if res.tool_calls:
                for tc in res.tool_calls:  # 串行执行（模型未承诺并行工具调用）
                    stats["tool_calls"] += 1
                    result_text, final = _execute(tc, con, stats)
                    trace.append({"type": "tool_result", "step": step, "tool": tc.name,
                                  "call_id": tc.id, "result": result_text[:2000],
                                  "latency_ms": 0})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
                if final is not None:
                    break
                continue
            nudge += 1
            if nudge >= 2:
                break  # 预算内没调 final_answer → refuse
            messages.append({"role": "user", "content": "请继续：用 run_sql 查询，并以 final_answer 收尾。"})
    finally:
        con.close()
    if final is None:
        final = Answer(answer_md="（v0：步数用尽或未落入 final_answer，拒答）", claims=[], status="refuse")
    stats["latency_s"] = round(time.time() - t_start, 2)
    return final, {"trace": trace, **stats}

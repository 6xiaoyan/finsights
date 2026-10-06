"""多 Agent 子角色执行器（handoff_multi_agent_attribution_development.md §5/§7）。

同步执行、共享证据 store、独立子上下文；初版无 spawn；子调用计入共享预算并留痕。
结构化输出经 pydantic 校验，协议错误给一次纠错机会，仍失败返回错误交主 Agent。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.analysis_state import AnalysisArtifact, Review, ReviewFinding

ROLE_TOOLS = {
    "planner": [],                                    # 无数据库工具（§5）
    "worker": ["list_metrics", "query_metric", "variance", "working_capital",
               "seasonal_check", "peer_compare", "calc", "recall",
               "check_identities", "run_sql"],
    "reviewer": ["recall", "check_identities"],
}
SUB_MAX_STEPS = {"planner": 4, "worker": 6, "reviewer": 4}


@dataclass
class SubResult:
    ok: bool
    payload: dict | None          # 校验通过的结构化输出
    error: str = ""
    child_steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    events: list[dict] = field(default_factory=list)
    # 收尾轮：步数在取数/计算中用尽时，由程序再给一次"只交协议 JSON"的机会（M2 live 取证）
    finalized: bool = False
    steps_exhausted: bool = False


def _extract_json(text: str) -> dict | None:
    """从回复提取 JSON（```json 块或裸对象）；失败返回 None。"""
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.S)
    raw = m.group(1) if m else (text or "")
    try:
        start = raw.index("{")
        return json.loads(raw[start:raw.rindex("}") + 1])
    except Exception:
        return None


class SubAgentRunner:
    """同步子 Agent 循环：独立子上下文（共享 store），工具经白名单与守卫。"""

    def __init__(self, llm: Any, parent_ctx: Any, role: str,
                 system_prompt: str, max_steps: int | None = None):
        self.llm = llm
        self.parent = parent_ctx
        self.role = role
        self.system = system_prompt
        self.max_steps = max_steps or SUB_MAX_STEPS.get(role, 6)
        self.child = self._child_ctx()

    def _allowed_tools(self) -> list[str]:
        """角色白名单 ∩ 父运行配置的工具暴露集（§5：继承相同 DB/as_of/禁用配置）。"""
        base = list(ROLE_TOOLS[self.role])
        enabled = getattr(self.parent, "enabled_tools", None)
        return [t for t in base if enabled is None or t in enabled]

    def _child_ctx(self) -> Any:
        from agent.tools.data import ToolContext
        allowed = self._allowed_tools()
        return ToolContext(db_path=self.parent.db_path, store=self.parent.store,
                           cfg=self.parent.cfg, as_of=self.parent.as_of,
                           enabled_tools=allowed)

    def run(self, user_content: str) -> SubResult:
        from agent.tools import data as tool_data
        from agent.tools.base import DESCRIPTIONS, all_tool_schemas
        allowed = self._allowed_tools()
        schemas = [sc for sc in all_tool_schemas() if sc["function"]["name"] in allowed]
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": user_content}]
        events: list[dict] = []
        in_tok = out_tok = 0
        requests = 0                # 实际发出的模型请求数＝消耗的轮数（含收尾轮）
        correction_used = False
        payload: dict | None = None
        exhausted = False           # 收尾前一直在调用工具 → 没有自行交付协议 JSON
        finalized = False           # 结果是在"预留的最后一轮"里交付的
        # 最新裁定（2026-10-06）：每轮＝一次模型请求，无工具输出、协议纠错和收尾请求都算一轮；
        # 额外收尾轮必须在 max_steps 之内——最后一轮不再开放工具，只要求交付已有证据与缺口，
        # 不能用隐藏的 +1 追加请求绕过上限（旧写法在循环外补一次 chat，实际用了 max_steps+1 轮）。
        last_round = self.max_steps - 1
        for step in range(self.max_steps):
            delivering = step == last_round
            if delivering:
                messages.append({"role": "user", "content":
                    "现在不要再调用任何工具。基于上面已经真实拿到的工具结果，只输出一个符合协议的 "
                    "JSON 对象：已完成的数字写进 facts/calculations（rid 必须是你确实拿到过的，"
                    "不得凭记忆补数），没做完的写进 missing_inputs 与 limitations，"
                    "summary 说明当前进展与缺口。"})
                events.append({"type": "subagent_finalize", "role": self.role,
                               "step": step, "steps_exhausted": exhausted})
            resp = self.llm.chat(messages, tools=None if delivering else (schemas or None),
                                 role="agent")
            requests += 1
            # §9/§10：子 Agent 的 API 重试同样要留痕（共享客户端每次 chat 重置该列表）
            for ev in getattr(self.llm, "last_call_events", []) or []:
                events.append({**ev, "role": self.role, "step": step,
                               **({"phase": "finalize"} if delivering else {})})
            u = resp.usage or {}
            in_tok += u.get("prompt_tokens", 0)
            out_tok += u.get("completion_tokens", 0)
            if delivering:
                payload = _extract_json(resp.content or "")
                finalized = payload is not None
                break
            if resp.tool_calls:
                exhausted = True
                calls = []
                for tc in resp.tool_calls:  # §4：按调用顺序执行，每 call 必有结果
                    try:
                        args = json.loads(tc.arguments or "{}")
                    except Exception:
                        args = {}
                    out = tool_data.execute(tc.name, args, self.child, step=step)
                    calls.append({"tool_call_id": tc.id,
                                  "content": out.text if not out.ok else
                                  (out.text[:2000] if out.text else "（结果见 rid）"),
                                  "rid": out.rid})
                    events.append({"type": "tool_result", "role": self.role, "step": step,
                                   "tool": tc.name, "ok": out.ok, "rid": out.rid,
                                   "call_id": tc.id,                  # §10：调用可一一对应
                                   "args_head": (tc.arguments or "")[:300],
                                   "result_head": (out.text or "")[:200]})
                    messages.append({"role": "assistant", "content": "",
                                     "tool_calls": [{"id": tc.id, "type": "function",
                                                     "function": {"name": tc.name,
                                                                  "arguments": tc.arguments}}]})
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": calls[-1]["content"]})
                continue
            # 无工具调用：尝试解析结构化输出
            exhausted = False
            text = resp.content or ""
            payload = _extract_json(text)
            if payload is None and not correction_used:
                correction_used = True
                messages.append({"role": "assistant", "content": text[:500]})
                messages.append({"role": "user",
                                 "content": "输出不是合法 JSON。请只输出一个符合协议的 JSON 对象，"
                                            "不要包含其他文字。仍无法给出则输出 {\"error\": \"...\"}。"})
                events.append({"type": "protocol_correction", "role": self.role, "step": step})
                continue
            break
        error = ""
        if payload is None:
            error = ("子循环步数耗尽且收尾仍无合法 JSON" if exhausted
                     else "收尾输出仍不是合法 JSON")
        elif "error" in payload:
            error = str(payload.get("error"))[:200] or "子 Agent 报告无法完成"
        # child_steps 就是实际发出的模型请求数（收尾轮已计入），不超过 max_steps——最新裁定要求
        # 每轮＝一次模型请求，收尾轮在预算之内，不存在隐藏的 +1 追加请求。
        return SubResult(ok=payload is not None and "error" not in payload,
                         payload=payload, error=error,
                         child_steps=requests,
                         prompt_tokens=in_tok, completion_tokens=out_tok, events=events,
                         finalized=finalized, steps_exhausted=exhausted)


# ---- 结构化输出协议 ----

class PlanProposal(BaseModel):
    revision_reason: str = ""
    nodes: list[dict]

    def validate_semantics(self, max_nodes: int = 0):
        """只校验补丁的**局部**约束（M2 复审 §2）：node_id 非空、本批不重复、不自依赖。

        补丁式修订常常只列改动节点（例如把 n2 的口径细化，depends_on 沿用已在图里的 n1），
        此时 n1 不在本次 nodes 中——若在这里查"依赖必须存在于本次列表"就会把合法补丁判成
        `依赖 n1 不存在`。合并后的整图校验（依赖存在、节点数、自依赖、成环）统一交给
        `AnalysisState.save_plan()`，避免两套互相冲突的校验。
        """
        ids = [n.get("node_id") for n in self.nodes]
        if len(ids) != len(set(ids)) or any(not i for i in ids):
            raise ValueError("plan_invalid: node_id 缺失或本批重复")
        for n in self.nodes:
            if n.get("node_id") in (n.get("depends_on") or []):
                raise ValueError(f"plan_invalid: 自依赖 {n.get('node_id')}")


class ArtifactProposal(BaseModel):
    node_id: str
    execution_status: str = "completed"
    facts: list[dict] = []
    calculations: list[dict] = []
    interpretations: list[str] = []
    hypotheses: list[dict] = []
    limitations: list[str] = []
    missing_inputs: list[str] = []
    summary: str = ""


class ReviewProposal(BaseModel):
    target_type: str
    target_id: str
    verdict: str
    findings: list[dict] = []
    unresolved_items: list[str] = []

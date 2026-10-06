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

    def _child_ctx(self) -> Any:
        from agent.tools.data import ToolContext
        allowed = list(ROLE_TOOLS[self.role])
        return ToolContext(db_path=self.parent.db_path, store=self.parent.store,
                           cfg=self.parent.cfg, as_of=self.parent.as_of,
                           enabled_tools=allowed)

    def run(self, user_content: str) -> SubResult:
        from agent.tools import data as tool_data
        from agent.tools.base import DESCRIPTIONS, all_tool_schemas
        allowed = ROLE_TOOLS[self.role]
        schemas = [sc for sc in all_tool_schemas() if sc["function"]["name"] in allowed]
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": user_content}]
        events: list[dict] = []
        in_tok = out_tok = 0
        correction_used = False
        payload: dict | None = None
        for step in range(self.max_steps):
            resp = self.llm.chat(messages, tools=schemas or None, role="agent")
            u = resp.usage or {}
            in_tok += u.get("prompt_tokens", 0)
            out_tok += u.get("completion_tokens", 0)
            if resp.tool_calls:
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
                                   "tool": tc.name, "ok": out.ok, "rid": out.rid})
                    messages.append({"role": "assistant", "content": "",
                                     "tool_calls": [{"id": tc.id, "type": "function",
                                                     "function": {"name": tc.name,
                                                                  "arguments": tc.arguments}}]})
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": calls[-1]["content"]})
                continue
            # 无工具调用：尝试解析结构化输出
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
        return SubResult(ok=payload is not None and "error" not in payload,
                         payload=payload, child_steps=step + 1 if 'step' in dir() else 0,
                         prompt_tokens=in_tok, completion_tokens=out_tok, events=events)


# ---- 结构化输出协议 ----

class PlanProposal(BaseModel):
    revision_reason: str = ""
    nodes: list[dict]

    def validate_semantics(self, max_nodes: int):
        ids = [n.get("node_id") for n in self.nodes]
        if len(ids) != len(set(ids)) or any(not i for i in ids):
            raise ValueError("plan_invalid: node_id 缺失或重复")
        if len(self.nodes) > max_nodes:
            raise ValueError(f"plan_invalid: 节点数超上限 {max_nodes}")
        idset = set(ids)
        for n in self.nodes:
            for d in n.get("depends_on") or []:
                if d not in idset:
                    raise ValueError(f"plan_invalid: 依赖 {d} 不存在")
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

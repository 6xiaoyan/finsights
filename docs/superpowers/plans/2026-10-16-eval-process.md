# 运行包适配、微观埋点与角色独立评测实施计划（Agent B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让一次 multi_agent/text2sql 运行可以离线复现地评估——把运行包规范化为公共类型并关联计划/节点/角色调用/查询/结果/审查版本，补齐可见上下文与 Text2SQL 观测埋点，独立评测 Planner/Worker/Reviewer 与查询链路，输出逐题与汇总报告。

**Architecture:** B 独占 `eval/agent_process/`（适配、监控、角色评测、查询诊断、报告、CLI），复用 `eval/financial_e2e.adapter` 的运行包读取与 `eval/financial_e2e.judge` 的 Judge 协议/离线替身，不另造。`agent/` 只加四处**纯观测**埋点（`tools/data.py`、`subagents.py`、`multi.py`、`cli.py`），不改模型输入、工具返回文本、调度或轮次。

**Tech Stack:** Python ≥3.11、pydantic v2、pandas、pytest；复用 `agent.llm`、`agent.query.*`、`eval.contracts.case`。

**Spec:** `docs/superpowers/specs/2026-10-16-eval-process-design.md`（PRD：`docs/prd-agent-evaluation.md` §4/§7/§8/§9）

**Baseline:** 分支 `feat/eval-process`，worktree `D:\finsights\finsights-dev-eval-process`，从 `42baa3f` 切出。固定回放包：`runs/cli-20261016-134035`。

## Global Constraints

- 埋点**只允许观测性改动**：不改变模型输入、工具返回值（`Outcome.text`/渲染文本）、Agent 调度、轮次、评分逻辑。
- 优先复用现有事件与结果，**不重复存整份大对象**：`rows`/`sql`/dataframe 留在 `results.json`，靠 `result_ids` 引用。
- 上下文快照**只记录实际发给模型的内容**；密钥类字段必须脱敏（`sk-…`、`Bearer …`、`api_key|auth_token|authorization|token`）。
- 本阶段**不调用真实 API**；`--judge` 默认 `fake`（离线）。模拟/固定样例结论一律标 `pipeline_check`。
- 历史包缺失的新字段一律 `null`/`unknown`，**不得填 0**；跳过不计成功；缺 usage 不填零。
- 不修改 `eval/contracts/`、`eval/financial_e2e/`、`eval/schema.py`、`eval/runner.py`、`eval/graders/`、`eval/generators/`；不改 `agent/query/*`。
- 不改 Agent 任务拆分策略、角色提示词、查询策略、轮次默认值、审核策略。
- 不 push、不合并 main；只提交本地分支。
- CLI 约定同 A：`__main__.py` → `cli.main(sys.argv)`；`main` 先 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；`args.func(args)` 返回退出码。
- PRD §9.3 的 **M3「真实执行贯通」不得标记为完成**。

## Review Focus

以下五类输入/失败模式是 spec 隐含、最容易被实现者踩中的，每个都在其归属任务里有对应测试：

1. **multi 包 `trace.jsonl` 末尾有两行 `type=result`**（`multi.py` 一行 + `cli.py` 一行）。取错行会让终态与 `run_meta.status` 打架。期望：取**最后一行**为终态，且把行数差异记录出来，不隐藏。
2. **同一 QueryPlan 复用 rid**（真实运行 11 次调用只得 9 个 rid）。把 rid 当调用键会数错「哪些查询没有结果」。期望：计数一律按调用级 `query_trace_id`，rid 只作结果引用。
3. **`tool_skipped` 的 `ok` 是 `null`**。按 `ok is not False` 统计会把跳过算成成功。期望：跳过进 `skipped`，不进 `ok`，`ok` 缺失进 `unknown_status`。
4. **`empty` 结果 `ok=True` 但 `rid=None`**（`_h_query_financial_data` 里 `ok = status in ("ok","empty")`）。按 `ok==True` 推「有数据」会把空结果算成取到数。期望：`empty` 有独立原因码，`result_ids` 为空。
5. **`created_step` 恒为 0**（`executor.py` 里 `store.put(..., step=0)` 硬编码），导致 `initial_packet` 对查询结果也成立（真实运行 16 条里 10 条）。期望：适配层**不使用** `initial_packet` 判定，改用 `tool` 名；该缺陷登记为已知限制，不修。

---

## 文件结构

**新增（B 独占）**
- `eval/agent_process/__init__.py` — 公共导出
- `eval/agent_process/__main__.py` — `python -m eval.agent_process`
- `eval/agent_process/schema.py` — B 自有类型（`ProcessPackage`、`RoleCall`、`QueryRecord`、`PlanView`、`ArtifactView`、`ReviewView`、`RoleScore`）
- `eval/agent_process/adapt.py` — 运行包 → 规范化对象；`FIELD_COMPAT` 兼容表；`load_process_package()`
- `eval/agent_process/metrics.py` — 运行监控统计
- `eval/agent_process/query.py` — 微观查询链路诊断
- `eval/agent_process/roles/__init__.py`
- `eval/agent_process/roles/common.py` — Judge 装配、证据读取、评分依据标注
- `eval/agent_process/roles/planner.py`、`worker.py`、`reviewer.py`
- `eval/agent_process/samples/planner.jsonl`、`worker.jsonl`、`reviewer.jsonl`
- `eval/agent_process/report.py` — 逐题报告 + 汇总报告
- `eval/agent_process/cli.py` — 6 子命令
- `tests/test_agent_process.py`、`tests/test_process_instrumentation.py`
- `docs/eval_process_delivery.md`

**修改（`agent/`，纯观测）**
- `agent/tools/data.py` — `Outcome.query_trace_id` 字段；`_h_query_financial_data` 写 `ctx.stats["query_trace"]`
- `agent/subagents.py` — 子 `tool_result` 事件增 `query_trace_id` 键
- `agent/multi.py` — `_run_role` 增发 `subagent_context` 事件
- `agent/cli.py` — `_export_package` 增写两个 jsonl 与 `run_meta` 键

---

## Task 1: 运行包适配与字段兼容表

**Files:**
- Create: `eval/agent_process/__init__.py`, `eval/agent_process/schema.py`, `eval/agent_process/adapt.py`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `eval.financial_e2e.adapter.load_run_artifact/load_trace_events/run_question/parse_claims`；`eval.contracts.case.RunArtifact/TraceEvent`
- Produces:
  - `eval.agent_process.schema.ProcessPackage`（pydantic `BaseModel`）字段：`run_id:str`、`case_id:str`、`run_dir:str`、`final_status:str`、`final_answer:str|None`、`verified:bool|None`、`contract:dict|None`、`latest_revision:int|None`、`plans:list[PlanView]`、`artifacts:list[ArtifactView]`、`reviews:list[ReviewView]`、`answer_candidate:dict|None`、`role_calls:list[RoleCall]`、`query_records:list[QueryRecord]`、`usage:dict`、`run_meta:dict`、`diagnostics:dict`、`unknown_fields:list[str]`
  - `PlanView{revision:int, plan_id:str, revision_reason:str, created_ts:str, nodes:list[PlanNodeView]}`
  - `PlanNodeView{node_id, goal, depends_on:list[str], input_refs:list[str], completion_criteria, required_for_answer:bool, execution_status, attempt:int, latest_artifact_id:str|None, review_status}`
  - `ArtifactView{artifact_id, node_id, plan_revision:int, attempt:int, execution_status, version:str, stale:bool, evidence_refs:list[str], facts:list[dict], calculations:list[dict], summary, declared_status, completion_report:list[dict], required_missing_inputs:list[str], optional_missing_inputs:list[str], next_actions:list[str], limitations:list[str]}`
  - `ReviewView{review_id, target_type, target_id, target_version, verdict, findings:list[dict], unresolved_items:list[str], created_ts, round_index:int|None}`
  - `RoleCall{agent_call_id, role, main_step:int|None, plan_revision:int|None, node_id:str|None, target_type:str|None, target_id:str|None, target_version:str|None, ok:bool|None, child_steps:int|None, prompt_tokens:int|None, completion_tokens:int|None, latency_ms:int|None, error:str|None, query_trace_ids:list[str], context_refs:dict|None}`
  - `QueryRecord`（同 spec §5.3 字段，外加以下四项——pre-flight 判定的必需补齐，见 ledger）：
    - `source: Literal["run_package","derived"]`
    - `columns: list[str] = []`（从被引用的 `results.json` 行的键派生；取不到则空表）
    - `agent_call_id: str | None`、`node_id: str | None`、`role: str | None`（查询调用 ↔ 角色调用的连接）
    - `linkage: Literal["query_trace_id","derived","unknown"]`（用哪条路径连上的；老包为 `unknown`）
  - `adapt.load_process_package(run_dir: str|Path, case_id: str = "") -> ProcessPackage`
  - `adapt.artifact_version(plan_revision:int, attempt:int) -> str`（复刻 `multi._artifact_version` 的 `f"rev{plan_revision}-attempt{attempt}"`，不 import agent 内部）
  - `adapt.FIELD_COMPAT: list[dict]`，每项 `{area, source, normalized, status}`，`status ∈ {available, derived, new_instrumentation, unknown_legacy}`
  - `query_records` 在无 `analysis/query_trace.jsonl` 时**从 `results.json` 派生**（`source="derived"`），字段缺失置 `None`

- [ ] **Step 1: 写失败测试**

`tests/test_agent_process.py`，文件顶部 helper（后续任务复用）：

```python
def _write_run_package(tmp_path, *, status="refuse", with_instrumentation=False, two_result_lines=True):
    """构造一个最小 multi_agent/text2sql 运行包，形状对齐 runs/cli-20261016-134035。"""
```

测试用例与断言（名字即契约）：
- `test_loads_plan_nodes_artifacts_reviews`：`plans[0].nodes` 有 2 个节点、`latest_revision == 1`；`artifacts[0].version == "rev1-attempt1"`；`reviews[0].verdict == "needs_revision"`。
- `test_role_calls_keyed_by_agent_call_id`：`role_calls` 里 `agent_call_id == "worker@2#n1"` 的项 `role=="worker"`、`node_id=="n1"`、`plan_revision==1`。
- `test_reviewer_call_carries_target_version`：reviewer 调用项 `target_id=="art1"`、`target_version=="rev1-attempt1"`。
- `test_missing_instrumentation_is_none_not_zero`：`with_instrumentation=False` 时 `query_records[0].query_trace_id is None`、`.call_index is None`、`.attempts is None`、且 `"analysis/query_trace.jsonl" in pkg.unknown_fields`。
- `test_final_status_follows_run_meta_not_node_states`：包内节点 `completed`、`semantic_review_status=="accepted"`，但 `pkg.final_status == "refuse"` 且 `pkg.final_answer is None`（**Review Focus：不因节点 completed 改记成功**）。
- `test_multi_package_two_result_lines_uses_terminal`：`two_result_lines=True` 时 `pkg.diagnostics["result_lines"] == 2`，且 `final_status` 取自最后一行（与 `run_meta.status` 一致）。（**Review Focus 1**）
- `test_initial_packet_flag_not_trusted`：构造 `created_step==0` 且 `initial_packet==True` 的查询结果，断言派生出的 `query_records` 仍按 `tool=="query_financial_data"` 归类为查询链路，且 `initial_packet` 不出现在任何判定里。（**Review Focus 5**）
- `test_field_compat_covers_every_normalized_field`：`FIELD_COMPAT` 中 `normalized` 去重后的集合 ⊇ `ProcessPackage.model_fields` 键，且每条 `status` 必须在四值枚举内。
- `test_query_record_links_to_role_call_via_query_trace_id`：子 `tool_result` 带 `query_trace_id="q1"` 且 `parent_call_id="worker@2#n1"` → 该 `QueryRecord` 的 `agent_call_id=="worker@2#n1"`、`node_id=="n1"`、`role=="worker"`、`linkage=="query_trace_id"`。
- `test_query_record_linkage_unknown_without_query_trace_id`：老包（无 `query_trace_id`）→ `linkage=="unknown"`、`node_id is None`，**不猜测归属**。
- `test_query_record_columns_from_results_rows`：`results.json` 行含 `region` 列 → `columns` 含 `region`；无对应结果时 `columns == []`。
- `test_absent_usage_not_zero_filled`：`run_meta` 无 `usage` 时 `pkg.usage == {}` 且 `metrics` 不在本任务算（断言 `pkg.usage.get("prompt_tokens") is None`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.agent_process'`

- [ ] **Step 3: 实现 `schema.py` 与 `adapt.py`**

`schema.py` 用 pydantic v2 `BaseModel`（与 `eval/contracts/case.py` 一致）。所有"可缺失"字段用 `X | None = None`，不用 0 默认值。

`adapt.py`：
- `load_process_package` 调 A 的 `load_run_artifact` / `load_trace_events` / `run_question` 拿基础事实，再**自己**读 `analysis/analysis_state.json`（A 不解析它）与 `analysis/query_trace.jsonl`、`analysis/visible_context.jsonl`。
- 缺文件时把路径记进 `unknown_fields`，字段置 `None`。
- `result_lines` 统计 `type=="result"` 的行数；终态取最后一行，与 `run_meta.status` 不一致时写进 `diagnostics["status_mismatch"]`。
- `role_calls` 由 `subagent_start`/`subagent_end` 按 `agent_call_id` 配对；缺 `subagent_end` 的项 `latency_ms=None` 等，不填 0。
- `query_records`：有 `analysis/query_trace.jsonl` 则 `source="run_package"`；否则从 `results.json` 里 `tool=="query_financial_data"` 的记录派生（`query_trace_id` 取 `meta.query_trace_id`，`results.json` 丢 `meta` 故通常为 `None`），`source="derived"`；`plan` 取 `args.plan`；`columns` 取该行 `rows[0]` 的键（无行则为 `[]`）。
- **查询↔角色连接**：先由子 `tool_result` 事件（`tool=="query_financial_data"`）建立 `query_trace_id → parent_call_id` 映射，再用 `parent_call_id` 在 `role_calls` 里取 `role`/`node_id`；命中记 `linkage="query_trace_id"`。老包没有 `query_trace_id` → `linkage="unknown"`、`agent_call_id`/`node_id`/`role` 全 `None`，**不按顺序或文本猜测归属**。
- `artifact_version` 复刻 `f"rev{plan_revision}-attempt{attempt}"`，并在 docstring 注明它与 `agent/multi.py::_artifact_version` 必须保持一致（测试用已知运行包值钉住）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/__init__.py eval/agent_process/schema.py eval/agent_process/adapt.py tests/test_agent_process.py
git commit -m "feat(eval-process): run-package adapter and field compatibility table"
```

---

## Task 2: 运行监控统计

**Files:**
- Create: `eval/agent_process/metrics.py`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `ProcessPackage`（Task 1）
- Produces:
  - `metrics.compute_metrics(pkg: ProcessPackage) -> dict`，返回：
    `{"tool_calls": {"total", "ok", "error", "skipped", "unknown_status", "by_tool": {tool: {"ok","error","skipped"}}}, "exception_types": {name: n}, "retries": {"llm_retry", "protocol_correction", "stop_reject"}, "role_calls": {"total", "by_role": {...}, "pairs_missing_end"}, "rounds": {"main_assistant_events", "main_steps_reported", "subagent_child_steps", "subagent_child_steps_reported"}, "extraction": {"calls_reported", "attempts_seen", "format_retries_seen", "available"}, "result_reads": {"recall_calls", "read_analysis_object_calls", "reviewer_evidence_reads"}, "latency_ms": {"roles_total", "by_role": {...}}, "tokens": {"prompt_tokens", "completion_tokens", "source"}, "cost_usd_estimate": float|None, "denominators": {...}, "discrepancies": [...]}`
  - `metrics.METRICS_VERSION = "process-metrics-v1"`

- [ ] **Step 1: 写失败测试**

- `test_tool_calls_deduped_by_parent_and_call_id`：同一 `tool_result` 行出现两次（模拟转发重复）→ `tool_calls.total` 只计 1。
- `test_tool_skipped_not_counted_as_success`：`tool_skipped` 行（`ok` 为 `None`）→ `skipped==1`、`ok==0`、`unknown_status==1`。（**Review Focus 3**）
- `test_missing_ok_goes_to_unknown_status`：`tool_result` 无 `ok` 键 → `unknown_status` 增 1，`ok`/`error` 不动。
- `test_role_calls_pair_start_and_end`：1 个 start + 0 个 end → `total==1`、`pairs_missing_end==1`，`latency_ms` 不含该项。
- `test_rid_reuse_not_treated_as_call_count`：`results.json` 里 2 条记录同 `id=="r2"` → `query_calls` 按调用计 2、`unique_result_ids==1`。（**Review Focus 2**）
- `test_missing_usage_is_null_not_zero`：`run_meta` 无 `usage` → `tokens.prompt_tokens is None`、`tokens.source == "missing"`，且不出现 `0`。
- `test_subagent_tokens_not_added_to_run_total`：`run_meta.usage.prompt_tokens=1000`、`subagent_end.prompt_tokens=400` → `tokens.prompt_tokens == 1000`（不是 1400），且 `discrepancies` 记录子集关系。
- `test_reported_vs_recomputed_discrepancy_flagged`：`run_meta.multi_agent.subagent_calls=13` 但 trace 里 12 个 `subagent_start` → `discrepancies` 含 `{"field":"subagent_calls","reported":13,"recomputed":12}`。
- `test_cost_estimate_null_when_pricing_absent`：`run_meta.config.llm.pricing_usd_per_mtok` 缺失 → `cost_usd_estimate is None`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k metrics`
Expected: FAIL — `ImportError: cannot import name 'metrics'`

- [ ] **Step 3: 实现 `metrics.py`**

口径按 spec §5.4。去重键：工具 `(parent_call_id or "main", call_id)`；角色 `agent_call_id`。`discrepancies` 逐项写 `{field, reported, recomputed}`。费用只在 `pricing_usd_per_mtok` 有 `input`/`output` 且 token 非 `None` 时算，否则 `None`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k metrics`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/metrics.py tests/test_agent_process.py
git commit -m "feat(eval-process): run monitoring metrics with dedupe and denominators"
```

---

## Task 3: Text2SQL 埋点（query_trace 记录与调用级 id）

**Files:**
- Modify: `agent/tools/data.py`（`Outcome` 定义 ~L59-64；`_h_query_financial_data` ~L434-451）
- Modify: `agent/subagents.py`（子 `tool_result` 事件 ~L262-266）
- Test: `tests/test_process_instrumentation.py`

**Interfaces:**
- Produces:
  - `agent.tools.data.Outcome.query_trace_id: str | None = None`（新字段，默认 `None`；`text`/`rid`/`ok`/`stored` 语义不变）
  - `ToolContext.stats["query_trace"]: list[dict]`（每次 `query_financial_data` 调用追加一项，键见 spec §5.3）
  - 子 `tool_result` 事件（`subagents.py`）在 `query_trace_id` 非 `None` 时带该键
- Consumes: `agent.query.extractor.extract_plan`、`agent.query.executor.execute_plan`（**不改动**）

- [ ] **Step 1: 写失败测试**

`tests/test_process_instrumentation.py`，复用 `tests/conftest.py` 的 `query_db` 夹具与既有 `MockLLM` 写法（参考 `tests/test_query_tool.py`）：

- `test_query_call_records_trace_entry`：`execute("query_financial_data", {"question": "...", "node_task": "..."}, ctx)` 后 `ctx.stats["query_trace"]` 长度 1，且该项含 `query_trace_id=="q1"`、`call_index==0`、`question`、`node_task`、`status=="ok"`、`plan` 非空、`prompt_version`、`catalog_version`、`data_version`、`result_ids` 非空、`sql_chars>0`。
- `test_outcome_carries_query_trace_id`：返回的 `Outcome.query_trace_id == "q1"`。
- `test_outcome_text_unchanged_by_instrumentation`：同一输入下，`Outcome.text` 与关闭埋点时的文本逐字相同（用 `_render_query_result` 直接调用结果比对，或断言 `text` 不以任何新键开头且等于快照字符串）。（**约束：不改工具返回值**）
- `test_second_call_gets_new_query_trace_id`：连续两次不同 question → `query_trace_id` 为 `"q1"`、`"q2"`，`call_index` 0、1。
- `test_non_ok_extraction_records_status_and_no_result_ids`：提取返回 `unknown_value` → 记录 `status=="unknown_value"`、`plan is None`、`result_ids==[]`、`sql_chars is None`（**不伪造 SQL**）。
- `test_empty_result_records_no_result_ids`：查询命中空集 → `status=="empty"`、`result_ids==[]`。（**Review Focus 4**）
- `test_extractor_events_attached_to_record`：`unknown_value` 触发一次模型调用 → 记录里 `attempts==1`、`extractor_events` 含 `{"type":"extractor_call"...}`。
- `test_child_tool_result_event_has_query_trace_id`：用 `SubAgentRunner`（或直接调其事件构造路径）跑一次 `query_financial_data` → 子事件含 `query_trace_id`。
- `test_other_tools_do_not_gain_query_trace_id`：`list_metrics` 的 `Outcome.query_trace_id is None`，其子事件**不含**该键。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_process_instrumentation.py -q`
Expected: FAIL — `AttributeError: 'Outcome' object has no attribute 'query_trace_id'`

- [ ] **Step 3: 实现**

`agent/tools/data.py`：
1. `Outcome` 加字段 `query_trace_id: str | None = None`。
2. `_h_query_financial_data`：进入时 `idx = len(ctx.stats.setdefault("query_trace", []))`、`qtid = f"q{idx + 1}"`；成功/失败两条路径都在返回前 `ctx.stats["query_trace"].append({...})`；返回的 `Outcome(..., query_trace_id=qtid)`。
   - `plan` 取 `extraction.plan or result.plan` 的 `model_dump()`（非 ok 路径也要能拿到 `empty`/`unsupported` 的 plan，取不到就 `None`）。
   - `plan_digest` 取 `result.provenance.get("query_trace_id") or None`（空串转 `None`）。
   - `sql_chars`：仅当存在真实 SQL 时记长度，否则 `None`——**执行器未产生 SQL 时不伪造**。`execute_plan` 不返回 SQL，故用 `result.result_ids` 非空 + `provenance` 有值作为"已执行"证据，`sql_chars` 从 `ctx.store.get(rid).sql` 现取。
   - `_render_query_result` 的调用与文本**一行不改**。
3. `agent/subagents.py`：`events.append({...})` 里加 `**({"query_trace_id": out.query_trace_id} if getattr(out, "query_trace_id", None) else {})`（`out` 仅在非 `extra_tools` 分支存在；`extra_tools` 分支不加）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_process_instrumentation.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 跑既有回归，确认没改行为**

Run: `python -m pytest tests/test_query_tool.py tests/test_query_multi.py tests/test_tools.py tests/test_multi_agent.py -q`
Expected: PASS（全绿，无新增失败）

- [ ] **Step 6: Commit**

```bash
git add agent/tools/data.py agent/subagents.py tests/test_process_instrumentation.py
git commit -m "feat(agent): record query_trace per text2sql call (observation only)"
```

---

## Task 4: 可见上下文埋点

**Files:**
- Modify: `agent/multi.py`（`_run_role` ~L615-651）
- Create: `eval/agent_process/redact.py`（供 `agent/` 与 `eval/` 共用，避免 agent 依赖 eval 之外的路径）
- Test: `tests/test_process_instrumentation.py`

**Interfaces:**
- Produces:
  - `agent.multi._context_refs(user: str, ident: dict) -> dict`，返回 `{"rids":[...], "artifact_ids":[...], "node_ids":[...], "answer_candidate_ids":[...], "target_version":str|None, "contract_data_version":str|None}`
  - `agent.multi._redact_text(text: str) -> str`
  - `trace.jsonl` 新事件 `subagent_context`：`{type, agent_call_id, role, main_step(step), plan_revision, node_id, target_id, target_version, system_prompt_sha256_12, user_sha256_12, user_chars, context_refs, user_preview, user_truncated}`（`ident` 中不存在的键置 `None`）
  - `ToolContext.stats["visible_context"]: list[dict]`——**整份实际内容只存这里**，每项 `{agent_call_id, role, main_step, plan_revision, node_id, target_id, target_version, system_prompt_sha256_12, user_content, user_chars, user_truncated}`（`user_content` 已脱敏；超 64000 字符截断并置 `user_truncated=True`）。trace 事件只带预览，两处不重复存同一内容。
- Consumes: `_run_role` 的既有入参 `sys_prompt`、`user`、`ident`

- [ ] **Step 1: 写失败测试**

- `test_subagent_context_event_emitted_per_role_call`：跑一次 planner 角色调用（mock LLM）→ `ctx.trace.events` 里有 1 个 `type=="subagent_context"`、`role=="planner"`、`agent_call_id` 与对应 `subagent_start` 相同。
- `test_context_refs_extract_rids_and_artifacts`：`_context_refs("... r2 r15 art3 answer-cand7 ...", {})` → `rids == ["r2","r15"]`、`artifact_ids == ["art3"]`、`answer_candidate_ids == ["answer-cand7"]`；顺序按首次出现、去重。
- `test_context_refs_carry_ident_version_fields`：`ident={"target_type":"artifact","target_id":"art1","target_version":"rev1-attempt1"}` → `target_version == "rev1-attempt1"`。
- `test_redact_masks_secrets`：`_redact_text('key sk-abcdefgh12345678 and Bearer abc.def')` 不含 `sk-abcdefgh12345678` 与 `abc.def`，含 `***REDACTED***`。
- `test_context_event_does_not_change_model_input`：mock LLM 记录的 `messages` 在开启/关闭埋点两次运行下**逐字相同**。（**约束：不改模型输入**）
- `test_context_event_preview_is_bounded`：超长 `user` → `user_preview` 长度 ≤1200、`user_truncated is True`、`user_chars` 为真实长度。
- `test_visible_context_stats_holds_full_redacted_content`：`ctx.stats["visible_context"]` 长度 1，其 `user_content` 等于完整 `user`（未截断时），且不含密钥串；trace 事件的 `user_preview` 比 `user_content` 短。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_process_instrumentation.py -q -k context`
Expected: FAIL — `AttributeError: module 'agent.multi' has no attribute '_context_refs'`

- [ ] **Step 3: 实现**

`eval/agent_process/redact.py` 放 `redact_text()` 与 `SECRET_PATTERNS`；`agent/multi.py` 从 `eval.agent_process.redact` import（`agent` 依赖 `eval` 在本仓库已有先例：`eval` 反向依赖 `agent`，此处新增反向依赖需评估——**若判定为不当耦合，改为在 `agent/multi.py` 内联同一实现**，并把两处一致性用一个测试钉住）。Ruling 见 ledger。

在 `_run_role` 中 `ctx.trace.event(type="subagent_start", ...)` **之后**、`t0 = time.perf_counter()` **之前**插入 `subagent_context` 事件，并把整份记录 append 到 `ctx.stats.setdefault("visible_context", [])`（同一处、同一份脱敏结果，只做一次截断与脱敏）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_process_instrumentation.py -q -k context`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑既有回归**

Run: `python -m pytest tests/test_multi_agent.py tests/test_multi_resume.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add eval/agent_process/redact.py agent/multi.py tests/test_process_instrumentation.py
git commit -m "feat(agent): emit subagent_context event with refs (observation only)"
```

---

## Task 5: 运行包导出扩展

**Files:**
- Modify: `agent/cli.py`（`_export_package` ~L86-300）
- Test: `tests/test_process_instrumentation.py`

**Interfaces:**
- Produces: 运行包新增 `analysis/query_trace.jsonl`、`analysis/visible_context.jsonl`；`run_meta.json` 新增 `query_trace_calls:int`（真实条数）与 `process_files` 追加这两个文件名
- Consumes: `ctx.stats["query_trace"]`（Task 3）、`ctx.stats["visible_context"]`（Task 4）

- [ ] **Step 1: 写失败测试**

- `test_export_writes_query_trace_jsonl`：跑一次带 `query_financial_data` 的导出 → `analysis/query_trace.jsonl` 存在且行数 == `ctx.stats["query_trace"]` 长度。
- `test_export_writes_visible_context_jsonl`：有 planner 调用 → `analysis/visible_context.jsonl` 存在，每行有 `agent_call_id`/`role`/`user_content`，且**不再含** `user_preview`（不重复存）。
- `test_visible_context_content_is_redacted`：把 `sk-…` 注入 `sys_prompt`/`user` → 落盘内容无该串。
- `test_run_meta_query_trace_calls`：`run_meta.json["query_trace_calls"] == 2`。
- `test_process_files_lists_new_files`：`run_meta["multi_agent"]["process_files"]` 含 `query_trace.jsonl`、`visible_context.jsonl`。
- `test_export_omits_files_when_no_data`：无查询、无子 Agent 调用时**不写空文件**，但 `run_meta` 仍写 `query_trace_calls == 0`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_process_instrumentation.py -q -k export`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 实现**

在 `_export_package` 写 `analysis_state.json` 之后追加两段写入；`process_files` 在既有 `["analysis_state.json","analysis_review.md"]` 基础上追加实际写出的新文件。已有 `analysis/` 目录在 multi 模式下由 `AnalysisState` 创建；非 multi 模式需 `mkdir(parents=True, exist_ok=True)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_process_instrumentation.py -q`
Expected: PASS（全文件）

- [ ] **Step 5: 跑既有回归**

Run: `python -m pytest tests/test_cli.py tests/test_multi_agent.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/cli.py tests/test_process_instrumentation.py
git commit -m "feat(agent): export query_trace and visible_context in run package"
```

---

## Task 6: 微观查询链路诊断

**Files:**
- Create: `eval/agent_process/query.py`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `ProcessPackage.query_records`（Task 1）
- Produces:
  - `query.REASON_CODES: dict[str, str]`（`ok`/`catalog_miss`/`ambiguous`/`no_data`/`parse_fail`/`exec_fail`/`unknown`）
  - `query.classify_query(rec: QueryRecord) -> str`
  - `query.diagnose_queries(pkg: ProcessPackage) -> dict`，返回 `{"calls": int, "by_reason": {code: n}, "records": [per-call dict], "plan_conformance": {...}, "notes": [...]}`
  - `query.plan_conformance(rec: QueryRecord, rows: list[dict] | None) -> dict`：`{"periods_requested", "periods_returned", "period_violations", "columns_present", "columns_missing", "status"}`
  - `query.QUERY_DIAG_VERSION = "process-query-v1"`

- [ ] **Step 1: 写失败测试**

- `test_classify_unknown_value_is_catalog_miss`：`status=="unknown_value"` → `catalog_miss`。
- `test_classify_unsupported_and_clarification`：`unsupported` → `unknown`（原因码表里 `unsupported` 归 `unknown`，因为无法区分是不支持组合还是目录外），`needs_clarification` → `ambiguous`。
- `test_classify_no_data`：`status=="empty"` → `no_data`；`issue_codes` 含 `empty_filter` 或 `no_data` 时同样 `no_data`。
- `test_classify_parse_fail_from_issue_code`：`status=="unsupported"` 且 `issue_codes==["extractor_protocol"]` → `parse_fail`。
- `test_classify_exec_fail`：`status=="error"` → `exec_fail`。
- `test_empty_status_has_no_result_ids`：`status=="empty"` 的记录 `result_ids==[]` 且 `by_reason["no_data"]==1`，**不得**计为 `ok`。（**Review Focus 4**）
- `test_periods_returned_outside_requested_flagged`：`plan.periods==["FY2024Q1","FY2024Q3"]` 而结果行含 `FY2024Q2` → `period_violations == ["FY2024Q2"]`。
- `test_columns_missing_detected`：`plan.group_by==["region"]` 但 `columns` 无 `region` → `columns_missing == ["region"]`。
- `test_derived_records_marked_low_confidence`：`source=="derived"` 的记录 `notes` 含"派生，缺少提取明细"。
- `test_counts_are_per_call_not_per_rid`：2 次调用同 rid → `calls == 2`。（**Review Focus 2**）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k query`
Expected: FAIL — `ImportError: cannot import name 'query'`

- [ ] **Step 3: 实现**

`is_derived`（plan 里 metric 属衍生指标）不自行判定——从 `results.json` 行的 `columns`/`unit` 与 `plan.metrics` 交集推 `columns_missing` 即可，衍生判定留给 Worker 角色评测。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k query`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/query.py tests/test_agent_process.py
git commit -m "feat(eval-process): micro query-chain diagnosis with reason codes"
```

---

## Task 7: Planner 独立评测与固定样例

**Files:**
- Create: `eval/agent_process/roles/__init__.py`, `eval/agent_process/roles/common.py`, `eval/agent_process/roles/planner.py`, `eval/agent_process/samples/planner.jsonl`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `ProcessPackage`（Task 1）、`eval.financial_e2e.judge.{Judge, CallableJudge, LLMJudge}`
- Produces:
  - `roles.common.evidence_basis(measured: bool, judged: bool) -> str` → `"measured" | "model_judged" | "pipeline_check" | "unknown"`
  - `roles.common.load_samples(path: Path) -> list[dict]`
  - `roles.common.make_judge(mode: str, *, point_fn=None) -> Judge`（`mode="fake"` → `CallableJudge`；`"live"` → `LLMJudge`）
  - `roles.planner.PLANNER_DIMENSIONS = ("executability","dependency_soundness","coverage","duplication","scope_drift")`
  - `roles.planner.evaluate_plan_measured(plan: PlanView) -> dict`：`{"node_count", "required_node_count", "duplicate_goals": [[node_id,...]], "dangling_deps": [...], "self_deps": [...], "cycle": bool, "scope_drift": [...]}`
  - `roles.planner.evaluate_package(pkg, *, judge=None, points=None) -> dict`：`{"plan_revisions", "nodes_by_revision", "measured": {...}, "coverage": {"status": "unknown"|"judged", "mapping": [...], "reason"}, "evidence_basis": {...}}`
  - `roles.planner.evaluate_sample(sample: dict, judge) -> dict`

- [ ] **Step 1: 写失败测试**

- `test_plan_measured_detects_duplicate_goals`：两节点 `goal` 归一化后相同 → `duplicate_goals` 非空。
- `test_plan_measured_detects_dangling_dep`：`depends_on=["n9"]` 而 `n9` 不存在 → `dangling_deps == ["n9"]`。
- `test_plan_measured_detects_cycle`：`n1→n2→n1` → `cycle is True`。
- `test_plan_measured_counts_required_nodes`：`required_for_answer` 为 True 的节点数正确。
- `test_scope_drift_flags_device_outside_contract`：TaskContract 限定 `period=FY2024Q2`，节点 goal 提到 `FY2023Q4` → `scope_drift` 含该 node_id。
- `test_coverage_unknown_without_points`：无参考点 → `coverage["status"] == "unknown"`、`evidence_basis["coverage"] == "unknown"`，**不编造覆盖结论**。
- `test_coverage_judged_with_points_fake_judge`：给 2 个参考点 + `CallableJudge` → `coverage["status"] == "judged"`、`evidence_basis["coverage"] == "pipeline_check"`（**不冒充真实评分**）。
- `test_plan_revisions_counted`：两个 revision → `plan_revisions == 2`、`nodes_by_revision` 有两个键。
- `test_samples_file_has_recommended_split`：`samples/planner.jsonl` ≥5 条，每条含 `sample_id`/`question`/`contract`/`plan`/`annotations`，且 `annotations` 至少覆盖 `uncovered_point` 与 `duplicate_node` 各一条。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k planner`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.agent_process.roles'`

- [ ] **Step 3: 实现**

`common.py` 的 `evidence_basis` 是**唯一的依据标注入口**，所有角色评测都必须经它产 `evidence_basis`。`planner.evaluate_package` 的覆盖映射需要参考点（来自调用方传入的 gold 得分点）；无 gold 一律 `unknown`——**计划覆盖与最终答案覆盖分开**，本函数不看最终答案。

`samples/planner.jsonl` 覆盖：全覆盖、合理隐含、未覆盖点、重复节点、范围偏移、依赖不合理（≥6 条）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k planner`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/roles/ eval/agent_process/samples/planner.jsonl tests/test_agent_process.py
git commit -m "feat(eval-process): independent planner evaluation with fixed samples"
```

---

## Task 8: Worker 独立评测与固定样例

**Files:**
- Create: `eval/agent_process/roles/worker.py`, `eval/agent_process/samples/worker.jsonl`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `roles.common`（Task 7）、`ProcessPackage`
- Produces:
  - `roles.worker.WORKER_DIMENSIONS = ("query_intent","data_completeness","result_structure","calculation","evidence_support","task_completion","gap_statement")`
  - `roles.worker.evaluate_artifact_measured(art: ArtifactView, pkg: ProcessPackage) -> dict`：`{"facts_with_rid", "facts_missing_rid", "rids_not_in_store", "calculations_missing_output_rid", "criteria_covered", "criteria_uncovered", "evidence_refs_empty", "declared_status"}`（`rids_not_in_store` 用 `results.json` 的 id 集合判定）
  - `roles.worker.evaluate_node(pkg, node_id: str, *, judge=None) -> dict`：合并 measured + Judged 维度 + `counts`（轮数/重试/冗余调用/读取）+ `attribution`（区分上游目标错误/上下文交接遗漏/意图错误/解析错误/执行器错误/数据不可用，证据不足时 `unknown`）
  - `roles.worker.evaluate_sample(sample: dict, judge) -> dict`

- [ ] **Step 1: 写失败测试**

- `test_facts_with_and_without_rid_counted`：2 个 fact 有 rid、1 个没有 → `facts_with_rid==2`、`facts_missing_rid==1`。
- `test_rid_not_in_store_flagged`：fact 写 `rid="r99"` 而 `results.json` 只有 `r1`/`r2` → `rids_not_in_store == ["r99"]`。
- `test_completion_criteria_coverage_from_completion_report`：`completion_report` 覆盖 1/2 条 `criterion` → `criteria_covered==1`、`criteria_uncovered==1`。
- `test_node_completed_does_not_imply_success`：`execution_status=="completed"` 但 fact 全无 rid → 返回里 `evidence_basis["evidence_support"]=="measured"` 且 `measured.facts_missing_rid>0`，**不出现** `success=True` 字段。
- `test_counts_are_per_node`：该节点的 `subagent_start` 次数、`llm_retry`、冗余同 tool 重复调用、`recall` 读取次数分别计入 `counts`。
- `test_attribution_unknown_when_evidence_insufficient`：节点失败但无查询记录、无上游缺失证据 → `attribution["cause"] == "unknown"`、`reason` 说明证据不足（**不凭最终错误倒推根因**）。
- `test_attribution_data_unavailable_from_reason_code`：查询记录原因码为 `no_data` → `attribution["cause"] == "data_unavailable"`。
- `test_attribution_parse_error_from_text2sql`：原因码 `parse_fail` → `attribution["cause"] == "text2sql_parse_error"`。
- `test_samples_file_covers_required_classes`：`samples/worker.jsonl` ≥6 条，`annotations.cause` 覆盖 `upstream_goal_error`/`handoff_omission`/`intent_error`/`parse_error`/`executor_error`/`data_unavailable` 六类各至少一条。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k worker`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.agent_process.roles.worker'`

- [ ] **Step 3: 实现**

`evaluate_node` 的 Judged 维度（查询意图、计算与结论准确性）经 `common.make_judge`；`judge is None` 时这些维度置 `None` 并把 `evidence_basis` 标 `unknown`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k worker`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/roles/worker.py eval/agent_process/samples/worker.jsonl tests/test_agent_process.py
git commit -m "feat(eval-process): independent worker evaluation with fixed samples"
```

---

## Task 9: Reviewer 独立评测与固定样例

**Files:**
- Create: `eval/agent_process/roles/reviewer.py`, `eval/agent_process/samples/reviewer.jsonl`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: `roles.common`、`ProcessPackage`
- Produces:
  - `roles.reviewer.REVIEW_CATEGORIES = ("wrong_period","unit_error","omission","unsupported_attribution","wrong_citation","alternative_algorithm_ok","insufficient_evidence")`
  - `roles.reviewer.score_against_annotations(detected: list[dict], annotated: list[dict]) -> dict`：`{"true_positive", "false_positive", "false_negative", "precision": float|None, "recall": float|None}`（分母为 0 时精度/召回为 `None`，**不填 0**）
  - `roles.reviewer.evaluate_sample(sample: dict, judge) -> dict`
  - `roles.reviewer.link_review_rounds(pkg) -> dict`：`{"rounds": [{"review_id","target_id","target_version","verdict","findings":[...],"next_version":str|None,"fixed":[...],"not_fixed":[...],"new_errors":[...]}], "deduped_findings": [...], "unlinkable": [...]}`（`unlinkable` 记录无法关联版本的审查，**不伪造改进率**）
  - `roles.reviewer.evaluate_package(pkg, *, judge=None) -> dict`

- [ ] **Step 1: 写失败测试**

- `test_precision_recall_on_annotated_sample`：标注 3 个问题、检出 2 个（1 个对、1 个误报）→ `true_positive==1`、`false_positive==1`、`false_negative==2`、`precision==0.5`、`recall==1/3`。
- `test_precision_none_when_nothing_detected`：检出 0 → `precision is None`、`recall == 0.0`（分母非 0 时为 0）。
- `test_clean_sample_false_positive_only`：无错误样例、检出 1 个 → `true_positive==0`、`precision==0.0`、`recall is None`。
- `test_review_round_linked_to_next_version`：`art1@rev1-attempt1` 被 `rev1` 判 `needs_revision`，随后同节点产生 `art2@rev1-attempt2` → `rounds[0].next_version == "rev1-attempt2"`。
- `test_finding_response_status_marks_fixed`：finding `response_status=="addressed"` → `fixed` 含该 `finding_id`。
- `test_unlinkable_review_reported_not_faked`：审查目标再无后续产物 → `unlinkable` 非空，且 `rounds` 中该项 `next_version is None`、**不出现 `improvement_rate` 字段**。
- `test_duplicate_findings_deduped_with_rounds_kept`：两轮提同一 `(category, statement)` → `deduped_findings` 1 条且含 `rounds: [1, 2]`。
- `test_accepted_is_treated_as_tested_output`：`verdict=="accepted"` 的审查结果里 `evidence_basis["verdict"]=="measured"`，且不产生"问题已修复"的推断。
- `test_samples_file_covers_required_categories`：`samples/reviewer.jsonl` ≥8 条，`annotated_issues[].category` 覆盖 `REVIEW_CATEGORIES` 全 7 类，且至少 2 条 `annotated_issues == []`（无错误样例）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k reviewer`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.agent_process.roles.reviewer'`

- [ ] **Step 3: 实现**

`link_review_rounds` 用 `Review.target_id`+`target_version` 找同 `node_id` 的后续 artifact（`plan_revision`/`attempt` 递增者），比对 `response_status` 与 `verdict`。区分"被指出的问题是否修复"与"是否新引入错误"：后者只在该轮新产物出现新 `must_fix`/`contradiction` 类 finding 时报。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k reviewer`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/roles/reviewer.py eval/agent_process/samples/reviewer.jsonl tests/test_agent_process.py
git commit -m "feat(eval-process): independent reviewer evaluation with fixed samples"
```

---

## Task 10: 报告

**Files:**
- Create: `eval/agent_process/report.py`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Consumes: T1–T9 全部
- Produces:
  - `report.case_report_md(pkg: ProcessPackage, *, metrics: dict, roles: dict, queries: dict, e2e: dict|None) -> str`
  - `report.summary_report_md(records: list[dict], *, run_ids: list[str], dataset_name: str|None) -> str`
  - `report.compat_table_md() -> str`（渲染 `adapt.FIELD_COMPAT`）
  - `report.build_record(pkg, *, metrics, roles, queries, e2e=None) -> dict`（按 `(case_id, run_id)` join A 的 `results.jsonl`）
  - `report.write_reports(outdir: Path, records: list[dict], *, run_ids, dataset_name) -> dict[str, Path]`
  - `report.REPORT_VERSION = "process-report-v1"`

- [ ] **Step 1: 写失败测试**

- `test_case_report_has_required_sections`：输出含 `## 最终状态与答案`、`## 运行监控`、`## 中观角色评测`、`## 微观查询诊断`、`## 依据与未知项`、`## 字段兼容表` 六个标题。
- `test_case_report_shows_nodes_completed_and_final_undelivered`：节点 `completed` + `semantic_review_status=accepted` + `final_status=refuse` → 报告中同时出现"节点已完成"/"内部审查 accepted"与"最终未提交"，且含 `final_answer` 校验反馈与轮数终止原因。（**PRD §9.3 回放验收**）
- `test_case_report_labels_evidence_basis`：`pipeline_check` 字样出现在报告里，并注明"仅验证评测流程，不是真实质量评分"。
- `test_case_report_no_single_total_score`：报告不含"总分"字样，且质量/耗时/token 分区呈现。
- `test_blocked_or_undelivered_not_dropped`：`final_answer is None` 的包仍产出逐题报告，并在汇总的"未交付"段出现。
- `test_unknown_fields_section_lists_unknown_fields`：`unknown_fields` 非空时报告列出，并说明历史包缺埋点。
- `test_summary_groups_by_type_and_split`：两条记录（不同 `question_type`/`split`）→ 汇总的分组表有两行。
- `test_summary_reports_sample_counts`：分组行含样本数、完成数、失败数、评分错误数。
- `test_join_with_e2e_results_by_case_and_run`：给 `e2e` 记录 → 报告中并列宏观必答点命中率；`e2e=None` 时宏观段写 `未提供`，**不编造**。
- `test_compat_table_lists_unknown_legacy_fields`：`compat_table_md()` 含 `unknown_legacy` 行。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k report`
Expected: FAIL — `ImportError: cannot import name 'report'`

- [ ] **Step 3: 实现**

排版对齐 A 的 `eval/financial_e2e/report.py` 风格（`_esc`、表格用 `|`）。汇总末尾固定写一句：本版不把质量/耗时/token 拼成单一总分。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q -k report`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/report.py tests/test_agent_process.py
git commit -m "feat(eval-process): per-case and summary process reports"
```

---

## Task 11: CLI 与 replay

**Files:**
- Create: `eval/agent_process/cli.py`, `eval/agent_process/__main__.py`
- Test: `tests/test_agent_process.py`

**Interfaces:**
- Produces:
  - `cli.build_parser() -> argparse.ArgumentParser`，`prog="python -m eval.agent_process"`，6 子命令：`adapt`、`metrics`、`roles`、`queries`、`report`、`replay`
  - `cli.main(argv: list[str]) -> int`
  - `cmd_adapt/cmd_metrics/cmd_roles/cmd_queries/cmd_report/cmd_replay(args) -> int`
  - 参数：`adapt --run-dir --case-id --out`；`metrics --run-dir --out`；`roles --run-dir --mode {trajectory,samples,both} --role {planner,worker,reviewer,all} --samples --judge {fake,live} --out`；`queries --run-dir --out`；`report --run-dir --e2e-results --out --out-dir`；`replay --run-dir --case-id --out-dir --e2e-results --judge`

- [ ] **Step 1: 写失败测试**

- `test_parser_has_six_subcommands`：`{"adapt","metrics","roles","queries","report","replay"}` 全部在 `choices` 里。
- `test_adapt_writes_json`：跑 `cmd_adapt` → `out/adapt.json` 存在且 `json.load` 出 `run_id`/`final_status`。
- `test_metrics_writes_json`：`metrics.json` 含 `tool_calls`、`role_calls`、`tokens`、`discrepancies`。
- `test_queries_writes_json`：`queries.json` 含 `by_reason`。
- `test_roles_samples_mode_offline`：`--mode samples --judge fake` → 写出三份 `roles_<role>.json`，每份有 `evidence_basis`。
- `test_test_roles_trajectory_mode`：`--mode trajectory --judge fake` → `roles_trajectory.json` 含三角色键。
- `test_report_writes_markdown`：`report` → `--out` 指向的文件存在且含 `# 过程评测报告`。
- `test_replay_runs_all_steps`：`replay` → `out_dir` 下有 `adapt.json`、`metrics.json`、`queries.json`、`roles_*.json`、`report.md`。
- `test_cli_main_returns_zero_on_success`：`main(["python -m eval.agent_process","adapt","--run-dir",str(run)," --case-id",""])` 返回 0。
- `test_live_judge_requires_flag`：`--judge live` 下不实例化 `LLMClient`（用 monkeypatch 断言**未被调用**），证明默认离线。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_agent_process.py -q -k "cli or replay"`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.agent_process.cli'`

- [ ] **Step 3: 实现**

`main` 先 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（Windows）。`replay` 依次调 T1→T10 的函数，把每步产物写进 `out_dir`。所有输出 JSON 用 `ensure_ascii=False, indent=1`（对齐 `AnalysisState.persist`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_agent_process.py -q`
Expected: PASS（全文件）

- [ ] **Step 5: Commit**

```bash
git add eval/agent_process/cli.py eval/agent_process/__main__.py tests/test_agent_process.py
git commit -m "feat(eval-process): CLI with adapt/metrics/roles/queries/report/replay"
```

---

## Task 12: 固定包回放与交付说明

**Files:**
- Create: `docs/eval_process_delivery.md`
- Modify: `eval/agent_process/__init__.py`（补齐最终导出）
- Test: `tests/test_agent_process.py`（补一个真实包冒烟测试，缺包时 skip）

**Interfaces:**
- Consumes: 全部

- [ ] **Step 1: 准备固定回放包**

```powershell
Copy-Item -Recurse "D:\finsights\finsights-dev\runs\cli-20261016-134035" "D:\finsights\finsights-dev-eval-process\runs\"
```
`runs/` 在 `.gitignore` 里，不入库；命令与路径写进交付说明。

- [ ] **Step 2: 写真实包冒烟测试（缺包 skip）**

- `test_real_package_smoke`：`runs/cli-20261016-134035` 不存在则 `pytest.skip`；存在则断言 `final_status=="refuse"`、`final_answer is None`、`metrics["role_calls"]["by_role"]["reviewer"]==7`、`role_calls.total == 13`、`tokens.prompt_tokens == 383994`、`queries.by_reason` 非空、`unknown_fields` 含两个新文件、且**无任何新增字段被填成 0**。

- [ ] **Step 3: 跑真实回放**

```powershell
python -m eval.agent_process replay --run-dir runs/cli-20261016-134035 --case-id "" --out-dir eval/reports/process
```
Expected: 退出码 0；`eval/reports/process/` 下生成 `adapt.json`、`metrics.json`、`queries.json`、`roles_planner.json`、`roles_worker.json`、`roles_reviewer.json`、`roles_trajectory.json`、`report.md`、`compat_table.md`。

- [ ] **Step 4: 核对回放结论**

人工核对并记录进交付说明：
- 报告同时呈现「节点 completed / 内部审查 accepted」与「最终未提交、E2E 未完成」；
- `final_answer` 校验反馈（`final_answer_rejects=1`）与终止原因（`主循环轮数用尽（max_steps=20，未提交最终答案）`）出现在报告里；
- 原运行包未被修改（`git status` 干净、文件 mtime 未变）。

- [ ] **Step 5: 写 `docs/eval_process_delivery.md`**

必含：已实现项；仅定义协议项；**准确命令**（逐条可直接复制）；**字段兼容表**（渲染 `FIELD_COMPAT`）；**已知限制**（至少列 §4.3 的 8 条缺口 + 本次新增埋点对历史包不可用）；**M3 真实执行验收未完成**的明确声明；模拟/固定样例结果不是真实质量评分的声明。

- [ ] **Step 6: 全量测试**

Run: `python -m pytest -q`
Expected: PASS（含既有全部测试；任何失败逐条记录，不隐瞒）

- [ ] **Step 7: Commit**

```bash
git add docs/eval_process_delivery.md eval/agent_process/__init__.py tests/test_agent_process.py eval/reports/process
git commit -m "docs(eval-process): delivery notes, fixed-package replay and known limits"
```

---

## Self-Review

**Spec 覆盖**

| Spec 节 | 任务 |
|---|---|
| §5.1 模块边界 / `agent/` 四处观测点 | T1、T3、T4、T5 |
| §5.2 可见上下文 | T4、T5 |
| §5.3 query_trace | T3、T5 |
| §5.4 运行监控口径 | T2 |
| §5.5 评分依据分级 | T7（`common.evidence_basis`，其余角色复用） |
| §5.6 三角色评什么 | T7、T8、T9 |
| §5.7 查询原因码 | T6 |
| §5.8 CLI | T11 |
| §5.9 报告与 A 对接 | T10 |
| §7 验收 1–7 | T12（1、2、3、6、7）、T1–T11 的 TDD（4）、T3/T4 的行为不变测试（5） |

**Review Focus 归属**：1→T1；2→T2、T6；3→T2；4→T3、T6；5→T1。五条各有测试。

**类型一致性**：`ProcessPackage`/`RoleCall`/`QueryRecord`/`PlanView`/`ArtifactView`/`ReviewView` 在 T1 定义，T2/T6/T7/T8/T9/T10 消费，字段名与 T1 的 Produces 块一致。`common.evidence_basis` 在 T7 定义、T8/T9 消费。`_context_refs`/`_redact_text` 在 T4 定义、T4 消费，`redact_text` 被 T5 复用。

**未决点（我的取舍）**：`agent` 是否可直接 import `eval.agent_process.redact`（T4 Step 3 给了两条路与判据）；`unsupported` 的原因码归 `unknown`（T6 已说明理由）；固定样例的具体条目内容（spec §6 已留给 builder）。

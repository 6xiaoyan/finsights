# 运行包适配、微观埋点与角色独立评测设计（Agent B）

日期：2026-10-16；状态：设计草案，待审核。
权威规格：`docs/prd-agent-evaluation.md` §4、§7、§8、§9；公共协议：`eval/contracts/case.py`（已冻结）。
基线：分支 `feat/eval-process`，从 `42baa3f`（契约与 A 的 E2E 模块所在提交）切出。

本文件只记录**决定**。凡 PRD 已明确要求的，不在此重复论证；凡本文件未写、PRD 已写的，以 PRD 为准。

## 1. 意图与目标

让「一次 Agent 运行到底发生了什么」可以被离线、可复现地评估，且不改变被测 Agent。

具体交付四件事：

1. **运行包适配**：把 `runs/<run>/` 规范化成公共类型（`RunArtifact`/`TraceEvent`），并把计划、节点、角色调用、工具查询、结果、审查版本关联起来，缺口如实标 `unknown`。
2. **补埋点**：现有运行包缺「子 Agent 实际收到的上下文」「Text2SQL 的 query_trace_id / 提取调用明细 / 非 ok 路径的 QueryPlan」。补上，纯观测。
3. **运行监控**：工具调用成功/失败/跳过、异常、重试、角色调用次数、主/子轮数、提取次数、结果读取次数、耗时、token、费用。保留分母与原始计数，跳过不计成功，缺 usage 不填零，嵌套去重。
4. **三角色独立评测 + 查询链路诊断 + 报告**：Planner / Worker / Reviewer 各自「固定样例」与「真实轨迹」两种输入；Text2SQL 链路按原因码诊断；输出逐题报告与汇总报告。

## 2. 目标 / 非目标 / 反目标

**目标**：固定运行包可完整回放；角色评分输入独立于最终答案；质量与计数/耗时/token 分开呈现；每个结论标注依据来源。

**非目标**：不调用真实 API（本阶段）；不新增 Web/数据库/前端；不实现 Agent 优化；不做 Reviewer 开关对照实验；不把固定样例或模拟评分当真实质量分。

**反目标**（做了就是失败）：
- 把「节点 completed / Reviewer accepted」当作 E2E 成功。
- 把跳过、超时、异常、`unknown` 从分母静默剔除。
- 缺 usage 填 0 冒充已测量。
- 把质量、耗时、token 拼成单一总分。
- 用模拟 Judge 的结果冒充真实模型评分。
- 改 Agent 的任务拆分、提示词、查询策略、轮次、审核策略、调度。

## 3. 约束（用户 2026-10-16 裁定）

埋点**只允许观测性改动**：

- 不改变模型输入、工具返回值、Agent 调度、评分逻辑。
- 优先复用现有事件与结果，**不重复存整份大对象**（rows / SQL / dataframe 留在 `results.json`，靠 `result_ids` 引用）。
- 确需上下文快照时，**只记录实际发给模型的内容**，并对密钥等敏感字段脱敏。
- 先写测试验证新增事件与关联字段，再回放 `cli-20261016-134035`。
- 历史包缺失的新字段如实标 `unknown`，不伪造。
- 本阶段不做真实 API 运行；PRD §9.3 的 M3「真实执行贯通」**不得标记为完成**。

其他：不改 `eval/financial_e2e/`、`eval/contracts/`、`eval/schema.py`、`eval/runner.py`；不自行合并 main。

## 4. 已核实的现状（决定的地基）

以下是实现前实测确认的事实，决定了设计的形状。

### 4.1 运行包已有什么

| 文件 | 关键内容 |
|---|---|
| `run_meta.json` | `status`、`verified`、`usage{steps,prompt_tokens,completion_tokens,latency_s,sql_errors,refuse_reason}`、`multi_agent{...}`（`subagent_calls`、`subagent_role_calls`、`plan_revisions`、`reviews_total`、`findings_total`、`artifacts_total`、`task_coverage_status`、`unresolved_must_fix`、`stop_reason`）、`catalog_version`、`extraction_prompt_version`、`extraction_calls` |
| `trace.jsonl` | `meta`、`assistant`（仅主循环）、`tool_result`、`tool_skipped`、`subagent_start`、`subagent_end`、`subagent_finalize`、`result`（multi 包有**两行**）、`protocol_correction`、`llm_retry`、`final`、`refuse`、`nudge`、`tool_blocked`、`stop_reject`、`compact*` |
| `analysis/analysis_state.json` | `contract`、`plans{rev→Plan}`、`latest_revision`、`artifacts{id→Artifact}`、`reviews{id→Review}`、`answer_candidate`、`plan_change`、`continuations` |
| `results.json` | `{results:[{id,tool,args,sql,unit,data_version,created_step,digest,initial_packet,rows}],any_truncated}` |

### 4.2 关联键

- 角色调用：`subagent_start`/`subagent_end` 的 `agent_call_id = f"{role}@{step}#{node_id|target_id|plan}"`。worker 带 `node_id`/`plan_revision`/`attempt`；reviewer 带 `target_type`/`target_id`/`target_version`；planner 无。
- **产物版本不落盘**：`AnalysisArtifact` 没有 `version` 字段，版本由 `multi._artifact_version(a) = f"rev{plan_revision}-attempt{attempt}"` 现算。`Review.target_version` 存了这个字符串，是唯一落盘的版本锚点。
- **审查↔版本**：`Review{review_id,target_type∈(plan,artifact,answer),target_id,target_version,verdict∈(accepted,needs_revision,inconclusive),findings[],unresolved_items}`。无反向索引，靠遍历。
- **findings**：`{finding_id,category∈7值,severity∈(must_fix,limitation,suggestion),statement,explanation,suggested_action,evidence_refs,node_id,response_status∈(open,addressed,declined)}`。`response_status` 是「被指出的问题是否修复」的现成锚点。
- 无 `rejected` 判定值；无 coverage 对象（只在 `run_meta`）；无顶层 must_fix 列表（靠 `severity=must_fix` 过滤）。

### 4.3 明确的缺口（埋点要补的）

1. **子 Agent 可见上下文完全没存**。只在暂停节点的 `continuations[].messages` 里有片段。系统提示词只存 sha256（`role_prompt_sha256_12`）。
2. **Text2SQL 明细丢失**：`extract_plan` 返回的逐次尝试事件存在 `ctx.stats["extractor_events"]`（内存），从不落盘；只有 `run_meta.extraction_calls` 计数和 `extraction_prompt_version` 存活。
3. **无 `query_trace_id`**：`executor._provenance()` 算了 `sha256(sql+plan)[:12]`，写进 `store.put(meta=...)`，但 `results.json` 导出时**丢掉了 `meta`**。所以每结果的 provenance 全部丢失。
4. **非 ok 路径的 QueryPlan 不可恢复**：`store.put` 只在完全成功的路径调用；`unsupported`/`needs_clarification` 时 `plan=None`，`empty`/`error` 的 plan 不进 `results.json`。
5. **`tool_skipped` 只有一处**（cli.py 跳过 text2sql 初始包），不是通用语义。
6. **`results.json` 的 `initial_packet`/`created_step` 无意义**：`executor.py` 里 `store.put(..., step=0)` 硬编码，导致每个查询结果 `created_step=0` → `initial_packet=true`（真实运行 16 条里 10 条）。→ 登记为已知缺陷，**不修**（属 Agent 行为）。
7. **同一计划共享 rid**：`store.put` 按 `tool|args|data_version` 去重，11 次查询只得 9 个 rid。→ rid 不是「每次调用」的键，需要一个调用级 id。
8. **两行 `result`**：multi 包 `trace.jsonl` 末尾有两行 `type=result`（multi.py 一行 + cli.py 一行）→ 适配器必须处理，不能双计数。

### 4.4 可复用的既有实现（不得另造）

- `eval.financial_e2e.adapter`：`load_run_artifact(run_dir, case_id) -> RunArtifact`、`load_trace_events(run_dir) -> list[TraceEvent]`、`run_question`、`parse_claims`、`answer_body`。A 的适配器**不解析 `analysis_state.json`**，那是 B 的。
- `eval.financial_e2e.judge`：`Judge` Protocol、`LLMJudge`（`client.chat(..., role="judge")`）、`CallableJudge`（离线注入）、`_extract_json`。**复用，不写第二个 Judge 客户端。**
- `eval.contracts.case`：`RunArtifact`/`TraceEvent`/`GradeResult`/`Claim` 等公共类型。

## 5. 决策（builder 不该再改的部分）

### 5.1 模块边界

B 独占 `eval/agent_process/`，外加 `agent/` 的 4 处观测点、`tests/`、`docs/eval_process_delivery.md`。

```
eval/agent_process/
├── __init__.py        公共导出
├── __main__.py        python -m eval.agent_process
├── cli.py             argparse 6 子命令
├── schema.py          B 自有类型（RoleCall/QueryRecord/RoleScore/...）
├── adapt.py           运行包 → 规范化对象；兼容表数据
├── metrics.py         运行监控统计（去重、分母）
├── query.py           微观查询链路诊断
├── roles/
│   ├── __init__.py
│   ├── common.py      三角色共用的 Judge 装配与证据读取
│   ├── planner.py
│   ├── worker.py
│   └── reviewer.py
├── report.py          逐题报告 + 汇总报告
└── samples/           B 自撰固定样例
    ├── planner.jsonl
    ├── worker.jsonl
    └── reviewer.jsonl
```

`agent/` 的四处观测点（全部可加不可改）：

| 文件 | 改动 | 为什么在这里 |
|---|---|---|
| `agent/tools/data.py` | `Outcome` 增字段 `query_trace_id: str\|None = None`；`_h_query_financial_data` 写 `ctx.stats["query_trace"]` 记录并回填该字段 | 唯一的 text2sql 汇聚点 |
| `agent/subagents.py` | 子 `tool_result` 事件增键 `query_trace_id` | 让工具调用事件与查询记录同键 |
| `agent/multi.py` | `_run_role` 增发 `subagent_context` 事件 | 唯一的子 Agent 汇聚点（`_run_role` 是唯一写 `subagent_start` 的入口） |
| `agent/cli.py` | `_export_package` 增写 `analysis/query_trace.jsonl`、`analysis/visible_context.jsonl`；`run_meta` 增键 | 运行包导出唯一入口 |

`agent/query/*` **完全不动**：handler 已经拿到 `extraction`、`events`、`result`，够用。

### 5.2 可见上下文怎么存（不重复存大对象）

- **`subagent_context` 事件**（进 `trace.jsonl`，小）：`agent_call_id`、`role`、`main_step`、`plan_revision`/`node_id`/`target_id`/`target_version`、`system_prompt_sha256_12`、`user_content_sha256_12`、`user_chars`、`context_refs`（结构化引用）、`user_preview`（≤1200 字符，脱敏）、`user_truncated`。
- **`analysis/visible_context.jsonl`**（整份实际内容，唯一存放处）：每角色调用一行 `{agent_call_id, role, main_step, plan_revision, node_id, target_id, target_version, system_prompt_sha256_12, user_content, user_chars}`。超过 64000 字符则截断并置 `truncated=true`。
- `context_refs` 是从 `user` 文本里**程序提取**的引用清单，不是重存：`rids`（`r\d+`）、`artifact_ids`（`art\d+`）、`node_ids`、`answer_candidate_ids`、`target_version`、`contract_data_version`。它回答「这次调用到底看得见哪些上游产物」。
- **脱敏**（两处都做）：`sk-[A-Za-z0-9_\-]{8,}`、`(?i)bearer\s+\S+`、`(?i)"?(api_key|auth_token|authorization|token)"?\s*[:=]\s*"?\S+` → `***REDACTED***`。

### 5.3 query_trace 怎么存

`analysis/query_trace.jsonl`，每次 `query_financial_data` 调用一行：

```
{query_trace_id, call_index, step, question, node_task,
 status, issue_codes[], plan (可空), plan_digest (可空),
 attempts, extractor_events[],
 prompt_version, catalog_version, data_version,
 result_ids[], sql_chars, total_rows, truncated}
```

- `query_trace_id = f"q{n}"`，运行内唯一、每次调用必有 → **这是调用级连接键**。
- `plan_digest = result.provenance["query_trace_id"]`（`sha256(sql+plan)[:12]`，内容寻址，非 ok 路径为 `null`）。**两套同名不同义，字段兼容表必须写明。**
- `plan` 存完整 `QueryPlan.model_dump()`（小对象，且用户明确要求补齐）；`sql` 只存 `sql_chars`，全文留在 `results.json` 靠 `result_ids` 取。
- `raw sql` 为空时**不伪造** SQL 字段（PRD §7.1）；`status != "ok"` 时 `result_ids` 为空且不造 rid。
- 旧包没有这个文件 → 全部字段 `unknown`，不填 0。

### 5.4 运行监控口径（防重复计数）

- 工具调用：按 `(parent_call_id or "main", call_id)` 去重，取 `tool_result` 事件。主循环派发角色工具（`execute_analysis`/`review_analysis`）与子循环内部工具是**不同 tool 名**，天然不重复；仍按上述键去重以防转发重复。
- 角色调用：按 `agent_call_id` 去重，取 `subagent_start`；`subagent_end` 配对给耗时/子步数/token。
- 跳过：`tool_skipped` 计入 `skipped`，**不计入** `ok`。缺 `ok` 字段的事件（如 `tool_skipped` 的 `ok:null`）计入 `unknown_status`。
- `result` 事件：multi 包两行，只取最后一行定终态，并记录 `result_lines=2` 到诊断里（不隐藏）。
- token/费用：以 `run_meta.usage` 为准；`subagent_end` 的 token 是子集，**不相加**，只用于交叉核对。缺 usage → `null` + `usage_missing=true`，不填 0。
- 原包自报值（`run_meta.multi_agent.*`）与重算值并列输出，不一致时 `discrepancy` 标出差异。

### 5.5 评分依据分级（报告必须显示）

| 依据 | 含义 |
|---|---|
| `measured` | 程序从运行包算出来的（计数、结构、rid 存在性、版本匹配） |
| `model_judged` | 真实模型 Judge 判定（本阶段不可得，标为不可用） |
| `human_reviewed` | 固定样例里的自撰标注（B 撰，非人工审核真实答案） |
| `unknown` | 运行包没有该信息，或无法判定 |

**固定样例与模拟 Judge 的结论一律带 `pipeline_check` 标记**，报告中注明「仅验证评测流程，不是真实质量评分」。

### 5.6 三角色评什么（可测的用程序，不可测的用 Judge，没有的标 unknown）

**Planner**：程序侧 `measured` — 节点数、计划修订次数、重复节点目标（归一化后比对）、依赖合法性（悬空/自依赖/环）、`required_for_answer` 节点数、范围偏移（节点 `goal` 提到的期间/公司/指标是否在 TaskContract 范围内）。覆盖映射（得分点 → 显式覆盖/合理隐含/未覆盖）需参考点：有 gold 时 `model_judged`，无 gold 时 `unknown`。**计划覆盖与最终答案覆盖分开**，不把 Worker 补救算成原计划覆盖。

**Worker**：程序侧 `measured` — 产物声明的 `facts[].rid` 是否真实存在于 store、`calculations[].output_rid` 是否存在、`completion_report` 是否覆盖 `completion_criteria`、`evidence_refs` 非空、查询结果表结构（`columns` 是否含请求的指标/分组、`total_rows`、`truncated`、`flag` 值域）。Judge 侧 — 查询意图是否符合节点目标、计算与结论是否被证据支持。归因不可测（不凭最终错误倒推根因），按证据标 `unknown`。另列轮数、重试、冗余调用、读取次数。

**Reviewer**：固定样例有自撰标注问题清单 → 程序算 precision/recall/漏检/误报/严重性，`measured`（标注驱动）。真实轨迹 — 「审查前对象 vs 审查后对象」按 `Review.target_id`+`target_version` 与后续同 `node_id` 的新 artifact 配对，比对被指出问题是否修复、是否新引入错误；同一问题多轮重复提出按 `(category, 归一化 statement)` 去重并保留轮次。**无法关联版本时 `unknown`，不伪造改进率。** Reviewer 的 `accepted` 是被测输出，不是标准答案。

### 5.7 查询链路诊断原因码

由 `QueryToolResult.status` + `issue_codes` 映射，区分：`catalog_miss`（目录外，`unknown_*`）、`ambiguous`（`needs_clarification`）、`no_data`（`empty`/`no_data`/`empty_filter`）、`parse_fail`（`extractor_protocol`/`format`）、`exec_fail`（`error`/`query_error`）、`ok`。多写/少写查询次数**不自动**加减分，与质量并列展示。

### 5.8 CLI

```
python -m eval.agent_process adapt   --run-dir RUN [--out DIR]
python -m eval.agent_process metrics --run-dir RUN [--out DIR]
python -m eval.agent_process roles   --run-dir RUN [--mode trajectory|samples|both] [--role R] [--judge fake|live] [--out DIR]
python -m eval.agent_process queries --run-dir RUN [--out DIR]
python -m eval.agent_process report  --run-dir RUN [--e2e-results PATH] [--out PATH]
python -m eval.agent_process replay  --run-dir RUN [--out-dir DIR]     # 前五步一次跑完
```

约定同 A：`__main__.py` → `cli.main(sys.argv)`；`main` 先把 stdout/stderr 设 utf-8；`args.func(args)` 返回退出码；`--judge` 默认 `fake`（离线）。

### 5.9 报告与 A 的对接

- 不写进 A 的 `result` 结构。B 输出 `results.jsonl` 的兄弟键：`{case_id, run_id, final_status, process:{metrics}, roles:{planner,worker,reviewer}, queries:{...}, evidence_basis:{...}}`，按 `(case_id, run_id)` join。
- 有 `--e2e-results` 时在逐题报告里并列 A 的宏观结论；没有则宏观段标 `未提供`，不假装。

## 6. 留给 builder 的

- 固定样例的具体条目内容与数量（要求见 §5.6 与 PRD §8.3：Reviewer 必须覆盖错期间、单位错误、遗漏、无依据归因、错误引用、合理替代算法、证据不足 + 无错误样例）。
- 报告 Markdown 的具体排版（对齐 A 的 `report.py` 风格即可）。
- `context_refs` 提取正则的细节。
- 各角色分数如何归一（PRD 禁止单一总分，维度分与计数分列即可）。

## 7. 验收标准

1. `python -m eval.agent_process replay --run-dir runs/cli-20261016-134035` 可复现产出报告。
2. 该包回放同时呈现「节点 completed / 内部审查 accepted」与「最终未提交、E2E 未完成」，保留 final_answer 校验反馈与轮数终止原因。
3. 旧包缺失的新埋点字段全部为 `unknown`/`null`，无一处填 0。
4. 新增事件与关联字段有测试，且测试在实现前先失败。
5. 埋点改动可证明不改模型输入与工具返回文本（有测试断言）。
6. 报告区分 `measured`/`model_judged`/`human_reviewed`/`unknown`，固定样例与模拟 Judge 标注 `pipeline_check`。
7. 交付说明含准确命令、字段兼容表、已知限制；明确写出 M3 真实执行验收**未完成**。

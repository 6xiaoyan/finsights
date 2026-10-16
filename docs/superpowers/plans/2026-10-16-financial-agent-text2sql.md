# 财务 Agent + Text2SQL 查询工具 实施计划

> **For agentic workers:** 用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实施。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 在 FinSights 现有多 Agent 编排上新增 `query_financial_data` 自然语言查询工具——内部完成「结构化提取 → 严格校验 → 计划适配 → 确定性 SQL → 只读执行 → 比较/排序 → ResultStore」，并用 `--query-backend metric|text2sql` 受控接入单 Agent 与多 Agent。

**Architecture:** 只新增 `agent/query/`（catalog / schema / prompt / extractor / executor），适配已有 `agent/tools/base.py`、`agent/tools/data.py`、`agent/subagents.py`、`agent/cli.py`、`config.yaml`。不新建 planner/orchestrator，不替换 `multi.py`/`analysis_state.py`，不引入新 Agent 框架。模型只产出结构化查询计划，SQL 由语义层编译器生成。

**Tech Stack:** Python ≥3.11、pydantic v2、duckdb、pandas、pyyaml、既有 `agent.llm.LLMClient`、pytest。

**Spec:** `D:/finsights/finsights-study/docs/prd-financial-agent-text2sql.md`（v1.1）

**Baseline:** `dev/p6-engine-resume-2026-10-09` @ `6c1330b`；分支 `feat/financial-agent-text2sql`。

## Global Constraints

- 查询使用只读连接（`agent.tools.db.connect`：`read_only=True` + `enable_external_access=false`），不改源数据库。
- 不修改 `semantic/metrics.yaml` 公式；不重建数据库/ETL；不接入 Lenovo/MySQL/Power BI/新服务。
- 提取状态：`ok | needs_clarification | unknown_value | unsupported`；非 `ok` 时 `plan=null`，禁止执行 SQL。
- 计划与返回协议严格（pydantic `extra="forbid"`），拒绝未知字段与非法组合。
- `period_basis ∈ {fiscal, calendar}`，分别用 `FY2024Q2` / `2024Q2`；同一计划不混用。
- 保留指标原生单位，不做币种/金额单位换算；明确要求换算 → `unsupported`。
- 存量指标不跨季度求和；不支持季度合成年报/自定义跨期汇总。
- 空结果 → `empty`，不得当作数值零；失败调用不生成假 rid。
- 结果在完整集上做比较/排序，截断预览不得用于全量排名。
- 保留已有轮数控制（主 20 / Planner 4 / Worker 6 / Reviewer 4）与提交审核机制；不恢复时间/token/子调用上限。
- 提取模型调用单独计数（`extraction_calls`），不计入 Worker 外层轮数。
- 不输出 API key/认证头/完整环境变量。
- 不被指示不 commit/push：本计划执行阶段只改工作区与本地分支，不 push。

## 受控运行模式（PRD §7）

保留已有 `--agent-mode single_agent|multi_agent`，新增 `--query-backend metric|text2sql`：

| backend | Worker 白名单 | Reviewer 证据通道 |
|---|---|---|
| 未指定（默认） | 历史大工具集（现状不变） | `recall`,`check_identities`,`read_analysis_object` |
| `metric` | `list_metrics`,`query_metric`,`calc`,`recall` | `recall`,`read_analysis_object` |
| `text2sql` | `query_financial_data`,`list_metrics`,`calc`,`recall` | `recall`,`read_analysis_object` |

两种受控 backend 共用同一数据版本、输出协议与其他工具集合；最终程序核验（`verifier`，含 `check_identities`）保留但记为核验查询。

## Review Focus（spec 隐含、最易踩的失败输入）

1. **离散期间**：请求 `FY2024Q1`+`FY2024Q3`，用最小/最大区间取数会把 Q2 也带回 → 执行后必须精确过滤到请求集合，且不得让被过滤期间参与比较/排序。
2. **`not_in` 排除全部合法值**：补集为空 → 返回 `empty`，绝不能把空筛选变成全量。
3. **零/负基期**：变化率 `(current-baseline)/baseline`；基期 0 → `null` 且标注 `zero_baseline`；负基期按公式算并标注口径。
4. **目录外值 / 歧义**：未知指标/公司/期间/分组 → `unknown_value`；问题与节点任务冲突或多值无法定 → `needs_clarification`；不支持的组合 → `unsupported`；都不猜最近值、不执行部分计划。
5. **排序与 Top N**：在完整结果（含所有行）上排序，`null` 排末尾，并列按公司/分组键稳定排序；`top_n` 按 N 返回并说明并列规则。
6. **明细支持边界**：`region`/`product_line` 仅 `revenue`/`inventory`/`accounts_receivable` 有 `facts_detail`；衍生指标或明细不支持的组合 → `unsupported`，不降级成公司总量。
7. **单位与口径混用**：不同指标单位不同；`period_basis` 不混用；跨公司不默默等同财年标签。

---

## 文件结构

**新增**
- `agent/query/__init__.py`
- `agent/query/catalog.py` — `BusinessCatalog`：启动时用只读元信息生成合法公司/指标/单位/期间/明细维度目录 + 版本号
- `agent/query/schema.py` — `QueryPlan`、`Comparison`、`SortSpec`、`FilterClause`、`ExtractionResult`、`QueryToolResult`、`QueryIssue`
- `agent/query/prompt.py` — 提取系统提示词（v1）+ `PROMPT_VERSION`
- `agent/query/extractor.py` — `extract_plan()`：模型调用 + 严格校验 + 至多一次格式修复
- `agent/query/executor.py` — `execute_plan()`：计划→`MetricRequest` 适配、SQL、执行、比较、排序、落库
- `tests/test_query_catalog.py`、`tests/test_query_schema.py`、`tests/test_query_extractor.py`、`tests/test_query_executor.py`、`tests/test_query_tool.py`、`tests/test_query_multi.py`

**修改**
- `agent/tools/base.py` — 注册 `QueryFinancialDataArgs` + `DESCRIPTIONS`
- `agent/tools/data.py` — 注册 `_h_query_financial_data` 到 `_HANDLERS`；扩展 `ToolContext`（`llm`、`catalog`、`query_backend`、`extraction_calls` 统计）
- `agent/subagents.py` — 按 backend 选择 Worker/Reviewer 白名单（`ROLE_TOOLS` 增受控集）
- `agent/cli.py` — `--query-backend`、受控 `enabled_tools`、运行包字段
- `config.yaml` — `query:` 段（提取模型温度、预览行数上限、是否暴露明细）
- `README.md` — 中文使用说明（新路径命令）
- `docs/` — 完成/未完成说明

**测试夹具**
- `tests/conftest.py` 增 `query_db` 夹具：临时 DuckDB，含 `companies/periods/accounts/facts/facts_detail` 小样本（Lenovo/HP/Dell，含离散季度、零值基期、明细 region/product_line）。

---

## Task 1：业务目录 `agent/query/catalog.py`

**Files:** Create `agent/query/__init__.py`, `agent/query/catalog.py`; Test `tests/test_query_catalog.py`

**Interfaces:**
- Produces:
  - `class BusinessCatalog`
  - `BusinessCatalog.build(db_path: str, metrics_path: Path = METRICS_PATH) -> BusinessCatalog`
  - `.companies: list[str]`, `.company_aliases: dict[str,str]`
  - `.metrics: dict[str, dict]`（name → {label_zh,type,unit,dims,account,formula}）
  - `.metric_aliases: dict[str,str]`（含 `label_zh`）
  - `.periods_fiscal: dict[str, set[tuple[int,int]]]`（company → {(fy,q)}）
  - `.periods_calendar: dict[str, set[str]]`（company → {"2024Q3",…}）
  - `.regions: list[str]`, `.product_lines: list[str]`
  - `.detail_metrics: set[str]`
  - `.catalog_version() -> str`（sha256 前 12 位，覆盖 companies/periods/metrics/dims）
  - `.resolve_metric(name) -> str | None`, `.resolve_company(name) -> str | None`
  - `.metric_unit(name) -> str`, `.metric_type(name) -> str`
- Consumes: `semantic.compiler.load_metrics`、`agent.tools.db.query_df`

- [ ] **Step 1: 写失败测试** —— `test_catalog_builds_from_db`：用 `query_db` 夹具建目录，断言 `companies==["Dell","HP","Lenovo"]`（排序）、`"inventory" in metrics`、`metric_aliases["存货"]=="inventory"`、`regions` 含 `Americas`、`"inventory" in detail_metrics`、`catalog_version()` 长度 12 且对同一库稳定。
- [ ] **Step 2: 跑测试确认失败**（`ModuleNotFoundError: agent.query.catalog`）。
- [ ] **Step 3: 实现** —— 只读查询 `companies`、`periods`（取 fy/q 与 calendar_quarter 去重）、`facts_detail` 的 `region/product_line`；指标与 dims 从 `metrics.yaml` 读；别名表内置公司中文名（联想/惠普/戴尔）+ 指标 `label_zh`。版本号 `sha256` 这些结构。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: add query business catalog`。

## Task 2：计划与返回协议 `agent/query/schema.py`

**Files:** Create `agent/query/schema.py`; Test `tests/test_query_schema.py`

**Interfaces（Produces）:**
- `class Comparison(BaseModel)`: `current: str`, `baseline: str`, `operation: Literal["difference","change_rate"]`（`extra=forbid`）
- `class FilterClause(BaseModel)`: `field: Literal["region","product_line"]`, `operator: Literal["in","not_in"]`, `values: list[str]`（非空）
- `class SortSpec(BaseModel)`: `by: Literal["value","difference","change_rate"]`, `metric: str`, `current: str | None`, `baseline: str | None`, `direction: Literal["asc","desc"]`, `absolute: bool=False`, `top_n: int | None`（≥1）
- `class QueryPlan(BaseModel)`: `metrics: list[str]`（非空）, `companies: list[str]`（非空）, `period_basis: Literal["fiscal","calendar"]`, `periods: list[str]`（非空）, `group_by: list[Literal["company","region","product_line"]]`, `filters: list[FilterClause]`, `comparisons: list[Comparison]`, `sort: SortSpec | None`（`extra=forbid`）
  - `validate_against(catalog: BusinessCatalog) -> list[QueryIssue]`：格式/枚举/目录/一致性校验（见 Step 3）
- `class QueryIssue(BaseModel)`: `code: str`, `message: str`
- `class ExtractionResult(BaseModel)`: `status: Literal["ok","needs_clarification","unknown_value","unsupported"]`, `issues: list[QueryIssue]`, `plan: QueryPlan | None`；模型级校验：`status!="ok" ⇒ plan is None` 且 `issues` 非空；`status=="ok" ⇒ plan is not None`
- `class QueryToolResult(BaseModel)`: `status: Literal["ok","empty","needs_clarification","unknown_value","unsupported","error"]`, `issues: list[QueryIssue]`, `plan: QueryPlan | None`, `result_ids: list[str]`, `columns: list[str]`, `rows: list[dict]`, `total_rows: int`, `truncated: bool`, `provenance: dict`

- [ ] **Step 1: 写失败测试**（`test_query_schema.py`）：
  - `test_plan_rejects_unknown_field`：带 `unit` 键 → `ValidationError`。
  - `test_plan_rejects_mixed_basis_format`：`period_basis=fiscal` 但 `periods=["2024Q2"]` → `validate_against` 返回码 `period_format`。
  - `test_period_prefix_and_basis_ok`：`fiscal`+`FY2024Q2`、`calendar`+`2024Q2` → 无 issue。
  - `test_comparison_baseline_must_be_in_periods`：baseline 不在 periods → `baseline_not_in_periods`。
  - `test_comparison_mismatched_basis_formats`：`current` 与 `period_basis` 格式不符 → `comparison_format`。
  - `test_sort_by_comparison_requires_declared_relation`：`sort.by="difference"` 但无对应 comparison → `sort_relation_undeclared`；`sort.by="value"`+`baseline!=None` → `sort_value_baseline`。
  - `test_group_by_and_filter_unknown_value`：`group_by=["channel"]` → 未知；`filters` region 值不在目录 → `unknown_value`。
  - `test_derived_detail_unsupported`：`metrics=["gross_margin"]`+`group_by=["region"]` → `unsupported`。
  - `test_extraction_result_invariant`：`status="needs_clarification"` 且 `plan!=None` → `ValidationError`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现 `validate_against`**：正则 `^FY(\d{4})Q([1-4])$`（fiscal）/`^(\d{4})Q([1-4])$`（calendar）；公司/指标用 catalog 解析（别名→规范名）；period 格式与 basis 一致；periods 去重且比较基期都在其中；group_by/filters 值需在目录（region/product_line）；derived 指标 + 明细 → `unsupported`；`not_in` 与 `in` 同字段冲突（同字段同时出现两种 operator）→ `unsupported`/`invalid_filter_logic`。issue.code 稳定字符串，message 中文可读。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: add query plan and result schema`。

## Task 3：提取提示词与提取器 `agent/query/prompt.py`、`agent/query/extractor.py`

**Files:** Create `agent/query/prompt.py`, `agent/query/extractor.py`; Test `tests/test_query_extractor.py`

**Interfaces:**
- Produces:
  - `PROMPT_VERSION: str`、`build_extractor_prompt(catalog: BusinessCatalog) -> str`
  - `extract_plan(question: str, node_task: str, catalog: BusinessCatalog, llm, *, ctx=None, node_id: str | None = None) -> tuple[ExtractionResult, list[dict]]`
    - 返回（校验后的 `ExtractionResult`，事件列表）。事件含 `{"type":"extractor_call","status":...,"attempt":...}`。
- Consumes: `agent.query.schema`、`agent.query.catalog`、`agent.llm.LLMClient.chat`、`agent.query.prompt`

- [ ] **Step 1: 写失败测试**（`MockLLM` 固定响应，不联网）：
  - `test_ok_plan_parsed`：固定 `{"status":"ok","issues":[],"plan":{...inventory Lenovo FY2024Q1/Q2...}}` → `ExtractionResult.status=="ok"`、plan 校验通过。
  - `test_non_ok_forces_null_plan`：模型给 `needs_clarification` 但带 plan → 结果 plan 置空且举例说明。
  - `test_unknown_metric_is_unknown_value`：模型 `plan.metrics=["foo"]` → 提取器按 catalog 转 `unknown_value`，不执行。
  - `test_conflict_question_vs_task_needs_clarification`：`question` 说 FY2024Q2、`node_task` 说 FY2023Q4（无唯一确定）→ `needs_clarification`（由提示词规则 + 结果校验保证；用固定模型响应断言通过路径）。
  - `test_format_error_repaired_once`：首个响应非法 JSON，第二个合法 → 成功且事件含 `attempt=2`；连续两次非法 → `error`，不再第三次。
  - `test_business_ambiguity_not_retried`：`needs_clarification` 响应不触发第二次模型调用（断言 `len(llm.calls)==1`）。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：`prompt.py` 写 v1 提取提示词——角色、字段字典（metrics/companies/period_basis/periods/group_by/filters/comparisons/sort）、目录闭集（来自 catalog）、时间/比较/排序规则、状态与原因码表（`ok|needs_clarification|unknown_value|unsupported`）、输出协议（顶层 `status,issues,plan`，`extra` 禁止）。`extractor.py`：组装消息（系统提示 + 目录 + `{原始问题, 节点任务}`），调用 `llm.chat`，提取 JSON（复用 `agent.subagents._extract_json`），`ExtractionResult.model_validate` + `QueryPlan.validate_against(catalog)` → 目录类 issue 归一到 `unknown_value`/`unsupported`；格式/协议错误至多修复一次；业务类（非 ok）不重试。所有调用计数写入 `ctx.stats["extraction_calls"]`（若 ctx 提供）。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: add query plan extractor`。

## Task 4：执行器 `agent/query/executor.py`

**Files:** Create `agent/query/executor.py`; Test `tests/test_query_executor.py`

**Interfaces:**
- Produces:
  - `plan_to_metric_request(plan: QueryPlan, catalog: BusinessCatalog) -> MetricRequest`（供测试与调试直接调用）
  - `filter_to_requested_periods(df, plan, catalog) -> pd.DataFrame`（离散期间精确过滤）
  - `compute_comparisons(df, plan) -> pd.DataFrame`（新增 `current,baseline,operation,value` 行）
  - `apply_sort(df, plan) -> pd.DataFrame`
  - `execute_plan(plan: QueryPlan, catalog: BusinessCatalog, store, db_path: str, *, preview_rows: int = 50) -> QueryToolResult`
- Consumes: `semantic.compiler.compile`/`MetricRequest`、`agent.tools.db.query_df`、`agent.result_store.ResultStore`

- [ ] **Step 1: 写失败测试**（`query_db` 夹具，固定小样本）：
  - `test_single_metric_plan_to_request`：`plan_to_metric_request` 产出 `companies`/`period_from`/`period_to`=min/max、`group_by` 去掉 `company`、`filters` 映射到 dict。
  - `test_discrete_periods_filtered`：请求 Q1+Q3，断言结果只含 Q1/Q3（不含 Q2）——**Review Focus 1**。
  - `test_not_in_complement_empty_returns_empty`：`filters=[{region,not_in,全部合法 region}]` → `status=="empty"`，`total_rows==0`——**Review Focus 2**。
  - `test_not_in_complement_maps_to_in`：排除 `APAC` → 结果只含 Americas/EMEA。
  - `test_difference_and_change_rate`：两期比较，`difference=current-baseline`、`change_rate=(cur-base)/base`，由代码计算——**Review Focus 3**。
  - `test_zero_baseline_change_rate_null_with_flag`：基期 0 → change_rate 行 `value is None` 且 provenance/result 标注 `zero_baseline`。
  - `test_empty_result_is_empty_not_zero`：无该期数据 → `status=="empty"`，`rows==[]`，`result_ids==[]`。
  - `test_sort_value_topn_and_null_last`：按 value 降序 top_n=2 → 前 2 行，`null` 不在前列，并列稳定。
  - `test_sort_by_change_rate_uses_full_set`：排序依据完整结果，`truncated` 预览不影响排名——**Review Focus 5**。
  - `test_derived_detail_unsupported`：`gross_margin`+`region` → `status=="unsupported"`。
  - `test_result_saved_with_provenance`：成功时 `result_ids` 非空、`store.get(rid)` 存在、`provenance` 含 `data_version`/`catalog_version`/`prompt_version`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：适配（`period_basis` 决定 from/to 前缀；`not_in` 求补集，补集空 → `empty`；`group_by` 中 `company` 不进编译器明细维度）；`compile` → `query_df`（只读）；`filter_to_requested_periods` 精确裁剪；`compute_comparisons` 生成比较行并处理零/负基期；`apply_sort`（`absolute` → 按 `|key|`；`null` 末尾；并列按 company/分组键稳定）；落库经 `ResultStore.put`（长表，含 unit 与 meta：`catalog_version`/`prompt_version`/`data_nature`）；构造有界预览（`columns/rows/total_rows/truncated`）与 `provenance`。任何异常 → `status="error"`，`result_ids=[]`。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: add query plan executor`。

## Task 5：注册 `query_financial_data` 工具

**Files:** Modify `agent/tools/base.py`, `agent/tools/data.py`; Test `tests/test_query_tool.py`

**Interfaces:**
- Produces:
  - `class QueryFinancialDataArgs(BaseModel)`: `question: str`（`Field(description=...)`，模型只传自然语言任务；db/目录/as_of 由运行时绑定）
  - `agent.tools.data.ToolContext` 新增字段：`llm: Any = None`、`catalog: Any = None`、`query_backend: str = "metric"`、`extraction_calls: int = 0`
  - `_h_query_financial_data(args, ctx, step) -> Outcome`
- Consumes: `agent.query.catalog.BusinessCatalog`、`agent.query.extractor.extract_plan`、`agent.query.executor.execute_plan`、`agent.tools.data._stored`/`Outcome`

- [ ] **Step 1: 写失败测试**：
  - `test_tool_registered`：`"query_financial_data" in base.ARGS_MODELS` 且 `in base.DESCRIPTIONS` 且 `in data._HANDLERS`。
  - `test_tool_requires_question`：`validate_args("query_financial_data", {})` 抛错。
  - `test_tool_disabled_when_not_in_enabled_tools`：`enabled_tools=["list_metrics"]` → `execute` 返回 `ok=False`。
  - `test_tool_runs_with_mock_llm`：`ctx.llm` 为固定响应 MockLLM、`ctx.catalog` 来自夹具 → `execute("query_financial_data", {"question": "..."}, ctx)` 返回含 rid 的 `Outcome`。
  - `test_non_ok_status_no_rid`：提取返回 `unknown_value` → `Outcome.ok is False` 且 `rid is None`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：`base.py` 注册 args/描述；`data.py` 加 `ToolContext` 字段与 handler（内部调用 extractor→executor，把 `QueryToolResult` 渲染为模型可读文本 + `Outcome(rid=首个 result_id or None, ok=status=="ok")`；`empty` 也算成功但 rid 为空并按 PRD 说明）。`execute()` 已有的白名单/校验/缓存路径自动生效（`query_financial_data` 不入 `STORED_TOOLS` 缓存，避免同问题复用旧提取）。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: register query_financial_data tool`。

## Task 6：CLI `--query-backend` 与单 Agent 接入

**Files:** Modify `agent/cli.py`, `agent/loop.py`（仅构造 `ToolContext` 时传入 `llm`/`catalog`/`query_backend`）, `config.yaml`; Test `tests/test_query_tool.py`（cli 部分）

**Interfaces:**
- `--query-backend {metric,text2sql}`，默认 `None`（历史行为）。
- `enabled_tools` 计算：默认 = 现状；`metric`/`text2sql` = 受控集合 + `todo_write/final_answer/load_skill`（保持循环闭合）。
- `question.json`/`run_meta.json` 写 `agent_mode`、`query_backend`、有效工具白名单、`catalog_version`、`prompt_version`。

- [ ] **Step 1: 写失败测试**：`test_cli_accepts_query_backend`（argparse 解析三值）；`test_controlled_enabled_tools_metric/text2sql`（白名单精确集合）；`test_default_backend_keeps_legacy`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：cli 增参数与白名单计算；`make_run_ctx`/`loop` 把 `llm`、`catalog` 注入 `ToolContext`；`_export_package` 增字段。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: add query-backend CLI switch`。

## Task 7：多 Agent 接入 `agent/subagents.py`

**Files:** Modify `agent/subagents.py`; Test `tests/test_query_multi.py`

**Interfaces:**
- `ROLE_TOOLS` 增受控集（按 `parent.query_backend` 选择）：`worker` 与 `reviewer` 白名单见表。
- `SubAgentRunner._allowed_tools()`：当父运行 `query_backend ∈ {metric,text2sql}` 时用受控白名单；否则用历史 `ROLE_TOOLS`。

- [ ] **Step 1: 写失败测试**：`test_text2sql_worker_tools`（= `query_financial_data,list_metrics,calc,recall`）；`test_metric_worker_tools`；`test_reviewer_evidence_only_both_backends`（= `recall,read_analysis_object`）；`test_default_backend_legacy_tools`；`test_query_financial_data_not_available_in_metric_backend`。
- [ ] **Step 2: 跑测试确认失败**。
- [ ] **Step 3: 实现**：`subagents.py` 依据 `parent.query_backend` 分派白名单；`ctx.query_backend` 由 cli 设置。
- [ ] **Step 4: 跑测试确认通过**。
- [ ] **Step 5: Commit** `feat: wire query backend into multi-agent roles`。

## Task 8：文档与交付说明

**Files:** Modify `README.md`; Create `docs/query_financial_data.md`（完成/未完成说明）

- [ ] **Step 1**：README 增「选择新路径」命令（`--agent-mode multi_agent --query-backend text2sql`）与四种模式说明。
- [ ] **Step 2**：`docs/query_financial_data.md` 写：已实现项、未实现项（§1.2 不做）、如何用临时 DuckDB + 固定模型响应做本地验证。
- [ ] **Step 3: Commit** `docs: document text2sql query backend`。

---

## Self-Review

- **Spec 覆盖**：§2 复用（Task 5-7）／§3 任务边界（提示词 + Review Focus）／§4.1-4.4 接入（Task 5-7）／§5 工具与协议（Task 1-5）／§6 数据库适配与计算（Task 2-4）／§7 可比较模式（Task 6-7）／§8 运行记录（Task 6 + executor provenance）／§9 交付物（全）／§10 验收（测试名逐条对应）。
- **类型一致性**：`QueryPlan`/`ExtractionResult`/`QueryToolResult` 字段在 task 间一致；`execute_plan` 返回 `QueryToolResult` 供 Task 5 渲染。
- **未决点（我的取舍，标注为可选）**：提取提示词的具体措辞；`sort.absolute` 语义定为「按排序键绝对值」；`--query-backend` 缺省保持历史行为。

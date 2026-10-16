# `query_financial_data`：自然语言财务查询工具（Text2SQL 受控路径）

本文档对应 PRD `docs/prd-financial-agent-text2sql.md`，说明已实现范围、未实现范围、
运行方式与本地验证方法。**本文一切"通过"均指本地离线测试通过，不代表真实模型准确率通过。**

## 1. 是什么

模型只给一句自然语言，工具内部完成：

```
自然语言问题 → 结构化查询计划（提取模型，一次+有限修复）
            → 业务目录严格校验（公司/指标/期间/粒度/筛选值）
            → 确定性 SQL（复用 semantic/compiler.py）
            → 只读执行（read_only + 禁外部访问）
            → 离散期间精确过滤 → 比较（差额/变化率）→ 排序/TopN
            → 结果落库（rid） + provenance
```

模型**不写 SQL、不能改 db/as_of、不能自由查库**；SQL 由代码生成，数字由代码计算。

## 2. 运行方式

```powershell
# 推荐日常路径：多 Agent 编排 + text2sql 取数
.venv\Scripts\python -m agent.cli --question "联想 FY2024Q2 与 FY2024Q1 存货差额与变化率" `
    --agent-mode multi_agent --query-backend text2sql

# 对照路径：多 Agent + 语义层取数（旧通道）
.venv\Scripts\python -m agent.cli --question "..." --agent-mode multi_agent --query-backend metric
```

`--query-backend` 默认缺省（不传）时**保持历史行为**：不暴露 `query_financial_data`，
工具集与 Reviewer 能力与改动前一致。

### 受控工具白名单

| 模式 | Planner | Worker | Reviewer |
|---|---|---|---|
| 缺省（无 `--query-backend`） | 历史 | 历史大工具集 | 历史（含 `check_identities`） |
| `--query-backend metric` | `[]` | `list_metrics, query_metric, calc, recall` | `recall, read_analysis_object` |
| `--query-backend text2sql` | `[]` | `query_financial_data, list_metrics, calc, recall` | `recall, read_analysis_object` |

受控模式下 `run_sql`、`variance`、`working_capital`、`peer_compare` 等取数旁路**不可用**，
防止绕过提取通道。主循环另有 `todo_write/final_answer/load_skill`。

### 配置（`config.yaml` 的 `query:` 段）

| 键 | 默认 | 含义 |
|---|---|---|
| `extraction_temperature` | `0.0` | 提取模型温度；`null` 则回退 `llm.temperature_agent` |
| `max_format_repairs` | `1` | **仅格式/协议错误**允许的修复次数；业务歧义与目录外值不重试 |

有效值写入 `run_meta.json` 的 `query_config`；缺省路径不读取本段。

## 3. 查询计划 v1（模型输出，代码校验）

| 字段 | 说明 |
|---|---|
| `metrics` | 指标名或别名；复用 `semantic/metrics.yaml`（30 base + 7 derived） |
| `companies` | `Lenovo` / `HP` / `Dell` |
| `period_basis` | `fiscal`（FY2024Q2）或 `calendar`（2024Q2），映射依据 `periods` 表 |
| `periods` | 离散期间集合；区间取数后按集合精确裁剪 |
| `group_by` | `region` / `product_line`（仅基础科目有明细） |
| `filters` | `region`/`product_line` 的 `in` / `not_in`（`not_in` = 对目录合法值取补集） |
| `comparisons` | `difference` / `change_rate`，含 current/baseline |
| `sort` | 按 `value` 或某比较关系排序，`direction`、`top_n`、`absolute` |

### 执行语义（对应 PRD §5.2/§5.3、§10 验收清单）

- **离散期间**：先用区间取数，再按请求集合精确裁剪；被裁掉的中间季度不进入结果或排序。
- **排除筛选**：`not_in` 映射为对目录合法值的补集；补集为空 → `empty`，**不退化为全量**。
- **空值 vs 零**：DuckDB NULL 与 pandas NaN 都按缺失处理并剔除；没有可用数值 → `empty`（不是 0）。
- **零基期**：`change_rate` 基期为 0 → `value=null` 且 `flag=zero_baseline`。
- **排序/TopN**：在**完整结果**上排序（预览截断不影响排名），null 排末尾，并列稳定。
- **衍生指标 + 明细**：`gross_margin` 等的地区/产品线明细不支持 → `unsupported`（不执行部分计划）。
- **歧义/目录外值**：分别返回 `needs_clarification` / `unknown_value` / `unsupported`，
  并在 `issues` 里给出候选；非 `ok/empty` 状态**不生成 rid**。

## 4. 状态与返回

`QueryToolResult.status` ∈ `ok | empty | needs_clarification | unknown_value | unsupported | error`。

- `rows` 为长表：`company, metric, period, period_basis, unit, value, operation, current, baseline, flag`
  （明细查询另含 `region`/`product_line`）。
- 成功结果写入 ResultStore 并分配 `rid`（可被 `recall` 与答案 claim 引用）。
- `provenance`：`data_version`、`catalog_version`、`prompt_version`、`query_trace_id`（SQL+计划的哈希）。

## 5. 可追踪性

运行包 `runs/cli-<ts>/` 中：

- `question.json` / `run_meta.json` 记录 `query_backend`、`enabled_tools`、`catalog_version`、
  `extraction_prompt_version`、`extraction_calls`。
- `results.json` 中每个 rid 带工具名、计划参数、SQL 与数据行。
- trace 中每次提取请求（含超时/重试）与工具结果一一对应。

链路：答案 `[rN]` → `results.json` 行/SQL → 查询计划（`results.json` 的 args）→ 提取调用（trace）。

## 6. 已实现 / 未实现

### 已实现（本地离线测试覆盖）

- 业务目录（公司/期间/指标/单位/明细取值）、计划与返回协议、提取器（一次修复 + 业务歧义不重试）。
- 执行器：离散期间、`not_in` 补集、比较/变化率、零基期、空结果、排序/TopN、落库 + provenance。
- 工具注册 `query_financial_data`、`--query-backend` 开关与受控白名单（单 Agent 与多 Agent 两路径）。
- 运行元信息记录与提取调用计数。

### 未实现（对齐 PRD §1.2，本期不做）

- 不接入 Copilot Studio / Direct Line / Lenovo MySQL / Power BI / SAP / 业务权限系统。
- 不新建 FastAPI/MCP 服务、聊天前端、部署平台、长期记忆系统。
- 不重建数据库与 ETL；不实现完整评测平台、自动裁判、线上采样与看板。
- 仅一次输入、一次运行；需澄清时返回问题，用户补充后重跑，**不实现跨请求续聊**。
- 预测、财报正文检索、真实业务原因归因不进入新路径；不支持自定义币种/单位换算与任意 SQL。

## 7. 本地验证（离线，不联网）

```powershell
.venv\Scripts\python -m pytest tests/test_query_catalog.py tests/test_query_schema.py `
    tests/test_query_extractor.py tests/test_query_executor.py tests/test_query_tool.py `
    tests/test_query_cli.py tests/test_query_multi.py -q
```

测试用临时 DuckDB 小样本（`tests/conftest.py` 的 `query_db` 夹具，含离散期间、零基期、
region/product_line 明细）+ 固定模型响应，只覆盖协议与执行逻辑，不依赖外部服务。
全量回归：`.venv\Scripts\python -m pytest -q`。

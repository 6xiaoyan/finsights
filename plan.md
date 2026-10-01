# FinSights Data Agent — 实施计划（plan v0.1）

> 依据：`prd.md` v0.1　｜　日期：2026-10-01
> 执行者：GLM 5.3 Flash（编码）；人工负责：标注、抽检、关键决策（文中标 ⚠️ 的地方）

---

## 0. 给执行者的规则（必读）

1. **按阶段顺序执行**。每个任务都有"验收标准"，达标后再进入下一个任务。不要跳步，不要提前实现后面阶段的功能。
2. **接口以本文为准**。文中给出的函数签名、表结构、JSON 格式不要擅自改动。确实需要改时，先在本文第 12 节"变更记录"里写明原因，再改代码。
3. **遇到 ⚠️ 标记就停下来**，把问题和你的建议写进 `docs/questions.md`，等人工确认后再继续。
4. **任何数字都由代码计算**。不允许在 prompt 或代码里让 LLM 做算术。
5. **合规红线**：只使用公开财报和自行合成的数据，不接触任何联想内部数据。
6. 每完成一个任务就做一次 git commit，提交信息格式为 `[P<阶段>.<任务>] 简述`，例如 `[P1.2] 拉取 HP companyfacts`。
7. 每个模块都要写 pytest 测试，`pytest -q` 全部通过才算完成。
8. 不确定的事情（API 字段名、模型名、XBRL 标签）**先查官方文档或真实数据再写代码**，不要凭记忆编造。

---

## 1. 总览

### 1.1 技术栈
| 用途 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | — |
| 数据库 | DuckDB（单文件） | 分析型、零运维；支持只读连接；可以按 `as_of` 快速生成快照 |
| LLM | Agnes AI `agnes-3.0-flash`（OpenAI 兼容接口）；备选 DeepSeek V4.1 Flash | PRD 4.6 及其批注（2026-10-01 起切换，成本考虑） |
| 统计 | pandas、statsmodels（ETS/SARIMA） | — |
| 校验 | pydantic v2 | 工具参数和评测题目的 schema |
| 测试 | pytest | — |
| 追踪 | 先用本地 JSONL；之后可选接入 Langfuse | 先跑通再接入 |
| 配置 | `.env` + `config.yaml` | 模型名、base_url、预算等不要写死在代码里 |

### 1.2 目录结构
```
finsights/
├── prd.md / plan.md
├── docs/questions.md          # 需要人工确认的问题
├── config.yaml / .env.example
├── data/
│   ├── raw/                   # 原始下载（SEC JSON、联想 PDF），不改动
│   ├── manual/                # 人工录入的 CSV（联想）
│   ├── finsights.duckdb       # 基础库（真实数据 + 合成明细）
│   ├── scenarios/             # 异常注入后的场景库，每个场景一个 .duckdb
│   └── snapshots/             # 按 as_of 生成的快照库（缓存）
├── etl/                       # 下载、映射、入库、勾稽检查
├── semantic/
│   ├── metrics.yaml           # 指标定义
│   ├── compiler.py            # 指标请求 → SQL
│   └── catalog.py             # 给 agent 看的指标目录
├── fincalc/                   # 确定性计算库（同比、分解、周转、预测、勾稽）
├── agent/
│   ├── loop.py                # 主循环
│   ├── llm.py                 # LLM 客户端封装
│   ├── context.py             # 上下文管理与压缩
│   ├── result_store.py        # 工具结果存储与引用
│   ├── tools/                 # 每个工具一个文件
│   ├── skills/                # 每个 skill 一个目录，内含 SKILL.md
│   ├── hooks.py               # pre_tool / stop 钩子（护栏）
│   ├── verifier.py            # 数字核验
│   └── prompts/system.md
├── eval/
│   ├── datasets/              # l1.jsonl … l6.jsonl
│   ├── generators/            # 模板出题、异常注入
│   ├── graders/               # 每类题一个打分器
│   ├── runner.py
│   └── reports/
├── runs/                      # 每次运行的 trace
└── tests/
```

### 1.3 阶段划分与 PRD 里程碑对应
| 阶段 | 内容 | 对应 PRD 里程碑 | 详略 |
|---|---|---|---|
| P0 | 环境与骨架 | 第 1 周 | 略 |
| P1 | 数据入库 | 第 1 周 | 略（有几个坑要注意） |
| P2 | 语义层 + 计算库 | 第 1–2 周 | 中 |
| P3 | 评测框架 + L1/L2 题 + v0 基线 agent | 第 1 周 | 中 |
| P4 | **Agent 核心（类 Claude Code 架构）** | 第 2–3 周 | **详** |
| P5 | 异常注入 + L3 归因评测 | 第 2 周 | **详** |
| P6 | 预测 + `as_of` 隔离 + L4 评测 | 第 3 周 | 中 |
| P7 | L5 报告评测、judge 校准、L6 鲁棒性 | 之后 | 中 |
| P8 | 版本对比实验 | 持续 | 略 |

> 说明：PRD 的原则是"先搭评测，再做 agent"。但评测流程需要有一个被测对象，所以 P3 会先做一个最简单的 v0（纯 text2sql）agent 来跑通流程，正式的 agent 在 P4 实现。

---

## 2. P0 环境与骨架（略）

- [ ] `git init`；写 `.gitignore`（忽略 `.env`、`data/*.duckdb`、`data/snapshots/`、`runs/`）。
- [ ] 按 1.2 建目录；写 `pyproject.toml`，依赖：duckdb, pandas, statsmodels, pydantic, pyyaml, openai, httpx, python-dotenv, pytest。
- [ ] `config.yaml` 包括：`llm.base_url`、`llm.model`、`llm.temperature`（agent 0.2，judge 0）、`agent.max_steps=20`、`agent.max_tokens_budget`、`context.window`、`context.compact_threshold`。
- [ ] `agent/llm.py`：封装 chat 调用（支持 tools），带重试（指数退避，最多 3 次）和超时，返回 token 用量。
- ⚠️ DeepSeek V4.1 Flash 的准确模型名、上下文长度、是否支持并行工具调用、前缀缓存规则：**查 DeepSeek 官方文档**，写进 `config.yaml` 和 `docs/questions.md`，不要猜。

**验收**：`python -m agent.llm "ping"` 能返回结果；一个带 1 个工具的调用能正确返回 tool_call。

---

## 3. P1 数据入库（略写，注意几个坑）

### 3.1 任务
- [ ] **HP、Dell**：从 SEC `companyfacts` 接口拉 JSON（`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`，**必须带 User-Agent 请求头，包含联系邮箱**，限速 ≤10 次/秒）。CIK 要先到 EDGAR 上核实（HP Inc. 应该是 47217，Dell Technologies 应该是 1571996，以 EDGAR 为准）。原始 JSON 存到 `data/raw/`。
- [ ] **联想**：从联想投资者关系网站或港交所披露易下载季度业绩公告和年报 PDF，存到 `data/raw/lenovo/`。用 pdfplumber 抽取资产负债表和利润表关键行，输出到 `data/manual/lenovo_*.csv`。**人工抽检 10% 的数字**。
- [ ] 建立**标准科目表**（附录 A），把 XBRL 标签和联想报表行映射过去：`etl/account_map.yaml`。
- [ ] 入库到 `data/finsights.duckdb`，表结构见附录 B。

### 3.2 必须注意的坑
1. **时间窗口（已确认）**：从**自然年 2017 年起**（约 36 个季度）。HP 在 2015 年 11 月拆分，Dell 在 2016 年 9 月合并 EMC，此前的数据口径不可比，不入库。
2. **Q4 利润表要倒算**：10-K 和年报只给全年数，Q4 = 全年 − Q1 − Q2 − Q3。资产负债表是时点数，不需要倒算。
3. **XBRL 一个标签可能有多个值**（重述、不同 filing 重复报送）：同一个 (标签, 期末日) 取**最早 filed** 的值作为 `available_date` 时点的原始值，重述值另存为一条 `version=restated` 记录。这一点对 P6 的 `as_of` 防泄露很关键。
4. **财年对齐**：联想财年截止 3 月；HP 截止 10 月 31 日；Dell 截止 1 月底或 2 月初（52/53 周）。`periods` 表同时存财年季度和 `calendar_quarter`（按期末日所在的自然季度）。同行对比一律用 `calendar_quarter` 对齐。
5. **单位**：全部统一为**百万美元**（联想报表单位是千美元，要换算）。
6. **`available_date`**：SEC 数据取 `filed` 字段；联想取业绩公告日期。这是 `as_of` 隔离的依据。

### 3.3 入库后的勾稽检查
- [ ] `etl/check_identities.py`：对每家公司、每一期检查 资产 = 负债 + 所有者权益、分项加总 = 合计（容差 0.5%）。输出报告 `data/identity_report.md`。
- 不通过的期次：先查是不是映射漏了科目（例如少数股东权益、"其他"类科目）。实在对不上的，补一个 `other_*` 科目吸收差额，并在报告中记录。

**验收**：三家公司共同窗口内每期都有数据；勾稽检查 100% 通过（含吸收项）；`pytest tests/test_etl.py` 通过。

---

## 4. P2 语义层 + 计算库（中）

### 4.1 合成明细维度
- [ ] `etl/synth_detail.py`：对 营收、存货、应收账款 三个科目，按 地区（Americas / EMEA / APAC）× 产品线（PC / Infra / Other）拆分。
  - 份额用 Dirichlet 生成，随时间平滑漂移（相邻季度份额变化 < 3 个百分点），固定随机种子。
  - 硬约束：每期明细加总 = 总额，误差为 0（最后一格用总额减其他格得到）。
- ⚠️ 明细粒度（是否需要月度）是 PRD 待定问题，先只做季度。

### 4.2 指标定义 `semantic/metrics.yaml`
```yaml
- name: inventory
  label_zh: 存货
  type: base               # base = 直接取科目；derived = 由公式计算
  account: inventory
  unit: usd_mn
  dims: [company, period, region, product_line]
- name: dio
  label_zh: 存货周转天数
  type: derived
  formula: "avg(inventory, inventory_prev) / cogs * days_in_quarter"
  unit: days
  dims: [company, period]
  note: "季度口径；days_in_quarter 用实际天数"
```
至少包括：附录 A 中的所有 base 指标，以及 gross_margin、dso、dio、dpo、ccc、current_ratio、debt_to_equity。

### 4.3 编译器 `semantic/compiler.py`
```python
class MetricRequest(BaseModel):
    metrics: list[str]
    companies: list[str]
    period_from: str | None = None   # "FY24Q1" 或 "2024Q1"（自然季度）
    period_to: str | None = None
    last_n: int | None = None
    period_basis: Literal["fiscal", "calendar"] = "fiscal"
    group_by: list[str] = []         # 可选 region / product_line
    filters: dict[str, list[str]] = {}

def compile(req: MetricRequest) -> str: ...   # 返回 SQL，不执行
```
- 指标名或维度不存在时，报错信息里要**列出最接近的合法名称**（给 agent 自我纠错用）。
- derived 指标在 SQL 里算，或者取出 base 数据后交给 fincalc 计算，二选一，但必须确定性。

### 4.4 计算库 `fincalc/`
纯函数，输入 DataFrame，输出 DataFrame 或 dict，不做任何 IO：
| 函数 | 说明 |
|---|---|
| `yoy(df)`, `qoq(df)` | 同比 / 环比，绝对值和百分比 |
| `contribution(total_df, parts_df)` | 贡献度分解：各分项对总额变化的贡献，加总必须等于总变化 |
| `working_capital(df)` | DSO、DIO、DPO、CCC |
| `seasonal_baseline(series, quarter)` | 同一季度历年变化的分布：均值、标准差、分位数，以及当前值的 z-score 和分位 |
| `peer_compare(metric, period, companies)` | 同一自然季度各公司的变化及相对位置 |
| `check_identities(df)` | 返回违反项列表 |
| `forecast(series, method, horizon, level)` | P6 实现 |

**验收**：每个函数都有单元测试，包含手算的小例子；`contribution` 的加总误差 < 1e-9。

---

## 5. P3 评测框架 + L1/L2 + v0 基线（中）

### 5.1 题目格式（所有类别通用的字段）
```json
{
  "id": "l1_0001",
  "category": "L1",
  "question": "联想FY24Q3存货是多少？",
  "db": "base",                      // base 或场景 id（P5）
  "as_of": null,                     // P6 使用
  "gold": {"value": 6123.4, "unit": "usd_mn", "tolerance": 0.005},
  "gold_sql": "SELECT ...",          // 生成标准答案用的 SQL，便于复查
  "tags": ["inventory", "lenovo"]
}
```

### 5.2 出题
- [ ] `eval/generators/template_l1_l2.py`：用模板 × 实体组合生成，**标准答案由代码查库或用 fincalc 计算**。
  - L1：30 题（单个科目、单期、单公司；含自然季度的写法，如"2024 年第三季度"）。
  - L2：20 题（DIO/DSO 趋势、毛利率、环比、多公司对比）。
- [ ] 问法要多样化：每个模板准备 3–5 种中文说法；单位写法有"亿美元""百万美元"两种。
- [ ] 人工抽检 10 题，确认题意无歧义。

### 5.3 答案格式（所有 agent 版本都必须遵守）
agent 最后调用 `final_answer` 工具（见 6.4），输出：
```json
{"answer_md": "...", "claims": [{"text": "...", "value": 6123.4, "unit": "usd_mn", "ref": "r3"}], "status": "answered | clarify | refuse"}
```
L1/L2 打分器只看 `claims` 里的 `value`（容差比较）；不从自由文本中解析数字。

### 5.4 runner
- [ ] `eval/runner.py --agent v0 --datasets l1,l2 --trials 3 --concurrency 4`
- 每题跑 3 次，记录：是否正确、步数、工具调用次数、SQL 报错次数、token 用量、延迟、成本。
- 每次运行的 trace 存到 `runs/<run_id>/<qid>_<trial>.jsonl`（每条消息、每次工具调用一行）。
- 输出 `eval/reports/<run_id>.md`：按类别统计准确率均值 ± 标准差，以及过程指标。
- [ ] `eval/compare.py run_a run_b`：两次运行的对比表；差值小于两次运行方差之和时，标注"不显著"。

### 5.5 v0 基线 agent
- 最简单的 text2sql：system prompt 里放表结构，只提供 `run_sql` 和 `final_answer` 两个工具，没有语义层、skills、护栏和压缩。
- 它就是 PRD 6.8 版本对比表里的 v0 行。**不要优化它**。

**验收**：`runner.py` 跑完 L1/L2 × 3 次，生成报告；v0 的分数写入 `eval/reports/leaderboard.md`。

---

## 6. P4 Agent 核心：类 Claude Code 架构（详）

### 6.1 设计原则
借鉴 Claude Code 的做法：
1. **单一主循环 + 工具**。不做多 agent 编排框架和状态机。规划能力来自模型本身加一个 todo 工具。
2. **Skills 渐进加载**。system prompt 里只放每个 skill 的名字和一句话描述，需要时 agent 调用 `load_skill` 读全文。
3. **Hooks**。`pre_tool` 钩子做安全检查（SQL 只读、`as_of` 注入），`stop` 钩子做护栏（数字核验、勾稽校验）。
4. **子 agent**（后期可选）：上下文独立的子任务，例如写报告时每家公司各开一个子 agent。
5. **分层的上下文压缩**，在 Claude Code 的基础上利用"工具结果是确定性的"这一点做优化（6.6）。

本项目要额外保证的一点：**每个数字都必须有出处**。因此所有数据类工具的结果都进入一个 **Result Store**，并分配 `result_id`（r1, r2, ...）。最终答案中的数字必须引用某个 `result_id`。这套机制同时支撑了三件事：证据链、数字核验、上下文压缩。

### 6.2 主循环 `agent/loop.py`
```python
@dataclass
class RunContext:
    run_id: str
    db_path: str               # base / 场景库 / as_of 快照
    as_of: date | None
    store: ResultStore
    todos: list[Todo]
    loaded_skills: dict[str, str]
    budget: Budget             # 步数、token、时间
    trace: TraceWriter

def run(question: str, ctx: RunContext) -> FinalAnswer:
    messages = [system_message(ctx), user_message(question)]
    stop_retries = 0
    for step in range(ctx.budget.max_steps):
        view = context_manager.build_view(messages, ctx)          # 6.6：只改发送视图
        try:
            resp = llm.chat(view, tools=TOOL_SCHEMAS)
        except ContextTooLong:
            resp = llm.chat(context_manager.on_context_overflow(view, ctx), tools=TOOL_SCHEMAS)
        messages.append(resp.message)
        if not resp.tool_calls:
            # 模型没调用 final_answer 就结束了：提醒它，最多提醒 1 次
            messages.append(user_nudge("请调用 final_answer 工具提交答案。"))
            continue
        for call in resp.tool_calls:              # 只读工具可以并发执行
            hook = hooks.pre_tool(call, ctx)       # 可以拒绝或改写参数
            if hook.blocked:
                messages.append(tool_msg(call, hook.reason)); continue
            if call.name == "final_answer":
                verdict = hooks.stop(call.args, ctx)          # 6.8 护栏
                if verdict.ok or stop_retries >= 2:
                    return finalize(call.args, verdict, ctx)  # 未通过时标注"未通过验证"
                stop_retries += 1
                messages.append(tool_msg(call, verdict.feedback)); continue
            result = execute(call, ctx)            # 数据类工具写入 store
            messages.append(tool_msg(call, render(result)))
    return budget_exhausted_answer(ctx)
```
要点：
- `llm.chat` 的错误（超时、429）在 `llm.py` 内部重试；工具执行出错**不抛异常**，而是把错误文本作为工具结果返回给模型，让它自我纠正。
- 每一步都写 trace：输入 token 数、输出、工具调用参数、结果的 result_id、耗时。
- 预算耗尽时返回 `status=refuse`，并说明原因，**不要编造答案**。

### 6.3 消息里的工具结果长什么样
数据类工具返回给模型的内容统一渲染成：
```
[r7] query_metric(metrics=[inventory], companies=[Lenovo,HP,Dell], last_n=8, period_basis=calendar)
rows=24 cols=[company, calendar_quarter, inventory]  unit=usd_mn
| company | calendar_quarter | inventory |
|---|---|---|
| ... 最多 50 行 ...
（结果超过 50 行时：只显示前 50 行，并提示"完整结果可用 recall(r7, offset=50) 查看"）
```
第一行（result_id + 工具名 + 参数）就是 6.6 中压缩后保留下来的部分。

### 6.4 工具清单与规格
所有工具的参数都用 pydantic 定义，自动生成 JSON schema。**工具描述要写清楚"什么时候用"和"什么时候不用"**，这比参数说明更重要。

| 工具 | 类型 | 说明 |
|---|---|---|
| `list_metrics()` | 只读 | 返回指标目录（名称、中文名、单位、可用维度） |
| `query_metric(MetricRequest)` | 数据 | **首选**取数工具，经语义层编译出 SQL 后执行；结果中附带生成的 SQL |
| `run_sql(sql)` | 数据 | 兜底工具；只读；最多 200 行；描述里写明"只在 query_metric 无法表达时使用" |
| `calc(expression, refs)` | 数据 | 安全的算术表达式，例如 `"r3.inventory[Lenovo,FY24Q2] / r3.inventory[Lenovo,FY23Q2] - 1"`；用 AST 白名单求值，**禁止 eval** |
| `variance(metric, company, period, basis)` | 数据 | 同比/环比 + 贡献度分解（有明细维度时） |
| `seasonal_check(metric, company, period)` | 数据 | 当前变化在历年同季度变化中的分位和 z-score |
| `peer_compare(metric, calendar_quarter)` | 数据 | 三家公司同一自然季度的对比 |
| `working_capital(company, periods)` | 数据 | DSO/DIO/DPO/CCC |
| `check_identities(company, periods)` | 数据 | 勾稽校验 |
| `get_filing_notes(company, period)` | 数据 | 报表附注（P5 中由注入器写入的口径变更说明也从这里读） |
| `forecast(...)` | 数据 | P6 |
| `recall(result_id, offset=0)` | 只读 | 从 Result Store 重新取出完整结果（不重新查库） |
| `load_skill(name)` | 只读 | 加载 skill 全文 |
| `todo_write(items)` | 状态 | 写计划或更新计划（借鉴 Claude Code 的 TodoWrite） |
| `final_answer(answer_md, claims, status)` | 终止 | 见 5.3；`status=clarify` 时 `answer_md` 写澄清问题 |

注意：
- `variance`、`seasonal_check` 等分析工具在内部调用 fincalc，**同样产生 result_id**，所以分析得出的派生数字也可以被引用。
- 不提供任意 Python 执行工具：会破坏确定性，也会带来安全问题。
- `calc` 的结果也进 store，保证"差了 18%"这种派生数字可以追溯。

### 6.5 Result Store `agent/result_store.py`
```python
@dataclass
class StoredResult:
    id: str                    # r1, r2, ...
    tool: str
    args: dict
    data_version: str          # db 文件的哈希 + as_of
    df: pd.DataFrame
    sql: str | None
    created_step: int
    digest: str                # 代码生成的摘要，见 6.6

class ResultStore:
    def put(self, tool, args, df, sql, step) -> StoredResult
    def get(self, rid) -> StoredResult
    def find_value(self, rid, value, unit, tol=0.005) -> bool   # 数字核验用，处理单位换算和百分比
    def dump(self, path)                                        # 随 trace 一起落盘，作为证据链
```
- 缓存：`(tool, args, data_version)` 相同时直接返回已有结果，不重复查库（模型经常重复查询）。

### 6.6 上下文管理与压缩（重点）

#### 6.6.1 参考：Claude Code 的做法
以下信息来自第三方的源码分析（基于 2026 年 3 月泄露的 v2.1.88 源码），不是官方文档，常量可能随版本变化，但整体结构可以参考。

**(1) 每次调用模型之前，按从便宜到昂贵的顺序依次经过 5 道处理**：
| 层 | 名称 | 做什么 | 调用 LLM |
|---|---|---|---|
| 1 | Budget Reduction | 单个工具结果超过大小上限时，替换成"引用 + 预览"，完整内容落盘 | 否 |
| 2 | Snip | 按时间裁掉较早的对话片段，并插入一条边界消息 | 否 |
| 3 | Microcompact | 对白名单内的工具（读文件、shell、grep、web 等）清除较早的结果内容，tool_use 记录保留。有一个 cache-aware 版本：通过 Anthropic 的 cache_edits 在服务端删除，**不破坏前缀缓存** | 否 |
| 4 | Context Collapse | 不改动存储的历史，只在发送前生成一个"投影视图" | 否 |
| 5 | Auto-Compact | 先尝试 **Session Memory Compact**（用后台维护的结构化记忆替换旧消息，不额外调用 LLM）；不够用时才做 **Full Compact**（LLM 按 9 段模板总结） | 部分调用 |

另外还有两个兜底机制：
- **Reactive compact**：API 返回上下文超长错误时，只压缩够用的部分，每轮最多触发一次。
- **PTL 重试**：压缩请求本身超长时，按 API 轮次分组后从最早的组开始丢弃，最多重试 3 次。

**(2) Full Compact 的 9 段摘要模板**：①用户主要请求与意图 ②关键技术概念 ③文件与代码片段（要求保留完整代码片段）④错误与修复 ⑤问题解决过程 ⑥用户的所有消息（逐条列出）⑦待办任务 ⑧当前工作 ⑨可选的下一步。
- 模型先在 `<analysis>` 中按时间顺序梳理，再写 `<summary>`；`<analysis>` 部分由代码删除，不进入上下文。
- 部分压缩时有变体：⑧ 改为"已完成的工作"，⑨ 改为"继续工作所需的上下文"。

**(3) 压缩之后重新注入**：最近读过的文件（最多 5 个，共 50K token，每个最多 5K）、计划、已加载的 skills（每个有 token 上限）、工具声明、预算。

**(4) 工程细节**：
- 触发阈值 = 有效窗口 − 13K，其中有效窗口 = 模型窗口 − 20K（为摘要输出预留）。
- 连续压缩失败 3 次就熔断，不再自动压缩。
- 所有截断都按"API 轮次"分组执行，保证 tool_use 和 tool_result 不会被拆开。

Anthropic API 也提供了同类的官方能力：`clear_tool_uses`（参数有 trigger、keep、clear_at_least、exclude_tools）和服务端 compaction。

**结论**：我在 v0.1 中设计的 T1（替换工具结果）大致对应 Microcompact，T2 对应 Full Compact。但 v0.1 缺了几样东西：Session Memory 这一层、压缩后的重新注入、reactive 兜底、熔断，以及按轮次分组的不变量。下面按 Claude Code 的结构重新设计，并在每一层做本场景的优化。

#### 6.6.2 本场景的两个关键差异
1. **数据都在 Result Store 中，是确定性的，可以无损召回。** 在 Claude Code 里，旧的文件内容被清掉后，只能重新读文件，而且文件可能已经被修改。在本项目中，`recall(rid)` 一定能拿回原样的结果。所以工具结果这部分的压缩**几乎是无损的**。真正有损的只有模型的推理过程（看过哪些数据、排除了哪些假设、为什么）。
2. **推理过程可以由代码结构化记录。** 分析类工具（seasonal_check、peer_compare、variance）的输出本身就是对假设的检验结果，例如"z=3.1，超出历年同季度区间"。所以 Claude Code 中需要 LLM 维护的 Session Memory，在这里可以**由代码自动维护**，称为证据账本（Evidence Ledger）。

另外，DeepSeek **没有** cache_edits 这类接口（⚠️ 以官方文档为准）。任何对历史消息的改动都会让之后的前缀缓存失效，所以修改历史要**攒批、一次性做**，并且每次至少释放一定数量的 token 才值得（对应 `clear_at_least`）。

#### 6.6.3 压缩流水线（每次调用模型前，按顺序执行）
| 层 | 名称 | 对应 Claude Code | 触发条件 | 动作 | 调用 LLM |
|---|---|---|---|---|---|
| L1 | 结果预算 | Budget Reduction | 每次工具返回时 | 超过 50 行或 2K token：只放前 50 行 + 摘要 + `recall(rid, offset)` 提示 | 否 |
| L2 | 轮次折叠 | Snip | 多轮会话中，已经完成并且通过护栏的问答轮 | 整轮替换为：用户问题 + final_answer（含 claims 和 rid） | 否 |
| L3 | 结果存根化 | Microcompact | 可压缩的旧结果总量 ≥ `clear_at_least`（默认 8K token），并且上下文超过有效窗口的 50% | 除最近 3 个之外的可压缩结果，都替换成存根（工具名 + 参数 + rid + 代码摘要） | 否 |
| L4 | 账本压缩 | Session Memory Compact | 上下文超过 `有效窗口 − 13K` | 用证据账本替换旧的消息轮，保留最近 2 轮 | 否 |
| L5 | 全量摘要 | Full Compact | L4 之后仍超过阈值 | LLM 按领域版 9 段模板总结，**摘要中的数字要经过 verifier 核验** | 是 |
| 兜底 | Reactive / PTL | 同名机制 | API 返回上下文超长 | 按轮次分组，从最早的组开始丢弃，最多重试 3 次 | — |
| 熔断 | Circuit breaker | 同名机制 | 连续 3 次压缩失败 | 停止压缩，以 `status=refuse` 结束，并说明原因 | — |

**L2 轮次折叠**是本场景特有的优化。final_answer 本身就是一份**经过护栏核验的摘要**（每个数字都有 rid），所以把一个已完成的问答轮折叠成"问题 + 答案"几乎不会损失信息。这在多轮会话中非常有效。

**L3 的白名单和例外**：
- 可压缩：query_metric、run_sql、variance、seasonal_check、peer_compare、working_capital、check_identities、forecast、get_filing_notes、list_metrics、recall
- 不压缩：load_skill、todo_write、护栏反馈、用户消息
- 报错的结果：压成一行 `已失败: <原因>`
- **被最近一条 assistant 消息引用过 rid 的结果暂不压缩**（模型正在使用它）

存根格式：
```
[r7 已压缩] query_metric(metrics=[inventory], companies=[Lenovo,HP,Dell], last_n=8, period_basis=calendar)
摘要: rows=24; Lenovo 最新 6,123 (2024Q3) QoQ +18.2%; HP +2.1%; Dell -0.5%
原始数据: recall("r7")
```
- tool_call 本身（参数）保持不变，只替换 tool 消息的 content，所以配对关系不受影响。
- 摘要由 `digest()` 纯代码生成，上限 200 字符；生成规则见下。

`digest()` 的生成规则：
- 单值：直接给出数值；
- 时间序列：最新值、上期值、变化率，以及最大值和最小值所在期；
- 多公司：每家公司一段，同上。

**L4 证据账本**（`agent/ledger.py`，由代码维护，每次工具返回后更新）：
```markdown
## 口径
公司: Lenovo | 期间口径: fiscal | as_of: 2024-11-15 | 单位: usd_mn | db: <scenario hash>
## 已取得的数据
- r3 query_metric(inventory, Lenovo, last_n=8) → 最新 6,123 (FY24Q2), QoQ +18.2%
- r5 seasonal_check(inventory, Lenovo, FY24Q2) → z=3.1, 历年同季度 P95=+9.0%
## 假设检验记录（由分析工具的结果自动生成）
- seasonal: ✗ 超出历年同季度区间 (r5)
- industry_wide: ✗ HP +2.1%, Dell -0.5% (r7)
- reclassification: ✓ 附注无口径变更 (r9)
## 失败记录
- run_sql: 列 inv 不存在 → 已改用 query_metric
## 计划（todo 的当前状态）
```
- 账本**完全由代码生成**：数字直接取自 Result Store，不可能出现幻觉。
- 每类分析工具都要实现一个 `to_ledger_entry(result) -> str`，把结果转成假设检验记录。
- L4 之后的上下文 = system prompt + 账本 + 已加载的 skills + 用户原始问题 + 最近 2 轮。

**L5 领域版 9 段模板**（把 Claude Code 的 9 段改成适合数据分析的内容）：
| # | Claude Code | 本项目 | 由谁生成 |
|---|---|---|---|
| 1 | 用户主要请求与意图 | 用户问题（原文）和意图 | LLM |
| 2 | 关键技术概念 | **口径与约定**：财年还是自然季度、单位、as_of、公司 | 代码（取自账本） |
| 3 | 文件与代码片段 | **已取得的数据**：rid 索引和摘要 | 代码（取自账本） |
| 4 | 错误与修复 | 报错、被护栏打回的原因及修正方式 | 代码 + LLM |
| 5 | 问题解决过程 | **假设检验记录**：已排除的假设和依据（rid） | 代码给出记录，LLM 补充推理 |
| 6 | 用户的所有消息 | 用户的所有消息（逐条） | 代码（直接复制） |
| 7 | 待办任务 | todo 状态 | 代码 |
| 8 | 当前工作 | 当前工作 | LLM |
| 9 | 可选的下一步 | 下一步 | LLM |

- 第 2、3、6、7 段直接由代码填写，**LLM 只写第 1、4、5、8、9 段的推理部分**。这样调用 LLM 的输出更短，出错的空间也更小。
- 输出先写 `<analysis>` 再写 `<summary>`，`<analysis>` 由代码删除。
- **摘要核验**（本项目特有）：对摘要运行 verifier，所有数字都必须带 rid，并且能在 Result Store 中找到。不通过就重新生成一次，仍不通过则退回到 L4 的结果，只用账本。

**压缩之后重新注入**（L4 和 L5 都要做）：system prompt（不变，保证前缀缓存）、已加载的 skills 全文（每个上限 3K token）、todo 列表、用户原始问题、证据账本、as_of 提醒。

#### 6.6.4 必须遵守的不变量（写成 pytest）
1. **按 API 轮次分组**：一个 assistant 消息加上它的所有 tool 结果是一个原子组，删除或替换都以组为单位。任何时候都不能出现孤立的 tool_call 或 tool 结果（否则 API 直接报错）。
2. 压缩只修改**发送视图**（类似 Context Collapse 的投影），trace 和存储的原始历史永不改动。
3. system prompt 在整个运行过程中逐字节不变。
4. 压缩后，所有曾经出现过的 rid 都能 recall。
5. 每次压缩都写一条 trace 事件：层级、压缩前后的 token 数、被处理的 rid、缓存命中变化。

#### 6.6.5 实现 `agent/context.py`
```python
class ContextManager:
    def build_view(self, history: list[Message], ctx) -> list[Message]:
        view = apply_result_budget(history, ctx)               # L1（实际在工具返回时已经处理）
        view = fold_completed_turns(view, ctx)                 # L2
        if should_stub(view, ctx):                             # L3：阈值 + clear_at_least
            view = stub_old_results(view, keep_last=3, ctx=ctx)
        if tokens(view) > ctx.cfg.autocompact_threshold:
            view = ledger_compact(view, ctx)                   # L4
            if tokens(view) > ctx.cfg.autocompact_threshold:
                view = full_compact(view, ctx)                 # L5，带 verifier 和熔断
        return view

    def on_context_overflow(self, view, ctx) -> list[Message]: # reactive / PTL
        ...
```
- 压缩状态（哪些 rid 已经存根化、当前用的是哪份账本或摘要）要保存在 ctx 中。下一轮直接复用同一个视图，**不要每轮重新计算出不同的结果**，否则会破坏前缀缓存。
- 阈值全部从 config 读取。⚠️ DeepSeek V4.1 Flash 的窗口大小和缓存规则需要查文档后填入。

#### 6.6.6 压缩的评测（版本对比表中的一行）
- [ ] 构造 15 道长任务题：L5 三家公司报告、一个 session 内连续 5 问的多轮对话、需要 8 步以上的归因题。
- [ ] 评测时人为把窗口设小（例如 16K），以触发压缩。对比 5 组：
  | 组 | 配置 | 目的 |
  |---|---|---|
  | A | 不压缩，用真实的大窗口 | 上界参考 |
  | B | 只有 L5 通用模板（原版 Claude Code 9 段） | 朴素基线 |
  | C | 复刻 Claude Code 的结构：L1 + 通用 microcompact（只留占位符"旧结果已清除"）+ L5 通用模板 | 原版结构基线 |
  | D | 本方案：L1–L5 全部，包括存根摘要、账本、领域模板和摘要核验 | 主方案 |
  | E | D 去掉存根中的摘要（只留工具名 + 参数） | 消融：摘要是否必要 |
- 指标：正确率、数字忠实度、recall 调用次数、总 token、缓存命中率、成本、是否触发 L5。
- 预期：D 的正确率接近 A，并且高于 C；D 的成本低于 B 和 C。**实验不支持这个预期时，如实记录**。
- 面试时的讲法："我复刻了 Claude Code 的压缩体系作为基线，再利用'数据可确定性召回'这个领域特性做了改进，并用评测证明了改进的收益"。

### 6.7 Skills `agent/skills/<name>/SKILL.md`
格式：
```markdown
---
name: attribution
description: 回答"为什么某指标变化了"类问题时使用。给出根因标签和证据。
---
（正文：步骤、需要调用的工具、判断规则、输出要求）
```
system prompt 中只列出 name 和 description。agent 调用 `load_skill` 后，正文作为工具结果进入上下文，并且**不会被压缩**。

初版 5 个 skill：
| skill | 正文要点 |
|---|---|
| `retrieval` | 先用 list_metrics 找到指标；区分财年和自然季度；单位换算规则；有歧义时用 clarify |
| `attribution` | **固定步骤**：①量化变化（variance）②与历年同季度比较（seasonal_check）③同行同季度比较（peer_compare）④检查对手科目（如存货对应应付和现金）⑤查附注，看有无口径变化（get_filing_notes）⑥检查总额不变但内部转移的情况（variance 中的 contribution）⑦从**根因标签枚举**（附录 C）中选择标签，并给出置信度。**变化在正常范围内时，必须回答 no_anomaly**，不能硬找理由 |
| `working_capital` | DSO/DIO/DPO/CCC 的口径和解读，以及何时需要同行对比 |
| `forecast` | P6 编写：预测数字只能来自 forecast 工具；必须报告区间和基线对比 |
| `report` | P7 编写：报告结构、每个结论都带 result_id、需要说明不确定性 |

业务知识（软先验，PRD 4.3）写在 `attribution` skill 末尾的"可检验假设"小节中，例如"开学季备货：Q2 存货上升 → 必须用 seasonal_check 证明当前变化在历年同季度区间之内，否则不能用这条解释"。

### 6.8 Hooks 与运行时护栏 `agent/hooks.py`
**pre_tool**
- `run_sql`：用 sqlglot 解析，只允许 SELECT；拒绝 ATTACH、COPY、PRAGMA 等语句；自动加 LIMIT 200。
- 所有数据工具：连接只能打开 `ctx.db_path`（只读）。`as_of` 由 runtime 选择快照库来保证（P6），**模型无法修改**。
- `final_answer` 之前如果没有调用过任何数据工具，而答案里又有数字，直接打回。

**stop（护栏）**，在 `final_answer` 时执行，`agent/verifier.py`：
1. **引用核验**：每个 claim 的 `ref` 必须存在，`value` 必须能在对应结果中找到（`find_value`，容差 0.5%，处理 百万/亿、% 和小数之间的换算）。
2. **未引用数字扫描**：用正则提取 `answer_md` 中的所有数字，排除年份、季度标签、序号后，每个数字都必须对应某个 claim。
3. **勾稽校验**：答案涉及的 (公司, 期间) 跑一次 `check_identities`，有违反就提示。
4. **归因题额外检查**（agent 调用过 attribution skill 时）：答案中必须包含根因标签，且标签在枚举中。

不通过时，把具体问题作为反馈返回给模型，例如 `claim 2 的数值 18.5% 在 r7 中找不到，r7 中最接近的是 18.2%`，最多重试 2 次。仍不通过就在最终答案中标注"⚠ 以下数字未通过验证：..."。

### 6.9 子 agent（P7 再做，可选）
- `spawn_subagent(task, skills)`：开一个新的上下文，共享同一个 Result Store（这样 result_id 全局有效），返回结构化摘要和 result_id 列表。
- 用于 L5 报告：每家公司一个子 agent，主 agent 负责汇总。这也是一种上下文压缩，可以作为 6.6.4 实验中的另一组。

### 6.10 System prompt 骨架 `agent/prompts/system.md`
```
你是财务数据分析 agent。数据：联想、惠普、戴尔的季度资产负债表和利润表关键行，单位百万美元。
## 铁律
1. 你不做任何算术。所有数字来自工具，派生数字用 calc。
2. 最终答案的每个数字必须在 claims 里引用 result_id。
3. 会计恒等式不可违反；业务常识只是待检验的假设，必须用数据证明。
4. 变化在正常范围内时，如实说"未见异常"，不要编造原因。
5. 问题有歧义或前提错误时，用 status=clarify / refuse。
## 财年说明
（三家公司财年起止表；"FY24Q2"按公司财年理解；"2024年第三季度"按自然季度理解）
## 可用 skills
{{skills 的 name: description 列表}}
## 工作方式
复杂问题先用 todo_write 列计划；做归因先 load_skill("attribution")。
```
system prompt 保持稳定（利于前缀缓存）：动态内容（`as_of` 日期等）放在第一条 user 消息里，不放 system。

### 6.11 P4 的任务拆分与验收
- [ ] P4.1 ResultStore + 渲染 + 数据类工具（不含 forecast）+ 单元测试
- [ ] P4.2 主循环 + trace + 预算控制；用 mock LLM 写测试（按脚本返回 tool_calls）
- [ ] P4.3 skills 加载 + todo_write
- [ ] P4.4 hooks + verifier；用构造的错误答案测试（数字错误、未引用数字、引用了不存在的 id）
- [ ] P4.5a 证据账本 `ledger.py` + 各分析工具的 `to_ledger_entry`
- [ ] P4.5b 压缩流水线 L1–L3 + 6.6.4 的不变量测试（**最常见的 bug：删除或改写消息后 tool_call_id 对不上，API 直接报错**）
- [ ] P4.5c L4 账本压缩 + 重新注入
- [ ] P4.5d L5 全量摘要（领域 9 段模板 + `<analysis>` 删除 + 摘要核验）+ reactive/PTL + 熔断
- [ ] P4.6 跑 L1/L2 评测，记为 v1 写入 leaderboard

**验收**：v1 在 L1/L2 上的准确率高于 v0；所有 pytest 通过；随机抽 3 条 trace 人工检查，能从答案中的每个数字追溯到 SQL。

---

## 7. P5 异常注入 + L3 归因评测（详）

### 7.1 场景库
每道归因题对应一个场景：复制基础库 → 注入 → 保存为 `data/scenarios/<scenario_id>.duckdb`。同时生成场景说明 `<scenario_id>.json`（即标准答案，**agent 永远看不到**）。阴性对照题直接用基础库，或者使用一个"空注入"的场景（推荐后者，这样 agent 无法通过库名猜到答案）。场景库的文件名用随机 id，不要包含注入类型。

### 7.2 注入器 `eval/generators/inject.py`
```python
class Injection(BaseModel):
    type: Literal["company_specific", "industry_wide", "mix_shift", "reclassification", "none"]
    companies: list[str]
    calendar_quarter: str
    account: str
    magnitude_sigma: float      # 以该科目历年同季度环比变化的标准差为单位
    counterpart: str | None     # 用来平衡恒等式的对手科目
    persist: bool               # 只影响当期，还是之后各期都保留
    seed: int

def inject(base_db, inj: Injection) -> tuple[str, dict]   # 返回场景库路径和标准答案
```

各类型的实现（**每种都必须保持 A = L + E 和分项加总 = 合计**）：
| 类型 | 做法 | 平衡方式 | 标准答案 |
|---|---|---|---|
| company_specific | 只修改一家公司：科目 += Δ | 对手科目同向调整：存货 +Δ → 应付账款 +Δ（负债方）或 现金 −Δ（资产内部）；同时修改上级合计科目 | `company_specific` + 科目 + 方向 |
| industry_wide | 三家公司在同一自然季度都修改，幅度 Δ×(1±20% 随机) | 同上 | `industry_wide` |
| mix_shift | 在同一合计科目的两个子科目之间转移 Δ，合计不变；或者在合成明细中转移地区份额 | 合计本身就不变 | `mix_shift` |
| reclassification | 从 t 期起，把科目 A 的一部分持续划到科目 B（persist=True），并在 `filing_notes` 表写一条附注："自本期起，X 由 A 重分类至 B" | 同一侧科目之间转移 | `reclassification` |
| none | 不修改 | — | `no_anomaly` |

幅度规则：
- 注入题：`magnitude_sigma` 在 2.5–4 之间，保证变化在统计上明显。
- 选期规则：**先计算真实数据中每个 (公司, 科目, 期间) 的 z-score**。注入题只在真实 |z| < 1 的期间注入（避免和真实异常叠加）；阴性对照只选真实 |z| < 1 的期间。
- 真实 |z| > 2.5 的期间作为"真实事件"候选，交给人工结合 MD&A 标注（7.4）。

利润表的联动：存货注入只影响资产负债表；如果注入的是营收类科目，需要同步修改应收账款、毛利，保持一致。⚠️ 第一版只注入资产负债表科目，利润表联动放到后面。

### 7.3 出题与打分
- [ ] 20 道注入题（4 种类型各 5 道）+ 10 道阴性对照题（问法和注入题一样："为什么 X 明显上升？"，这类问题带有错误前提）。
- 题目 JSON 在 PRD 6.3 的基础上增加 `scenario_id`，`expected_root_cause` 改为结构化格式：
```json
"gold": {"label": "company_specific", "account": "inventory", "direction": "up"},
"must_not_claim": ["seasonal", "industry_wide"]
```
- agent 的 `final_answer` 在归因题中必须输出 `root_cause: {label, account, direction, confidence}`（attribution skill 中要求，verifier 检查）。
- 打分器 `graders/l3.py`（确定性的）：
  - top-1 命中：label 和 account 都对；
  - 误归因：说出了 `must_not_claim` 中的标签；
  - 误报率：阴性对照题中 label ≠ no_anomaly 的比例；
  - 证据完整性：trace 中是否调用了 seasonal_check 和 peer_compare（过程指标）。

### 7.4 真实事件（人工为主）
- [ ] 脚本列出真实 |z| > 2.5 的候选期间。
- [ ] 人工阅读对应期间的 10-Q/10-K MD&A 或联想业绩公告，标注 15–20 个事件，包括原因的原文摘录和根因标签。
- 打分：标签用确定性比对；原因描述用 LLM judge 比对（P7 校准之后才计入总分）。

**验收**：注入后的所有场景库都通过勾稽检查（写成 pytest，对每个场景都运行）；L3 跑完 3 次 × 30 题，生成报告。

---

## 8. P6 预测 + `as_of` 隔离 + L4（中）

### 8.1 `as_of` 快照
- [ ] `etl/snapshot.py make_snapshot(as_of) -> path`：从基础库复制 `available_date <= as_of` 的数据（并且对每个事实只取当时已披露的版本，不包含之后的重述），写入 `data/snapshots/asof_<date>.duckdb`，结果缓存复用。`facts_detail`、`filing_notes`、`periods` 没有 `available_date` 字段，要通过和 `facts` 中同一 (公司, 期间) 的首次披露日期关联来过滤，**不能整表复制**。
- runtime 根据题目的 `as_of` 设置 `ctx.db_path` 指向快照库。**agent 没有任何途径访问基础库**。
- [ ] 防泄露测试：对任一 as_of，用所有数据工具（包括 run_sql 查询任意表）都查不到晚于 as_of 的数据。

### 8.2 `fincalc.forecast`
- 方法：`seasonal_naive`（基线，等于去年同期值）、`ets`、`sarima`、`auto`（在训练段内做滚动验证，选 MASE 最低的方法）。
- 输出：点预测、80% 区间、所用方法、训练段内的 MASE。
- 序列太短（< 12 期）时退回 seasonal_naive，并在结果中注明。
- ⚠️ PRD 待定问题"统计模型和 LLM 推理的分工"，建议第一版：**数字只来自 forecast 工具，LLM 只负责选择方法和解释**，不允许 LLM 调整数字。之后可以做一个对照实验"LLM 根据附注或事件调整预测"，用 MASE 判断是否有改进。

### 8.3 L4 评测
- [ ] 滚动起点：对每家公司的 应收账款、存货、营收 3 个指标，在最后 8–12 个季度上逐期设置截止点。每个截止点出一道题，`as_of` = 截止期的 available_date。
- 打分器 `graders/l4.py`：在所有题目上汇总计算 MASE（以 seasonal_naive 为基准）和 80% 区间覆盖率。
- 报告中要同时列出"forecast 工具直接输出"的成绩，用于区分是 agent 的问题还是模型的问题。

**验收**：防泄露测试通过；L4 报告生成；MASE 打不过基线时如实记录。

---

## 9. P7 L5 报告、judge 校准、L6 鲁棒性（中）

### 9.1 L5 报告
- [ ] 10–15 道报告题（单公司营运资本分析、三家公司对比、某季度异常复盘）。
- [ ] 编写 `report` skill。
- 打分：
  1. **数字忠实度**：用一个独立的 LLM 调用（结论抽取，DeepSeek V4.1 Flash，temperature 0）从报告正文中抽取所有 (数字, 所描述的对象) → 逐条回查数据库。**不使用 agent 自己的 claims 列表**，避免自己给自己作证。
  2. **LLM judge**：按评分细则打 1–3 分，维度见 PRD 6.5。judge 的 prompt 只包含评分细则、报告和证据数据（Result Store 导出的表），不包含 agent 的推理过程。

### 9.2 judge 校准（人工）
- [ ] 生成 50 份报告（混合不同版本的 agent，使质量有高有低）。
- [ ] 人工按同一份评分细则打分 → `eval/datasets/judge_calibration.jsonl`。
- [ ] `eval/graders/judge_kappa.py` 计算 Cohen's kappa。< 0.6 就修改评分细则后重测，记录每一版细则和对应的 kappa。
- A/B 对比时交换顺序评两次，两次结论不一致的判为平局。
- kappa 达标前，judge 分数不进入 leaderboard。

### 9.3 L6 鲁棒性
- [ ] 人工构造 20 题：无法回答（比如问现金流量表、问没有的公司）、错误前提（"联想 FY24Q2 存货下降的原因"，实际上是上升）、有歧义（"Q3"没有说明财年还是自然年，且两种理解答案不同）。
- 打分：status 是否正确（refuse / clarify / 纠正前提），确定性判断。

---

## 10. P8 版本对比实验（略）
在 PRD 6.8 的表格基础上增加压缩实验和成本列，每个版本跑完整回归：
| 版本 | 改动 |
|---|---|
| v0 | 纯 text2sql |
| v1 | + 语义层 + Result Store + 计算工具 |
| v2 | + skills（attribution 等）+ 同行对比 |
| v3 | + 运行时护栏 |
| v4 | + 分级压缩（在长任务集上对比） |

每一行都要有 3 次运行的均值 ± 标准差。提升小于方差的改动，标注"不显著"。

---

## 11. 风险与需要人工决策的点
| # | 问题 | 建议 |
|---|---|---|
| 1 | 时间窗口：HP 拆分和 Dell 合并 EMC 导致早期数据不可比 | ✅ 已确认：从 2017 年开始，PRD 已同步 |
| 2 | 联想 Q1/Q3 业绩公告中是否有完整的资产负债表 | 先下载两期确认；缺失就在 periods 中标记，不能插值 |
| 3 | DeepSeek V4.1 Flash 是否支持并行工具调用、上下文长度、缓存规则 | 查文档；不支持并行就串行执行 |
| 4 | GLM 5.3 Flash 能力有限 | P4.5（压缩）和 P5.2（注入器）完成后，人工 review 代码，或者交给更强的模型 review |
| 5 | 同一个模型同时担任 agent 和 judge | 见 PRD 6.5 |
| 6 | 评测样本少（L3 只有 30 题） | 每题 3 次运行；报告中写出置信区间；之后用注入器扩充题量 |

---

## 12. 变更记录
| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-10-01 | 初版 | — |
| 2026-10-01 | 明确 total_equity 含少数股东权益；快照对无 available_date 的表按关联过滤 | 编写 verify.md 时发现的歧义 |
| 2026-10-01 | 时间窗口定为 2017 年起；重写 6.6 压缩设计（参考 Claude Code 的 5 层流水线和 9 段模板，加入证据账本和摘要核验） | 用户确认；对齐 Claude Code 的做法 |
| 2026-10-01 | LLM 由 DeepSeek V4.1 Flash 切换为 Agnes AI `agnes-3.0-flash`（限时免费）；`config.yaml` 的 base_url / model / context.window 同步更新（512K） | 用户决策：成本考虑；PRD 4.6 已加批注 |

---

## 附录 A：标准科目表（初版，可按实际数据增减）
**资产负债表**：cash, short_term_investments, accounts_receivable, inventory, other_current_assets, total_current_assets, ppe_net, goodwill, intangibles, other_noncurrent_assets, total_assets, accounts_payable, short_term_debt, accrued_liabilities, deferred_revenue_current, other_current_liabilities, total_current_liabilities, long_term_debt, other_noncurrent_liabilities, total_liabilities, noncontrolling_interest, total_equity, total_liabilities_and_equity

**利润表（SFR）**：revenue, cogs, gross_profit, operating_income, net_income

每个科目都要有：`code, name_zh, name_en, statement(BS/IS), parent_code, is_total, sign`。

口径约定：`total_equity` **包含**少数股东权益（`noncontrolling_interest` 是它的子项），因此 `total_assets = total_liabilities + total_equity = total_liabilities_and_equity`。

## 附录 B：表结构
```sql
CREATE TABLE companies (company_id TEXT PRIMARY KEY, name TEXT, fy_end_month INT, source TEXT);
CREATE TABLE periods (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT,
  period_end DATE, calendar_quarter TEXT,      -- 例如 '2024Q3'
  days INT, PRIMARY KEY (company_id, fiscal_year, fiscal_quarter));
CREATE TABLE accounts (code TEXT PRIMARY KEY, name_zh TEXT, name_en TEXT, statement TEXT,
  parent_code TEXT, is_total BOOLEAN, sign INT);
CREATE TABLE facts (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  value DOUBLE,                                -- 百万美元
  available_date DATE,                         -- 首次披露日期，as_of 依据
  version TEXT DEFAULT 'original',             -- original / restated
  source TEXT, source_ref TEXT,                -- 例如 XBRL 标签 + accn，或 PDF 页码
  is_derived BOOLEAN DEFAULT FALSE);           -- Q4 倒算值为 TRUE
CREATE TABLE facts_detail (
  company_id TEXT, fiscal_year INT, fiscal_quarter INT, account_code TEXT,
  region TEXT, product_line TEXT, value DOUBLE, synthetic BOOLEAN DEFAULT TRUE);
CREATE TABLE filing_notes (company_id TEXT, fiscal_year INT, fiscal_quarter INT, note TEXT);
```

## 附录 C：根因标签枚举
| 标签 | 含义 |
|---|---|
| `no_anomaly` | 变化在历年同季度的正常范围内 |
| `seasonal` | 有明显变化，但与历年同季度的规律一致 |
| `industry_wide` | 同行在同一自然季度出现同向的类似变化 |
| `company_specific` | 只有本公司出现异常变化 |
| `mix_shift` | 合计变化不大，内部结构发生转移 |
| `reclassification` | 口径变化（附注中有披露），不代表经营变化 |

归因答案格式：`{"label": ..., "account": ..., "direction": "up|down|none", "confidence": 0-1}`

# FinSights Data Agent — 验收文档（verify v0.1）

> 依据：`prd.md` v0.1、`plan.md` v0.1　｜　日期：2026-10-01
> 用途：执行者（GLM 5.3 Flash）完成每个阶段后，按本文逐项自检。**所有项目都通过，这个阶段才算完成。**

---

## 0. 使用规则（必读）

### 0.1 状态定义
| 状态 | 含义 |
|---|---|
| `PASS` | 已执行检查，结果符合通过标准，并且附上了证据 |
| `FAIL` | 已执行检查，结果不符合通过标准 |
| `BLOCKED` | 依赖某个 ⚠️ 问题，需要人工先做决定（同时写进 `docs/questions.md`） |
| `HUMAN` | 必须由人工确认的项目。**执行者不能把它标成 PASS**，只能准备好材料后标为 `HUMAN-待确认` |

### 0.2 证据要求
- 每个检查项都要在 `docs/verification_log.md` 中记一行：
  ```
  | 检查 ID | 状态 | 证据 | commit |
  | V1.6 | PASS | `python -m etl.check_identities` → "0 failures / 3 companies / 108 periods" | a1b2c3d |
  ```
- 证据必须是**真实执行的命令和真实的输出摘要**。只写"已完成"或"测试通过"不算证据。
- 输出太长时，存到 `docs/evidence/<检查ID>.txt`，日志里写文件路径。

### 0.3 禁止的行为（违反任何一条，整个阶段判为 FAIL）
1. 为了通过检查而修改本文的检查项或通过标准。
2. 修改测试或阈值让失败的测试变成通过：降低容差、加 `skip`/`xfail`、删除断言、改成 `assert True`。
3. 在 agent 代码中读取标准答案（`eval/datasets/*`、`data/scenarios/*.json`）。
4. 把 `HUMAN` 项目标为 `PASS`。
5. 针对评测题目做特殊处理（例如在 prompt 里写入某道题的答案或题目原文）。

确实认为某个检查项有问题时，在 `docs/questions.md` 中说明原因，由人工决定是否修改。

### 0.4 阶段关卡
每个阶段结束时：
1. 先跑一遍第 1 节的**全局检查**；
2. 再跑这个阶段的专项检查；
3. 再跑**之前所有阶段**的自动检查（回归）；
4. 全部 PASS（`HUMAN` 项已由人工确认）之后，打 git tag：`phase-P<n>-done`。

### 0.5 联网检查（live check）
需要真实 LLM 或网络的检查（V0.5、V0.6、V4.44、所有评测运行），**不放进 pytest**：
- 放在 `scripts/live/<检查ID>_*.py` 中，用 `python scripts/live/xxx.py` 单独运行；
- 运行输出保存为 `docs/evidence/<检查ID>_output.txt`（包含运行时间、模型名、原始响应摘要），作为证据；
- pytest 中**全部是离线测试**，LLM 一律用 mock。这样 G1 不受网络和限速影响，也不需要 skip。

---

## 1. 全局检查（每个阶段结束都要跑）

> 注意：表格单元格里的 `|` 要写成 `\|` 才能正确渲染，因此 G2–G7 的命令**不写在表格里，以下面代码块中的写法为准**（代码块里的 `|` 是真正的管道或"或"）。

```bash
# G2：测试数量
pytest -q --collect-only | tail -1
# G3：新增的 skip/xfail（P0 跳过）
git diff phase-P<n-1>-done..HEAD -- tests | grep -nE "^\+.*(skip|xfail)"
# G4：密钥泄露。① .env 没有被跟踪 ② 仓库中不包含 .env 里的任何密钥值 ③ 没有常见的密钥形态
git ls-files --error-unmatch .env 2>/dev/null && echo "FAIL: .env 被跟踪"
grep -E "KEY|TOKEN|SECRET" .env | cut -d= -f2- | grep -v '^$' | while read -r v; do git grep -nF "$v" && echo "FAIL: 密钥值出现在仓库中"; done
git grep -nE "sk-[A-Za-z0-9]{16,}|api_key\s*=\s*['\"][^'\"]+"
# G6：agent 不读取标准答案
grep -rnE "eval/datasets|scenarios/.*\.json|gold" agent/ semantic/ fincalc/
# G7：没有执行任意代码的入口
grep -rnE "(^|[^_.[:alnum:]])(eval|exec)\(|subprocess|os\.system" agent/ semantic/ fincalc/   # 不会误报 ast.literal_eval
```
上面每条命令的通过标准都是**无输出**（G2 除外，见下表）。

**自检正则本身是否有效**：第一次执行 G3、G4、G6、G7 时，先在一个临时分支上故意制造一处违规（例如在 tests 中加一行 `pytest.mark.skip`、在 agent/ 中写一行 `eval(`），确认命令**有输出**，然后删除这个临时分支。把这一步的结果记为证据 `G-selftest`。

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| G1 | 全部测试通过（离线） | 断网或不设置 API key 的情况下运行 `pytest -q` | 0 failed, 0 error（见 0.5：pytest 中不能有联网测试） |
| G2 | 测试数量没有减少 | 见上方代码块，与上一阶段日志中记录的数量比较 | 数量 ≥ 上一阶段 |
| G3 | 没有新增的 skip/xfail | 见上方代码块 | 无输出 |
| G4 | 没有提交密钥 | 见上方代码块 | 无输出 |
| G5 | 工作区干净，提交格式正确 | `git status --porcelain`；`git log --format=%s phase-P<n-1>-done..HEAD` | 无未提交的改动；每条提交都符合 `[P<n>.<m>] ...` |
| G6 | agent 不读取标准答案 | 见上方代码块 | 无输出 |
| G7 | 没有执行任意代码的入口 | 见上方代码块 | 无输出（calc 用 AST 实现，不能用 eval） |
| G8 | 本阶段的问题都已关闭 | 查看 `docs/questions.md` | 没有与本阶段相关的未决问题 |
| G9 | 合规 | 人工确认 `data/` 中只有公开财报数据和合成数据 | `HUMAN`（仅 P1 和 P5 结束时检查） |

---

## 2. P0 环境与骨架

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V0.1 | git 和 .gitignore | `git check-ignore .env data/finsights.duckdb data/snapshots/x runs/x` | 4 个路径都被输出（都被忽略） |
| V0.2 | 目录结构 | 检查 plan 1.2 中列出的每个目录 | 全部存在 |
| V0.3 | 依赖可以安装和导入 | `pip install -e .` 后运行 `python -c "import duckdb, pandas, statsmodels, pydantic, yaml, openai, httpx, dotenv"` | 无报错 |
| V0.4 | 配置项完整 | `python -c "import yaml;c=yaml.safe_load(open('config.yaml'));print(c['llm']['base_url'],c['llm']['model'],c['agent']['max_steps'],c['context']['window'])"` | 正常输出，不报 KeyError；代码中没有写死模型名（`grep -rniE "agnes|deepseek" agent/ --include=*.py` 无输出） |
| V0.5 | LLM 可以连通 | live check：`python -m agent.llm "ping"` | 返回非空文本，并打印 token 用量 |
| V0.6 | 工具调用可用 | live check（见 0.5）`scripts/live/V0.6_tool_call.py`：定义一个 `get_weather(city)` 工具，问"北京天气"；解析逻辑另用 mock 在 `tests/test_llm.py` 中测试 | 返回的 tool_call 名为 `get_weather`，参数中包含北京 |
| V0.7 | 重试逻辑 | 单元测试：mock 前 2 次返回 429，第 3 次成功 | 最终成功，共调用 3 次；连续 4 次失败时抛出异常 |
| V0.8 | 模型信息已查清 | `docs/questions.md` 中记录了**当前所用模型**（config 中的 `llm.model`）的模型名、上下文长度、是否支持并行工具调用、前缀缓存规则，**并且每一项都附有官方文档链接** | 4 项都有，并且都有链接 |
| V0.9 | thinking 模式显式配置 | `config.yaml` 中有 `llm.enable_thinking`（agent、judge 分别配置）；`agent/llm.py` 每次请求都显式传这个参数；离线测试检查请求体中有这个字段，并且响应中的 reasoning 内容不会被写回 messages | 通过 |

---

## 3. P1 数据入库

数据库检查统一用这个辅助命令（下文简写为 `Q "<SQL>"`）：
```bash
Q() { python -c "import duckdb,sys;print(duckdb.connect('data/finsights.duckdb',read_only=True).sql(sys.argv[1]).df().to_string())" "$1"; }
```

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V1.1 | 原始数据存在且合法 | `python -c "import json;[print(json.load(open(f))['entityName']) for f in ['data/raw/sec/CIK0000047217.json','data/raw/sec/CIK0001571996.json']]"`（CIK 以 EDGAR 核实后的为准） | 依次输出 HP 和 Dell 的公司名 |
| V1.2 | SEC 请求带 User-Agent | `grep -rn "User-Agent" etl/` | 有，并且包含邮箱 |
| V1.3 | 表结构符合附录 B | `Q "SELECT table_name, column_name FROM information_schema.columns ORDER BY 1"` | 6 张表和全部字段都存在 |
| V1.4 | 时间窗口：从 2017 年开始 | `Q "SELECT company_id, MIN(calendar_quarter), MAX(calendar_quarter), COUNT(*) FROM periods GROUP BY 1"` | 每家公司的 MIN 是 `2017Q1` 或 `2017Q2`；COUNT ≥ 34 |
| V1.5 | 季度连续，没有缺口 | 写脚本 `etl/check_gaps.py`：按公司检查 calendar_quarter 是否连续 | 输出"0 gaps"；有缺口的必须在 `docs/questions.md` 中列出原因（例如联想某季没有披露资产负债表），并标为 `BLOCKED` |
| V1.6 | 勾稽检查 | `Q "SELECT company_id, fiscal_year, fiscal_quarter FROM (SELECT company_id,fiscal_year,fiscal_quarter, MAX(CASE WHEN account_code='total_assets' THEN value END) a, MAX(CASE WHEN account_code='total_liabilities' THEN value END) l, MAX(CASE WHEN account_code='total_equity' THEN value END) e FROM facts WHERE version='original' GROUP BY 1,2,3) WHERE a IS NULL OR l IS NULL OR e IS NULL OR ABS(a-l-e)/a > 0.005"` | **0 行**（total_equity 含少数股东权益） |
| V1.7 | 分项加总 = 合计 | `python -m etl.check_identities` | 输出 0 failures；`data/identity_report.md` 中列出了所有使用 `other_*` 吸收项的期间及吸收金额 |
| V1.8 | 吸收项不能太大 | 检查 identity_report 中的吸收金额 | 任何一期的吸收项都 < 该期总资产的 5%；超过的列入 `docs/questions.md` |
| V1.9 | 核心科目没有缺失 | `Q "SELECT p.company_id,p.fiscal_year,p.fiscal_quarter,a.code FROM periods p CROSS JOIN (SELECT UNNEST(['total_assets','inventory','accounts_receivable','accounts_payable','revenue','cogs']) code) a LEFT JOIN facts f ON f.company_id=p.company_id AND f.fiscal_year=p.fiscal_year AND f.fiscal_quarter=p.fiscal_quarter AND f.account_code=a.code AND f.version='original' WHERE f.value IS NULL"` | 0 行 |
| V1.10 | 单位正确（百万美元） | `Q "SELECT company_id, MIN(value), MAX(value) FROM facts WHERE account_code='revenue' AND version='original' GROUP BY 1"`；另写脚本计算每家公司相邻季度营收的环比变化 | ①所有季度营收都在 5,000–100,000 之间；②环比变化都在 ±50% 以内，超出的逐条说明原因 |
| V1.11 | Q4 利润表是倒算的 | `Q "SELECT f.derivation, a.statement, f.fiscal_quarter, COUNT(*) FROM facts f JOIN accounts a ON f.account_code=a.code GROUP BY ALL ORDER BY ALL"` | `q4_backout` 只出现在利润表的 Q4；利润表 Q4 的 revenue 和 cogs 全部是 `q4_backout`；资产负债表中没有 `q4_backout`；`is_derived = (derivation IS NOT NULL)` 对所有行都成立 |
| V1.12 | 倒算结果正确 | 脚本：对 HP 和 Dell 的每个财年，Q1–Q4 营收之和 vs. 原始 JSON 中的 10-K 全年营收 | 每年误差 < 0.1% |
| V1.13 | 财年映射正确 | `Q "SELECT company_id, fiscal_quarter, MONTH(period_end) m, COUNT(*) FROM periods GROUP BY 1,2,3 ORDER BY 1,2"` | 联想 Q1 的期末月份 = 6；HP Q1 = 1；Dell Q1 = 4 或 5；每家公司每个季度只有 1–2 个期末月份（52/53 周造成的差异） |
| V1.14 | 披露日期合理 | `Q "SELECT COUNT(*) FROM facts f JOIN periods p USING(company_id,fiscal_year,fiscal_quarter) WHERE f.version='original' AND (f.available_date <= p.period_end OR f.available_date > p.period_end + INTERVAL 150 DAY)"` | 0 |
| V1.15 | 重述数据处理正确 | `Q "SELECT company_id,fiscal_year,fiscal_quarter,account_code,version,COUNT(*) c FROM facts GROUP BY 1,2,3,4,5 HAVING c>1"`；另外检查每条 restated 的 available_date 都晚于对应 original | 第一条查询 0 行；第二项全部满足 |
| V1.16 | 来源可追溯 | `Q "SELECT COUNT(*) FROM facts WHERE source_ref IS NULL OR source_ref=''"` | 0 |
| V1.17 | 联想数据抽检 | 随机抽取 10% 的联想数字（用固定种子生成抽样清单 `docs/evidence/V1.17_sample.csv`，包含 PDF 文件名和页码），由人工核对 | `HUMAN`：人工在清单上逐条标注 ✓/✗，错误率为 0 才通过。2026-10-01 起由两个 AI 模型分别独立核对（不能使用执行者的抽取代码），用户确认最终结果 |
| V1.18 | fiscal_year 写法统一 | `Q "SELECT company_id, MIN(fiscal_year), MAX(fiscal_year) FROM periods GROUP BY 1"` | 全部是 4 位数（2017–2027）；联想 FY18 记为 2018 |
| V1.19 | days 正确 | `Q "SELECT company_id, fiscal_year, fiscal_quarter, days, d FROM (SELECT *, DATEDIFF('day', LAG(period_end) OVER (PARTITION BY company_id ORDER BY period_end), period_end) d FROM periods) WHERE d IS NOT NULL AND d <> days"`；窗口第一期的 days 单独用窗口外那一期的期末日核对 | 0 行；Dell FY2017 Q4 = 98 |
| V1.20 | 口径连续性 | 脚本 `etl/check_continuity.py`：①列出每个 (公司, 标准科目) 在各年映射的报表行或 XBRL 标签，报表行发生变化的位置标出来；②列出每个科目环比变化超过历年同季度 3σ 的跳变 | 输出 `data/continuity_report.md`；每个映射变化和每个跳变都有一句说明（"真实经营变化"并附证据，或者"口径变化"并已写入 filing_notes，或者"映射错误"并已修复） |
| V1.21 | SEC 数据的披露日期准确 | 脚本：用 EDGAR submissions 接口拿到每个期间本身的 10-Q 或 10-K 的提交日期，与 original 版本的 available_date 比较 | 全部相等；不相等的逐条说明原因（例如 companyfacts 缺失、已人工补录） |
| V1.22 | 残差拆解 | 查看 identity_report | 每个占总资产超过 5% 的残差，都列出了由哪些报送标签或报表行构成，并且加起来与残差相符（误差 < 1%） |

---

## 4. P2 语义层 + 计算库

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V2.1 | 明细加总 = 总额 | `Q "SELECT d.company_id,d.fiscal_year,d.fiscal_quarter,d.account_code, SUM(d.value)-MAX(f.value) diff FROM facts_detail d JOIN facts f USING(company_id,fiscal_year,fiscal_quarter,account_code) WHERE f.version='original' GROUP BY 1,2,3,4 HAVING ABS(diff) > 1e-6"` | 0 行 |
| V2.2 | 份额变化平滑 | 脚本：计算每个 (公司, 科目, 维度格) 相邻季度的份额变化 | 最大值 < 3 个百分点 |
| V2.3 | 合成过程可复现 | 运行 `synth_detail` 两次，比较 `facts_detail` 按主键排序后的哈希 | 两次哈希相同 |
| V2.4 | 合成数据有标记 | `Q "SELECT COUNT(*) FROM facts_detail WHERE synthetic IS NOT TRUE"` | 0 |
| V2.5 | 指标定义完整 | 测试：加载 `metrics.yaml`，检查附录 A 的所有 base 指标，以及 gross_margin、dso、dio、dpo、ccc、current_ratio、debt_to_equity 是否存在；每个指标都有 unit 和 dims | 全部存在 |
| V2.6 | 编译器的报错有提示 | 测试：`compile(MetricRequest(metrics=["inventroy"], ...))` | 抛出的错误信息中包含 `inventory` |
| V2.7 | 编译器只生成 SELECT | 测试：用 sqlglot 解析 10 个不同请求编译出的 SQL | 全部是单条 SELECT |
| V2.8 | 语义层结果与直接查库一致 | 测试：随机抽 20 个 (公司, 期间, base 指标)，比较 query_metric 的结果和直接 SQL 查询 facts 的结果 | 全部完全相等 |
| V2.9 | 派生指标与手算一致 | 测试：挑 3 个具体的 (公司, 期间)，用测试代码中写死的原始数字手算 DIO、DSO、毛利率 | 误差 < 1e-6 |
| V2.10 | 自然季度对齐 | 测试：`period_basis="calendar"`，查 2024Q3 的存货 | 正好 3 行，每家公司 1 行 |
| V2.11 | 贡献度分解闭合 | 测试：随机生成 100 组数据，检查各分项贡献之和 = 总变化 | 误差 < 1e-9 |
| V2.12 | fincalc 是纯函数 | `grep -rnE "duckdb\|open\(\|requests\|httpx\|read_csv" fincalc/` | 无输出 |
| V2.13 | seasonal_baseline 正确 | 测试：构造一个已知分布的序列（例如每年 Q2 环比都是 +5%，当期为 +20%） | z-score 明显大于 2，分位数 = 100% |

---

## 5. P3 评测框架 + L1/L2 + v0 基线

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V3.1 | 题目数量和格式 | 脚本：用 pydantic 校验 `l1.jsonl`、`l2.jsonl` 的每一行 | L1 = 4 题，L2 = 6 题；全部通过校验；id 唯一 |
| V3.2 | 标准答案可复现 | 脚本：重新执行每道题的 `gold_sql`（L2 题重新调用 fincalc），与 `gold.value` 比较 | 10/10 一致 |
| V3.3 | 题目覆盖 | 查看题目 | L1 中至少 1 题用自然季度的写法、至少 1 题用"亿美元"；L2 覆盖 DIO/DSO 趋势、毛利率、环比、三家对比 |
| V3.4 | 打分器自检（正向） | 用一个"标准答案 agent"（直接返回 gold 值的 mock）跑 runner | L1/L2 都是 100% |
| V3.5 | 打分器自检（反向） | 用 mock agent 返回 gold × 1.01 | L1/L2 都是 0%（超出 0.5% 容差） |
| V3.6 | 打分器边界 | 用 mock 返回 gold × 1.004 | 判为正确（在容差内） |
| V3.7 | runner 的产出完整 | 用 v0 跑一次：`python -m eval.runner --agent v0 --datasets l1,l2 --trials 3` | `runs/<run_id>/` 中有 30 个 trace 文件；`eval/reports/<run_id>.md` 中有每类题的均值 ± 标准差，以及步数、工具调用次数、SQL 报错次数、token、延迟、成本 |
| V3.8 | trace 内容完整 | 随机打开 1 个 trace 文件 | 每一步都有一行记录，包括消息、工具调用参数、耗时和 token |
| V3.9 | compare 能判断显著性 | `python -m eval.compare <run_id> <run_id>`（同一个 run 和自己比） | 所有指标都标为"不显著" |
| V3.10 | v0 是最简版本 | 测试：v0 agent 的工具列表 | 只有 `run_sql` 和 `final_answer` |
| V3.11 | leaderboard | 查看 `eval/reports/leaderboard.md` | 有 v0 这一行，每格都是"均值 ± 标准差"，并注明 run_id 和 commit |
| V3.12 | 题目人工审阅 | 全部 10 题导出到 `docs/evidence/V3.12.md` | `HUMAN` |

---

## 6. P4 Agent 核心

### 6.1 Result Store、工具、主循环
| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V4.1 | 存取和缓存 | 测试：两次 put 相同的 (tool, args, data_version) | 返回同一个 rid；数据库只查了一次（用 mock 计数） |
| V4.2 | 单位换算核验 | 测试 `find_value`，至少覆盖以下用例 | 全部符合预期 |
| | | 存储 6123.4 (usd_mn)，claim 61.234 (usd_100mn) | 找到 |
| | | 存储 0.182 (ratio)，claim 18.2 (pct) | 找到 |
| | | 存储 6123.4，claim 6150 (usd_mn) | 找不到（超出 0.5%） |
| | | claim 引用不存在的 rid | 返回错误，不抛异常 |
| V4.3 | 渲染格式 | 测试：第一行符合正则 `^\[r\d+\] \w+\(.*\)$`；60 行的结果只显示 50 行，并出现 `recall(` 提示 | 通过 |
| V4.4 | 工具 schema | 测试：遍历所有工具 | 每个工具都有 pydantic 参数模型；描述中包含"何时使用"；工具列表与 plan 6.4 一致（forecast 在 P6 加入） |
| V4.5 | 工具参数中没有 db 和 as_of | 测试：遍历所有工具 schema 的字段名 | 不存在 `db`、`db_path`、`as_of` 等字段 |
| V4.6 | calc 的安全性 | 测试以下输入 | 全部被拒绝，并以工具错误文本返回，不抛异常 |
| | | `__import__('os')`、`open('x')`、`(lambda: 1)()`、`1/0`、`r99.x`（不存在的引用） | |
| V4.7 | calc 的正确性 | 测试：`r3.inventory[Lenovo,FY24Q2] / r3.inventory[Lenovo,FY23Q2] - 1` | 结果与手算一致，并产生新的 rid |
| V4.8 | run_sql 护栏 | 测试以下输入 | 全部被拒绝 |
| | | `INSERT ...`、`DROP TABLE facts`、`ATTACH 'x.db'`、`COPY facts TO 'x.csv'`、`PRAGMA ...`、`SELECT 1; DROP TABLE facts` | |
| V4.9 | 自动加 LIMIT | 测试：执行没有 LIMIT 的 SELECT | 最多返回 200 行 |
| V4.10 | 只读连接 | 测试：绕过护栏，直接用工具内部的连接执行 `CREATE TABLE t(x INT)` | 报错 |
| V4.11 | 主循环（mock LLM） | 用脚本化的 mock LLM 测试以下场景 | 每个场景都符合预期 |
| | | a. 正常：查数 → final_answer | 返回答案，状态为 answered |
| | | b. 没有调用工具就结束 | 提醒 1 次；第二次仍未调用 → 按预算处理 |
| | | c. 工具报错 | 错误文本作为工具结果返回，循环继续 |
| | | d. 达到 max_steps | status=refuse，答案中没有编造的数字 |
| | | e. final_answer 连续 3 次核验失败 | 第 3 次之后返回，答案标注"未通过验证" |
| | | f. 答案中有数字，但之前没有调用过数据工具 | 被 pre_tool 拦截 |
| V4.12 | 并发执行只读工具 | 测试：mock 一次返回 3 个只读工具调用 | 3 个结果都正确返回，tool_call_id 一一对应 |

### 6.2 Skills、System prompt、护栏
| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V4.13 | skill 渐进加载 | 测试：system prompt 中包含每个 skill 的 name 和 description，**不包含**任何 skill 正文中的句子 | 通过 |
| V4.14 | load_skill | 测试：`load_skill("attribution")` | 返回的正文与 SKILL.md 正文一致；不存在的 skill 返回错误和可用列表 |
| V4.15 | attribution skill 内容 | 人工阅读 + 关键词测试：包含 plan 6.7 中的 7 个步骤、"必须回答 no_anomaly"规则、根因标签枚举 | 测试通过；`HUMAN` 确认表述清楚 |
| V4.16 | system prompt 稳定 | 测试：用不同的 as_of、不同的问题、不同的 db 构建 system prompt | 逐字节相同（哈希一致） |
| V4.17 | verifier 的用例 | 测试以下答案 | 全部符合预期 |
| | | 数字和引用都正确 | 通过 |
| | | claim 数值错误 | 不通过，反馈中给出 rid 中最接近的值 |
| | | 正文中有数字没有对应的 claim | 不通过，并指出是哪个数字 |
| | | 正文中只有 "FY24Q2"、"2024 年"、"第 3 点" 这类数字 | 不误报 |
| | | 引用不存在的 rid | 不通过 |
| | | 涉及的期间违反勾稽关系（用构造的坏数据库） | 提示勾稽问题 |
| V4.18 | 护栏反馈的内容 | 查看 V4.17 中的反馈文本 | 具体到哪个 claim、哪个 rid、期望值和实际值 |

### 6.3 上下文压缩
压缩相关的测试统一放在 `tests/test_context.py`，用**随机生成的对话历史**测试：写一个生成器，随机组合各种消息（user、assistant 文本、多个工具调用、报错结果、load_skill、todo_write、护栏反馈），每个测试至少跑 200 个随机历史（固定种子）。

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V4.19 | **不变量 1：没有孤立消息** | 对 200 个随机历史 × 每一层压缩（L1–L5 和 PTL），检查输出视图 | 每个 tool_call 都有对应的 tool 结果，每个 tool 结果都有对应的 tool_call；不存在两条连续的 assistant 消息 |
| V4.20 | **不变量 2：原始历史不变** | 压缩前后比较原始历史和 trace 文件的哈希 | 不变 |
| V4.21 | **不变量 3：system prompt 不变** | 压缩后视图的第一条消息 vs. 原始 system prompt | 逐字节相同 |
| V4.22 | **不变量 4：rid 都能召回** | 压缩后，对历史中出现过的每个 rid 调用 recall | 全部成功，并且与原始结果相同 |
| V4.23 | **不变量 5：压缩有 trace 事件** | 触发每一层压缩后查看 trace | 每次压缩都有事件，包含层级、压缩前后的 token 数和 rid 列表 |
| V4.24 | 视图稳定（利于缓存） | 对同一份历史调用 `build_view` 两次；在历史末尾追加一条消息后再调用一次 | 前两次结果完全相同；第三次结果的前缀 = 第二次结果（期间没有触发新的压缩时） |
| V4.25 | L1 结果预算 | 测试：工具返回 300 行 | 上下文中只有 50 行 + 摘要 + recall 提示；Result Store 中有完整的 300 行 |
| V4.26 | L2 只折叠通过核验的轮次 | 测试：两个已完成的轮次，一个通过护栏，一个标注"未通过验证" | 只有通过的那一轮被折叠；折叠后保留了用户问题和 final_answer 的 claims |
| V4.27 | L3 白名单 | 测试 | load_skill、todo_write、护栏反馈、用户消息**永远不被存根化**；白名单中的工具结果会被存根化 |
| V4.28 | L3 的保留规则 | 测试 | 最近 3 个可压缩结果保持原样；最近一条 assistant 消息中引用过的 rid 保持原样；报错结果被压成一行 `已失败: ...` |
| V4.29 | L3 的触发条件 | 测试：可释放的 token < `clear_at_least` 时 | 不执行存根化 |
| V4.30 | 存根格式 | 测试：存根符合 plan 6.6.3 的格式；digest ≤ 200 字符；同一个结果生成两次 digest，结果相同 | 通过 |
| V4.31 | digest 中的数字是真的 | 测试：对所有 digest 运行 verifier（把 digest 当作答案，对应的 rid 作为引用） | 所有数字都能找到 |
| V4.32 | 账本由代码生成 | 测试：用 mock LLM 跑一次完整的归因流程（不触发 L5），统计调用 LLM 的次数 | 生成账本时调用 LLM 的次数 = 0 |
| V4.33 | 账本内容 | 测试：跑完 seasonal_check、peer_compare、get_filing_notes 之后查看账本 | 有"口径""已取得的数据""假设检验记录""失败记录""计划"五个部分；每条假设检验记录都带 rid；对账本运行 verifier 全部通过 |
| V4.34 | 每个分析工具都能写账本 | 测试：遍历所有分析工具 | 每个都实现了 `to_ledger_entry`，并且输出非空 |
| V4.35 | L4 之后的结构 | 测试 | 视图 = system prompt + 账本 + 已加载的 skills + 用户原始问题 + 最近 2 轮，不多也不少 |
| V4.36 | L5 由代码填写的段落 | 测试：mock LLM 在第 2、3、6、7 段中故意写入错误内容 | 最终摘要中这 4 段是代码生成的正确内容，LLM 写的错误内容没有进入摘要 |
| V4.37 | L5 删除 analysis | 测试：mock 返回 `<analysis>xxx</analysis><summary>yyy</summary>` | 视图中没有 `xxx` |
| V4.38 | L5 摘要核验 | 测试：mock 摘要中包含一个编造的数字 | 第一次重新生成；仍然有编造数字时退回 L4 视图；两种情况都写 trace 事件 |
| V4.39 | 第 6 段：用户所有消息 | 测试：多轮会话 L5 之后 | 每一条用户消息的原文都在摘要中 |
| V4.40 | Reactive 兜底 | 测试：mock LLM 第一次抛出 ContextTooLong | 压缩后重试成功；同一轮最多触发 1 次 |
| V4.41 | PTL 重试 | 测试：mock 压缩请求连续超长 | 最多重试 3 次，每次以 API 轮次为单位丢弃最早的组 |
| V4.42 | 熔断 | 测试：压缩连续失败 3 次 | 不再尝试压缩，以 status=refuse 结束并说明原因 |
| V4.43 | 阈值来自配置 | `grep -rnE "[0-9]{4,}" agent/context.py` | 没有写死的 token 阈值（全部从 config 读取） |
| V4.44 | 真实 LLM 端到端冒烟测试 | 把窗口设为 16K，跑 1 道长任务归因题 | trace 中至少有 1 次 L3 事件；最终答案通过 verifier；没有 API 报错 |

### 6.4 P4 阶段成果
| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V4.45 | v1 与 v0 的对比 | `python -m eval.compare <v0_run> <v1_run>` | v1 在 L1+L2 上的总正确率不低于 v0；v0 和 v1 的失败题都已写入 `eval/badcases.md`，并且每条都有归类和根因分析 |
| V4.46 | leaderboard | 查看 | 有 v1 这一行 |
| V4.47 | 证据链可追溯 | 用固定种子抽 3 条 trace，把每个数字追溯到 SQL 的过程导出到 `docs/evidence/V4.47.md` | `HUMAN` |

---

## 7. P5 异常注入 + L3

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V5.1 | 题目数量 | 统计 `l3.jsonl` | 8 题：4 道注入题（4 种类型各 1 道）+ 1 道真实事件题 + 3 道阴性对照题 |
| V5.2 | 所有场景库都满足勾稽关系 | `pytest tests/test_scenarios.py`（对每个场景库参数化运行 V1.6 和 V1.7 的检查） | 全部通过 |
| V5.3 | 注入范围正确 | 脚本：比较每个场景库和基础库的 facts 表，列出所有有差异的单元格 | 每种类型都符合下表 |
| | | company_specific | 只有一家公司有改动；改动的科目 ⊆ {注入科目, 对手科目, 它们的上级合计科目} |
| | | industry_wide | 三家公司都有改动，并且在同一个自然季度 |
| | | mix_shift | 上级合计科目的值不变 |
| | | reclassification | 从 t 期到最后一期都有改动；`filing_notes` 中有对应的附注 |
| | | none | facts 表与基础库完全相同（哈希一致） |
| V5.4 | 注入幅度 | 脚本：对每道注入题，计算（注入后值 − 注入前值）/ 该科目历年同季度环比变化的标准差，以及注入后当期变化的 z-score | 前者在 2.5–4 之间（industry_wide 允许 ±20% 的浮动）；后者 \|z\| ≥ 1.5（确实看得出异常） |
| V5.5 | 选期规则 | 脚本：计算每道注入题和阴性对照题对应期间的**真实数据** z-score | 全部 \|z\| < 1 |
| V5.6 | 不泄露答案 | 检查场景库的文件名和库内的表名 | 文件名是随机 id，不包含类型的词；场景库中没有标准答案相关的表；阴性对照题也有自己的场景库文件 |
| V5.7 | 可复现 | 用同样的种子重新生成所有场景 | 所有场景库的 facts 哈希完全相同 |
| V5.8 | 打分器自检 | 用 3 个 mock agent 测试 | 全部符合预期 |
| | | 直接返回标准答案 | top-1 = 100%，误报率 = 0 |
| | | 永远回答 company_specific | 阴性对照题的误报率 = 100% |
| | | 永远回答 no_anomaly | 注入题 top-1 = 0，误报率 = 0 |
| V5.9 | 误归因统计 | mock 回答了 must_not_claim 中的标签 | 被计为误归因 |
| V5.10 | L3 报告 | 跑完 3 次 × 8 题 | 报告中有 top-1、误归因、阴性对照题的误报情况，以及证据完整性（调用 seasonal_check 和 peer_compare 的比例）；失败的题已写入 bad case 档案 |
| V5.11 | 真实事件候选 | 脚本列出真实 \|z\| > 2.5 的期间 → `docs/evidence/V5.11.csv` | 文件存在 |
| V5.12 | 真实事件标注 | 人工标注 1–3 个事件，包括 MD&A 原文摘录 | `HUMAN` |

---

## 8. P6 预测 + as_of 隔离 + L4

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V6.1 | 快照中没有未来数据 | 测试：对 5 个不同的 as_of 生成快照 | `MAX(available_date) <= as_of` |
| V6.2 | 重述数据的处理 | 测试：构造一条 as_of 之后才发布的 restated 记录 | 不出现在快照中；快照中对应位置是 original 值 |
| V6.3 | 关联表也被过滤 | 测试：在快照中查 `facts_detail`、`filing_notes`、`periods` | 不包含首次披露日期晚于 as_of 的 (公司, 期间) |
| V6.4 | **防泄露总测试** | 测试：对给定的 as_of，用每个数据工具（包括 `run_sql` 查询 `information_schema` 中列出的每一张表）尝试查询 as_of 之后的期间 | 全部查不到数据 |
| V6.5 | agent 无法切换数据库 | 测试：`run_sql("ATTACH 'data/finsights.duckdb' AS b")` 和 `run_sql("SELECT * FROM read_parquet('...')")` | 都被拒绝 |
| V6.6 | 基线预测正确 | 测试：`forecast(method="seasonal_naive")` | 点预测 = 去年同季度的值（完全相等） |
| V6.7 | 短序列退回 | 测试：长度 < 12 的序列，用 method=auto | 实际使用 seasonal_naive，并在结果中注明 |
| V6.8 | 区间合理 | 测试：ets / sarima 的输出 | 下界 ≤ 点预测 ≤ 上界；level = 0.8 |
| V6.9 | 回测题目没有泄露 | 脚本：对每道 L4 题检查 | as_of < 预测目标期间的 available_date；目标值不在对应的快照中 |
| V6.10 | L4 报告 | 查看报告 | 同时有"agent"和"forecast 工具直接输出"两组 MASE 和 80% 覆盖率；MASE 是相对 seasonal_naive 计算的 |
| V6.11 | 预测数字来自工具 | 对 L4 的结果运行 verifier | 答案中的预测值都引用了 forecast 结果的 rid |

---

## 9. P7 L5 报告、judge、L6

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V7.1 | 结论抽取是独立的 | 测试：构建结论抽取请求时，输入中不包含 agent 的 claims 列表 | 通过 |
| V7.2 | 数字忠实度打分器自检 | 构造一份报告：10 个数字中有 2 个是错的 | 忠实度 = 0.8 |
| V7.3 | judge 的输入隔离 | 测试：judge 请求中只有评分细则、报告和证据表 | 不包含 agent 的推理过程或工具调用记录 |
| V7.4 | 位置交换 | 测试：A/B 评审 | 调用 2 次，并且两次的顺序相反；结论不一致时判为平局 |
| V7.5 | kappa 计算正确 | 测试：用一组固定数据与手算结果比较 | 一致 |
| V7.6 | judge 分数标注为参考 | 查看 leaderboard | judge 列标注"仅供参考"；积累 ≥ 15 份后有一次 kappa 参考值 |
| V7.7 | judge 的温度 | 查看配置 | judge temperature = 0 |
| V7.8 | 人工阅读报告 | 每轮评测后，人工按评分细则给 3 份报告打分，追加到 `judge_calibration.jsonl` | `HUMAN` |
| V7.9 | L6 题目 | 统计 `l6.jsonl` | 5 题，覆盖无法回答、错误前提、有歧义三类，每类至少 1 题 |
| V7.10 | L6 打分器自检 | mock agent 永远回答 answered | 得分为 0 |
| V7.11 | 有歧义题的答案确实不同 | 脚本：对每道有歧义的题，分别按两种理解查库 | 两个答案都不同（否则这道题不算有歧义） |

---

## 10. P8 版本对比与压缩实验

| ID | 检查内容 | 命令 / 方法 | 通过标准 |
|---|---|---|---|
| V8.1 | leaderboard 完整 | 查看 | v0–v4 每一行都有：3 次运行的均值 ± 标准差、run_id、commit；表头注明"30 题，方向性参考" |
| V8.2 | leaderboard 可追溯 | 脚本：leaderboard 中的每个数字 vs. 对应 `eval/reports/<run_id>.md` 中的数字 | 全部一致 |
| V8.3 | 各版本使用相同的题目 | 检查每次运行记录的数据集哈希 | 同一列中各版本的数据集哈希相同 |
| V8.4 | 压缩实验 | 查看报告 | A–E 五组都有结果，指标包括正确率、数字忠实度、recall 次数、总 token、缓存命中率、成本、L5 触发次数；窗口大小已注明 |
| V8.5 | 如实记录 | 人工确认实验结论与数据一致（包括不符合预期的结果） | `HUMAN` |
| V8.6 | bad case 档案 | 查看 `eval/badcases.md` | 每个版本的每道失败题都有记录，包括现象、归类、根因、改进和验证；至少有 3 条形成"发现 → 改进 → 验证通过"的完整闭环 |

---

## 11. 阶段完成清单（复制到 `docs/verification_log.md` 顶部）

```
P0: [ ] 全局 G1–G8  [ ] V0.1–V0.9                     tag: phase-P0-done
P1: [ ] 全局 G1–G9  [ ] V1.1–V1.16, V1.18–V1.22  [ ] HUMAN V1.17   tag: phase-P1-done
P2: [ ] 全局 G1–G8  [ ] V2.1–V2.13  [ ] 回归 P0–P1      tag: phase-P2-done
P3: [ ] 全局 G1–G8  [ ] V3.1–V3.11  [ ] HUMAN V3.12  [ ] 回归   tag: phase-P3-done
P4: [ ] 全局 G1–G8  [ ] V4.1–V4.46  [ ] HUMAN V4.15/V4.47  [ ] 回归   tag: phase-P4-done
P5: [ ] 全局 G1–G9  [ ] V5.1–V5.11  [ ] HUMAN V5.12  [ ] 回归   tag: phase-P5-done
P6: [ ] 全局 G1–G8  [ ] V6.1–V6.11  [ ] 回归            tag: phase-P6-done
P7: [ ] 全局 G1–G8  [ ] V7.1–V7.11  [ ] HUMAN V7.8（每轮）  [ ] 回归    tag: phase-P7-done
P8: [ ] V8.1–V8.4, V8.6  [ ] HUMAN V8.5
```

P4 的任务比较多，可以按 plan 6.11 的子任务分批检查：
| 子任务 | 对应检查 |
|---|---|
| P4.1 | V4.1–V4.10 |
| P4.2 | V4.11–V4.12 |
| P4.3 | V4.13–V4.16 |
| P4.4 | V4.17–V4.18 |
| P4.5a | V4.32–V4.34 |
| P4.5b | V4.19–V4.31 |
| P4.5c | V4.35 |
| P4.5d | V4.36–V4.44 |
| P4.6 | V4.45–V4.47 |

---

## 12. 变更记录
| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-10-01 | 初版 | — |
| 2026-10-01 | G2–G7 的命令移到表格外的代码块；G4 改为按 .env 中的实际密钥值检查；新增正则自检 `G-selftest` | questions.md Q1：表格中的 `\|` 在 ERE 中是字面竖线，导致检查失效 |
| 2026-10-01 | 新增 0.5 联网检查规则；V0.5/V0.6/V4.44 改为 live check；G1 要求离线通过 | questions.md Q3 |
| 2026-10-01 | 新增 V0.9 thinking 模式显式配置；V0.4/V0.8 不再写死 DeepSeek | questions.md Q2；模型已切换为 agnes-3.0-flash |
| 2026-10-01 | V1.10 改为 5,000–100,000 加环比 ±50%；V1.11 改用 derivation 字段；V1.17 改为由两个 AI 模型独立核对；新增 V1.18–V1.22 | questions.md Q5、Q6、Q7 |
| 2026-10-01 | 评测集总规模改为 30 题：V3.1/V3.2/V3.3/V3.7/V3.12/V5.1/V5.10/V5.12/V7.9 的数量随之调整；V4.45 不再要求显著；judge 改为参考（V7.6/V7.8）；新增 V8.6 bad case 档案 | 用户决策 |

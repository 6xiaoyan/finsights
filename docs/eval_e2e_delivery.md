# Agent A 交付说明：E2E 评测与数据集工具

日期：2026-10-16；分支：`feat/eval-e2e`；依据：`docs/prd-agent-evaluation.md` §5–6 与
`eval/contracts/`。

本交付实现宏观 E2E 评测、真实财报 30 题数据集与数据集审核工具。**不改变 Agent 行为**，
不修改 B 的目录（`eval/agent_process/`），不自行合并 main。

## 1. 文件清单

| 路径 | 说明 |
|---|---|
| `eval/contracts/case.py` | 公共协议：CaseInput / CaseGold / RunArtifact / TraceEvent / GradeResult / SourceRef / ScoringPoint / NumericAssertion |
| `eval/financial_e2e/schema.py` | CaseRecord（输入+gold+元数据）、读写、`to_input()`/`to_gold()` 隔离 |
| `eval/financial_e2e/sources.py` | 港交所来源索引（标题/URL/发布日期/PDF/sha256），`write_sources_index` |
| `eval/financial_e2e/pageindex.py` | PDF 页码索引（pypdfium2），数值→页码锚定 |
| `eval/financial_e2e/adapter.py` | 运行包 → RunArtifact / TraceEvent（只读，不重跑 Agent） |
| `eval/financial_e2e/judge.py` | LLM-as-judge（逐点/整篇）+ 可注入 CallableJudge（离线） |
| `eval/financial_e2e/grader.py` | 逐点/整篇/数值程序核验/汇总（unknown 保留在分母） |
| `eval/financial_e2e/generate.py` | 得分点候选生成（强制 draft） |
| `eval/financial_e2e/report.py` | 逐题报告与汇总报告 |
| `eval/financial_e2e/cli.py` | 命令入口 |
| `eval/datasets/financial_real_v1/cases.jsonl` | 30 题数据集（含答案、得分点、来源、审核状态） |
| `eval/datasets/financial_real_v1/sources.json` | 来源索引（URL→PDF/sha256/涉及题） |
| `eval/datasets/financial_real_v1/dataset_meta.json` | 数据集元信息（题数/数据版本/split/blocked） |
| `eval/datasets/financial_real_v1/build_cases.py` | 可重建脚本（从 DB + PDF 重算） |
| `docs/superpowers/specs/2026-10-16-eval-e2e-design.md` | 设计说明 |
| `tests/test_financial_e2e.py` | 离线测试（38 项） |

## 2. 准确命令

```powershell
# 校验数据集结构、来源与审核状态
.venv\Scripts\python -m eval.financial_e2e validate

# 核验已披露项能否追溯到引用来源页（--strict：未命中返回码 1）
.venv\Scripts\python -m eval.financial_e2e verify-sources --strict

# 生成得分点候选（强制 draft；需 API key；--write 落盘，--judge-fake 离线自检）
.venv\Scripts\python -m eval.financial_e2e generate-points --cases eval/datasets/financial_real_v1/cases.jsonl --ids LV-E2E-... --write

# 对已有运行包评分（只读原包，不重新请求 Agent）
# 正式评分要求每题 approved；校准时显式加 --allow-draft（结果按非正式处理）
.venv\Scripts\python -m eval.financial_e2e score-run --run-dir runs/cli-20261016-134035 --case-id LV-E2E-FY2026Q1-CONFLICT-001 --out eval/reports/e2e --allow-draft

# 按固定数据集调用当前 Agent（子进程 agent.cli）并评分
.venv\Scripts\python -m eval.financial_e2e run-dataset --cases eval/datasets/financial_real_v1/cases.jsonl --agent-mode multi_agent --query-backend text2sql --out eval/reports/e2e --reset --allow-draft
.venv\Scripts\python -m eval.financial_e2e run-dataset --dry-run    # 只打印将执行的题（含 blocked 标记）

# 汇总报告
.venv\Scripts\python -m eval.financial_e2e report --results eval/reports/e2e/results.jsonl --out eval/reports/e2e_summary.md

# 人工/独立审核（批准必须带 --reviewer，禁止自动批准）
.venv\Scripts\python -m eval.financial_e2e review --case-id LV-E2E-... --status approved --reviewer <name> --note "..."
```

`score-run` / `run-dataset` 默认用 LLM judge（`agent.llm.LLMClient`，role=judge）。
`--judge-fake` 仅用于离线自检，报告与结果会标注 judge=callable，不冒充真实评分。

**运行性质**：`--allow-draft` 只解除"必须 approved"的闸门，**不改变是否会调用被测 Agent**。
只有 `score-run`（对已有运行包重评分）不会重跑 Agent；`run-dataset` 即使带 `--allow-draft`
仍会通过子进程调用被测 Agent 的**真实模型**，不是离线运行。结果记录 `formal` 字段与报告
"结果性质"栏区分正式/校准。

## 3. 数据集组成

30 题，题型与 PRD §5.1 建议分布一致：

| 主类型 | 数量 | 覆盖 |
|---|---:|---|
| 多期比较与财务计算 | 6 | 同比/环比、累计转单季、加权 vs 简单平均、单位口径 |
| 变化分解与归因 | 10 | 收入→毛利→费用→利润桥、费用率、营运资金、负债结构、利润层 |
| 证据边界与综合判断 | 8 | 单季 vs 累计、证据冲突、数据缺口、合成明细、列/期间误用 |
| 历史时点预测 | 6 | 固定 as_of、点/区间、方法、不确定性 |

- 复用已审开发题 9 条（S01、S02、S03、S04×2、S05、S06、S07；另 R02 作为 blocked 题）：
  `origin=reused`，保留 `selected_id` 与审核历史。S01–S07 全部覆盖。
- 新构造 21 题：`origin=new`。
- 开发/留出：**dev 25 / holdout 5**。按 `split_group` **整组划分，同一组不跨集**
  （复用的 S01–S07 所在组 FY2026/FY2025Q3/FY2027Q1 归开发集）。留出题逐题检查期间是否
  与开发集重叠并写入 `data_visibility.independent_holdout`：其中 FY2024×2 与 global 为
  **独立留出（3 题）**，FY2025Q4、FY2027Q2 预测与开发集共享期间，**标记为非独立留出**，
  不得据此宣称泛化能力。实际数量（25/5、独立 3）如实记录，因 S01–S07 已占多期窗口。
- 数据版本记录在 `eval/datasets/financial_real_v1/dataset_meta.json`（`db_sha256_12`、
  题型/来源/split/审核状态计数）。
- 全部 `review_status=draft`（生成器不得自批）。
- `blocked=1`：`LV-E2E-FY2027Q1-SEGMENT-GAP-001`（结构化库无分部收入/抵销项）。
- 预测题真实实绩仅在评测侧 `forecast_truth`，不进入 `CaseInput`；`LV-E2E-FY2027Q2-...`
  目标期间尚未披露，`forecast_truth=null`，不伪造未来值。

### 来源

- 来源限定为 `data/manual/lenovo_manifest.json`（港交所公告清单：标题/URL/发布日期/sha256）
  与 `data/raw/lenovo/*.pdf` 本地原文。
- 每题 `sources` 记录公告标题、港交所 URL、发布日期、报告期、PDF 页码、行/列与备注。
- `verify-sources --strict`：**22/22 已披露项全部在引用页命中**；36 条为派生值（增速/比率/
  天数/由累计推出的单季），由已披露项计算，不要求逐字出现。

## 4. 评分方法

- **准入把关**：`score-run` / `run-dataset` 默认要求每题 `approved`，未审核题被拒绝
  （`--allow-draft` 仅用于校准/离线，报告会据结果显示实际 judge 与来源）。
  `validate --require-approved` 要求**校验范围内每一道可评分题**都 approved，而非"至少一道"。
- 逐点：LLM-as-judge → met / partial / missing / contradicted / unknown。
  `unknown` 保留在必答分母，并给保守/乐观命中区间；optional 独立展示，不入必答分母。
- 整篇：扣题、推理连贯、结论与证据一致、不确定性、表达清晰度各 1–5 分；不读取内部
  Reviewer 的 accepted/rejected。
- 数值：必须先确认数字出现在对应**期间口径**附近（单季与累计/9M/H1 严格区分，
  FY2026Q1 的数字不会被当成 FY2026 9M 的命中），且**正向单位词实际出现**
  （`%`/`百万美元`/`天` 等），才标 `measured`；期间不符或单位词缺失标 `model_judged`；
  单位明显冲突（如把百分比写成"元"）标 `measured_unit_mismatch`（ok=False）。
  只按数值近似命中会把碰巧出现的数字误报为已核实，本版已禁止。
- 未交付（refuse/clarify/error）：所有必答点记 missing，计入未完成分母，不因节点 completed
  或 Reviewer accepted 改记成功。
- 未运行/缺运行包、被阻塞题：均写入结果并进入汇总分母，单列呈现，不静默丢弃。
- 汇总按题型/真实合成/split/审核状态分组；blocked、未交付、未运行分别单列；本版不拼单一总分。
- 汇总报告中的 `judge` 名与 model 来自结果记录，不再固定写成 `llm`；并标注**结果性质**
  （正式 = 全部 approved；校准 = 用了 `--allow-draft`）。
- 汇总报告把留出分为**独立留出**与**非独立留出**，非独立题单列并**不计入留出成绩**。
- 预测题另列事后误差：点预测（优先"预测值："契约行；否则在**屏蔽财年/年份后**取邻近单位词
  的数字，并标注 `heuristic_masked_period`）、绝对/相对误差（真实值为零时不报相对误差）、
  区间是否覆盖真实值、区间宽度；与论证质量分开呈现。

## 5. 样例（离线）

对已有运行包（`runs/cli-20261016-134035`，status=refuse）重评分：

```
$ .venv\Scripts\python -m eval.financial_e2e score-run --run-dir runs/cli-20261016-134035 \
    --case-id LV-E2E-FY2026Q1-CONFLICT-001 --out eval/reports/e2e_dev --judge-fake --allow-draft
LV-E2E-FY2026Q1-CONFLICT-001: status=refuse delivered=False 必答命中=0.00 \
  missing=4 contradicted=0 unknown=0
```

该包在 `run_meta.json` 中 semantic_review_status=accepted、节点 completed，但最终 status=refuse，
工具据最终交付状态判为未完成（符合 PRD §9.3）。

## 6. 审核与阻塞清单

| 项 | 状态 | 说明 |
|---|---|---|
| 30 题 gold | 全部 **draft** | 待人工/独立审核来源、可回答性、替代答案公平性后才能评分 |
| 得分点生成 | 已实现 | 生成即强制 draft；`generate-points` 需另跑并人工复核 |
| `LV-E2E-FY2027Q1-SEGMENT-GAP-001` | **blocked** | 无分部收入/抵销项结构化数据，需补数 |
| 预测题真实实绩 | 仅评测侧 | FY2026Q1/Q2/Q3/Q4 有实绩；FY2027Q2 待披露 |
| 真实模型运行 | **未执行** | 需 API key/网络；命令已给出，属 live 检查，不伪造结果 |
| 已审 S01–S07 复用 | 9 条（覆盖 S01–S07） | 已按新协议重写得分点与来源锚点，保留 `selected_id` |

## 7. 已实现 / 仅定义协议 / 未实现

- 已实现：公共协议、30 题数据集与来源锚点、运行包适配（重评分）、逐点/整篇/数值评分、
  逐题与汇总报告、审核与校验命令、离线测试。
- 仅定义协议：CaseGold 的完整人工审核工作流 UI（本版用 CLI `review` + `review_log.md`）。
- 未实现（不在本期）：Planner/Worker/Reviewer 角色评测（B 的范围）、微观查询诊断（B）、
  自动调优、Web/前端。

## 8. 已知限制与偏离

- **未单独创建 worktree**：本次在 `D:\finsights\finsights-dev` 内新建分支 `feat/eval-e2e`
  开发（该检出为 A 独占）。PRD §4.2 建议的独立 worktree 用于 A/B 并行隔离；B 仍应使用
  独立 worktree。
- 30 题共 22 条已披露数值断言做了"数值→页码"自动核验；行列与期间的完整核对仍需人工。
- 留出集仅 3 题为"与开发集期间不重叠"的独立留出；另 2 题（FY2025Q4、FY2027Q2 预测）
  标注为非独立，报告中单列为诊断，不计入留出成绩。按复审决定，暂不为凑数量扩题。
- 未跑真实模型评分；judge 公平性需在人工校准后再批量评分（PRD §9.2）。

# 过程评测报告：20261016-134035（run=20261016-134035）

- report_version：process-report-v1
- 运行目录：`runs\cli-20261016-134035`
- 本报告只评估过程与角色职责，不改变被测 Agent；质量、耗时、token 分列展示，
  不拼成单一总分。

## 最终状态与答案

- 最终状态：refuse（未交付）
- 核验通过：False
- 终止原因：主循环轮数用尽（max_steps=20，未提交最终答案）
- final_answer 校验被打回次数：1
- 最终答案：（未提交，见下）

- 计划 revision：1，节点状态：n1=completed（审查 not_reviewed）；n2=completed（审查 accepted）；n3=completed（审查 accepted）；n4=completed（审查 accepted）
- 内部审查结论：semantic_review_status=accepted，task_coverage_status=covered

> **节点 completed、内部审查 accepted，都不代表 E2E 成功。** 本次运行最终未提交答案，E2E 应记为未完成。

### 宏观 E2E（来自 Agent A 的评分，若有）

- 未提供：未传入 `--e2e-results`，本报告不含宏观得分点评分。
  不编造宏观结论。

## 运行监控

- 工具调用：总 99（ok 95 / error 3 / skipped 1 / unknown 0）
- 角色调用：总 13，按角色 {'planner': 1, 'reviewer': 7, 'worker': 5}
- 主循环轮数：20（assistant 事件 20）；子循环步数：52
- 查询提取：run_meta 自报 11 次，逐次明细 无（历史包）
- 耗时：角色总 128712 ms
- token：prompt=383994 completion=22438（来源 run_meta）
- 费用估算：0.0710619（按 config.llm.pricing_usd_per_mtok 估算，非实际账单）
- 异常类型：{'CalcRejected': 2}
- 重试：{'llm_retry': 0, 'stop_reject': 0, 'protocol_correction': 0}

> 工具调用成功只描述程序运行状态，不表示取数业务正确（PRD §7.2）。
> 跳过不计成功；缺 usage 不填零。

## 中观角色评测

### Planner

- 计划修订数：1；每版节点数：{1: 4}
- 节点数 4（其中 required 4）
- 重复节点目标：（无）
- 悬空依赖：（无）；自依赖：（无）；成环：False
- 范围偏移（合同期间 FY2025Q1）：（无）
- 覆盖映射：unknown（未提供评测侧参考点（gold 未就绪），不推断覆盖情况）
- 依据：{'executability': 'measured', 'dependency_soundness': 'measured', 'duplication': 'measured', 'scope_drift': 'measured', 'coverage': 'unknown'}

### Worker

- 节点 `n1`（completed）：查询 2 次（失败 0、其中已恢复 0）、重试 0、读取 0、同工具重复调用 1（重复≠冗余，仅供参考）
  归因：**unknown** — 证据不足，未发现可归因的失败证据
  引用完整性（程序实测）：有 rid 的 fact 4 / 缺 rid 0；引用了不存在的 rid：（无）；证据引用不在库：（无）
  **产物自报**完成情况（artifact.completion_report，评测侧未独立核验）：达标 3 / 未达标 0；自报状态 completed
- 节点 `n2`（completed）：查询 1 次（失败 0、其中已恢复 0）、重试 0、读取 0、同工具重复调用 0（重复≠冗余，仅供参考）
  归因：**unknown** — 证据不足，未发现可归因的失败证据
  引用完整性（程序实测）：有 rid 的 fact 1 / 缺 rid 0；引用了不存在的 rid：（无）；证据引用不在库：（无）
  **产物自报**完成情况（artifact.completion_report，评测侧未独立核验）：达标 1 / 未达标 0；自报状态 completed
- 节点 `n3`（completed）：查询 6 次（失败 0、其中已恢复 0）、重试 0、读取 1、同工具重复调用 11（重复≠冗余，仅供参考）
  归因：**unknown** — 证据不足，未发现可归因的失败证据
  引用完整性（程序实测）：有 rid 的 fact 12 / 缺 rid 0；引用了不存在的 rid：（无）；证据引用不在库：（无）
  **产物自报**完成情况（artifact.completion_report，评测侧未独立核验）：达标 1 / 未达标 0；自报状态 completed
- 节点 `n4`（completed）：查询 2 次（失败 0、其中已恢复 0）、重试 0、读取 1、同工具重复调用 2（重复≠冗余，仅供参考）
  归因：**unknown** — 证据不足，未发现可归因的失败证据
  引用完整性（程序实测）：有 rid 的 fact 3 / 缺 rid 0；引用了不存在的 rid：（无）；证据引用不在库：（无）
  **产物自报**完成情况（artifact.completion_report，评测侧未独立核验）：达标 3 / 未达标 0；自报状态 completed

### Reviewer

- 审查次数：7，意见 22 条（must_fix 2）
- 判定分布：{'accepted': 4, 'inconclusive': 2, 'needs_revision': 1}
- `rev1` 审查 art3@rev1-attempt1 → needs_revision；后续版本：rev1-attempt2；新版本内容变化：True；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev2` 审查 art2@rev1-attempt1 → accepted；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev3` 审查 art4@rev1-attempt2 → accepted；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev4` 审查 art5@rev1-attempt1 → accepted；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev5` 审查 answer-cand6@334248eb96273f56 → inconclusive；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev6` 审查 answer-cand7@b019dac60fea0e29 → inconclusive；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**
- `rev7` 审查 answer-cand7@b019dac60fea0e29 → accepted；后续版本：（无，无法关联）；新版本内容变化：None；是否修好：未核验
  > 产物声明的处理状态全是默认值 `open`（当前 agent 从不写 addressed/declined），该栏不含信息，**不能读作「未修复」**

> 仅「是否存在更晚版本 + 内容是否变化」两项为实测；「问题是否被修好」未核验，fix_verified 恒为 null


| 类别 | 问题 | 严重性 | 出现轮次 |
|---|---|---|---|
| caliber | 「季度归属可辨：FY2024Q1 12,899.9、Q2 14,409.8、Q3 15,721、Q4 13,833.1，四个季度数值互不相同，FY2024Q3 的 15,721 不会与其他季度混淆」 | must_fix | [1] |
| caliber | 期间口径标注为 fiscal（财年季度），对应 2024年4-6月 | suggestion | [2] |
| caliber | region×product_line 明细仅返回 9 个组合（APAC/Americas/EMEA × Infra/Other/PC），若存在未列示的 revenue 构成项，加总路径的完备性无法从库内确认。 | limitation | [3] |
| caliber | 期间口径说明：FY2025Q1 为联想财年第一季度，对应自然季度 2024-04-01~2024-06-30；本节点所有比较均为 fiscal 同期同比，未混用环比或期末/平均口径。 | suggestion | [3] |
| caliber | r16 由 calc 工具返回值为 0，与 r15 直接给出的 0.197453 不一致，本节点以 r15 为准、r16 仅作留痕。 | limitation | [4] |
| caliber | 财年口径说明：4 月制财年，FY2025Q1 对应 2024-04-01 至 2024-06-30 | suggestion | [5] |
| caliber | 财年口径说明：FY2025Q1 对应 2024-04-01 至 2024-06-30 | limitation | [6] |
| caliber | 财年口径说明：FY2025Q1 对应自然季度 2024-04-01 至 2024-06-30 | suggestion | [7] |
| evidence_quality | 「本节点未获得 n2 的原始取数结果（rid 与数值），因此只能给出库内同口径基准值与判定规则，无法做逐位一致性确认」 | limitation | [1] |
| evidence_quality | 「交叉验证仅在同一数据源内部完成（利润表科目互证 + 分项加总互证），未接入外部财报/公告做库外验证」 | limitation | [1] |
| evidence_quality | 「计算『2153.86+1701.26+1832.73+1666.02+2462.99+1647.67+1333.29+1227』未引用任何 rid：纯常量计算，其结果不是财报事实来源」 | suggestion | [1] |
| evidence_quality | 来源为结构化财务数据库（query_trace_id 9e3ef01addb3），未直接引用财报/公告原文页码 | limitation | [2] |
| evidence_quality | 核对仅基于库内数据（data_version 2633728-1792117547120291300-d728e5137cbb），未与联想集团官方财报/公告等库外独立来源比对，因此不能完全排除库内数据系统性偏差。 | limitation | [3] |
| evidence_quality | 四条 calculations（r11、r12、r13、r14）的 limitations 记录为「未引用任何 rid：纯常量计算，其结果不是财报事实来源」。 | suggestion | [3] |
| evidence_quality | completion_report 中“数值与 n2/n3 一致”标记为 satisfied=true，但 limitations 与 missing_inputs 同时说明 n2/n3 原文未提供、无法逐字核对。 | limitation | [4] |
| evidence_quality | 答案声称 15,447.1 usd_mn 经「库内多路径核对一致」（直接查询、region×product_line 九项加总、与营业成本+毛利勾稽） | limitation | [5] |
| evidence_quality | 同比对照：FY2024Q1 = 12,899.9 usd_mn，同比 +19.7%（0.197453），ref = r15 | limitation | [5] |
| evidence_quality | 答案核心数字 15,447.1 usd_mn 引用 r2，交叉验证引用 r8/r9/r10 | limitation | [6] |
| evidence_quality | 库内未提供联想官方财报/公告原文，无法做库外第三方独立比对 | limitation | [7] |
| task_scope | 本节点核对的是 FY2024Q3 营业收入 15,721 usd_mn，而任务要求核对的是 FY2025Q1 营业收入（15,447.1 usd_mn）。 | must_fix | [1] |
| task_scope | 主问题「联想 FY2025Q1 营业收入是多少」已被直接回答 | limitation | [6] |
| unsupported_cause | 解释部分仅陈述数值、口径与同比幅度，明确不对变动原因作断言，并以 missing_evidence 假设标注驱动因素不可判定。 | suggestion | [4] |

## 微观查询诊断

- 查询调用数（按调用计，不是按 rid）：11
- 原因码分布：{'ok': 11}
- 覆盖：{'has_run_package_records': False, 'derived_calls': 11, 'unlinked_calls': 0}

| # | 调用 id | 节点 | 状态 | 原因码 | 结果 rid | 计划符合性 |
|---|---|---|---|---|---|---|
| 0 | — | n1 | ok | ok | ['r2'] | ok |
| 1 | — | n1 | ok | ok | ['r3'] | ok |
| 2 | — | n2 | ok | ok | ['r2'] | ok |
| 3 | — | n3 | ok | ok | ['r4'] | ok |
| 4 | — | n3 | ok | ok | ['r5'] | ok |
| 5 | — | n3 | ok | ok | ['r6'] | ok |
| 6 | — | n3 | ok | ok | ['r8'] | ok |
| 7 | — | n3 | ok | ok | ['r9'] | ok |
| 8 | — | n3 | ok | ok | ['r10'] | ok |
| 9 | — | n4 | ok | ok | ['r2'] | ok |
| 10 | — | n4 | ok | ok | ['r15'] | ok |

> 查询记录派生自 tool_result/results.json，缺少提取明细与调用级 id；派生记录只覆盖成功路径，失败调用在历史包中无记录，分母不完整
> 查询次数只作展示，多写或少写都不自动改变质量判断（PRD §7.3）

## 依据与未知项

- 依据来源：`measured` 程序实测 / `model_judged` 真实模型判定 / `pipeline_check` 模拟判定（**只验证流程，不是真实质量评分**）/ `human_reviewed` 人工标注 / `unknown` 无依据 / `mixed` 各节点依据不同。
- 程序实测的只有**结构与引用**（rid 是否存在、列与期间、计数）；「证据是否支持结论」「任务是否真的完成」属语义判断，无 Judge 时记 `unknown`。产物**自报**的完成情况单列，不当作独立核验。

- 答案候选：`answer-cand7`（来源产物 ['art2', 'art4', 'art5']，claims 8 条）—— 候选是诊断对象，不替代最终交付成绩。

- `measured`：planner.dependency_soundness, planner.duplication, planner.executability, planner.scope_drift, reviewer.review_round_linkage, reviewer.severity, reviewer.verdict, worker.data_completeness, worker.gap_statement, worker.reference_integrity, worker.result_structure
- `unknown`：planner.coverage, reviewer.actionability, reviewer.evidence_basis, reviewer.false_positives, reviewer.fix_verification, reviewer.misses, reviewer.problem_detection, worker.calculation, worker.evidence_support, worker.query_intent, worker.task_completion

> 本阶段不调用真实 API：凡标 `pipeline_check` 的结论只说明评测流程可用，
> **不得当作真实质量评分**。

**历史包缺失的新埋点字段**（记 unknown，不填 0）：

- `analysis/query_trace.jsonl`
- `analysis/visible_context.jsonl`

> trace.jsonl 里有 2 行 `result` 事件（multi 与 cli 各写一行）。终态以 `run_meta.status` 为准（refuse）；trace 末行是 refuse，仅作交叉核对。

## 已登记缺陷（本次不修）

- `created_step-hardcoded`：executor 落库时 step 恒为 0，导致 results.json 的 initial_packet 无法区分真正的初始包与查询结果；本评测不使用该字段判定（证据：10/16 条结果的 created_step 都是 0；登记待后续优化任务处理，本任务不改 Agent 行为）

## 字段兼容表

| 区域 | 来源 | 规范化字段 | 状态 | 说明 |
|---|---|---|---|---|
| package | `run_meta.json:run_id` | `run_id` | available |  |
| package | `调用方 --case-id` | `case_id` | available | 运行包本身不含 case_id，由评测侧绑定 |
| package | `--run-dir` | `run_dir` | available |  |
| package | `run_meta.json:status` | `final_status` | available | 以 run_meta 为准；trace 末行 result 仅作交叉核对 |
| package | `answer.md（仅 status==answered）` | `final_answer` | derived | 未提交时为 None，不填补 |
| package | `run_meta.json:verified` | `verified` | available |  |
| package | `analysis_state.json:contract` | `contract` | available | 仅 multi_agent 有 |
| package | `analysis_state.json:latest_revision` | `latest_revision` | available |  |
| package | `analysis_state.json:plans` | `plans` | available | 按 revision 字符串键 |
| package | `analysis_state.json:artifacts` | `artifacts` | available | version 字段不落盘，见 artifact_version() |
| package | `analysis_state.json:reviews` | `reviews` | available |  |
| package | `analysis_state.json:answer_candidate` | `answer_candidate` | available |  |
| package | `trace.jsonl:subagent_start/end` | `role_calls` | available | 按 agent_call_id 配对；缺 end 的项 token/耗时为 None |
| package | `analysis/query_trace.jsonl 或 results.json` | `query_records` | derived | 无 query_trace.jsonl 时从 results.json 派生；派生记录只覆盖成功路径（仅成功路径落库） |
| package | `run_meta.json:usage` | `usage` | available | 缺失时为空字典，不填 0 |
| package | `run_meta.json` | `run_meta` | available |  |
| package | `本适配器计算` | `diagnostics` | derived | result 行数、状态不一致等 |
| package | `本适配器计算` | `unknown_fields` | derived | 缺失的埋点文件清单 |
| role_call | `trace.jsonl:subagent_start.role` | `role_calls[].role` | available |  |
| role_call | `trace.jsonl:subagent_start.node_id` | `role_calls[].node_id` | available | 仅 worker 有 |
| role_call | `trace.jsonl:subagent_start.target_*` | `role_calls[].target_version` | available | 仅 reviewer 有 |
| role_call | `trace.jsonl:subagent_end` | `role_calls[].latency_ms` | available | 缺 end 时为 None |
| role_call | `subagent_context 事件` | `role_calls[].context_refs` | new_instrumentation | 历史包为 None |
| query | `query_trace.jsonl:query_trace_id` | `query_records[].query_trace_id` | new_instrumentation | 调用级 id；历史包为 None（不可用 rid 代替，rid 会复用） |
| query | `query_trace.jsonl:attempts/extractor_events` | `query_records[].attempts` | new_instrumentation | 历史包只有 run_meta.extraction_calls 总数 |
| query | `query_trace.jsonl:plan` | `query_records[].plan` | new_instrumentation | ok 路径可从 results.json[].args.plan 派生；非 ok 路径历史包不可恢复 |
| query | `results.json[].sql` | `query_records[].sql_chars` | derived | 只记长度，全文留在 results.json；未产生 SQL 时为 None |
| query | `results.json[].rows` | `query_records[].columns` | derived | 取首行键；无行时为空表 |
| query | `trace.jsonl 子 tool_result.query_trace_id` | `query_records[].linkage` | new_instrumentation | 历史包恒为 unknown，不按顺序/文本猜归属 |
| query | `results.json[].id` | `query_records[].result_ids` | available | 非 ok 路径为空；不伪造 rid |
| query | `（历史包不存在）` | `query_records[].status` | unknown_legacy | 派生记录只来自成功路径，故按 control-flow 推为 ok；失败调用在历史包中无任何记录 |

状态含义：`available` 今天的运行包里已有；`derived` 由包内数据推导；
`new_instrumentation` 本次补的埋点才产生（历史包为 unknown）；
`unknown_legacy` 历史包必然缺失，只能记 unknown。

**同名字段辨析**：`query_records[].query_trace_id` 是**调用级** id（`q1`、`q2`…，
运行内唯一、每次调用必有）；`query_records[].plan_digest` 才是 executor 的
**内容寻址** id（`sha256(sql + plan)[:12]`，非 ok 路径为 null）。
两者同名不同义，不能互换：同计划复用 rid 时，多次调用共享同一个 plan_digest，
但各有自己的 query_trace_id。


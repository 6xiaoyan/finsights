# 运行包适配、微观埋点与角色独立评测交付说明（Agent B）

日期：2026-10-16；分支：`feat/eval-process`；worktree：`D:\finsights\finsights-dev-eval-process`
基线：从 `42baa3f`（`feat/eval-e2e`，已冻结契约所在提交）切出，**未合并 main、未 push**。
权威规格：`docs/prd-agent-evaluation.md` §4/§7/§8/§9；设计：`docs/superpowers/specs/2026-10-16-eval-process-design.md`；计划：`docs/superpowers/plans/2026-10-16-eval-process.md`。

> **本阶段不调用真实 API。** 所有角色评分走离线/固定样例路径，报告中一律标 `pipeline_check`：
> 它证明评测流程可用，**不是真实质量评分**。PRD §9.3 的 M3「真实执行贯通」**未完成**。

## 1. 已实现

| 能力 | 位置 | 说明 |
|---|---|---|
| 运行包适配 | `eval/agent_process/adapt.py` | `load_process_package()` 把 `runs/<run>/` 规范化成 `ProcessPackage`；关联计划 revision／节点／角色调用／查询调用／结果 rid／审查版本；缺失一律 `None`+`unknown_fields`，不填 0 |
| 字段兼容表 | `adapt.FIELD_COMPAT`（31 条） | 每字段标 available / derived / new_instrumentation / unknown_legacy |
| 运行监控 | `metrics.py` | 工具调用成功/失败/跳过/未知、异常类型、重试、角色调用、主/子轮数、提取次数、结果读取、耗时、token、费用估算；保留分母与原始计数；自报值 vs 重算值差异单列 |
| 微观查询诊断 | `query.py` | 9 个互不塌陷的原因码；计划符合性（期间集合、列）；按**调用**计数而不是按 rid |
| Planner 评测 | `roles/planner.py` | 节点数／重复目标／悬空依赖／自依赖／成环／范围偏移（程序判定）；覆盖映射需参考点，无 gold 记 unknown |
| Worker 评测 | `roles/worker.py` | rid 真实性、完成标准覆盖、证据引用；归因区分上游目标错误/交接遗漏/意图错误/解析错误/执行器错误/数据不可用/未知 |
| Reviewer 评测 | `roles/reviewer.py` | 固定样例 precision/recall/漏检/误报/严重性；真实轨迹的审查前→意见→审查后版本关联、重复意见去重保留轮次；关联不上记 unlinkable |
| 报告 | `report.py` | 逐题报告＋汇总报告；区分 measured/model_judged/pipeline_check/human_reviewed/unknown；blocked/未交付不消失；与 A 的 E2E 按 `(case_id, run_id)` 对齐 |
| CLI | `cli.py` | 6 子命令，默认离线 |
| 埋点（纯观测） | `agent/tools/data.py`、`agent/subagents.py`、`agent/multi.py`、`agent/cli.py` | 见 §4 |

**仅定义协议、本阶段未跑通真实链路**：`--judge live`（`RoleLLMJudge`）已实现但从未执行——它需要真实 API，属集成审核之后的事。

## 2. 准确命令

全部从 worktree 根目录 `D:\finsights\finsights-dev-eval-process` 执行。解释器用项目 venv：
`D:\finsights\finsights-dev\.venv\Scripts\python.exe`（只读复用，不写 A 的目录）。

```powershell
# 0) 固定回放包（runs/ 不入库，需先拷入）
Copy-Item -Recurse -Force "D:\finsights\finsights-dev\runs\cli-20261016-134035" .\runs\

# 1) 一次跑完全部评测（推荐入口）
& $py -m eval.agent_process replay --run-dir runs/cli-20261016-134035 --out-dir eval/reports/process

# 2) 分步
& $py -m eval.agent_process adapt   --run-dir runs/cli-20261016-134035 --out eval/reports/process
& $py -m eval.agent_process metrics --run-dir runs/cli-20261016-134035 --out eval/reports/process
& $py -m eval.agent_process queries --run-dir runs/cli-20261016-134035 --out eval/reports/process
& $py -m eval.agent_process roles   --run-dir runs/cli-20261016-134035 --mode both --judge fake --out eval/reports/process
& $py -m eval.agent_process report  --run-dir runs/cli-20261016-134035 --out eval/reports/process/report.md

# 3) 只评某一个角色 / 只跑固定样例
& $py -m eval.agent_process roles --run-dir runs/cli-20261016-134035 --mode trajectory --role reviewer --out eval/reports/process
& $py -m eval.agent_process roles --run-dir runs/cli-20261016-134035 --mode samples --out eval/reports/process

# 4) 与 A 的 E2E 结果合看（A 跑完后提供 results.jsonl）
& $py -m eval.agent_process report --run-dir runs/cli-20261016-134035 `
      --e2e-results eval/reports/e2e/results.jsonl --out eval/reports/process/report.md

# 5) 提供评测侧参考点后，Planner 覆盖映射才会从 unknown 变 judged
& $py -m eval.agent_process roles --run-dir runs/cli-20261016-134035 --mode trajectory `
      --gold eval/datasets/financial_real_v1/cases.jsonl --out eval/reports/process

# 6) 测试
& $py -m pytest tests/test_agent_process.py tests/test_process_instrumentation.py -q   # 128 + 33
& $py -m pytest -q                                                                     # 全量
```

`--judge` 默认 `fake`（离线）。`live` 会实例化真实 `LLMClient`，本阶段不要用。

> **输出目录共用**：上面 2) 的分步命令与 3) 的单角色命令都写同一个 `--out` 目录，
> 会**覆盖** `replay` 产生的对应文件（例如只跑 `--role reviewer` 会把
> `roles_trajectory.json` 覆盖成只含 reviewer）。要保留完整回放产物，
> 分步调试时换一个 `--out`，或最后重跑一次 `replay`。

**可复现性已实测**：把 `eval/reports/process` 覆盖成残缺产物后重跑 1) 的命令，
`git status` 干净 —— 产物被逐字节复原。

## 3. 固定包回放结果（`runs/cli-20261016-134035`）

命令：`replay --run-dir runs/cli-20261016-134035 --out-dir eval/reports/process`
产物：`eval/reports/process/{adapt.json, metrics.json, queries.json, roles_trajectory.json,
roles_samples_{planner,worker,reviewer}.json, report.md, summary.md, compat_table.md, results.jsonl}`

回放结论与 PRD §2 的实测记录逐项吻合，且**同时呈现**「过程看起来完成了」与「最终没交付」：

| 项 | 值 | 与 PRD/run_meta |
|---|---|---|
| 最终状态 | `refuse`，`final_answer=None`，`verified=False` | 一致 |
| 终止原因 | 主循环轮数用尽（max_steps=20，未提交最终答案） | 一致 |
| final_answer 校验被打回 | 1 次 | 一致 |
| 计划 | revision 1，4 节点，全部 `completed` | 一致 |
| 内部审查 | `semantic_review_status=accepted`，`task_coverage_status=covered` | 一致 |
| 角色调用 | 13（planner 1 / worker 5 / reviewer 7） | 一致 |
| 查询调用 | 11 次 → 9 个唯一 rid（2 次复用） | 一致（PRD §2 的 11 次提取） |
| token | prompt 383994 / completion 22438 | 一致 |
| 子循环步数 | 52 | 一致 |
| 自报 vs 重算差异 | **0** | 重算未报假差异 |
| 工具调用 | 总 99（ok 95 / error 3 / skipped 1 / unknown 0） | 跳过未计成功 |
| 耗时 | 角色合计 128712 ms | — |
| 费用估算 | $0.0710619（按 config 单价，非账单） | — |

报告首段原文（验收要点）：

```
- 最终状态：refuse（未交付）
- 终止原因：主循环轮数用尽（max_steps=20，未提交最终答案）
- final_answer 校验被打回次数：1
- 计划 revision：1，节点状态：n1=completed（审查 not_reviewed）；n2=completed（审查 accepted）；…
- 内部审查结论：semantic_review_status=accepted，task_coverage_status=covered

> **节点 completed、内部审查 accepted，都不代表 E2E 成功。** 本次运行最终未提交答案，E2E 应记为未完成。
```

该包缺两处新埋点，如实记 unknown：`analysis/query_trace.jsonl`、`analysis/visible_context.jsonl`。
但**能测到的照实测**：11 次查询调用按调用级建记录、`sql_chars` 取自 `results.json`、
节点归属经事件自带的 `parent_call_id` 还原（`linkage=tool_result_parent`）。

固定样例流水线检查（**不是质量评分**）：

| 角色 | 样例数 | 结果 |
|---|---:|---|
| Planner | 6 | 6/6 产出覆盖映射 |
| Worker | 7 | 6/7 归因与标注一致；WK-02 需真实 Judge（`intent_error` 只能由 Judge 判 contracted 得出），离线诚实降级为 `unknown` |
| Reviewer | 9 | 7/9 的 precision 有定义（2 条无错误样例分母为 0 → `None`，不填 0）；平均 precision 0.786 |

## 4. 新增埋点（纯观测，不改行为）

| 文件 | 改动 |
|---|---|
| `agent/tools/data.py` | `Outcome` 增字段 `query_trace_id`（默认 `None`）；`_h_query_financial_data` 把每次调用写入 `ctx.stats["query_trace"]` |
| `agent/subagents.py` | 子 `tool_result` 事件在 `query_trace_id` 非空时带该键 |
| `agent/multi.py` | `_run_role` 增发 `subagent_context` 事件；整份实际内容写入 `ctx.stats["visible_context"]`；新增 `_context_refs`/`_redact_text`/`redact_record` |
| `agent/cli.py` | `_export_package` 增写 `analysis/query_trace.jsonl`、`analysis/visible_context.jsonl`；`run_meta` 增 `query_trace_calls`、`visible_context_calls` |

**行为不变的证据（有测试，不是人工检查）**：
- `test_outcome_text_unchanged_by_instrumentation`：工具返回文本与直接渲染同一结果**逐字相同**。
- `test_context_event_does_not_change_model_input`：mock 模型收到的 `messages` 恰好是
  `[system=system_prompt, user=user]`，埋点未注入任何内容。
- 埋点未触碰 `agent/query/*`、轮次默认值、角色提示词、审核策略、调度。
- 脱敏在**两处**执行：采集点与写盘边界（`redact_record`）；测试证明直接塞进 stats 的密钥也落不了盘。

## 5. 字段兼容表

以 `python -m eval.agent_process adapt` 生成的 `compat_table.md` 为准（由 `adapt.FIELD_COMPAT` 渲染，
31 条）。状态含义：`available` 今天的包已有；`derived` 由包内数据推导；`new_instrumentation`
本次埋点才产生（历史包为 unknown）；`unknown_legacy` 历史包必然缺失。

**关键辨析（容易踩）**：

| 名字 | 含义 | 取值 |
|---|---|---|
| `query_records[].query_trace_id` | **调用级** id，运行内唯一、每次调用必有 | `q1`、`q2`… |
| `query_records[].plan_digest` | executor 的**内容寻址** id，非 ok 路径为 null | `sha256(sql+plan)[:12]` |
| `results.json[].id`（rid） | 结果 id，**同计划会复用** | 11 次调用只得 9 个 |

三者同名或近似但语义不同，不能互换：判断「哪些调用没有结果」必须按调用级 id 计数，
按 rid 会少数（本包少 2）。

## 6. 已知限制

**运行包本身的历史缺口（PRD §2 已登记，本次复核确认）**

1. `AnalysisArtifact` 不落盘 `version`，版本只能由 `plan_revision`+`attempt` 现算；本适配器
   复刻 `rev{rev}-attempt{n}`，与 `agent/multi.py::_artifact_version` 保持一致（有测试钉住）。
2. `counters` 不落盘；无 coverage 对象、无顶层 must_fix 列表（只能按 `severity=must_fix` 过滤）。
3. `PlanNode` 无 assignee/role 字段；worker 归属只能从 trace 的 `subagent_start` 还原。
4. 无 `rejected` 判定值（只有 accepted/needs_revision/inconclusive）。
5. trace 事件（`meta` 行除外）**无时间戳**，只有 `run_meta.created` 一个锚点。
6. `tool_skipped` 只有一处来源（cli 跳过 text2sql 初始包），不是通用语义。
7. **`results.json` 的 `created_step`/`initial_packet` 不可用**：`executor.py` 落库时 `step` 恒为 0，
   本包 16 条里 10 条 `created_step=0`。→ 已登记为缺陷（`created_step-hardcoded`），
   本任务**不修**（属 Agent 行为），评测侧不使用该字段判定。
8. multi 包 `trace.jsonl` 末尾有**两行** `type=result`（multi 与 cli 各写一行）；
   适配器取末行为终态并在 `diagnostics.result_lines` 记录行数，不隐藏。

**本次新增埋点的边界**

9. 新埋点只对**将来**的运行生效；历史包这两处永久 unknown，只能走派生路径：
   派生记录来自 `tool_result` 事件（按调用，含失败调用），但在连事件都没有的极端情况下
   退回 `results.json`（`linkage=unknown`，只覆盖成功路径，分母不完整——诊断里已注明）。
10. 派生记录的失败调用**只知道失败了**，不知道是解析失败还是执行失败 →
   记 `failed_unclassified`，不猜类别。
11. `args_head` 在 `agent/subagents.py` 被截到 300 字符；截断后不是合法 JSON 时
    `question`/`node_task` 记 `None` 并留 `derivation_notes`，不猜内容。
12. `visible_context.jsonl` 里 `user_content` 上限 64000 字符；超限置 `user_truncated=True`，
   同时 `user_chars` 记录模型**实际收到**的长度、`user_content_chars` 记录落盘长度。
13. 续接调用（`resume_messages`）的模型还能看到历史 messages，记录只含新的 user turn，
    因此带 `resumed=True`/`resume_message_count` 标明记录是片面的。

**评测口径的限制**

14. **本阶段不调用真实 API**：所有 `pipeline_check` 结论不是质量评分。
    Worker 的 `intent_error` 与 Planner 覆盖映射的语义判定都需要真实 Judge。
15. Planner 覆盖映射在真实运行包上恒为 `unknown`——A 的 `financial_real_v1` 仍是 draft，
    且 `LV-E2E-*` 与现有任何运行包都不绑定。提供 `--gold` 后即可变成 `judged`。
16. Reviewer 固定样例的 `detected` 是**记录下来的输出**（离线不跑真实 Reviewer），
    因此 precision/recall 是程序实测，但"检出行为本身"只是流程复现，标 `pipeline_check`。
17. `results.json` 导出丢弃了结果记录的 `meta`，executor 的 `{catalog_version, prompt_version,
    query_trace_id}` 因此在历史包中不可得；新埋点已在 `query_trace.jsonl` 里补回。
18. 未做 Reviewer 开关对照实验，不宣称 Reviewer 带来能力提升；`accepted` 是被测输出，非标准答案。
19. 工具调用 3 次 error 中只有 2 次能解析出异常类型（`CalcRejected` 2 次），
    另 1 次的 `result_head` 未匹配异常格式——两者都如实计数，不做补全。

## 7. 检查记录

- `tests/test_agent_process.py`（128 项）：适配、监控、查询诊断、三角色、报告、CLI、真实包冒烟。
- `tests/test_process_instrumentation.py`（33 项）：新增事件与关联字段，含两条"行为不变"断言。
- 全量 `python -m pytest -q`：见 §2 命令 6；埋点改动前后均全绿。
- 埋点改动集中在 `agent/` 4 个文件，均为**新增键/新增事件/新增文件**，无既有分支被改写。
- 固定回放包与 A 的原件字节一致（只 `Copy-Item`，未修改原包）。

## 8. 未完成 / 待集成审核

- **PRD §9.3 M3「真实执行贯通」未完成**：未跑真实 API，未做评分校准，未产出经审核的小样本真实执行。
- `--judge live` 已实现未执行；真实 Judge 的角色提示词（`RoleLLMJudge`，独立于 A 的 E2E 提示词）
  需在集成审核后另行校准。
- Planner 覆盖映射缺 gold 绑定路径的实跑验证。
- 本分支未合并 main、未 push；`eval/contracts/`、`eval/financial_e2e/`、`eval/schema.py`、
  `eval/runner.py`、`eval/graders/`、`eval/generators/` 均未改动。

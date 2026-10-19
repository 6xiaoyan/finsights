# 运行包适配、微观埋点与角色独立评测交付说明（Agent B）

日期：2026-10-16；分支：`feat/eval-process`；worktree：`D:\finsights\finsights-dev-eval-process`
基线：从 `42baa3f`（`feat/eval-e2e`，已冻结契约所在提交）切出；**交付时未合并 main、未 push**，2026-10-19 经 `feat/eval-integration` 合入 main。
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
| Worker 评测 | `roles/worker.py` | rid 真实性、完成标准覆盖、证据引用；归因区分上游目标错误/交接遗漏/意图错误/解析错误/执行器错误/数据不可用/未知；**已恢复的失败不作根因**；语义类维度无实质判定时记 unknown |
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
& $py -m pytest tests/test_agent_process.py tests/test_process_instrumentation.py -q   # 164 + 34
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
    另外，Judge 判 `unknown` **不算给了依据**：该维度记 `unknown` 而不是 `pipeline_check`
    （所以离线默认跑出来的 trajectory 结果里，语义维度是 `unknown`，只有结构与引用是 `measured`）。
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

- `tests/test_agent_process.py`（164 项）：适配、监控、查询诊断、三角色、报告、CLI、真实包冒烟。
- `tests/test_process_instrumentation.py`（34 项）：新增事件与关联字段，含两条"行为不变"断言。
- 全量 `python -m pytest -q`：见 §2 命令 6；埋点改动前后均全绿。
- 埋点改动集中在 `agent/` 4 个文件，均为**新增键/新增事件/新增文件**，无既有分支被改写。
- 固定回放包与 A 的原件字节一致（只 `Copy-Item`，未修改原包）。

## 8. 集成审核发现的问题与修复（第一轮复审）

复审提出 4 处会影响评测结论的问题，全部确认属实并已修复（每项先写失败测试再改，见 §2 命令 6）：

| # | 问题 | 修复 |
|---|---|---|
| 1 | Worker 的「查询意图」判定**没看到实际查询**：只把用户问题、节点目标、产物摘要交给 Judge，判不了「意图是否对准节点目标」 | `_judge_inputs()` 把该节点**实际发出的每次查询**（问题文本、状态、原因码、issue 码）、**结果表结构**（rid/列/行数）、产物摘要与自报完成情况一并交给 Judge；空结果如实呈现为 `empty`，不显示成成功 |
| 2 | 一次失败查询会**覆盖后续成功**，导致根因误归因：只要该节点出现过 parse_fail/exec_fail/no_data 就记根因，即使之后重试成功 | 归因改为看**最后一次有结论的查询**：先失败后成功＝已恢复，只记 `counts.recovered_failed_query_calls` 并在 notes 说明，**不作根因**；先成功后失败＝未恢复，照旧归因。`test_unrecovered_failure_is_still_attributed` 防止修过头把真问题一起吞掉 |
| 3 | Worker 把 `evidence_support`/`task_completion` 标成 `measured`，但程序只验了**结构**：检查的是 rid 是否存在、以及产物**自报**的完成标准是否达标 | 拆成两个维度：`reference_integrity`（程序确实测了 rid 存在性）→ `measured`；`evidence_support`/`task_completion`/`calculation` 属语义判断，无实质判定则 `unknown`。产物的自报内容移到独立的 `self_reported` 块并注明来源与"评测侧未独立核验"。另外：Judge 判 `unknown` **等于没判**，不再因此标 `pipeline_check` |
| 4 | 监控拿**不同量纲**的计数互比，会产生假差异：用 `queries.calls`（工具调用数）比 `run_meta.extraction_calls`（提取模型请求数），格式修复时一次调用会发多次请求 | 只比较同量纲：有逐次明细时用 `extraction.attempts_seen` 比；**无明细时不比较**并加一条"不可比"说明（真实包即属此列）。测试夹具的 `extraction_calls` 也改为按请求数计，与 `agent` 实际口径一致 |

复审另提到「续接调用的上下文记录不含历史 messages」——已确认为设计内的部分可观测（记录带
`resumed=true` 与 `resume_message_count`），交付说明 §6 第 13 条保留该限制，使用时要继续按此理解。

> 关于分支状态：复审记录写「已推送」。**本任务从未执行 `git push`**。经 `git reflog show
> origin/feat/eval-process` 核对，远端确实存在该分支且指向本分支 HEAD（reflog 记为
> `update by push`），但推送动作不是本任务发起的，请以集成审核侧的实际操作为准。

## 8b. 全分支代码复审（第二轮，14 项）

独立复审又报了 14 项（2 项与 §8 重复，已修）。其余 12 项全部确认属实并已修复：

| # | 问题 | 严重性 | 修复 |
|---|---|---|---|
| F2 | Reviewer 的「已修复/未修复」**完全来自 `response_status`**，而该字段默认 `open` 且全仓**从不写入** `addressed`/`declined` —— 等于用默认值断言「所有问题都没修」，还标成 `measured` | 重要 | 删除 `fixed`/`not_fixed`/`declined` 结论；改为 `declared_response`（如实转述声明）+ `response_status_is_default`（标明不含信息）+ `fix_verified=None`（未做前后语义比对）。维度改名 `review_round_linkage`（实测）/ `fix_verification`（unknown），报告显式写「**不能读作「未修复」**」 |
| F3 | 缺数据因求和变成 `0`：子调用没有 `end`（崩溃/超时）时报告打印「耗时：角色总 0 ms」「子循环步数：0」且无说明；而 `pairs_missing_end` 明明算出来了却没露出 | 重要 | 加 `latency_ms.complete` / `rounds.child_steps_complete` / `tokens.subagent_complete`；有缺失时在 notes 里写明这些是**下界**；报告在提供不完整值时显示提示 |
| F4 | `redact_record` 只处理**顶层**字符串，记录里嵌套的 `plan`、`extractor_events`（含 `str(ValidationError)`，会带出模型原始载荷）可把密钥写到盘上；交付说明的「两处脱敏」说法过度声称 | 重要 | 改为**递归**遍历 dict/list/tuple；新增两条测试（嵌套密钥、非字符串保持原样） |
| F5 | `query_trace_id` 用 `len(stats["query_trace"])` 分配，而主循环对**只读工具走线程池**（`agent/loop.py`），两个 `query_financial_data` 并发会撞 id → 适配器把第二条查询挂到第一次调用上 | 重要 | 改为加锁的**独立单调计数器** `query_trace_seq`；新增并发测试（`threading.Barrier` 强制重叠），该测试在修前确实失败 |
| F6 | 报告的依据段**完全没有 Worker**（worker 只在节点里给 basis，无顶层）；Reviewer 轨迹路径标称 `misses`/`false_positives` 为 `measured`，但轨迹上没有标注、没算 precision/recall | 重要 | `worker.evaluate_package` 增顶层 `evidence_basis`（按节点汇总，不一致标 `mixed`）；Reviewer 轨迹路径把检出/漏检/误报一律改 `unknown` 并加 `basis_scope`；报告依据段现覆盖三角色 |
| F7 | 交付说明里的 `--gold` 命令**静默无效**：`pkg.case_id` 默认为空，gold 绑不上，却报「gold 未就绪」（理由与事实不符） | 重要 | 区分「没给 gold」与「给了但绑不上」：后者明确提示需传 `--case-id`，`coverage.reason` 写真实原因；新增两条测试（绑不上 / 传了 case-id 后确实 judged） |
| F8 | plan 目标的「后续版本」是**编的**：直接取 `latest_revision`，审查 rev1 而 latest 也是 1 时也会报「后续版本 1」 | 重要 | 改为只有存在**更晚**修订时才算后续版本，否则 `None` 并计入 `unlinkable`；plan 审查同样纳入关联检查 |
| F9 | 报告无条件写「取末行为终态」，与实际的「终态以 `run_meta.status` 为准」自相矛盾，且不显示不一致 | 次要 | 改为如实描述规则，并在两者不一致时显式告警「该包可能被人工改动过」 |
| F11 | 汇总表列名误导：`失败数` 把合法澄清/拒答也算失败；`评分错误数` 统计的其实是自报 vs 重算差异 | 次要 | 改为 `交付数`/`未交付数`/`计数差异数`，并加注说明「未交付≠答错」「差异数≠评分器错误」 |
| F12 | `cases/<id>.md` 只是**精简壳**，按 case 打开看不到角色评测与查询诊断 | 次要 | `write_reports` 接受 `pkgs`，有包时渲染完整逐题报告（本包 172 行） |
| F13 | 去重键含 `parent_call_id`，转发副本一旦丢了该字段就会被重复计数；无 `call_id` 的事件完全不去重 | 次要 | 有 `call_id` 时只用 `call_id` 作键；新增测试（丢 parent 的重复副本仍被去重） |
| F14 | 若干小项：`results.json` 缺 `rows` 键被算成 0 行；只有一项 token 时仍标 `run_meta`；`冗余调用` 措辞像结论；`answer_candidate` 适配了却从不出现 | 次要 | `total_rows` 缺键记 `None`；新增 `run_meta_partial` 来源；报告改用「同工具重复调用（重复≠冗余）」；报告列出答案候选（并注明候选是诊断对象，不替代最终成绩） |

复审同时确认了通过项：硬约束 1/3/4/5/6/8 均满足（含 `agent/query/*` 未被改动、
跳过不计成功、无单一总分、历史包诚实降级、每项离线结论都带标记）、
Review Focus 五条全部有测试覆盖、固定包回放可复现。

复审未能判定的部分（本交付同样未覆盖）：`--judge live` 全链路未执行；
固定样例标注本身的**正确性与公平性**未由评测侧之外的人复核；
F5 的并发竞态在真实运行中尚未观察到（参考包主循环未调用该工具）。

## 8c. 第三轮复审（2 项）

第三轮又指出 2 处会让 Worker 评测结果失真的问题，均确认属实并已修复：

| # | 问题 | 修复 |
|---|---|---|
| 1 | **一次 Judge 结论被算成多个维度都已评测**：Worker 只调用一次 Judge、结果只含 `query_intent`，但 `_work_basis` 把同一个「有结论」布尔量同时用于 `calculation`/`evidence_support`/`task_completion`，报告会显示这些维度已评测 | `_work_basis` 改为按**维度**分配依据（`judged_dims`）；新增 `_judged_dimensions()`：默认只有 `query_intent` 算数，Judge 若返回 `dimensions`（{维度: 判定}）则只认其中判定非 `unknown` 的维度。`judged.judged_dimensions` 如实记录到底判了哪些维度 |
| 2 | **不同查询被误当成同一次查询的重试**：`_query_outcomes` 把一个节点的所有查询拉平，只要最后一条成功就把前面的失败都标成「已恢复」；但 Worker 可能查不同指标/期间，后一次成功不代表前一次补齐 | 改为按**查询意图**分组（标准化问题文本；无问题文本时退到计划签名；都没有则按序号视作不同意图）：只有**同一意图**重试成功才算恢复。新增 `unresolved_failed_query_calls`，并规定：只有**节点确实没交付**（产物未完成/缺 rid/完成标准未达标/缺必需输入）时才把未解决的查询失败当作根因；节点已交付时只作观察，不归因 |

新增测试覆盖第三轮明确点名的场景：「失败查询后跟着一条**无关**成功查询」不算恢复
（`test_unrelated_later_success_is_not_a_recovery`），以及一次 Judge 只点亮 `query_intent`
（`test_single_judge_verdict_only_marks_query_intent`）、Judge 逐维返回时才点亮对应维度
（`test_judge_can_report_multiple_dimensions`）。

> 附带的口径修正：归因现在区分「节点已交付」与「节点未交付」。节点已交付时，
> 某条查询失败只作为观察记录（`unresolved_failed_query_calls`），不再把整节点归因成
> 数据不可用——这正是第三轮第 2 点要求的方向。

## 8d. 第四轮复审（1 项）

| # | 问题 | 修复 |
|---|---|---|
| 1 | **「失败 → 成功 → 再失败」的恢复计数不准**：同一意图只看最后一次结果，若最后是失败，就把该意图下**所有**失败都记成未解决，包括已被中间那次成功补上的 | `_query_outcomes` 改为按**每次成功划分失败段**：成功之前累积的失败计入「已恢复」并清零；只有最后一次成功之后仍剩下的失败才算「未解决」。`unresolved_reasons` 取各段最后一个未解决失败的原因码，按出现顺序排序 |

新增计数测试覆盖完整序列语义：
`失败→成功→失败`（恢复 1、未解决 1）、`失败→成功→失败→成功`（恢复 2、未解决 0）、
`失败→失败→成功`（恢复 2、未解决 0），以及原有的「不同问题不互相抵消」。



## 9. 未完成 / 待集成审核

- **PRD §9.3 M3「真实执行贯通」未完成**：未跑真实 API，未做评分校准，未产出经审核的小样本真实执行。
- `--judge live` 已实现未执行；真实 Judge 的角色提示词（`RoleLLMJudge`，独立于 A 的 E2E 提示词）
  需在集成审核后另行校准。
- Planner 覆盖映射缺 gold 绑定路径的实跑验证。
- 本分支交付时未合并 main、未 push，后经 `feat/eval-integration` 合入 main；`eval/contracts/`、`eval/financial_e2e/`、`eval/schema.py`、
  `eval/runner.py`、`eval/graders/`、`eval/generators/` 均未改动。

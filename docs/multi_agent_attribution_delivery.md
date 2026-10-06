# 多 Agent 归因第一版交付说明（P6-M1 实现 + P6-M2 修复与实测）

日期：2026-10-06。开发执行模型：GLM（本会话）。项目运行模型：agnes-3.0-flash（未改）。
依据规格：`docs/handoff_multi_agent_attribution_development.md`（§1–§13）。

## 0. 版本状态与诚实更正

M1（commit `fbfc5f0` + 会话内未提交改动）交付了协议、三类子 Agent、主循环与 11 项离线测试，但有三处必须更正：

1. M1 文档写的冒烟命令 `scripts/live/smoke_multi_agent_S01.py` **不存在**，违反"不虚构尚不存在的开关"。现给出可直接运行的真实命令（见 §1）。
2. M1 声称 CLI 导出 `analysis_review.md`，**实际没有该实现**（只有 `analysis/analysis_state.json`）。M2 已补齐导出与渲染。
3. M1 记录的"S01 live 冒烟待运行"实际跑过并**崩溃**（`docs/evidence/multi_agent_S01_smoke.txt`：
   `TraceWriter.event() got multiple values for keyword argument 'role'`）。M2 修崩溃后重跑，第二次仍没跑起来
   （子工具不在 schema 里，模型看不到 plan/execute/review，`subagent_calls=0`）；第三次才是 §4 那次可运行的冒烟。
   本文件只把 §4 当作"一次 live 冒烟"的交付结果，不把前两次粉饰成运行过。

M1 离线测试全部通过，但没有一条走通"主循环 → 子 Agent → trace 转发 → 导出"的真实路径，因此没能发现下列缺陷。M2 新增的离线测试（§3）在修复前会失败，可作回归防线。

## 1. 可直接运行的命令

```bash
# single_agent（默认，行为与旧版一致）
.venv\Scripts\python -m agent.cli --question "联想 FY2027Q1 的库存增长是否反映周转恶化？"

# multi_agent（本项目运行模型走 config.yaml；免费档限速显式给 8 RPM）
.venv\Scripts\python -m agent.cli --question "<业务 query 原文>" --agent-mode multi_agent ^
  --max-steps 40 --max-rpm 8 --out-root runs/multi_agent_S01

# live 运行前必须绕过代理（.env 的 LLM_API_KEY 只进环境，不进仓库与运行包）
set NO_PROXY=api.agnes-ai.cn,*.agnes-ai.cn,localhost,127.0.0.1
```

退出码沿用 MVP 约定：0=answered 且数字核验通过；2=核验未通过；3=clarify/refuse；4=运行异常（异常包仍导出）。
multi 模式的 `verified` **只表示现有数字/引用核验通过**，语义/覆盖/人工状态用独立字段表达（§5）。

## 2. M2 改动清单

| 文件 | 内容 |
|---|---|
| agent/multi.py | ①trace 转发不再重复传 `type/role/step`（崩溃根因）；②`_run_role()` 统一三类子调用并写 `subagent_start/subagent_end`（角色、节点/目标、子步数、token、耗时、错误）；③数字核验改走 `hooks.stop`，与 single 路径同一 verifier，不再跳过勾稽项，且 `_final_args` 返回值按 `(args, err)` 元组正确使用；④token/时间预算在每步与每次子调用前检查，`max_final_revisions` 打回计数与诚实终止；⑤latency 用单调时钟；⑥子工具结果附精简 state_view，Reviewer 协议失败落 `inconclusive` 审查记录；⑦`_multi_stats()` 输出 semantic/task/human 等独立字段与生效预算；⑧预算从 `config.yaml` 的 multi_agent 段读取（M1 该段是死配置） |
| agent/loop.py | 修同类潜在崩溃：`event(type="llm_retry", **ev)` 重复 `type`（single 路径一旦出现重试即崩，第二轮没触发过所以未暴露） |
| agent/subagents.py | 子 Agent 的 API 重试事件写入子循环事件（§9 重试需留痕）；角色工具白名单与父运行配置的工具暴露集取交集（§5 继承禁用配置） |
| agent/v1.py | `Paced.__getattr__` 透传真实客户端属性：限速包装下 `last_call_events` 不再恒空，重试可追查 |
| agent/cli.py | multi 分发传 `ctx.llm`（Paced）而非裸 client，子 Agent 请求同样计入 RPM 节流；异常包 latency 改单调时钟；`parse_scope` 增比较口径解析（通用词表，非逐题匹配）并写入 TaskContract；run_meta 增 `multi_agent` 块（生效预算、角色模型、四份角色提示 SHA256、调度统计）；导出 `analysis_review.md` 并在 review.md 增多 Agent 状态段 |
| agent/multi.py（新增函数） | `analysis_review_md()`：人工可读审查摘要（契约/计划版本/节点状态/artifact 事实与 rid/审查意见/候选答案/资源与边界声明） |
| agent/prompts/multi/reviewer.md | 修正一处表述歧义（"不使用 gold，也不凭常识补造事实"） |
| tests/test_multi_agent.py | 由 11 项增至 28 项（§3 第 13–21 组） |
| tests/test_cli.py | 新增 1 项 CLI 级 multi_agent 全流程离线测试 |

M2 后半程（第一次 live 冒烟之后）补的根因与协议缺口——第一次冒烟没有崩，但也没真正跑起来：

| 文件 | 内容 |
|---|---|
| agent/tools/base.py | **根因**：`plan_analysis/execute_analysis/review_analysis/draft_answer` 四类子工具只有参数模型，从未注册进任何 schema 生成器；主循环按名字过滤 `all_tool_schemas()` 后只剩 `recall + final_answer`，真实模型因此无法规划（冒烟包 `subagent_calls=0、plan_revisions=0`）。新增 `MULTI_ARGS_MODELS`/`MULTI_DESCRIPTIONS`/`multi_tool_schemas()`；`FinalAnswerArgs` 增 `answer_artifact_id`（提示早已要求，参数校验却会把它当非法字段拒掉）；`DraftAnswerArgs` 增 `source_artifact_ids`（§8 候选记录来源 artifact）。single 工具面不变（子工具不泄漏进 `all_tool_schemas()`，由测试钉住） |
| agent/multi.py | ①主循环写 `assistant` trace 事件与 `args_head/result_head/call_id/ok`，live 失败可复盘（原来只有 `result_len`，无法定位参数）；②`_tool_call_ok()` 修正 ok 判定口径（`工具错误:` 前缀此前被记成成功）；③`recall` 补上 dispatch 分支（在 schema 里却没有实现，返回"未知工具"）；④`ready_nodes` 改按节点执行状态判定（原按 artifact 主键查 node_id 永远查不到 → 有依赖的节点恒受阻，函数实为死代码）；⑤§7.3 review 前置校验：对象存在、引用有效、版本一致，stale/版本不符直接拒绝并回"重做指引"；Reviewer 输出不合协议时降级为 `inconclusive` 记录而不是崩运行；⑥§6.3 `_fact_provenance()`：无 rid/rid 不存在 → `unproven`，来源 `synthetic` → 按来源改写（模型不能把合成明细升级成真实事实），纯常量 calc 与未知 rid 记出处提示；⑦§8 提交条件 `_multi_final_problems()` 重写：候选 ID/正文哈希一致、末次 answer 审查 accepted 且审的就是当前这份（旧 accepted 不继承）、必要节点有有效结果或明确缺口、候选来源 artifact 仍有效；⑧子 Agent 调用带 `agent_call_id`/`parent_call_id`，累计 `subagent_child_steps`（§9）；⑨`artifacts_stale`、`task_coverage_status=no_plan` 等统计口径 |
| agent/analysis_state.py | `content_hash()`；`ready_nodes()` 返回 (ready, blocked)；`mark_stale_downstream()` 补齐：传递闭包下游、节点回到 `pending`/审核 `inconclusive`（原来只标 artifact，节点仍 completed → 主 Agent 看不出它要重做，§8 也把它当"已有结果"）、失效审核登记未解决项；`valid_artifact_ids()`（完成＋未 stale＋节点最新一次）；`set_node_review_status()`；`state_view()` 增就绪/受阻/有效证据；`answer_version()`；候选记录 `source_artifact_ids` |
| agent/subagents.py | 子 Agent 的 `tool_result` 事件补 `call_id`/`result_head`（§10） |
| agent/cli.py | run_meta 与 review.md 增子循环步数、未处理 findings、终止原因、未完成节点 |
| agent/prompts/multi/main.md | 列出六个可见工具与推进顺序（plan→execute→review→draft→review(answer)→final 带 `answer_artifact_id`）；根因在 schema 注册，提示只是把协议说清，不注入 S01 专属内容 |
| agent/prompts/multi/worker.md | 输出协议字段与运行时对齐（company/period/data_nature、calculations 的 input_rids/output_rid/caliber），rid 必须真实存在，缺口写 `missing_inputs` |
| tests/test_multi_agent.py | 新增 §3 的 16–21 组共 13 项（含钉住根因的 `test_multi_main_agent_sees_all_subtools`） |

M2 末段（第一次可运行的 live 冒烟之后，§4）按六个失效模式补的修复——这一轮模型真的能规划、能取数、能收尾，但结论仍不合格：

| 文件 | 内容 |
|---|---|
| agent/subagents.py | **W1**：子循环步数用尽且没有协议 JSON 时，程序补一次"不再调用工具，只交已真实拿到的结果"收尾轮；`SubResult` 增 `finalized`/`steps_exhausted`，`child_steps` 把收尾轮计入；子 `tool_result` 事件补 `args_head`（冒烟里 `CalcRejected` 无法复现具体表达式就是因为缺这一列） |
| agent/multi.py | ①**W1**：收尾轮产物按 §6.3 记 `blocked`（证据入库、节点不算完成、缺口必填），返回文本给出补算/修订/`recall(artID)` 三条出路；②**W2**：只有真正启动子 Agent 才计一次额度，被拒调用单独计 `subagent_refusals`，并按角色统计 `subagent_role_calls`；③**W3/W4**：`_tool_plan` 说明补丁式修订规则，返回变更摘要（沿用/定义变化/新增/本次失效）+ 精简 state_view；④**W5**：`recall` 支持 `art#`/`answer-cand#`，未知 ID 回明确指引而不是 KeyError；⑤**W6**：`draft_answer` 返回时即时提示"claims 的 ref 只认结果 rid，产物归属写 `source_artifact_ids`"，`_multi_final_problems()` 增加同口径检查（verifier 判据未改）；⑥`execute_analysis` 的 `plan_revision` 省略/0=最新，显式旧版本直接拒绝并说明原因 |
| agent/analysis_state.py | **W3**：`save_plan()` 改补丁合并——本次未列出的既有节点沿用原定义与原执行/审核状态（原先必须重列整张图，模型两次"只改 n4"都被 `依赖 n1 不存在` 拒掉）；**W4**：精确失效——只有定义变化的节点及其传递下游的 artifact/review 作废（原先一次修订作废全部证据，实测 3 个 Worker 白重跑，12 次额度见底、Reviewer 0 次）；新增 `plan_change` 变更摘要并落盘 |
| agent/tools/base.py | `ExecuteAnalysisArgs`：`node_id` 说明加硬（冒烟里漏传过），`plan_revision` 改为可选（默认 0=最新），`DraftAnswerArgs.claims`/`FinalAnswerArgs.claims` 说明写清 ref 只认 `r#` |
| agent/prompts/multi/main.md | 增补纪律 6–9：claims 引用口径、revise 只写改动节点、`plan_revision` 省略即最新、三种角色共用子调用额度（审查要留额度）。不注入 S01 专属内容 |
| tests/test_multi_agent.py | 新增 §3 第 24 组 9 项（W1–W6 + 版本校验），逐项在修复前会失败 |

不改动：题库/gold/ETL/财务算法/预测、single_agent 的回答逻辑与数字判据（本轮仅动 `v1.Paced`/`agent/loop.py` 的重试留痕 plumbing，不改答案，见 §6 回归影响行）、旧运行包。未 push。

## 3. 离线验收结果（mock，不联网）

```bash
.venv\Scripts\python -m pytest tests -q      # 228 passed
```

构成（本次实测，不靠历史数字外推）：全库 228 passed；`tests/test_multi_agent.py` 由 M1 的 11 项增至 37 项（M2 两半合计新增 26 项，含 §3 第 24 组 W1–W6 共 9 项），`tests/test_cli.py` 8 项（其中 1 项是 multi CLI 全流程包导出）。M1 的 §11 十一项原样保留并通过，旧断言无删除或放宽。

| # | 检查 | 结果 |
|---|---|---|
| 13 | trace 转发不重复传参（复现 live 崩溃）：主/子 Agent 重试事件、子事件都落 trace，`main_step` 与子 `step` 并存；未提交时诚实 refuse 且状态持久化 | ✓（把 multi.py 改回旧写法该测试即以 `TypeError: ... 'role'` 失败，已实测） |
| 14 | `Paced` 透传 `last_call_events`/`model`，私有名不泄漏 | ✓ |
| 15 | TaskContract 承接 company/period/comparison；`config.yaml` 预算生效且未知配置键不扩大预算 | ✓ |
| 16 | token 预算耗尽时在步首与子调用前停止，不发新请求、不起子 Agent | ✓ |
| 17 | CLI 级 multi 全流程：plan→execute→draft→review→final，rc=0，导出含 `analysis/analysis_state.json` + `analysis_review.md` + run_meta.multi_agent（subagent_calls=3、human_review_status=pending、角色提示哈希），trace 含 planner/worker/reviewer 三角色 | ✓ |
| 18 | **主 Agent 看得见六类工具**：`multi_tool_schemas()` = recall/final_answer + 四个子工具，全部带"何时使用"提示且参数模型在册；子工具不泄漏进 single 工具面。`final_answer` 接受 `answer_artifact_id`，single 不填保持为空 | ✓（改回 M1 的 schema 生成方式即失败——这正是第一次冒烟 `subagent_calls=0` 的根因） |
| 19 | 主 Agent `recall` 只读证据通道可用（含参数不合法/失败两种返回）；trace 记录 `args_head`/`call_id`/`result_head`；无计划时 `task_coverage_status=no_plan` | ✓ |
| 20 | §6.2/§7.3 版本与依赖：`ready_nodes` 按执行状态给出就绪/受阻；上游重做后下游 artifact 标 stale、节点回到 pending、旧 accepted 降为 inconclusive；stale/版本不符的 review 被拒绝且不产生记录；已 accepted 节点不能原地覆盖 | ✓ |
| 21 | §8 提交条件：候选正文改动后旧 accepted 不继承（哈希不一致）；必要节点无有效结果或来源被标 stale 时拒绝提交；worker 登记 `missing_inputs`/blocked 属于"明确缺口"，不阻塞收尾 | ✓ |
| 22 | §6.3 出处核验：`synthetic` 来源不得被写成 real；无 rid/rid 不存在标 `unproven`；纯常量 calc 与表达式内未知 rid 记提示；合法 `r2` 计算不误报 | ✓（M1 文档把这一项列为已通过，实际当时既无实现也无测试——现已补齐） |
| 23 | §7.3/§9 Reviewer 输出违规（非法 verdict + 未知 category）降级为 `inconclusive` 记录并写明原因，不崩运行；不存在的目标不产生 review | ✓ |
| 24 | 第一次可运行 live 冒烟的六个失效模式逐项钉住（§4）：W1 步数耗尽→收尾轮＋blocked 证据保留（反面：收尾仍无 JSON 时失败原因写"步数耗尽"）；W2 被拒与非法参数调用不扣子调用额度、按角色分开统计；W3 revise 只列改动节点即可通过依赖校验；W4 精确失效（未改节点的证据仍有效、改动节点及其传递下游作废并落盘变更摘要）；W5 `recall` 读 `art#`/`answer-cand#`，未知 ID 给指引而不是 KeyError；W6 claims 的 ref 写成产物 ID 时当场提示并在提交条件里拦（verifier 判据不变）；外加 `plan_revision` 省略=最新、旧版本拒绝 | ✓（9 项，修复前逐条失败） |
| — | single_agent 既有回归（tools/loop/context/ledger/verifier/cli/dashboard/eval…） | 228 项全过，无删除或放宽旧断言 |

Lint：本仓库 venv 未安装 ruff/flake8，未跑静态检查；改动文件全部通过 `py_compile`。

## 4. S01 live 冒烟结果（允许失败，完整保存）

按 handoff §12 只跑一次，用现有 S01 原始业务 query、项目当前可用模型（`agnes-3.0-flash`，未换模型凑成功）、
CLI 现有入口，输出到独立新目录；`get_filing_notes`/`search_disclosure` 沿用默认禁用（不暴露管理层文本）。

```
运行包：runs/multi_agent_S01_smoke2/cli-20261006-170258/
调用形式：.venv\Scripts\python -m agent.cli --question "联想 FY2027Q1 的库存增长是否反映周转恶化？请比较上年同期，量化变化，并区分现有数据能够支持与不能支持的解释。" ^
          --agent-mode multi_agent --max-steps 40 --max-rpm 8 --out-root runs/multi_agent_S01_smoke2
结局：status=refuse、verified=false、stop_reason=时间预算耗尽（exit 3 口径）
```

诚实标注：本次是 nohup 后台启动，日志只留下 CLI 输出而没有回显命令行，所以上面是按 §1 形式重建的调用。
可核对的证据是 `run_meta.budget.max_steps=40`（config 默认 20，说明确实显式传了 `--max-steps 40`）与
`tools_disabled=[get_filing_notes, search_disclosure]`；`--max-rpm` 没有落进运行包，无法从证据回读（见 §7 第 10 项）。

证据摘录（trace/run_meta/analysis_state 的关键行，运行包本身不入库）：`docs/evidence/multi_agent_S01_smoke2.txt`。

### 4.1 这一轮真的跑起来了

与 M1（`subagent_calls=0`、`plan_revisions=0`，取一次数就收尾）相比，本轮主 Agent 完整走了协议：

| 观察项 | 实测 |
|---|---|
| 任务图 | revision 1 建 5 节点（n1 存货、n2 收入、n3 COGS、n4 计算、n5 解释），revision 2 细化 n4；`plan_revisions=2` |
| 子 Agent 调度 | 12 次启动：planner 4、worker 8、reviewer 0（`subagent_start` 事件逐条可查，含 `agent_call_id`） |
| 并行 | 主 Agent 在第 2 步一次性发 3 个 `execute_analysis`（n1/n2/n3），程序按调用顺序执行 |
| 取数质量 | n1/n2/n3 的 artifact 事实带真实 rid（r2/r3/r5…），FY2027Q1 存货 15,741.90、FY2026Q1 8,742.64（百万美元），与库里口径一致；`sql_errors=0` |
| 收尾链 | `draft_answer` 产出候选 `answer-cand9` → `final_answer` 带 `answer_artifact_id` 提交（M1 完全没有这条链） |
| 导出 | `analysis/analysis_state.json`、`analysis_review.md`、`run_meta.multi_agent`（生效预算、角色模型、四份角色提示 SHA256、调度统计）齐全 |

### 4.2 结论仍不合格：结局与六个失效模式

`status=refuse`、`verified=False`、`stop_reason=时间预算耗尽`（latency 609.75s > `max_wall_s=600`），
`nodes_incomplete=["n4","n5"]`，`reviews_total=0`（Reviewer 一次都没跑上）。资源：steps 14、
子循环步数 34、prompt 221,345 / completion 14,615 tokens。

| # | 冒烟里发生了什么（证据） | 根因 | M2 处置 |
|---|---|---|---|
| W1 | n4 两次 `execute_analysis` 都返回"执行失败（无合法输出）"；但子事件显示第一次已拿到 r9–r12、第二次拿到 r13–r21（同比 +80.06%、平均存货 13,731.4/8,333.22、COGS +40.08%、收入 +43.09%），`child_steps=6` 全部用在取数/计算上 | Worker 没有机会自己收尾，步数用尽即整节点作废；主 Agent 因此把 n4 判成"目标太抽象"去改计划，改了也没证据 | 子循环补程序发起的收尾轮（不再调用工具，只交已真实拿到的 rid）；产物按 §6.3 记 `blocked`（事实保留、节点不算完成、缺口必填），返回文本给补算/修订/`recall(artID)` 三条出路 |
| W2 | `run_meta.subagent_calls=14` 而 trace 只有 12 次 `subagent_start`；第 11 步与第 13 步的调用被"预算耗尽"拒绝 | 被拒调用也计了数，预算口径失真 | 只有真正启动子 Agent 才 `+1`；被拒次数进 `subagent_refusals`，角色分布进 `subagent_role_calls` |
| W3 | 第 5、8 步两次 `plan_analysis(revise)` 返回 `plan_invalid: 依赖 n1 不存在`，白耗 2 次 planner | 修订必须重列整张图，模型只想改 n4，图中缺 n1 就过不了依赖校验 | `save_plan()` 补丁合并：未列出的既有节点沿用原定义与原执行/审核状态；提示与工具描述同步 |
| W4 | 第 9 步修订成功后，n1/n2/n3 的 artifact（art1–art3）全部作废，第 10 步重跑三个 Worker 取回同一批数据（art6/art7/art8），12 次子调用见底，第 11、13 步的补算与审查全部被拒 | 修订时保守地作废全部证据，代价没有说明也没有必要 | 精确失效：只有定义变化的节点及其传递下游作废；返回变更摘要（沿用/定义变化/新增/本次失效 artifact）并落盘 `plan_change` |
| W5 | 第 6 步 `recall(result_id=art4)` 想看失败节点的产物，结果库按 rid 查 → "recall 失败"死路 | `recall` 只实现工具结果通道，没有 artifact/候选读取 | `recall` 支持 `art#`/`answer-cand#`（读 `analysis_state`），未知 ID 回明确指引 |
| W6 | 第 14 步 `final_answer` 的 14 条 claim 把 `ref` 全写成 `art6/art7/art8`，核验逐条回"rid art6 不存在"（`result_len=2608`），而正文数字本身与库里一致；`source_artifact_ids` 用得是对的 | 产物 ID 长得像引用编号，模型混用两套 ID；程序此前没有即时提示 | 提示层纠正（不改 verifier 判据）：工具描述、`draft_answer` 返回、`main.md` 纪律 6、`_multi_final_problems()` 检查四处对齐 |

另有两处运行观察没有改代码：`max_wall_s=600` 在免费档 8 RPM 下装不下一次 multi 运行（本轮 48 次 LLM 请求，609.75s 触顶，见 §7）；
n4 子循环里有一次 `calc` 被 `CalcRejected: 表达式中不允许的节点: Attribute` 拒掉，而同轮 `r6.inventory[Lenovo, FY2027Q1]`（带空格）
可以通过——表达式归一化属工具层，本次不改财务算法，留给下一轮（§7）。子 `tool_result` 事件当时没有 `args_head`，
所以无法从运行包复现被拒的具体表达式；M2 已补该列。

## 5. 状态字段口径（不与数字核验混用）

| 字段 | 含义 | 冒烟取值 |
|---|---|---|
| `numeric_verified` / `run_meta.verified` | 沿用原义：现有数字/引用 verifier 是否通过 | false |
| `semantic_review_status` | 最终候选的**末次** `review_analysis(answer)` verdict，不按"存在候选"推断 | `not_reviewed`（0 次审查） |
| `task_coverage_status` | `no_plan` / `partial` / `covered`：必要节点是否都有有效结果或明确缺口 | `partial`（n4/n5 未完成） |
| `human_review_status` | 恒为 `pending`：模型审查 accepted ≠ 业务 PASS | `pending` |
| `subagent_calls` | 真正启动的子 Agent 次数（§9 父子合计） | 12（修好后不再出现 14） |
| `subagent_refusals` / `subagent_role_calls` | 被预算拒掉的次数 / 三角色各启动几次 | 2 / planner 4、worker 8、reviewer 0 |
| `subagent_child_steps` | 子循环累计步数，与主循环 `steps` 分开 | 34 / 14 |
| `budgets_effective` | 实际生效的子调用预算（config 覆盖后） | 12/8/6/2/3/2 |
| `artifacts_total` / `artifacts_stale` | 产物总数 / 被标失效数 | 8 / 5 |
| `plan_revisions` / `reviews_total` / `unresolved_findings` | 计划修订次数 / 审查次数 / 未处理 findings | 2 / 0 / 0 |
| `stop_reason` / `latency_s` | 终止原因；单调时钟时差 | 时间预算耗尽 / 609.75 |

> 取值来源要分清：本列里 `numeric_verified / semantic_review_status / task_coverage_status / human_review_status / subagent_child_steps / budgets_effective / plan_revisions / reviews_total / stop_reason / latency_s` 是保存的运行包 `run_meta.multi_agent` 原值；而 `subagent_calls=12`、`subagent_refusals=2`、`subagent_role_calls`（planner 4 / worker 8 / reviewer 0）是从 trace 的 12 条 `subagent_start` 与两次预算拒绝反推，`artifacts_total=8 / artifacts_stale=5` 来自 `analysis_state.json`（art1–art5 记 stale）。这四组在本次冒烟的 `run_meta` 里当时**没有对应字段**——该运行早于导出补丁，且 `run_meta` 里字面记的是 `subagent_calls=14`（§4.2 W2 的计数 bug，"修好后不再出现 14" 即指此）。M2 已把 `subagent_role_calls / subagent_refusals / artifacts_total / artifacts_stale` 补进 `run_meta.multi_agent` 导出，下一次运行会直接落盘、不必再从 trace 反推。

## 6. 与 single_agent 的差异

对照口径先说清：下表比的是**同一 `--max-steps 40` 预算与同一模型**。初始数据包（`cli.py` step=0 生成）在两种模式走同一段代码，且都只写进 ResultStore 与 trace，从不注入任一模式的模型 prompt（`agent/prompts/system.md` 与 `user_message` 都不提及它，`build_view` 只压缩已有消息）——所以 `initial_packet` **不是** single/multi 的提示差异，成本差异与它无关。"成本"一行的十倍量级来自 multi 把每次子 Agent（planner/worker/reviewer）都当作独立请求（各自完整角色 prompt + 任务上下文）、且 Worker 重新取数而非复用数据包，属结构差异。

| 维度 | single_agent | multi_agent（本轮实测） |
|---|---|---|
| 可见工具 | 数据类工具 + `todo_write` 等，模型自己取数自己写答案 | 主 Agent 只有 `plan_analysis/execute_analysis/review_analysis/draft_answer/recall/final_answer`，取数交 Worker |
| 证据组织 | 一条 Result Store 时间线 | 计划版本 + 节点 artifact（facts/calculations/limitations/missing_inputs）+ 候选答案，全部落 `analysis_state.json` |
| 收尾条件 | 数字核验通过即可 `final_answer` | 数字核验 **且** §8 提交条件（已审候选、版本一致、必要节点有结果或明确缺口、来源 artifact 未失效） |
| 上下文 | 走 5 层压缩流水线（L1–L5） | 子 Agent 各自独立子上下文，天然分段；主循环目前不接压缩（§7） |
| 成本 | 早期 v1 单 Agent 基线 prompt 为万级（提示结构、概念字典与本轮不同，非同条件对照） | 本轮 221,345 prompt + 14,615 completion，48 次 LLM 请求（14 主步 + 34 子步）——相对单 Agent 至少一个数量级以上，但精确倍数需同题同条件对照才能定（§7 第 9 项），不外推 |
| 可审计性 | `review.md` 单份 | 多 `analysis_review.md`（任务图/节点状态/事实 rid/审查/候选与边界声明） |
| 回归影响 | — | single 的取数/判据/工具面未改动：子工具不泄漏进 `all_tool_schemas()`，`tests/test_tools.py` 与 CLI 用例钉住。本轮只动了两处共享的可观测性 plumbing——`v1.Paced.__getattr__` 透传 `last_call_events`（之前 Paced 把它藏起来，单 Agent 的重试事件被静默丢弃）、`agent/loop.py` 的 `llm_retry` 事件不再重复传 `type`（配合上一条，否则单 Agent 首次重试就会 `TypeError` 崩溃）。单 Agent 路径只因此新增"重试留痕"，不改回答内容/数字核验；全库 228 项离线回归绿 |

## 7. 未解决项与停止点

需要人来定的（我没有自行放宽预算、换模型或改判据）：

1. **时间预算与限速不匹配**：`max_wall_s=600` + 免费档 8 RPM 装不下一次 multi 运行（48 次请求 ≥ 600s 是算术必然）。要么给 multi 单独放宽 wall/token，要么减小任务图，属配置决策。
2. **12 次子调用额度与角色配比**：本轮 reviewer 0 次，`semantic_review_status` 因此只能是 `not_reviewed`。是否给三种角色分设额度（而不是共享）需要裁定。
3. **`calc` 表达式归一化**：n4 的某个 Worker 步里一次 `calc` 被 `CalcRejected: 表达式中不允许的节点: Attribute` 拒掉，
   而同一轮里 `r13.inventory[Lenovo,FY27Q1] / r15.inventory[Lenovo,FY26Q1] - 1`（无空格）与
   `r6.inventory[Lenovo, FY2027Q1]`（带空格）都成功过——被拒的那条当时没有 `args_head`，无法从运行包复现。
   M2 已给子事件补 `args_head`，下一轮能定位；表达式 AST 归一化属工具层，本次不改财务算法。
4. **§8「final_answer 引用 review ID」未实现**：现在按候选 ID + 正文哈希 + 末次 verdict 三重比对，等价保护但未字面实现交接文档那一条。
5. **multi 主循环未接上下文压缩**：长跑依赖程序侧精简返回（state_view 已到位），尚未接 `context_manager`。
6. **`ctx.stats["tool_calls"]` 在 multi 路径不计数**：与单 Agent 的统计口径不完全一致，展示层未用到。
7. **暂不支持删除节点**：只能 `required_for_answer=false` 让它不再阻塞收尾。
8. **运行包没有回显实际命令行与限速参数**：`--max-rpm`、`--max-steps` 里只有后者能从 `run_meta.budget` 反推，
   前者不落盘（§4 的调用形式因此是重建的）。是否把生效命令行写进 run_meta 属交付口径决策。
9. **multi 的 prompt 成本 10 倍于单 Agent，结构原因未量化**：本轮 221,345 prompt tokens 来自 14 次主 Agent 请求叠加 34 次子 Agent 步——planner/worker 每次请求都自带完整角色 prompt、任务上下文与已取到的工具结果，反复进模型。初始数据包在两种模式都只进 ResultStore、从不进任一模式的 prompt（§6），所以这一成本差与 `initial_packet` 无关。要判断差值里多少来自 Worker 重复取数、多少来自角色 prompt 本身，需要一次同题 single 对照，不能靠本轮数字外推。
10. **七题全量 multi_agent 实验未跑**：按规格留给用户看过本次冒烟后另行决定；本轮也仅此一次 live 运行。
11. 单 Agent 基线那边仍待决策的四个杠杆（A max_steps / B grounding 抑制 / C verifier 严格度 / D gold 标注）依旧挂起，不因 P6 改变。

停止点：M2 交付到此。离线 228 项全绿，S01 冒烟包与证据已完整保存，未 push。
下一批动作应由人工审阅本次冒烟后给出：是调预算/额度配置、还是修 `calc` 归一化、或进入七题批量实验。


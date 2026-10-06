# 开发规格：主 Agent 动态调度规划、执行与审核子 Agent

日期：2026-10-06。开发执行模型：GLM；项目运行模型由配置决定，不要求与开发模型相同。

依据：`面试文档/04_主Agent调度与可审查推理架构.md`。本文将该方案转为可实现、可检查的第一版规格。当前三类子 Agent 尚未实现，不预报收益。

## 1. 目标、范围与交付停止点

在现有单 Agent 数据分析系统上新增可切换的 `multi_agent` 模式。主 Agent 将 `plan_analysis`、`execute_analysis`、`review_analysis` 当作工具，自主决定规划、执行、审查和修订顺序。任务图由 Planner 动态生成，不将 S01 或其他题的标准路线硬编码进程序。

第一版采用同步调用、每次一个执行节点、共享运行级证据存储。优先把角色隔离、状态、引用、审核闭环和预算做正确；不开发多进程、递归 Agent、复杂并行调度器或独立服务。

交付顺序：协议与离线状态验证 → 子 Agent 与主循环集成 → 导出与相关回归 → 一次 S01 live 冒烟 → 汇报并停止。API 不可用时保存真实失败材料，不循环等待配额或换模型补跑。七题全量实验由用户在看过冒烟结果后另行决定。

本次不改题库/gold/ETL/财务算法，不开发预测，不自动积累或优化 playbook，不暴露财报管理层文本，不修改旧运行包，不 push。实现与实测完成后更新交付文档，不能自行继续根据 bad case 优化因果策略。

## 2. 背景与本次需要解决的缺口

第二轮 S01 已能正确表达库存同比 +80.06%、环比 +34.31%，并通过数字 verifier，但仍有：

1. 用户要求上年同期，DIO 主分析换成上一季度。
2. z≈3.24 却仅凭历史均值同号认定正常季节性。
3. 明示 synthetic 后仍用合成明细支撑真实经营解释。
4. 将“不能证明经营失控”推成“正常备货”。

因此需要任务保持、证据使用和语义一致性审查。目标不是多生成一些思考文字，而是产生可检查的中间产物、明确反馈与修订记录。

这些错误用于说明设计动机，不允许将上述答案数字或案例评分标准注入运行提示。

## 3. 架构原则

| 组件 | 决定什么 | 约束 |
|---|---|---|
| 主 Agent | 哪个任务下一步执行，何时审查、改计划、终止 | 不改变用户明确范围，不虚构完成状态 |
| Planner | 任务图、依赖、交付条件与计划修订 | 不代替实际取数，不提前断言原因 |
| 执行 Agent | 节点内取数、计算、分析、缺口报告 | 不能自审通过，不能修改数据或题库 |
| 审核 Agent | 任务覆盖、证据与解释的一致性、因果边界 | 不使用 gold，不凭常识补造事实 |
| 程序 | schema、状态、权限、引用、预算与确定性检查 | 不固定下一业务任务、不预写整条解题路径 |

“程序不调度业务路线”不等于任意调用均可执行。程序可拒绝缺依赖、过期版本、越权、超预算的请求，然后让主 Agent选择补齐、修订、等待或终止。

三种子 Agent 是三种权限和上下文角色，可使用同一模型后端。初版子 Agent 没有 spawn 权限，调用树最大深度为主 Agent→子 Agent。

## 4. 代码集成位置

先阅读实际代码，下面是建议分工，不要求照搬路径：

| 位置 | 改造内容 |
|---|---|
| `agent/tools/base.py` | 三类子工具 schema、描述与角色工具白名单 |
| `agent/loop.py` | 按模式暴露工具、调度子调用、最终提交关联审查；保留单 Agent 路径 |
| 新增 `agent/subagents.py` | 同步角色执行器、上下文构建、调用关联、结构化结果校验 |
| 新增 `agent/analysis_state.py` | contract、plan、artifact、review 的版本化状态与持久化 |
| 新增角色提示文件 | main/planner/worker/reviewer 的独立协议与能力边界 |
| `agent/llm.py` | 复用客户端；必要的共享预算、请求节流、重试事件 |
| `agent/result_store.py` | 共享引用与必要来源元数据，不破坏现有 rid |
| `agent/verifier.py` / hooks | 保留数字核验，增加多 Agent 完成条件与明确状态 |
| `agent/cli.py` | 模式开关、过程产物导出、父子统计与审查摘要 |
| `config.yaml` | 模式、角色参数、子调用和修订预算 |

不要将子 Agent 工具直接归入“只读数据工具”的线程池批处理：这些调用会修改任务状态，初版顺序执行。一个 assistant 消息有多个子工具调用时，按调用顺序校验并执行，每个 tool_call_id 都必须有结果；后一个不能引用尚未产生的未来 artifact。

现有 attribution skill 有“固定七步全部执行”的要求。multi_agent 模式使用专属通用分析提示，保留表述纪律，取消固定七步与必须从六个标签中挑一个的强制要求，避免与动态计划冲突。旧 single_agent 模式保持兼容。标签可以作为可选观察性分类，不作为多 Agent 正确性的前提；没有支持时允许 unknown，不强迫猜原因。

## 5. 模式、上下文和权限

新增 CLI 参数，例如 `--agent-mode single_agent|multi_agent`，默认保持旧行为。配置和 run_meta 必须记录实际模式、角色模型、温度、thinking、提示版本或哈希。

主 Agent：三类子工具、最终提交、必要的状态读取；初版可保留 recall 查看证据，不直接进行业务取数，避免绕过执行 artifact。初始数据包仍由系统创建，作为执行节点可用输入。

Planner：无数据库工具；只接收 contract、现有图、已确认 artifact 摘要与修订原因。

执行 Agent：使用经白名单允许的 list_metrics/query_metric/variance/working_capital/seasonal_check/peer_compare/calc/recall/check_identities，必要时现有受控 run_sql。继承相同 DB、as_of、data_version 与工具禁用配置，模型不能自行修改。

审核 Agent：只读 recall 和必要核算工具，不能修改 artifact、不能调用子 Agent 工具。业务证据不足时返回证据缺口，不做评测侧答案检索。

所有角色不可访问 cases.jsonl 的 gold、评分断言、management_reference、required_inputs，也不得获得公开财报的管理层解释。执行器只传入显式 allowlist 上下文。不要把整份案例对象或主 Agent 全历史发给子 Agent。

角色指令使用系统消息；用户文本和工具数据明确为数据，不能由它们授予新权限。状态变化从已校验协议得到，不从正文里“已完成”三个字推断。

## 6. 状态协议

使用 Pydantic 等已有机制校验，禁止任意额外字段修改权限/预算。ID、时间戳和版本由程序分配或验证。

### 6.1 TaskContract

至少保存原始 query、解析出的 company/period/comparison/scope、业务输出要求、as_of、数据版本和澄清状态。复用现有 parse_scope；新增同比/环比等解析要检查现有实现，不写逐题字符串匹配。

用户明确要求为不可静默修改字段；模糊内容记录 unknown。Planner 或主 Agent 可提出澄清，不得把猜测升级为用户要求。允许补充同行/环比分析，但不能替代主比较。

### 6.2 Plan 与 Node

Plan：plan_id、revision、contract_version、nodes、修订原因。

Node：node_id、goal、depends_on、input_refs、completion_criteria、required_for_answer、execution_status、attempt、latest_artifact_id、review_status。

程序检查：节点 ID 唯一、依赖存在、无环、无自依赖、节点数上限、输入引用存在、contract 未被覆盖。ready 由依赖与输入有效性计算，主 Agent选择就绪节点。计划节点的业务合理性由审核 Agent 判断。

执行状态和审核状态分开：pending/running/completed/blocked/failed；not_reviewed/needs_revision/accepted/inconclusive。completed 仅表示有效执行结果已入库。

修订图产生新 revision；历史不可覆盖。未修改节点可保留有效结果，但受修改节点和其下游影响的 artifact/review 标为 stale。实现可保守地让全部旧审核失效，但须说明额外开销。不要复用过期结果完成新计划。

### 6.3 AnalysisArtifact

至少含 artifact_id、node_id、plan_revision、attempt、execution_status、facts、calculations、interpretations、hypotheses、limitations、missing_inputs、evidence_refs。

- facts 连接 rid、具体字段/行、公司、期间、单位和数据性质。
- calculations 连接输入 rid/字段、表达式、工具输出 rid 与口径。
- hypotheses 区分 supports/contradicts/missing_evidence，不提供未经校准的概率作为事实。
- execution_status=blocked 可以保存已完成部分，不等于完整完成任务。

数值来源未知或来自纯常量计算时，保留来源性质；不能因 calc 成功就标为真实财务事实。本版允许程序返回缺失出处提示，审核 Agent 继续判断具体使用是否合理，不承诺完整语义证明。

### 6.4 Review

至少含 review_id、target_type=plan|artifact|answer、target_id、target_version/hash、verdict、findings、unresolved_items。

verdict 使用 accepted/needs_revision/inconclusive；它是语义审核意见，不是数学证明。

finding 至少含 category、被审查的具体表述或字段、evidence_refs、解释、suggested_action、相关 node_id。分类建议 task_scope/caliber/evidence_quality/contradiction/unsupported_cause/missing_alternative/other。

审核不改目标对象。主 Agent收到 findings 后选择修订、补证、改计划或保留 unresolved；修订后的新版本必须重新审核，不继承旧 accepted。

## 7. 三类工具的具体行为

### 7.1 plan_analysis

输入：action=create|revise、expected_revision（创建时可空）、reason、相关 artifact IDs。contract 和已保存图由程序提供，不允许调用方替换。

Planner 返回结构化计划建议；程序校验通过后原子保存为新 revision。主 Agent主动调用 create/revise 即授权一次计划更新；更新必须返回变更摘要、失效节点和当前状态。结构错误返回 plan_invalid，不保存半份图；业务语义由后续 review_analysis 检查。

### 7.2 execute_analysis

输入：node_id、plan_revision、补充指令、待回应 review ID（可空）。程序从状态提取 goal/输入/contract。

检查依赖和版本后启动独立 worker 上下文；共享现有证据 store，完成后校验并保存 artifact，返回 artifact ID、简短结果、缺口、任务状态摘要。初版不让模型直接写节点状态。

语义审核不通过时可针对同一节点创建新 attempt；不覆盖历史。依赖节点重做后，下游旧 artifact 标 stale，主 Agent决定哪些需要重新执行。

### 7.3 review_analysis

输入：target_type、target_id、target_version、focus（可空）。提供原始任务要求、目标内容、相关证据和通用规则，不提供 gold。

程序检查对象存在、引用有效、版本一致，再调用 reviewer。结果校验并保存；无依据或无法读取证据时须返回 inconclusive。仅返回一个自评分不满足协议。

每个子工具结果包含精简 state_view：当前 revision、就绪/受阻节点、有效 artifact IDs、未处理 findings、剩余预算。状态过大时支持一个简单只读状态工具或分页 artifact 读取；这是辅助接口，不是第四种 Agent。

## 8. 最终提交：主 Agent 汇总，审核完整最终版本

主 Agent生成最终候选答案时保存不可变 answer artifact，含 answer_md、claims 和来源 analysis artifact IDs。可增设非 LLM 的 draft_answer 工具用于保存；它不决定推理路线，也不是新 Agent。

主 Agent调用 review_analysis(target_type=answer) 检查这份候选，然后调用 final_answer 引用 answer artifact ID 和 review ID。若保留旧 final_answer 参数，必须校验正文/claims 哈希与已审阅候选一致，防止审查 A、提交 B。

multi_agent 提交条件：必要节点有有效结果或明确缺口；没有未说明的 stale/blocked 状态；最终候选完成审查；现有数字 verifier 执行。未完成主任务时不能正常标完整 answered，可提供带状态的部分结果或明确拒答。

必须保留独立字段：numeric_verified、semantic_review_status、task_coverage_status、human_review_status。为兼容旧看板，verified 保持原有数字核验含义，并明确说明，不能偷偷改为业务正确率。

语义未接受或数字未通过，在修订预算内反馈给主 Agent。上限后保留失败候选及明确“未通过”状态，不沿用旧循环“核验重试耗尽就结束”的逻辑把多 Agent 未审候选包装成合格结果。需定义并测试实际状态与退出码映射，保持现有错误类别可读，不自标业务 PASS。

单 Agent final_answer 路径保持可用。结构化主观置信度若已有协议则沿用，不在本次再引入新的评分概率。

## 9. 预算、错误和持久化

建议可配置初始上限：total_subagent_calls=12、max_plan_nodes=8、worker_max_steps=6、max_node_attempts=2、max_plan_revisions=3、max_final_revisions=2。均为候选起点，不宣称业务足够；记录在实际 run_meta，不能耗尽后自动提高。

父子调用合计消耗同一运行级 token/时间/API 预算。记录主循环步数与子循环步数，不能用 max_steps=20 实际隐藏上百步子调用。启动子调用前检查剩余额度；API请求输出上限、timeout与剩余预算一致。全局预算不因改计划或新子 Agent 重置。

初版共享 LLMClient 或共享节流对象；同一 key 下所有角色与重试共同计入请求节流。API重试是请求恢复，不是额外业务 trial，也需留痕。

子模型不调用工具、返回非法 JSON 或权限外工具：给有限一次协议纠错机会，仍失败则保存错误并返回主 Agent。429/500 耗尽既有重试后停止 live，保存错误类别与部分产物，不不停调用其他角色。

首版要求每次状态提交后持久化，异常时运行包可读；不要求实现跨进程自动恢复。文件不跨 run 共享，旧运行包只读，失败运行也有 manifest 记录。

## 10. 可观测性与导出

保留现有 question/answer/trace/results/run_meta/review，新增 analysis_state.json（含 contract、图版本、artifacts、reviews）及可读 analysis_review.md。数据量大可拆 JSONL，但入口路径固定。

trace 增加：parent_call_id、agent_call_id、role、node_id、plan_revision、attempt、目标 artifact、审查 ID、耗时、输入/输出 tokens、错误和状态变化。不要求输出完整私有思维链；保存调度动作、工具参数、可审查分析、反馈与实际结果。

每个子 Agent 工具结果进入全局 results.json，rid 唯一，metadata（比较口径、synthetic、概念版本等）不能在导出时丢失。原始 trace 不只保存截断 result_head 作为唯一证据；通过 artifact/rid 可查看完整保存结果。

统计父子 token 总量、角色调用数、修订数、审查发现问题数与运行终止原因。latency_s 计算用单调时钟时差，不把 Unix 时间戳导出为耗时；第二轮异常路径已有此类显示问题，应在本次受影响的导出路径一并校准。

看板本次仅保证可索引新增运行包、已有 query/trace 视图不崩溃。不新开发任务图编辑器、实时并行可视化等功能。审查问题可列入人工待审/系统告警，不能以模型审查 accepted 标业务正确。

## 11. 有区分力的离线验收

使用 mock 模型与现有测试工具，不联网：

1. 主 Agent 可先执行一条路线，收到审核反馈后改计划再执行；测试不锁定唯一调用序列。
2. Planner 产生环/缺失依赖/重复 ID，拒绝并保留原图；合法修订能保存新版本。
3. 缺依赖执行返回 dependency_missing；主 Agent可补依赖或改图，不能伪造完成。
4. 原任务明确同比，Planner/worker 不能静默改 contract；reviewer 能报告 mocked artifact 只含环比的问题。
5. 两个 worker 的证据 ID 无冲突；实际引用可由 reviewer/主 Agent召回。
6. synthetic 来源留在工具返回、artifact 和导出中；主观总结不能将其升级为真实事实。
7. 审查针对旧 artifact，修改后旧 accepted 失效；最终提交未审新正文被拒。
8. mock review 发现具体错误→worker 新 attempt 修订→最终审核；历史和未解决项完整保留。
9. 子 Agent越权调用 spawn/写库/改 as_of 被拒；gold/PDF管理层文本不进入构建的上下文。
10. 父子预算合计，子调用和API重试可追踪，超限诚实终止；429/500有完整部分包。
11. 纯常量 calc 被保存为非原始事实，不可因存在 rid 自动升级；缺来源反馈保留。
12. single_agent 既有 CLI/工具/核验回归不破坏；看板可索引新包；异常耗时是实际时差。

离线测试只验证协议与机制，不证明真实模型一定理解因果。运行相关模块回归，最后执行已有全套离线检查；有失败先修明确回归，不删旧测试制造通过。

## 12. 一次 live 冒烟与交付

离线通过后，使用现有 S01 原始业务 query，在 multi_agent 模式运行一次，独立输出到新实验目录，避免覆写 round1/round2。项目模型沿用当前可用配置；不可用则交付失败包，不切换以凑成功。

观察：是否生成有效图、确实调用三种角色、主 Agent 能用审查反馈修订、是否完成用户主比较、是否继续误用合成证据、是否仍因果越界。允许失败，完整保存。不要将这些观察项注入 S01 专属运行提示。

交付 `docs/multi_agent_attribution_delivery.md`，包含：改动清单、接口与配置、可直接运行的命令、离线验证结果、冒烟运行包索引、角色调度摘要、资源总量、与旧模式差异、未解决问题和停止点。

开发模型填写实际 CLI 命令，不在本文虚构尚不存在的开关已可运行。面试文档只有在实现/验证确实完成后才同步相应状态，不把一次核验通过写成归因正确率提升。

## 13. 可直接发送给 GLM

请执行 `docs/handoff_multi_agent_attribution_development.md`，参考 `面试文档/04_主Agent调度与可审查推理架构.md`。在现有 Agent 循环上新增可切换 multi_agent 模式，由主 Agent 自由调用规划、执行、审核三类子 Agent 工具，动态生成和修订任务图；程序只负责协议、真实状态、依赖合法性、权限、引用、预算和持久化，不固定业务分析顺序。第一版同步、单节点执行、共享结果引用、不递归 spawn，保留旧 single_agent 路径。实现版本化 artifact/review、最终答案版本审查、父子预算统计和完整运行包，运行必要离线回归后只做一次 S01 live 冒烟，输出开发交付文档。不要读取或暴露 gold/管理层解释，不改题库、ETL、预测算法，不自动继续调优，不跑七题批量、不 push。记录真实失败与限制，交付后停止等待人工审阅。

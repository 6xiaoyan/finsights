# 多 Agent 调度 v2（PRD §1/§2/§5/§8）实现与实测交付

日期：2026-10-09。依据：`docs/prd_multi_agent_scheduler_v2.md`。
运行模型：`deepseek-flash`（2026-10-09 由 `agnes-3.0-flash` 切换，理由见 `config.yaml` 头注释）。
本文件是实现与实测记录；**没有真实验证的能力一律标"仍待验证"**，不把预期收益写成结论。

## 1. 本轮实现的范围

PRD §10 的 6 步里，本轮完成 **步骤 1、2 与步骤 5 的审查侧**；步骤 3、4（并发执行与并发安全）
**未实现**，理由见 §6。

| PRD 步骤 | 状态 |
|---|---|
| 1 完成判定、required/optional 缺口、阶段交付 | 已实现 + 离线测试 + live 验证 |
| 2 continuation 保存与恢复、对象读取统一 | 已实现 + 离线测试 + live 验证 |
| 3 有界执行池、启动与完成事件、统一提交 | **未实现** |
| 4 客户端/限速器/结果库/数据库并发安全 | 部分（限速器与 rid 分配已线程安全），并发场景未验证 |
| 5 Planner 并发建议、分对象审核、反馈闭环 | 审查侧已实现；Planner 并发建议**未实现** |
| 6 回归与新运行包报告 | 回归 285 passed；运行包已产出 |

## 2. 改动清单

| 文件 | 内容 |
|---|---|
| `agent/analysis_state.py` | `EXEC_STATES` 增 `paused`；`RELEASING_STATES=("completed",)`；`PlanNode` 增 `required_output_refs`/`requires_review`/`slice_index`；`AnalysisArtifact` 增 `declared_status`/`completion_report`/`required_missing_inputs`/`optional_missing_inputs`/`next_actions`/`continuation_id`（执行状态可 paused）；`ReviewFinding` 增 `severity`(must_fix/limitation/suggestion)/`finding_id`/`response_status`；`Review.unresolved_must_fix()`；continuation 存储与失效；`state_view` 增 `paused_nodes`、unresolved 只列未解决 must_fix |
| `agent/multi.py` | **新增 `_judge_completion()`**（按交付与完成标准判定，不再以 `finalized`/`steps_exhausted` 判 blocked）；paused 时保存 continuation；**新增 `_tool_resume()`**（续接同一上下文）；**新增 `_tool_read_object()`**（完整/分页读对象）；`_tool_review` 去掉 `[:3000]` 截断、加分对象审查范围、must_fix 门禁；依赖守卫改用 `RELEASING_STATES`；`_run_role` 支持 `resume_messages`/`extra_tools`；`_multi_stats` 增 paused/blocked/continuation 统计 |
| `agent/subagents.py` | `SubAgentRunner.run(resume_messages=)`；`SubResult` 增 messages 与新协议字段；`ArtifactProposal` 增新字段；`SubAgentRunner(extra_tools=)` 注入只读通道；**`_allowed_tools` 只对数据类工具套父配置**（见 §4 缺陷）；reviewer 白名单加 `read_analysis_object` |
| `agent/tools/base.py` | 新增 `ResumeAnalysisArgs`、`ReadAnalysisObjectArgs` 与描述；`_inline_refs()` 内联 `$ref`（跨厂商修复，非本轮 PRD 范围但阻塞 live）；`multi_tool_schemas()` 纳入两个新工具 |
| `agent/cli.py` | `run_meta.multi_agent` 落盘 paused/blocked/continuation/未解决 must_fix |
| `agent/prompts/multi/*.md` | worker 增加完成判定协议与 required/optional 说明；reviewer 增加 severity 语义、审查范围、读取要求；main 增加四种节点状态语义与 resume 说明 |

## 3. 实测对照（同一题 S01、同一模型 deepseek-flash、同为 max-steps 100）

| 指标 | 旧实现 `ds_multi_S01` | 本轮 `prd_v2c_multi_S01` |
|---|---|---|
| status / verified | answered / True | answered / True |
| **task_coverage** | **partial** | **covered** |
| 计划版本数 | 6 | **3** |
| **dependency_missing 次数** | **7** | **0** |
| 未完成节点 | 7（n1–n7 全部） | **0** |
| 产物状态 | blocked×9（全部） | completed 7 / paused 3 / blocked 3 |
| 审查次数 / accepted / inconclusive | 8 / 4 / 2 | 11 / **9** / **1** |
| continuation 使用 | —（无此能力） | 3 |
| 未解决 must_fix | 不可见（49 条 finding 无 severity） | 2（可见） |
| latency | 314s | 346s |
| prompt tokens | 2,106,539 | **1,568,385（−26%）** |
| sql_errors | 0 | 1 |

**这些数字不能当作"质量提升"的结论**。可以确认的只有：状态语义与调度过程确实变了
（不再全 blocked、覆盖变完整、审查不再大面积 input_unavailable）。业务归因质量、
数字忠实度仍需人工评审——`human_review_status` 恒为 pending。

## 4. 开发过程中发现并修复的两个真缺陷（都先由 live 暴露）

**缺陷 A：Reviewer 看得见工具却调不动。**
第一次 v2 live（`prd_v2_multi_S01`）里我在提示与工具描述里都让 Reviewer 用
`read_analysis_object` 读完整对象，但 `ROLE_TOOLS["reviewer"]` 没有这个名字 →
9 次审查里 8 次返回 `inconclusive` / `input_unavailable`。修法：加进白名单 + `extra_tools` 注入。

**缺陷 B：父运行的 `enabled_tools` 把角色专属通道一起滤掉。**
修完 A 后 v2b（`prd_v2b_multi_S01`）仍然 13/15 次 inconclusive，trace 显示
`read_analysis_object 返回 KeyError`。根因：CLI 把 `enabled_tools` 设为
`sorted(DESCRIPTIONS) - DISABLED_TOOLS`，而 `read_analysis_object` 不在 `DESCRIPTIONS` 里，
于是"角色白名单 ∩ 父配置"把它过滤掉了。修法：`_allowed_tools` 只对 `DESCRIPTIONS` 中的
数据类工具套用父配置；角色专属通道不受该开关影响（`get_filing_notes`/`search_disclosure`
仍然被禁用，隔离未被放开）。两次都有回归测试钉住。

## 5. 离线验收

```
.venv\Scripts\python -m pytest -q   →  285 passed
```
新增测试（`tests/test_multi_agent.py` 共 53 项，PRD §11 相关场景均走真实工具入口）：
- 预留轮交付合法 → `completed`（不再 blocked），下游可启动
- 仅可选缺口缺失 → `completed` 且限制保留；必需缺口 → `blocked` 并指名缺口
- 完成标准未满足 + next_actions → `paused` + continuation_id
- paused 节点不释放下游；真实入口对下游执行被 `dependency_missing` 拒绝
- resume 保留工具历史（断言续接轮上下文里能看到上轮 rid）、不重复取数、同一 continuation 不可重复用
- 节点定义变化 → continuation 失效（历史保留）
- 长 artifact 后半段的错误能通过 `read_analysis_object` 分页读到
- 节点审查带上 goal/completion_criteria，且明确"取数节点不必回答整题"
- 未解决 must_fix → accepted 被纠正为 needs_revision；limitation/suggestion 不阻塞
- Reviewer 能**实际调用**读取通道；该通道不泄漏给 worker；父配置开关不误伤它
- 旧协议 `missing_inputs` 保守按必需处理（不替 Worker 假定"可选"）

项目全局检查：G3（无新增 skip/xfail）、G6（agent/semantic/fincalc 不读 gold）、
G7（无 eval/exec/subprocess）、`.env` 未被跟踪——均无输出/通过。

## 6. 未实现与仍待验证（不得当作已完成）

**并发（PRD §3、§4）完全未做**：没有 `start_analysis_batch`/`wait_analysis`，
没有有界线程池，`_tool_execute` 仍同步串行。PRD §7 的 9 条并发安全项一条都没验证
（rid 分配锁在 `ResultStore` 上已存在，但并发相同查询是否产生矛盾缓存未测；
AnalysisState 目前无单一提交入口，多 Worker 写状态会不安全）。
**因此 PRD §11 里 A/B/C/D 那 4 行并发场景测试没有写，也不能写**——没有实现可测。

**Planner 并发建议**（`priority_hint`/`parallel_reason`/`start_analysis_batch` 授权）
未实现。

**仍待验证的其他项**：
- `no_progress` 事件（重复工具请求、相同错误）只有协议位，没有实现判定逻辑
- 审核反馈跨候选版本关联（`finding_id` 已能稳定生成，但没有"同一 finding 被重复审"的关联规则）
- 正文数字扫描的负号问题（PRD §8 末要求另列 bug 复现）未处理
- 多 Worker 并发下的 trace/统计口径未验证
- S02–S07 未跑；S01 只有单次运行，不代表稳定性

## 7. 运行包

- `runs/ds_multi_S01/`（旧实现，历史包未修改）
- `runs/prd_v2_multi_S01/`（缺陷 A/B 现场，保留）
- `runs/prd_v2b_multi_S01/`（缺陷 B 现场，保留）
- `runs/prd_v2c_multi_S01/`（修复后，本轮结论依据）

## 8. 配置与迁移

- 新增 `PlanNode.required_output_refs` / `requires_review`（默认 `True`）
- 新增 `ReviewFinding.severity`，**旧运行包/旧 mock 缺该字段时按 `limitation` 兜底**（偏保守，不放宽）
- `artifact.execution_status` 可为 `paused`；旧的 `blocked` 语义不变
- `state_view` 的 `unresolved_findings` 口径变了：只列未解决 must_fix（原来列 needs_revision 的全部 finding）

## 9. 待人工决定

1. `面试文档/04_主Agent调度与可审查推理架构.md` 有另一 Agent 的未提交改动
   （`git diff` 显示 158 增 165 删）。PRD §12 要求更新该文档的"已实现/已验证/仍待验证"状态，
   但为避免覆盖他人工作，**本轮未改该文件**，请确认由谁更新或何时更新。
2. 是否继续做并发（PRD §3/§4）。它是 PRD 里风险最高的一块（9 条并发安全项），
   建议单独一轮，且必须先有"一个 Worker 失败不取消其他""迟到结果 obsolete""rid 唯一"
   这几条的离线测试再上 live。
3. 本轮所有运行 `dirty=True`（工作区有未提交改动，含另一 Agent 的 SEC 数据文件），
   复现性不足，建议先提交一轮再复测。
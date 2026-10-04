# 第一阶段交付说明（PRD 评测与优化 · 第一阶段底座）

日期：2026-10-04。开发执行模型：GLM（本会话）；项目运行模型：agnes-3.0-flash（`run_meta.json` 的 `model` 字段为准），两者是不同配置。

## 1. 差异清单（PRD 3.6 要求的"已满足 / 需补齐"核对）

### 已满足（复用现有实现，未改动）

| 要求 | 现状 |
|---|---|
| 五件套运行包（question/answer/trace/results/run_meta） | 已有（`agent/cli.py::_export_package`），含数据哈希、dirty、脱敏配置、防御截断标记 |
| as_of 时点快照路由；未提供时明确声明"当前库，非历史回测" | 已有（`--as-of` → `etl.snapshot.make_snapshot`；question.json note） |
| 数字核验（claims 容差 + 未引用数字扫描 + 勾稽）与核验反馈 | 已有（agent/verifier.py） |
| results.json 可按 rid 关联 args/SQL/数据行/data_version | 已有 |
| 真实/合成标记 | facts_detail.synthetic=TRUE，导出行携带 |
| 密钥脱敏与零价格调用的 token/耗时记录 | 已有（_sanitize；usage 记录不依赖计费） |
| 退出码诚实（0/2/3） | 已有 |

### 需补齐（本次实现）

| 缺口 | 实现 | 位置 |
|---|---|---|
| 初始数据包（固定规则，先于 Agent 自主取数） | config `initial_packet`（metrics × quarters_back=8）；CLI 在 step=0 执行 query_metric 并标记 `initial_packet: true`；解析范围与规则写入 question.json | agent/cli.py `parse_scope`/main；config.yaml |
| 用户范围由系统映射 | `parse_scope`：公司别名（联想/惠普/戴尔）+ FYxxQx / yyyyQn / 中文季度；解析不到的字段由 Agent 澄清，不擅自定 | agent/cli.py |
| 披露检索禁用（落实到工具暴露层） | RunContext.enabled_tools 白名单；execute 守卫；CLI 排除 `get_filing_notes`/`search_disclosure`；run_meta 与 question.json 记录 `tools_disabled` | agent/loop.py、agent/tools/data.py、agent/cli.py |
| LLM 客户端内部重试可追查 | `last_call_events`（attempt/wait_s/error）→ loop 写入 trace.jsonl（type=llm_retry） | agent/llm.py、agent/loop.py |
| review.md 人工审阅摘要 | 范围与输入/答案位置/工具时间线/关键引用/错误与终止/资源统计 + 三视角待人工判断栏（默认"未评审"） | agent/cli.py `_export_package` |
| 运行中出错也落盘 | loop_run 异常捕获 → 保存已获得轨迹 + 状态（status=refuse，refuse_reason=异常），退出码 4 | agent/cli.py main |
| 步数预算透传 | `--max-steps`（默认取 config；预算记录进 run_meta） | agent/cli.py |
| token 计量校准（交接回答二步骤 1） | est_tokens CJK 感知 + usage 校准（EMA）+ est_prompt_tokens/实际值并写入 trace（此前已实现，本轮核验） | agent/context.py、agent/llm.py |
| 输出上限（交接回答二步骤 1） | llm.max_output_tokens_main=8000 / summary=4000，经 max_tokens 显式传入（能力探针待独立留档） | agent/llm.py、config.yaml |

### 活动证据保护与压缩候选参数（交接回答二步骤 2/3 的底座部分）

已实现：`CompState.active_rids`（召回的 rid 从存根集合移除并受保护）、L3/L4 不压缩活动依赖、L4 视图追加有界活动证据块、config 候选起点（l3_trigger_tokens=64000、compact_threshold=128000、compact_target_tokens=80000、stub_keep_last=6、ledger_keep_recent_turns=4、l5_enabled=false，旧 l3_ratio 保留但不再使用）。对照实验（步骤 4 的真实业务对照与步骤 5 的 V4.44 压力矩阵）按交接要求另行运行，不在本底座交付内提前声称收益。

## 2. 实际可用命令

```bash
PYTHONIOENCODING=utf-8 NO_PROXY=api.agnes-ai.cn .venv/Scripts/python -m agent.cli \
  --question "联想 FY2023Q3 存货下降是否意味着周转改善？请根据财务数据分析。" [--as-of YYYY-MM-DD] [--max-steps N]
```

## 3. 验收运行（PRD 3.6 指定 query）

- 第 1 次（默认 20 步预算）：**步数预算耗尽，诚实拒答**（status=refuse）。运行包 `runs/cli-20261004-191242/`，证据 `docs/evidence/phase1_acceptance_output.txt`。按 PRD 验收条件"完成运行或明确失败；状态与终止原因真实"成立。
- 第 2 次（`--max-steps 40`，预算变化已在 run_meta 记录）：见 `runs/cli-20261004-*/` 与 `docs/evidence/phase1_acceptance_40steps.txt`（结果与核验状态以运行包为准）。
- 两次运行包均保留，未覆写。

## 4. 已知限制

1. 步数预算：默认 20 步对本类归因问题偏紧（第 1 次验收即因预算拒答）；预算是显式参数，可在命令行调整，不构成能力评价。
2. 披露检索被禁用后，涉及经营解释的问题只能基于结构化数据分析；"解释是否成立"由人工审阅，不归本底座判定。
3. 时点回测的严格性受 companyfacts 比较期补报影响（V1.21 报告）：如需严格 as_of 评测，运行应标注"当前数据库历史回放"限制。
4. review.md 的三视角评审栏默认"未评审"，人工填写后即构成审阅记录；本底座不自动标任何业务 PASS。
5. 成本字段依赖 API 返回 usage；免费档未返回计费信息时记为"未知"，不伪造数值。

## 5. 停止点

按 PRD 3.5/6：底座交付后停止扩展，等待人工审阅与第二阶段指令；不建数据集、不跑批量、不做预测与回测。

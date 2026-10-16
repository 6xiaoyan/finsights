# E2E 评测与数据集工具设计

日期：2026-10-16；状态：设计草案，待审核。

## 1. 意图与目标

依据 `docs/prd-agent-evaluation.md` §5–6 及已冻结的 `eval/contracts/`，实现 Agent A 的 E2E 评测与数据集工具：

- 基于港交所联想公开财报构造 30 道真实财报问题
- 从报表和管理层解释提取参考答案及得分点
- 实现已有运行包重评分、当前 Agent 数据集运行适配、逐点评分、整篇评分及报告
- 不改变 Agent 行为，不修改 B 的目录或自行合并 main

## 2. 目标 / 非目标 / 反目标

### 目标
- 30 道真实财报题（含 7 道已审开发题 S01–S07）
- 每题含参考答案、原子得分点、数值规则、禁止断言、审核状态
- 三个可运行入口：生成得分点候选、对已有运行包评分、按固定数据集调用当前 Agent 并评分
- 逐题报告与汇总报告

### 非目标
- 不修改 Agent 的任务拆分策略、角色提示词、查询策略
- 不新增 Web 服务、数据库服务、前端
- 不自动批准自己生成的 gold
- 不修改 B 的目录（`eval/agent_process/`）

### 反目标
- 不把合成题与真实题混为一组报告
- 不把 blocked 题无声消失
- 不把模型报错、无法判断的点从分母静默剔除
- 不把质量、耗时和 token 拼成一个总分

## 3. 约束

- 来源限定为港交所公开披露的联想财报及业绩公告 PDF
- 优先复用项目已有的原文文件
- 预测的未来实绩只留评测侧
- 候选状态为 draft，不得由生成模型自行设 approved
- 使用两个独立 Git worktree 和分支

## 4. 架构设计

### 4.1 公共协议（eval/contracts/）

```python
# eval/contracts/case.py
class CaseInput(BaseModel):
    case_id: str
    question: str
    scope: str
    as_of: str | None
    data_refs: list[str]

class CaseGold(BaseModel):
    case_revision: int
    reference_answer: str
    sources: list[SourceRef]
    scoring_points: list[ScoringPoint]
    numeric_assertions: list[NumericAssertion]
    forbidden_claims: list[str]
    review_status: Literal["draft", "approved", "blocked"]
    data_visibility: dict

class RunArtifact(BaseModel):
    run_id: str
    case_id: str
    run_dir: Path
    final_status: str
    final_answer: str | None
    claims: list[Claim]
    candidate_refs: list[str]
    version: str
    process_objects: dict
    usage: dict

class TraceEvent(BaseModel):
    schema_version: str
    event_id: str
    run_id: str
    event_type: str
    start_time: str | None
    end_time: str | None
    status: str | None
    parent_event_id: str | None
    node_id: str | None
    plan_revision: int | None
    role: str | None
    role_call_id: str | None
    tool_call_id: str | None
    query_trace_id: str | None
    result_ids: list[str]
    artifact_id: str | None
    artifact_version: str | None

class GradeResult(BaseModel):
    grader_name: str
    grader_version: str
    case_id: str
    run_id: str
    object_id: str
    verdict: Literal["met", "partial", "missing", "contradicted", "unknown"]
    dimension_scores: dict[str, float]
    evidence_refs: list[str]
    unknown_reason: str | None
    model: str | None
    prompt_version: str | None
    usage: dict
```

### 4.2 数据集结构（eval/datasets/financial_real_v1/）

```
eval/datasets/financial_real_v1/
├── cases.jsonl          # 30 题（CaseInput + CaseGold 合并）
├── sources.json         # 来源索引（PDF URL、页码、发布日期）
├── review_log.md        # 审核记录
└── blocked.jsonl        # 无法回答的题（如有）
```

### 4.3 E2E 评测模块（eval/financial_e2e/）

```
eval/financial_e2e/
├── __init__.py
├── contracts.py         # 从 eval/contracts/ 导入并扩展
├── dataset.py           # 数据集加载、验证、审核工具
├── adapter.py           # 运行包适配（RunArtifact 提取）
├── grader.py            # 逐点评分 + 整篇评分
├── report.py            # 逐题报告与汇总报告
├── cli.py               # CLI 入口
└── __main__.py
```

### 4.4 CLI 入口

```bash
# 生成得分点候选
python -m eval.financial_e2e generate-points --cases cases.jsonl --output points.jsonl

# 对已有运行包评分
python -m eval.financial_e2e score-run --run-dir runs/cli-20261016-134035 --case-id LV-xxx

# 按固定数据集调用当前 Agent 并评分
python -m eval.financial_e2e run-dataset --dataset financial_real_v1 --cases cases.jsonl --output runs/eval/

# 生成汇总报告
python -m eval.financial_e2e report --runs runs/eval/ --output eval/reports/e2e_report.md
```

## 5. 题目设计

### 5.1 题目分布

| 主类型 | 数量 | 重点 |
|---|---:|---|
| 多期比较与财务计算 | 6 | 同比/环比、累计转单季、流量与余额、单位与口径 |
| 变化分解与归因 | 10 | 收入到利润的贡献桥、规模与比率、营运资金联动、替代解释 |
| 证据边界与综合判断 | 8 | 数值与结论的区分、指标改善是否足以支持业务判断、证据冲突 |
| 历史时点预测 | 6 | 固定历史截止日、明确目标与期限、条件与区间、不确定性 |

### 5.2 复用 S01–S07

从 `data/eval/lenovo_attribution_candidates/selected_v1/cases.jsonl` 复用 S01–S07，保留来源与审核历史。

### 5.3 新题设计原则

- 覆盖不同报告期（FY2023–FY2027）
- 覆盖不同财务变化（增长、下降、结构变化）
- 覆盖不同业务判断（盈利能力、营运资金、业务组合）
- 至少 24 题需要组合多项证据或执行两步以上实质推导

## 6. 评分设计

### 6.1 逐点评分

- 每个得分点包含：稳定 point_id、原子判断、适用条件、required/optional、权重、证据来源、可接受等价表达、矛盾反例、核验方式
- 使用 LLM-as-judge：输入问题、参考点、可用事实和最终答案
- 判定：met/partial/missing/contradicted/unknown

### 6.2 整篇评分

- 维度：扣题、推理连贯、结论与证据一致、条件/不确定性和表达清晰度
- 每维 1–5 分及证据理由
- 整篇 Judge 不读取内部 Reviewer 的 accepted/rejected

### 6.3 数值断言

- 记录指标、公司、期间、比较基期、数值、单位、方向、公式与接受的舍入精度
- 区分核心答案数字、可选支持数字和内部自查数字

## 7. 实施计划

### 阶段 1：公共协议与 3–5 题样本（M1）
1. 创建 `eval/contracts/` 公共协议
2. 创建 `eval/financial_e2e/` 模块骨架
3. 交付 3–5 题完整样本（覆盖不同题型）
4. 校准得分点生成方式

### 阶段 2：完成 30 题候选（M2）
1. 完成剩余 25–27 题
2. 记录官方来源与页码
3. 生成参考答案、原子得分点、数值规则、禁止断言和审核状态

### 阶段 3：评分工具与报告（M3）
1. 实现已有运行包重评分
2. 实现当前 Agent 数据集运行适配
3. 实现逐点评分、整篇评分及报告
4. 交付准确命令、样例和审核/阻塞清单

## 8. 验收标准

- 30 题中哪些已审核/阻塞有明确记录
- 固定包回放可复现
- 经审核小样本真实执行能贯通
- 无评分反馈改变被测行为
- 交付说明写清哪些已实现、哪些仅定义协议

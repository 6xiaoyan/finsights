# FinSights — 财报分析 Data Agent（MVP）

基于公开财报数据 + 自合成场景的财务分析 Agent：自由问题 → 工具取数/计算/检索披露 →
带来源引用的分析结论与限制 → 数字/引用核验 → 完整运行记录落盘，供人工分析 bad case。

**合规红线**：只使用公开财报数据与自行合成数据，不含任何企业内部数据（含脱敏数据）。
API key 只从 `.env` 读取，严禁进入仓库或运行包。

## 环境与配置

- Python 3.11+；`pip install -e .`（依赖见 `pyproject.toml`）。
- `.env`：`LLM_API_KEY=...`（OpenAI 兼容端点，模型/base_url/限速在 `config.yaml`）。
- 数据：`data/finsights.duckdb`（已建好，USD 百万；三家美企 Lenovo/HP/Dell 季度数）。
  重建路径：`etl/fetch_lenovo.py` / `extract_lenovo.py` / `synth_detail.py` / `load_db.py`。
- 披露语料：`data/disclosures/`（正文目前**主要覆盖联想**；HP/Dell 财务数字可查但缺经营
  披露——涉及时 Agent 会声明限制。`data/manual/` 合成分部明细是自合成数据，不是真实分部）。

## 单问题运行（MVP 入口）

```powershell
.venv\Scripts\python -m agent.cli --question "联想 FY2023Q3 存货下降是否意味着周转改善？"
# 时点隔离（走 as_of 快照库 + 披露 available_date 过滤，非仅 prompt 传日期）：
.venv\Scripts\python -m agent.cli --question "..." --as-of 2024-06-30
```

终端显示：结论、status、核验结果、run_id、输出目录。退出码 `0`=answered 且核验通过，
`2`=核验未通过（数字/引用规则不符，不得当正常成功），`3`=clarify/refuse（含预算拒答）。
"核验通过"仅指现有数字/引用规则通过，不代表经营因果结论已被证明。

每次运行自动保存人工可读运行包 `runs/cli-<时间戳>/`：

| 文件 | 内容 |
|---|---|
| `question.json` | 原始问题、as_of、数据范围说明 |
| `answer.md` | 完整结论 + 引用 claim（值/单位/rid）+ 核验状态 |
| `trace.jsonl` | 每步模型可见输出、工具名/参数、结果 rid、报错与核验反馈、压缩事件 |
| `results.json` | ResultStore 全量：rid → 工具、参数、SQL、数据行（引用可回查） |
| `run_meta.json` | 模型、脱敏配置、commit+dirty、库/披露索引哈希、预算、实际 usage/步数/耗时 |

人工可从 `answer.md` 的 `[rN]` 引用 → `results.json` 里同 rid 的行与 SQL 查到数据出处。
导出按工具返回范围（防御上限 2000 行/结果，超出记 `truncated`），无密钥字段。
失败/争议案例按 `docs/badcase_card_template.md` 填卡，连同运行包供人工与强模型讨论
（人工自行发送，不做自动外发）。

## 批量评测

```powershell
.venv\Scripts\python -m eval.runner --agent v1 --datasets l1,l2   # 其余参数见 --help
```

题集在 `eval/datasets/`（gold 与题目分离，Agent 不可见）；报告写 `eval/reports/`，
汇总在 `eval/reports/leaderboard.md`；失败案例档案 `eval/badcases.md`。
live 验收脚本在 `scripts/live/<ID>_*.py`，取证输出到 `docs/evidence/<ID>_output.txt`。

## 测试（完全离线，不联网）

```powershell
.venv\Scripts\python -m pytest -q
```

## 已知失败与限制

- 归因类 L3：同行比较目前用原始环比百分比而非按各自波动（z）标准化，industry_wide 题
  存在系统性误判风险（见 `eval/badcases.md` V5.10 冒烟小节；待人工讨论裁定）。
- V4.44 压缩冒烟 FAIL、V4.15/V4.47 批阅 REVIEWED-FAIL：保留待修复，非本次 MVP 前置。
- 预测（L4/V6.10）代码与回测已提交但 live 暂缓；80% 预测区间实测覆盖率约 63%。
- 财季映射以 `periods.calendar_quarter` 为准（三家公司财年不同），直接按日历季猜会错。
- 准确率不承诺 100%；失败可复现（run 包 + commit + dirty + 数据哈希）是 MVP 承诺。

## 文档索引

- 需求/计划/验收：`prd.md`、`plan.md`（含 §12 变更记录与 MVP 范围收敛公告）、`verify.md`
- 进展与证据：`docs/verification_log.md`、`docs/evidence/`
- 人工裁定：`docs/questions.md`（Q8/Q9/Q10/Q12、V5.12 等待办在案）

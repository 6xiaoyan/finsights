# 验证日志

> 记录格式见 verify.md 0.2。每个检查项一行，证据必须是真实执行的命令和输出摘要。

## 阶段完成清单

```
P0: [x] 全局 G1–G8  [x] V0.1–V0.9（G3 按验证文档 P0 跳过）   tag: phase-P0-done
```

## 全局检查（P0 关闭时执行，2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| G-selftest | PASS | 临时分支 g-selftest 制造违规，G3/G4②/G4③/G6/G7 全部抓到；G7 未误报 ast.literal_eval。发现并处置：G4③ 的 `api_key=` 字面量模式命中测试中的假 key `api_key="test-key"`（3 处）→ 检查项不动，测试改用常量 `_TEST_KEY` 传入。明细见 `docs/evidence/G-selftest.txt` | （本次） |
| G1 | PASS | `pytest -q` → **7 passed**（全程离线 mock，无网络依赖） | （本次） |
| G2 | PASS | `pytest --collect-only` → 7 tests（P0 为首阶段，此数记为基线） | （本次） |
| G3 | — | P0 跳过（无上一阶段 tag，验证文档同此约定） | — |
| G4 | PASS | ① `git ls-files --error-unmatch .env` → 未跟踪；② `.env` 密钥值逐个 `git grep -nF` → 无输出；③ `sk-…` 形态与 `api_key="…"` 扫描 → 无输出 | （本次） |
| G5 | PASS | 提交后 `git status --porcelain` 无输出；P0 提交均为 `[P0.<n>] …` 格式 | （本次） |
| G6 | PASS | `grep -rnE "eval/datasets\|scenarios/.*\.json\|gold" agent/ semantic/ fincalc/` → 无输出 | （本次） |
| G7 | PASS | `grep -rnE "(^\|[^_.[:alnum:]])(eval\|exec)\(\|subprocess\|os\.system" agent/ semantic/ fincalc/` → 无输出 | （本次） |
| G8 | PASS | questions.md 三问均"已关闭"（Q1 正则修复、Q2 thinking 显式关闭、Q3 联网检查归 scripts/live/） | （本次） |

## P0 环境与骨架

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V0.1 | PASS | `git check-ignore .env data/finsights.duckdb data/snapshots/x runs/x data/raw/sec/x.json` → 4 个计划内路径全部输出（另将 `data/raw/` 整体纳入忽略，因 SEC JSON 体积大且可由 etl 重下） | e23ddd1 |
| V0.2 | PASS | `find . -type d` 对照 plan 1.2：etl / semantic / fincalc / agent(含 tools、skills、prompts) / eval(含 datasets、generators、graders、reports) / runs / tests / data(含 raw、manual、scenarios、snapshots) / docs 全部存在 | 41af55c |
| V0.3 | PASS | `pip install -e .`（.venv）成功；`python -c "import duckdb, pandas, statsmodels, pydantic, yaml, openai, httpx, dotenv"` → "V0.3 imports OK" | （本次） |
| V0.4 | PASS | `config.yaml` 四项输出：`https://apihub.agnes-ai.com/v1 agnes-3.0-flash 20 512000`；`grep -rn "deepseek" agent/ --include=*.py` → 无输出 | （本次） |
| V0.5 | PASS | `python -m agent.llm "ping：请回复一个字：通"` → 返回"通"，`[usage] {'prompt_tokens': 81, 'completion_tokens': 2, 'total_tokens': 83}`。端点为 `api.agnes-ai.cn/v1`（中国站；国际站 apihub 对该 key 返回 401，诊断见上一版本记录） | ee82d62 |
| V0.6 | PASS | live check（verify 0.5）：`python scripts/live/V0.6_tool_call.py` → tool_call name=`get_weather`、arguments 含 `"city": "北京"`，输出 `docs/evidence/V0.6_output.txt`（2026-10-01T13:58, agnes-3.0-flash）。解析逻辑的离线覆盖在 `tests/test_llm.py::test_tool_call_parsing` | （本次） |
| V0.7 | PASS | `pytest -q` → **5 passed**。`test_retry_succeeds_after_two_429`：2 次 429 后第 3 次调用成功（共 3 次调用）；`test_retry_raises_after_exhaustion`：连续 4 次失败抛 RateLimitError（共 4 次调用）；另有认证错误不重试、tool_call 解析、温度按 role 读取共 5 项 | （本次） |
| V0.8 | PASS | DeepSeek 与 Agnes 的模型名、上下文长度、工具调用、缓存规则均已查证并附官方文档链接，见 `docs/questions.md`"已查证"两节 | （本次） |
| V0.9 | PASS | `config.yaml` 含 `llm.enable_thinking`（agent/judge 分别为 false）；`agent/llm.py` 每次请求显式传 `extra_body.chat_template_kwargs.enable_thinking`；离线测试 `test_enable_thinking_sent_explicitly`（请求体含该字段）与 `test_reasoning_content_not_written_back`（reasoning 只进 stderr 日志，不写回 messages）均通过 | （本次） |
| （附）能力探针 | 3/5 | 两次运行（温度 0）结果一致。通过：连通与中文、工具调用、company_specific 归因（label/JSON 全对）。失败×2（同一失败模式）：① 三家共振场景判成 company_specific——被"联想 z=4.7 高于同行 4.1"的排名带偏，无视三家同向同量级；② 错误前提题不纠正前提，反而把存货数据误读成营收后顺着"下降"编造归因。结论与设计启示见 questions.md"P0 能力探针结论" | （本次） |

> 阶段注：P0 已于 2026-10-01 关闭（tag: phase-P0-done）。V0.5/V0.6 曾因端点问题 BLOCKED，切换中国站端点后补齐；全局检查见上方"全局检查"表。

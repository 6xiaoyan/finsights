# 验证日志

> 记录格式见 verify.md 0.2。每个检查项一行，证据必须是真实执行的命令和输出摘要。

## P0 环境与骨架

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V0.1 | PASS | `git check-ignore .env data/finsights.duckdb data/snapshots/x runs/x data/raw/sec/x.json` → 4 个计划内路径全部输出（另将 `data/raw/` 整体纳入忽略，因 SEC JSON 体积大且可由 etl 重下） | e23ddd1 |
| V0.2 | PASS | `find . -type d` 对照 plan 1.2：etl / semantic / fincalc / agent(含 tools、skills、prompts) / eval(含 datasets、generators、graders、reports) / runs / tests / data(含 raw、manual、scenarios、snapshots) / docs 全部存在 | 41af55c |
| V0.3 | PASS | `pip install -e .`（.venv）成功；`python -c "import duckdb, pandas, statsmodels, pydantic, yaml, openai, httpx, dotenv"` → "V0.3 imports OK" | （本次） |
| V0.4 | PASS | `config.yaml` 四项输出：`https://apihub.agnes-ai.com/v1 agnes-3.0-flash 20 512000`；`grep -rn "deepseek" agent/ --include=*.py` → 无输出 | （本次） |
| V0.5 | BLOCKED | 用户提供 key 后仍 401。诊断记录（curl 实测，约 20 次请求，2026-10-01 11:59–12:19 UTC+8）：① 所有推理端点（/v1/chat/completions、/v1/messages、/v1/responses）与两种 token 形式（带/不带 `sk-` 前缀）、三种认证头形式均为 401 "Invalid token"；② 唯一一次通过是 12:12 用**裸 token**（去前缀）访问 /v1/models 得 HTTP 200，几分钟后同请求也变 401，之后连续 3 轮间隔重试均 401。结论：请求形式已排除，网关对该 key 的校验结果不稳定 → 疑似新 key 未完成同步/激活、key 被平台侧停用，或 key 非本人账号所建。等用户在控制台核验/换新 key 后重跑 `python -m agent.llm "ping"` | — |
| V0.6 | BLOCKED | 同 V0.5。探针脚本已就绪：`docs/evidence/P0_capability_probe.py` 探针 2 即 get_weather/北京 用例（实现位置见 questions.md Q3） | — |
| V0.7 | PASS | `pytest -q` → **5 passed**。`test_retry_succeeds_after_two_429`：2 次 429 后第 3 次调用成功（共 3 次调用）；`test_retry_raises_after_exhaustion`：连续 4 次失败抛 RateLimitError（共 4 次调用）；另有认证错误不重试、tool_call 解析、温度按 role 读取共 5 项 | （本次） |
| V0.8 | PASS | DeepSeek 与 Agnes 的模型名、上下文长度、工具调用、缓存规则均已查证并附官方文档链接，见 `docs/questions.md`"已查证"两节 | （本次） |

> 阶段注：P0 尚未关闭——V0.5/V0.6 等待有效 key；G1–G8 全局检查在阶段关闭时统一执行。

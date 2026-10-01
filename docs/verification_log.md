# 验证日志

> 记录格式见 verify.md 0.2。每个检查项一行，证据必须是真实执行的命令和输出摘要。

## P0 环境与骨架

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V0.1 | PASS | `git check-ignore .env data/finsights.duckdb data/snapshots/x runs/x data/raw/sec/x.json` → 4 个计划内路径全部输出（另将 `data/raw/` 整体纳入忽略，因 SEC JSON 体积大且可由 etl 重下） | e23ddd1 |
| V0.2 | PASS | `find . -type d` 对照 plan 1.2：etl / semantic / fincalc / agent(含 tools、skills、prompts) / eval(含 datasets、generators、graders、reports) / runs / tests / data(含 raw、manual、scenarios、snapshots) / docs 全部存在 | 41af55c |
| V0.3 | PASS | `pip install -e .`（.venv）成功；`python -c "import duckdb, pandas, statsmodels, pydantic, yaml, openai, httpx, dotenv"` → "V0.3 imports OK" | （本次） |
| V0.4 | PASS | `config.yaml` 四项输出：`https://apihub.agnes-ai.com/v1 agnes-3.0-flash 20 512000`；`grep -rn "deepseek" agent/ --include=*.py` → 无输出 | （本次） |
| V0.5 | PASS | `python -m agent.llm "ping：请回复一个字：通"` → 返回"通"，`[usage] {'prompt_tokens': 81, 'completion_tokens': 2, 'total_tokens': 83}`。端点为 `api.agnes-ai.cn/v1`（中国站；国际站 apihub 对该 key 返回 401，诊断见上一版本记录） | ee82d62 |
| V0.6 | PASS | 探针脚本探针 2：`agnes-3.0-flash` 对"北京天气怎么样？"返回 1 个 tool_call，name=`get_weather`，arguments 含 `"city": "北京"`。证据：`docs/evidence/P0_capability_probe_output.txt` | （本次） |
| V0.7 | PASS | `pytest -q` → **5 passed**。`test_retry_succeeds_after_two_429`：2 次 429 后第 3 次调用成功（共 3 次调用）；`test_retry_raises_after_exhaustion`：连续 4 次失败抛 RateLimitError（共 4 次调用）；另有认证错误不重试、tool_call 解析、温度按 role 读取共 5 项 | （本次） |
| V0.8 | PASS | DeepSeek 与 Agnes 的模型名、上下文长度、工具调用、缓存规则均已查证并附官方文档链接，见 `docs/questions.md`"已查证"两节 | （本次） |
| （附）能力探针 | 3/5 | 两次运行（温度 0）结果一致。通过：连通与中文、工具调用、company_specific 归因（label/JSON 全对）。失败×2（同一失败模式）：① 三家共振场景判成 company_specific——被"联想 z=4.7 高于同行 4.1"的排名带偏，无视三家同向同量级；② 错误前提题不纠正前提，反而把存货数据误读成营收后顺着"下降"编造归因。结论与设计启示见 questions.md"P0 能力探针结论" | （本次） |

> 阶段注：P0 尚未关闭——V0.5/V0.6 等待有效 key；G1–G8 全局检查在阶段关闭时统一执行。

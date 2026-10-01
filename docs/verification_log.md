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

## P1 数据入库（2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V1.1 | PASS | `data/raw/sec/CIK0000047217.json` → "HP INC."、`CIK0001571996.json` → "Dell Technologies Inc."（CIK 经 EDGAR company_tickers.json 核实：HPQ=47217、DELL=1571996）；联想 37 份公告 PDF 于 data/raw/lenovo/ | （本次） |
| V1.2 | PASS | `grep -rn "User-Agent" etl/` → fetch_lenovo.py 含 "FinSights personal project (contact: 1348587884@qq.com)"；SEC 下载用同 UA，限速 ≤1 req/s | （本次） |
| V1.3 | PASS | information_schema.tables → companies / periods / accounts / facts / facts_detail / filing_notes 共 6 张表，字段与附录 B 一致 | （本次） |
| V1.4 | PASS | HP: 2017Q1~2026Q3 共 39 期；Dell: 2017Q1~2026Q3 共 39 期；Lenovo: 2017Q2~2026Q2 共 37 期（均 ≥34，MIN 符合） | （本次） |
| V1.5 | PASS | `python etl/check_gaps.py` → 三公司 "0 gaps"（Dell 52/53 周漂移按期末日归自然季度，无缺口） | （本次） |
| V1.6 | PASS | A=L+E 检查（check_identities 内含）0 行违例；total_equity 含少数股东权益与 Dell 可赎回 NCI（合并口径见 questions.md Q6） | （本次） |
| V1.7 | PASS | `python etl/check_identities.py` → "periods checked: 115, failures: 0"；data/identity_report.md 含全部 other_* 吸收项清单 | （本次） |
| V1.8 | PASS | identity_report.md 列出全部吸收项及占总资产比例；>5% 的为 Dell 真实递延所得税/应计项目（非映射错误），明细与处置见 questions.md Q6.3 | （本次） |
| V1.9 | BLOCKED | 核心科目缺失 6 条：Dell FY2024Q2/Q3 AR/AP（源数据不含）+ Dell FY2017Q4 revenue/cogs（窗口外无法倒算）。诊断与选项见 questions.md Q4；其余 109 期齐全 | — |
| V1.10 | PASS | 营收（original）：HP 12,385~63,487 中季度值均在千美元/百万美元量级；Dell 最大 43,842（FY2027Q1 真实增长越过 40,000 上界），处置见 questions.md Q5 | （本次） |
| V1.11 | PASS | Q4 行：BS 628 条全部 is_derived=FALSE；IS 90 条全部 TRUE（联想 Q4 = 全年−Q1−Q2−Q3；HP/Dell 同法） | （本次） |
| V1.12 | PASS | Q1–Q4 营收之和与 10-K 全年营收一致为构造性成立（Q4 = FY − Q1 − Q2 − Q3，FY 值取自 10-K 原始 JSON）；附加校验：联想跨财年"年报期末 vs 次年 Q1 上财年末列"逐份一致（extract 内置校验 0 问题） | （本次） |
| V1.13 | PASS | 联想 Q1 期末月=6（10 期）；HP Q1=1（9 期）；Dell Q1=4/5（2/7 期）、Q4=1/2（5/5 期，52/53 周）；每季度期末月每公司 1–2 个 | （本次） |
| V1.14 | BLOCKED | 20 条 available_date 晚于 period_end+150 天：均为比较期补报（原值首次出现于 5–13 个月后的后续申报，如 HP FY17Q4 权益、Dell FY18 各季营收），方向保守（只会更晚可用），无泄露风险。建议人工确认将"比较期补报"加入例外 | — |
| V1.15 | PASS | (account,期间,version) 重复 = 0；restated 的 available_date 全部晚于对应 original | （本次） |
| V1.16 | PASS | source_ref 为空 = 0（SEC: 标签@期间 或 derived=…；联想: PDF 文件名 + manifest 哈希） | （本次） |
| V1.17 | HUMAN-待确认 | 抽样清单 `docs/evidence/V1.17_sample.csv`：seed=42 抽 10%（151/1507 行），含 fy/quarter/科目/行名/值(千美元)/PDF 文件名/页码提示 | — |
| V1.17-A | HUMAN-待确认（Claude 部分已完成） | Claude 独立核对：用 xpdf `pdftotext -table` 提取（与执行者的 pdfplumber 不同），151/151 一致、0 错误，明细见 `docs/evidence/V1.17_claude_check.csv`，脚本见 `V1.17_claude_check_script.py.txt`；整库交叉核对：资产负债表 740 格、利润表 169 格（含 Q4 倒算）全部一致。另发现 5 个系统性问题，见 questions.md Q7。等第二个模型独立核对后由用户确认 | — |

> 附注：本阶段共 7 个 pytest 文件级测试通过（13 个用例，含 6 个新 ETL 纯函数测试）。V1.9/V1.14 的 BLOCKED 不阻塞 P2 开工（缺口科目不影响语义层与计算库的任何接口），但 P1 tag 待 Q4–Q6 人工确认后补打。

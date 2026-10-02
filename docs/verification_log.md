# 验证日志

> 记录格式见 verify.md 0.2。每个检查项一行，证据必须是真实执行的命令和输出摘要。

## 阶段完成清单

```
P0: [x] 全局 G1–G8  [x] V0.1–V0.9（G3 按验证文档 P0 跳过）   tag: phase-P0-done
```

> 2026-10-02 最新状态：V1.17 已由用户人工终确认，第二轮 AI 抽检已按用户指示取消；Q8/Q9/Q10 决策关闭，G4 复核通过、V4.2 通过。V4.15/V4.47 本次批阅不通过，V4.44 仍待修复；P4 不关闭。各阶段全局验收和 tag 不随这些单项裁定自动通过。下文历史日志保留，最新批阅见下表。

## 2026-10-02 用户授权批阅（最新状态）

| 检查 ID | 状态 | 证据 |
|---|---|---|
| Q8 / G4 | CLOSED / PASS | 选 B，精确路径+行哈希例外；只读复核 `.env` 未跟踪、实际密钥命中 0、模式命中 11 个且均已审阅登记；见 `docs/evidence/G4_example_allowlist.json`、`review_20261002.json` |
| Q9 / V4.2 | CLOSED / PASS | 选 A，将错误负向用例 6150 改为 6250，0.5% 容差不变；已有双向测试覆盖；本轮 123 测试通过 |
| Q10 / V4.44 | CLOSED / FAIL-待修复 | 选 B，授权现在修 prompt/skill 和相关已确认实现缺陷，重跑同一长任务；不换题、不改通过标准 |
| V4.15 | REVIEWED-FAIL | no_anomaly 与 seasonal 条件冲突、步骤结论优先级不明确；具体批注与修复要求见 `docs/evidence/review_20261002.md` |
| V4.47 | REVIEWED-FAIL | 三样本 final.verified 为 false/false/true；SQL 和正文截断、计算输入 SQL 缺失、勾稽差额无结构化数字证据；HP 1473 误报已定位为运行时重复加总 |
| V1.17 | PASS-USER-CONFIRMED | 用户直接确认已看过并取消第二轮抽检；未宣称第二模型已完成；既有 151/151 Claude 核对记录保留 |
| G1（本次回归） | PASS | 初次沙箱临时目录权限导致 6 errors；获准沙箱外重跑 `.venv/Scripts/python -m pytest -q` → 123 passed in 19.71s，无阈值/测试改动 |

批阅证据与 GLM 下一步：[review_20261002.md](evidence/review_20261002.md)。本轮未改业务实现、未打阶段 tag；G5 不自判通过，本次批阅修改尚待提交。

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

## P1 批阅修复（2026-10-01，Q4–Q6 落地 + Q7 五项修复）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| Q7-1 (V1.18) | PASS | fiscal_year 统一 4 位数：Lenovo 2018–2027、HP 2017–2026、Dell 2017–2027（`SELECT MIN/MAX(fiscal_year) FROM periods GROUP BY 1`） | （本次） |
| Q7-2 (V1.19) | PASS | days = 本期期末 − 上期期末：`DATEDIFF(LAG(period_end), period_end) <> days` 违例 **0**；窗口第一期用窗口外真实期末日核对：Dell FY2017Q4 = **98**、HP FY2017Q1 = **92**、Lenovo FY2018Q1 = **91** | （本次） |
| Q7-3 (V1.20) | PASS | `etl/check_continuity.py` → data/continuity_report.md：①映射来源变化 17 处，全部有说明（标签演化/回退机制/修复，0 待说明；Lenovo FY23 应付口径已统一——應付票據计入 AP——且 filing_notes 已记录）；②环比超历年同季度 3σ 跳变 = **0** | （本次） |
| Q7-4 | PASS | net_income 补齐：根因为盈亏方向导致的标签排列变体（期內溢利/期內(虧損)/溢利/期內溢利/(虧損)/年內(虧損)/溢利）+ "內/内" 字形变体 + 损益表跨页；现 3m 28 期（Q1–Q3 全齐）+ 9m 9 + 12m 9，Q4 全部由 q4_backout 生成；FY19Q4 operating_income 的旧值系按 0 补齐的错误倒算产物，已随新倒算逻辑（缺输入即跳过）消灭 | （本次） |
| Q4-1 | PASS | Dell FY2024Q2/Q3 AR/AP 人工补录（data/manual/dell_patch.csv，4 条，source=manual_edgar，accn 见 questions.md）；补录后残差假异常消失（other_current_liabilities 不再出现 594→21,221 跳变，见 continuity_report） | （本次） |
| Q4-2 | PASS | Dell FY2024Q4 AR/AP available_date 修正为 2024-03-25（10-K accn=0001571996-24-000036，数值不变，data/manual/available_date_patch.csv）；Dell FY2017Q4 revenue/cogs 按 9M-YTD 倒算补录（20,074 / 15,543，data/manual/dell_fy2017q4_is.csv，derivation=q4_backout） | （本次） |
| Q6-1 | PASS | HP total_liabilities 改为分项加总（tcl + ltd + other_ncl，derivation=sum_of_parts）；实测 HP "Other liabilities" 行已含非流动递延收入/递延税/退休福利，故 dr_nc 为子项不入和；A=L+E 检查对 HP 恢复真实性（分项缺失即失败） | （本次） |
| Q6-3 | PASS | facts 表新增 derivation 字段（附录 B 已改）：分布 reported 2280 / sum_of_parts 350 / residual 273 / q4_backout 83 / rev_minus_cogs 78 / manual_patch 4；is_derived = (derivation IS NOT NULL)；残差 >5% 总资产者已在 identity_report 按报送标签拆解（V1.22） | （本次） |
| Q6 补充 | PASS | 新增标准科目 financing_receivables（仅流动）与 deferred_revenue_noncurrent；Dell 的 NotesAndLoansReceivableNetCurrent / ContractWithCustomerLiabilityNoncurrent 及 HP 的对应科目（606 前后回退）已映射；未报送的不填 0 | （本次） |
| V1.9 | PASS | 核心科目缺失 0（6 条缺口已按 Q4 答复补齐） | （本次） |
| V1.10 | PASS | ①营收 5,000–100,000 ✓；②相邻季度环比 ±50% 违例 0（check_continuity 第③节） | （本次） |
| V1.11 | PASS | q4_backout 仅出现在利润表 Q4（83 = Lenovo 45 + HP 18 + Dell 18 + 人工 2）；BS 无 q4_backout；is_derived=(derivation IS NOT NULL) 全表成立 | （本次） |
| V1.14 | PASS | 原 20 条"超 150 天"重分类：全部为比较期补报（保守方向）；`etl/check_disclosure_dates.py`（V1.21）→ docs/evidence/V1.21_report.md：与自身申报日相等 1160 条、晚 32 条（逐期明细+原因）、**早 0 条（无泄露）** | （本次） |
| V1.20 | PASS | 见 Q7-3 行 | （本次） |
| V1.21 | PASS | 见 V1.14 行：early=0；late=32 逐条原因（companyfacts 比较期补报 / 人工修正） | （本次） |
| V1.22 | PASS | identity_report 新增残差拆解段：>5% 残差均按报送标签列出构成（子集和，容差 1%），如 Dell FY2024Q4 other_noncurrent_liabilities = OtherLiabilitiesNoncurrent(3,065) | （本次） |
| V1.17 | HUMAN-进行中 | 人工（Claude）独立核对 151/151 通过 + 整库交叉核对 909 格一致（docs/evidence/V1.17_claude_check.csv）；第二模型独立抽检进行中，完成后由用户确认 | — |

> 回归：pytest 14 用例通过（新增 days_since_prev_pe、q4_backout 测试）；G 系列于阶段关闭时统一执行。

## P2 语义层 + 计算库（2026-10-01）

> 交接说明：P2 由 GLM-5.3-Flash 完成大部分（synth_detail、metrics.yaml、compiler、catalog、calc、forecast 占位、两套测试），收尾与 bug 修复由本执行者完成。修复三处：①compiler 对两位/四位财年混用的解析（+2000 只用于两位年份）；②**compiler 真实缺陷**——请求区间只含单期时，LAG 窗口内只有过滤后的行，derived 指标（dio/dso/dpo/ccc）取不到上期值返回 NULL → period_from 过滤下沉到窗口之后（外层 WHERE，别名 t）；③synth_detail 份额漂移收敛——原实现先 clip 步长再归一化，归一化把单格净变化放大到 3.04pp（V2.2 违例）→ 改为归一化后复核、步长对折迭代直到净变化 ≤3pp。

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V2.1 | PASS | `Q "SELECT … SUM(d.value)-MAX(f.value) diff … HAVING ABS(diff)>1e-6"` → **0 行**（3 科目 × 3 公司 × 全部期 × 9 格，最后一格吸收舍入） | （本次） |
| V2.2 | PASS | 相邻季度单格份额变化：3024 对，最大 **2.9245pp** < 3pp（修复前 3.0387pp 违例） | （本次） |
| V2.3 | PASS | `etl/synth_detail.py` 连跑两次，facts_detail 按主键排序后 SHA-256 前 16 位均为 `9e99f531b31a4e17`（3105 行） | （本次） |
| V2.4 | PASS | `SELECT COUNT(*) FROM facts_detail WHERE synthetic IS NOT TRUE` → **0**（总行 3105，全部 TRUE） | （本次） |
| V2.5 | PASS | `tests/test_semantic.py::test_v2_5_metrics_completeness`：附录 A 全部 30 个 base + 7 个 derived，均有 unit 与 dims | （本次） |
| V2.6 | PASS | `test_v2_6_compiler_error_suggests_nearby`：`metrics=["inventroy"]` 抛错信息含 `inventory`（difflib） | （本次） |
| V2.7 | PASS | `test_v2_7_compiler_single_select`：10 个请求（含 detail、lag、last_n、日历/财年区间）sqlglot 解析均为单条 SELECT | （本次） |
| V2.8 | PASS | `test_v2_8_semantic_matches_direct_sql`：seed=42 随机 20 个 (公司,期间,base 指标)，语义层 vs 直接查 facts 差 <1e-9 | （本次） |
| V2.9 | PASS | `test_v2_9_derived_matches_hand_calc`：Lenovo FY2024Q3 / HP FY2023Q4 / Dell FY2025Q2，手算 DIO/DSO/毛利率与编译 SQL 差 <1e-6。修此测试时发现并修复 compiler 的 LAG 单期缺陷（见交接说明②）；手算上期值须经 periods 关联取 period_end（facts 无该列） | （本次） |
| V2.10 | PASS | `test_v2_10_calendar_alignment`：自然季度 2024Q3 存货 → 恰好 3 行、每公司 1 行 | （本次） |
| V2.11 | PASS | `test_v2_11_contribution_closes`：随机 100 组（6 期 × 4 分项），各分项贡献之和 = 总变化，误差 <1e-9；contribution 剔除首期（无上期不产生贡献），残差按 \|Δpart\| 比例分摊 | （本次） |
| V2.12 | PASS | `grep -rnE "duckdb\|open\(\|requests\|httpx\|read_csv" fincalc/` → 无输出（forecast.py 为 P6 占位，仅 raise NotImplementedError） | （本次） |
| V2.13 | PASS | `test_v2_13_seasonal_baseline`：5 组历史 Q2 环比（+4%~+6%）+ 当期 +20% → z>2、分位=100%；当前值不进基线样本 | （本次） |
| P1 回归 | PASS | `check_identities` 115 期 0 失败；`check_gaps` 三公司 0 gaps；`check_continuity` 映射变化 17 处（均有说明）、跳变 0、营收跳变 0 | （本次） |

## P2 全局检查（2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| G1 | PASS | `pytest -q` → **28 passed**（全离线） | （本次） |
| G2 | PASS | `pytest --collect-only` → 28 tests ≥ 基线 7 | （本次） |
| G3 | PASS | `git diff phase-P0-done..HEAD -- tests \| grep -nE "^\+.*(skip\|xfail)"` → 无输出 | （本次） |
| G4 | BLOCKED-待人工 | ①.env 未被跟踪 ✓；②.env 实际密钥值 `git grep -nF` → 无输出 ✓；③通用模式在**代码区无输出**，但命中 docs/ 下 3 处历史证据文本（`sk-SELFTEST…` 假 key、引述 `api_key="test-key"`）——均非真实密钥。检查项不能改、证据文件不宜改写 → 记 questions.md **Q8** 等人工裁定 | — |
| G5 | PASS | 提交后 `git status --porcelain` 无输出；提交均 `[P<n>.<m>] …` 格式 | （本次） |
| G6 | PASS | `grep -rnE "eval/datasets\|scenarios/.*\.json\|gold" agent/ semantic/ fincalc/` → 无输出 | （本次） |
| G7 | PASS | `grep -rnE "(^\|[^_.[:alnum:]])(eval\|exec)\(\|subprocess\|os\.system" agent/ semantic/ fincalc/` → 无输出 | （本次） |
| G8 | 待 Q8 | 与本阶段相关的新问题仅 Q8（G4③ 误报范围），已登记等人工答复 | — |


## P3 评测框架 + L1/L2 题目 + v0 基线（2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V3.1 | PASS | `tests/test_eval.py::test_v3_1_*`：pydantic 逐行校验 l1.jsonl/l2.jsonl；L1=4、L2=6、id 唯一 | （本次） |
| V3.2 | PASS | `test_v3_2`：重建数据集并重新执行每题 gold_sql / 重走 fincalc 路径，10/10 与 gold.value 一致 | （本次） |
| V3.3 | PASS | L1 含自然季度写法（l1_0002 HP calendar 2024Q3）与"亿美元"（l1_0003）；L2 覆盖 DIO/DSO 趋势、毛利率、环比、三家对比（docs/evidence/V3.12.md） | （本次） |
| V3.4 | PASS | mock agent 返回 gold×1.0 跑 runner：L1、L2 准确率均 100% | （本次） |
| V3.5 | PASS | mock 返回 gold×1.01：L1、L2 均 0%（超 0.5% 容差） | （本次） |
| V3.6 | PASS | mock 返回 gold×1.004：判为正确（容差内） | （本次） |
| V3.7 | PASS | live 运行 `scripts/live/V3.7_run_v0.py` → run `20261001-183059`：30 个 trace + summary.json；报告 L1 1.00±0.00、L2 0.78±0.10，含步数/工具调用/SQL 报错/token/延迟/成本（docs/evidence/V3.7_output.txt）。踩坑记录：①首跑 181956 单个 429 异常经线程池炸掉全局 → runner 增加 trial 级 try/except；②二跑 182502 免费档按分钟窗口的 429 配额墙致 22 error trial → 增加 `_call_with_rate_retry`（429 整 trial 退避重做，最多 3 次），并把负载压到 8 RPM、并发 2；三跑全 30 trial 完成、0 error | （本次） |
| V3.8 | PASS | 抽查 `runs/20261001-183059/l2_0001_1.jsonl`：meta + 每步 assistant 行（消息、tool_calls 参数、latency_ms、usage token）+ tool_result 行 + result 行 | （本次） |
| V3.9 | PASS | `python -m eval.compare 20261001-183059 20261001-183059`：全部指标"不显著"（eval/reports/compare_20261001-183059_vs_20261001-183059.md）；离线 `test_v3_9` 同过 | （本次） |
| V3.10 | PASS | `test_v3_10`：`[t["function"]["name"] for t in v0.TOOLS] == ["run_sql","final_answer"]`，无其他工具 | （本次） |
| V3.11 | PASS | leaderboard v0 行：L1 1.00±0.00、L2 0.78±0.10，注明 run_id=20261001-183059、commit=71933af | （本次） |
| V3.12 | PASS | 人工批阅：用户 2026-10-01 对 10 题审阅答复"通过"（材料 docs/evidence/V3.12.md） | — |

**v0 基线 bad case（P4 改进输入，均为真实失败样本，未做任何针对性特判）**
1. l2_0002（t1、t3）：题目未钉死 DSO 口径，v0 用"期末应收÷单季收入"，gold 用编译器平均余额口径 → 值与同比符号都算偏（+0.35/+0.90 天 vs gold −2.90 天）。启示：口径应由 skills/fincalc 统一，或 agent 应 clarify。
2. l2_0003 t2：v0 取数全对（8265.5/11081×92=68.63），但最后一步 LLM 心算写成 72.9。印证 plan"所有数值由代码算、LLM 不做算术"的原则，P4 需上 fincalc 工具。
3. l2_0001 t2：CCC 21.9 vs gold 21.42，同样是末步心算漂移（0.5% 容差外）。

**回归**：pytest 40 passed（含 P0–P2 全部 28 例）；P2 语义层/计算库未改动。

## P3 全局检查（2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| G1 | PASS | `pytest -q` → **40 passed**（全离线，无联网用例） | （本次） |
| G2 | PASS | `pytest --collect-only` → 40 tests ≥ 上一阶段 28 | （本次） |
| G3 | PASS | `git diff phase-P0-done..HEAD -- tests \| grep -nE "^\+.*(skip\|xfail)"` → 无输出；新增 test_eval.py 亦无 skip/xfail | （本次） |
| G4 | BLOCKED-待人工 | ①.env 未被跟踪 ✓；②.env 密钥值 `git grep -nF` → 无输出 ✓；③模式扫描：agent/eval/scripts/tests/config.yaml 全部无输出，新增证据文件（V3.7_output、reports）无密钥形态；仅历史 docs 证据 3 处假 key 命中（见 Q8，等人工裁定） | — |
| G5 | PASS | 提交后 `git status --porcelain` 无输出；提交 `[P3.5] …` 格式 | （本次） |
| G6 | PASS | `grep -rnE "eval/datasets\|scenarios/.*\.json\|gold" agent/ semantic/ fincalc/` → 无输出（gold 只存在于 eval/ 内） | （本次） |
| G7 | PASS | `grep -rnE "(^\|[^_.[:alnum:]])(eval\|exec)\(\|subprocess\|os\.system" agent/ semantic/ fincalc/` → 无输出（runner 的 subprocess 仅调 git rev-parse，且在 eval/，不在禁扫目录） | （本次） |
| G8 | 待人工 | 未决：Q8（G4③ 范围）、V1.17 第二模型抽检（P1 关闭项）；均已在 questions.md/证据中登记。V3.12 已人工通过 | — |

## P4 Agent 核心（V4.1–V4.12，2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V4.1 | PASS | `tests/test_tools.py::test_v4_1_*`：相同 (tool,args,data_version) 两次 put 返回同一 rid，monkeypatch `query_df` 计数证明第二次没有再查库；不同 args 产生新 rid | c4f472a |
| V4.2 | 待 Q9 | 四用例都已落成测试：6123.4(usd_mn)↔61.234(usd_100mn) 找到、0.182(ratio)↔18.2(pct) 找到、不存在 rid 返回错误不抛异常。**检查项第三例本身自相矛盾**（6150 vs 6123.4 差 0.434%，在 0.5% 容差内，不可能"找不到"）→ 已登记 questions.md Q9；测试用 6250（差 2.07%）验证"超容差→找不到并反馈最近值"规则，并如实断言 6150 可找到（不修改检查项） | c4f472a |
| V4.3 | PASS | 首行匹配 `^\[r\d+\] \w+\(.*\)$`；60 行结果渲染只显示 50 行并出现 `recall(` 提示；小结果无提示 | c4f472a |
| V4.4 | PASS | `test_v4_4`：遍历 `all_tool_schemas()`（11 个数据/只读 + todo_write + final_answer = 13）每个都有 pydantic 参数模型、描述含"何时使用"；与 plan 6.4 的 P4 范围一致（load_skill 在 P4.3 加入，forecast 在 P6） | c4f472a +（本次） |
| V4.5 | PASS | `test_v4_5`：遍历 `all_args_models()` 全部字段名，不存在 db/db_path/as_of | c4f472a +（本次） |
| V4.6 | PASS | calc 拒绝 `__import__('os')`、`open('x')`、`(lambda:1)()`、`1/0`、`r99.x[...]`、`r1.__class__[...]`，全部以"工具错误"文本返回、不抛异常 | c4f472a |
| V4.7 | PASS | `r3.inventory[Lenovo,FY24Q2]/r3.inventory[Lenovo,FY23Q2]-1` 与手算一致并产生新 rid | c4f472a |
| V4.8 | PASS | run_sql 拒绝 INSERT / DROP / ATTACH / COPY / PRAGMA / 多语句 `SELECT 1; DROP…` | c4f472a |
| V4.9 | PASS | 无 LIMIT 的 SELECT 自动加 LIMIT，最多返回 200 行 | c4f472a |
| V4.10 | PASS | 绕过护栏直接用工具内部只读连接 `CREATE TABLE t(x INT)` → duckdb 报错 | c4f472a |
| V4.11 | PASS | `tests/test_loop.py`（mock LLM 按脚本返回）六场景全过：a 查数→final_answer，trace 含 usage/result_id/latency；b 不调工具先提醒 1 次、再犯按预算 refuse（视图中 NUDGE 恰好 1 条）；c 工具报错文本进上下文循环继续；d 步数/token 预算耗尽 → refuse，claims 空、正文无数字；e 核验连败 3 次第 3 次返回并标注"未通过验证"（stop_reject attempt=[1,2]）；f 未调过数据工具而答案含数字 → pre_tool 拦截，取数后同样答案放行 | （本次） |
| V4.12 | PASS | 一条 assistant 消息含 3 个只读工具调用：`ThreadPoolExecutor` 并发执行（`ResultStore.put` 加锁保证 rid 分配原子），3 个 tool_call_id 与结果一一对应、rid 互不相同 | （本次） |

| V4.13 | PASS | `tests/test_skills.py::test_v4_13`：system prompt 含全部 3 个 skill 的 `- name: description` 行；逐行检查 3 个 SKILL.md 正文（≥12 字符的行）没有任何一句出现在 prompt | （本次） |
| V4.14 | PASS | `test_v4_14_*`：`load_skill("attribution")` 返回正文与 SKILL.md 去掉 frontmatter 后逐字一致（直调 + 经主循环两条路径都测）；不存在的 skill（forecast）返回 KeyError 错误文本并列出可用 3 个 | （本次） |
| V4.15 | PASS(自动)+待人工 | 关键词测试：attribution 正文含 7 步骤全部要素（variance/seasonal_check/peer_compare/对手科目/get_filing_notes/contribution/根因标签枚举）、"必须回答 `no_anomaly`" 规则、6 个标签、答案 JSON 格式、"可检验假设"软先验小节。表述是否清楚需 `HUMAN` 批阅（随 P4 关闭材料提交） | （本次） |
| V4.16 | PASS | `test_v4_16`：不同 as_of、不同问题、不同 db（另一文件）构建的 system prompt sha256 一致、逐字节相同；动态内容（as_of）只出现在首条 user 消息（test_loop 的 as_of 测试） | （本次） |

| V4.17 | PASS | `tests/test_verifier.py`（真实 verifier 直接调用 + 主循环集成）六用例全过：①数字与引用都对 → 通过；②claim 数值 v×1.5 → 不通过且反馈给出 r1 最接近值；③正文多出一个 9876543.21 无 claim → 不通过并指名该数字；④正文只有 "FY24Q2"、"2024 年"、"第 3 点"、"r1/r2" → 不误报；⑤引用不存在的 r42 → 不通过；⑥构造坏数据库（A=100, L+E=90）→ 提示"勾稽违反 A-L-E"。另：attribution 加载后答案缺根因标签 → 不通过（补 seasonal 后通过） | （本次） |
| V4.18 | PASS | `test_v4_18_feedback_is_specific`：反馈含 "claim 1"（哪个 claim）、"r1"（哪个 rid）、"期望 {claim 值}" 与 "最接近的是 {store 候选值}"（期望值与实际值）、"容差 0.5%"；主循环集成测试确认反馈原文作为工具结果送回模型，模型据此第二次提交通过核验 | （本次） |

**回归**：`pytest -q` → **96 passed**（P4.2 的 mock 测试改为默认走真实 verifier 后仍全过；V4.4/V4.5 遍历全部 14 个工具）。

## P4.5a 证据账本（V4.32–V4.34，2026-10-01）

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V4.32 | PASS | `tests/test_ledger.py::test_v4_32_ledger_is_code_generated`：mock LLM 跑完整归因流程（load_skill→variance→seasonal_check→peer_compare→get_filing_notes→final，6 次调用全过护栏）；`ledger.render` 调用前后 `len(ctx.llm.views)` 不变（生成账本 LLM 调用=0），两次渲染逐字符相同（纯函数） | （本次） |
| V4.33 | PASS | `test_v4_33_sections_and_verify`：真实 DB 播种 10 个工具结果后账本含"口径/已取得的数据/假设检验记录/失败记录/计划"五节且顺序正确；每条假设检验记录都带 rid；`verify_ledger(text, store)==[]`（数据/假设两节的每个带符号数字都能在其 rid 中找到，含 +%、千分位、负值）；`test_verify_ledger_catches_fabricated_numbers`：插入编造行 987654.3% 与无 rid 行均被抓到 | （本次） |
| V4.34 | PASS | `test_v4_34_every_analysis_tool_has_entry`：`LEDGER_BUILDERS` 键集合 == DATA_TOOLS ∪ HYPOTHESIS_TOOLS（= 全部 STORED_TOOLS + recall）；6 个分析工具逐个在真实 DB 执行，`to_ledger_entry` 输出非空且含该结果 rid | （本次） |

顺带修复：`verify_ledger` 原先按完整标题精确匹配，"假设检验记录（…）"节被整节跳过（核验形同虚设）→ 现按 `（` 前缀归一；`hooks._NUMBER_RE` 原先只允许一组 `[.,]\d+`，`1,234.5` 会被拆成 `1,234`+`5` 两个 token（verifier/账本都会误报）→ 改为 `(?:[.,]\d+)*`。账本大数改用千分位定点格式（避免 %g 的科学计数法无法核验）。

**回归**：`pytest -q` → **101 passed**；G6/G7 grep 无输出。

## P4.5b/c 压缩流水线 L1–L4（V4.19–V4.31、V4.35，2026-10-01）

实现：`agent/context.py` 重写为 L2 轮次折叠 → L3 结果存根化 → L4 账本压缩的投影管线；
状态冻结进 `CompState`（攒批触发、不回退，前缀友好）；阈值全部来自 `config.yaml` 的 context 段；
主循环在 final_answer 被接受/带标注返回时写入 `答案已提交（…）` 标记（L2 的折叠依据），
`on_context_overflow` 提供 reactive 基础版（强制全量存根 + 投影到最近 1 组，超限抛 ContextTooLong；完整 PTL/熔断在 P4.5d）。

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V4.19 | PASS | `tests/test_context.py::test_v4_19_24_invariants_over_200_random_histories`：固定种子 200 条随机历史 × 4 组配置（L2only/L3/L4/all），`check_invariants` 逐视图断言：每个 tool_call 有结果、无孤立 tool、无连续 assistant。实测触发统计：L2 折叠 138 条、L3 存根 18 条、L4 压缩 51 条、all 组 200 条全触发（脚本输出留档） | （本次） |
| V4.20 | PASS | 同上测试：每条历史 × 配置在 build_view 前后对原始历史做 sha256 比对（800 组合全部不变）；投影视图只生成新 dict，原始 messages 列表不被修改（主循环 `ctx.messages` 与视图分离） | （本次） |
| V4.21 | PASS | `check_invariants`：每个视图断言 `view[0] == history[0]`（system 消息逐字节相同，L2/L3/L4 均只重排其余部分） | （本次） |
| V4.22 | PASS | `check_invariants`：历史中出现过的每个 `[rN]` 头 rid 压缩后仍 `store.get` 可取；存根行含 `原始数据: recall("rN")` 指引；`test_v4_28/test_v4_35` 在触发后复检 | （本次） |
| V4.23 | PASS | `test_v4_23_compact_events_complete`：触发后 trace 有 `{type:"compact", level, tokens_before, tokens_after, rids}` 事件且 tokens_after ≤ before；L1 在 `render()` 写入时强制执行（结果头行本身即"引用+预览"，超预算行带 recall offset 提示，见 V4.3/V4.25） | （本次） |
| V4.24 | PASS | 同 V4.19 测试：同一 ctx 对同一历史连续两次 `build_view` 输出相等（800 组合）；`test_v4_24_prefix_stable_when_appending`：历史末尾追加一轮后重建，若压缩状态未变（stubbed/cut/fold/errors 四项），新视图前缀 = 旧视图逐条相等 | （本次） |
| V4.25 | PASS | `test_v4_25_l1_result_budget_300_rows`：300 行结果渲染数据行 ≤50（cfg result_max_rows）且出现 `完整结果可用 recall("r1", offset=…)` 提示；store 内仍是完整 300 行 | （本次） |
| V4.26 | PASS | `test_v4_26_l2_folds_only_verified_turns`：两个完成轮（一个 SUBMIT_OK、一个 SUBMIT_BAD）→ 只折叠通过轮；折叠保留用户问题原文 + 答案正文（111）+ claims 引用 rid（r1）；未通过轮的 assistant/tool 原样在视图 | （本次） |
| V4.27 | PASS | `test_v4_27_l3_whitelist_never_stubs_specials`：触发存根化后 load_skill 结果、todo_write 结果、护栏反馈（"答案未通过核验…"）、user 提醒消息逐字节不变；白名单（query_metric 等 11 个）结果被存根化 | （本次） |
| V4.28 | PASS | `test_v4_28_l3_keep_rules_and_errors`：6 个结果中最近 3 个（c3–c5）保持原样；最近 assistant 引用过 r2 → c1 不存根；实际存根集合恰为 {c0, c2}；两条报错中较早一条压成单行 `已失败: …`，最近一条报错保留全文供自我纠错 | （本次） |
| V4.29 | PASS | `test_v4_29_l3_no_trigger_below_clear_at_least`：可释放 < clear_at_least(10000) 时 `comp.stubbed == frozenset()`，视图无存根、无失败行 | （本次） |
| V4.30 | PASS | `test_v4_30_stub_format_and_digest_stable`：存根匹配 plan 6.6.3 正则 `[rN 已压缩] tool(args)\n摘要: …\n原始数据: recall("rN")`；同一结果两次生成存根相同；digest ≤200 字符 | （本次） |
| V4.31 | PASS | `test_v4_31_digest_numbers_traceable`：真实 DB 播种全部 10 个存储工具后，每个结果 digest 的全部带符号数字 token 都能在其 rid 中 `find_value` 命中 | （本次） |
| V4.35 | PASS | `test_v4_35_l4_view_structure`：L4 触发后视图 = system + 账本（与触发时刻冻结的 `ledger_text`、`ledger.render` 双重比对相等）+ 已加载 skills 块 + 用户原始问题 + 最近 2 轮，消息条数精确 == 4 + 保留轮总条数（"不多不少"），五不变量复检通过 | （本次） |

顺带：`to_ledger_entry` 加异常兜底（渲染崩不得拖垮压缩管线，随机历史里合成列形会走 passthrough）；
`hooks._NUMBER_RE` 加 `(?<![A-Za-z0-9.])` 前置断言——"p50/P95" 这类标识符内数字不再被当成主张值（V4.31 测试暴露）。
主循环提交标记测试：`test_loop_appends_submit_markers`（通过 → `答案已提交（通过护栏核验）`；注入 verifier 连败到上限 → `…（⚠ 未通过验证）`）。

**回归**：`pytest -q` → **114 passed**；G6/G7 grep 无输出。

## P4.5d L5 全量摘要 + reactive/PTL/熔断（V4.36–V4.43，2026-10-01）

实现：`agent/context.py` 增加 L5（领域版 9 段模板，第 2/3/6/7 段由代码从证据账本与历史原文填写，
LLM 只写第 1/4/5/8/9 段推理；`<analysis>` 由代码删除；摘要数字逐个核验——必须带 rid 且能在
Result Store 中找到，不过则重生成一次，仍不过退回 L4 视图并置 `summary_gave_up`，两种结局都写
trace 事件；摘要冻结进 `CompState`，重建不再调 LLM、视图逐字节不变）。
`on_context_overflow` 升级为完整 PTL（每次溢出比上一次多丢弃一个最早的轮次组，
`ptl_drop` 累积进 L4 投影）；主循环对压缩失败计数（`comp.failures`，成功归零），
连续 `compact_fail_limit` 次 → 熔断 → `status=refuse` 并说明原因（V4.42）。
`agent/llm.py` 把 API 的上下文超长 4xx（BadRequestError 且报文命中长度类关键词）转抛
`ContextTooLong`，其余参数错误维持立即抛出。config 新增 `l5_enabled` /
`l5_keep_last_turns` / `compact_fail_limit`（阈值全部来自配置）。

| 检查 ID | 状态 | 证据 | commit |
|---|---|---|---|
| V4.36 | PASS | `tests/test_context.py::test_v4_36_37_39_l5_domain_template`：mock 摘要在第 2/3/6/7 段写入 POISON-2/3/6/7 错误内容 → 最终视图序列化后不含任何 POISON 串；摘要第 2 段实为账本口径行（"期间口径: fiscal"）、第 3 段为账本 rid 索引（"r1 query_metric"）、第 5 段 = 账本记录 + "推理补充:" | （本次） |
| V4.37 | PASS | 同测试：mock 返回 `<analysis>…ANALYSIS-MARKER-甲…</analysis><summary>…</summary>`，压缩后视图不含 ANALYSIS-MARKER-甲 | （本次） |
| V4.38 | PASS | `test_v4_38_l5_summary_verification`：摘要含编造数字 987.65(r2) → 两次都不过时恰好调用摘要器 2 次、写 2 条 `level=L5, applied=false` trace 事件、视图退回 L4 结构（无摘要消息、987.65 不在视图）；第一次坏/第二次好 → 采用摘要（1 条 applied=false + 1 条正常 L5 compact 事件） | （本次） |
| V4.39 | PASS | 同 V4.36 测试：历史中每一条 user 消息原文（含 3 条追问）都逐字出现在摘要第 6 段 | （本次） |
| V4.40 | PASS | `test_v4_40_reactive_retry_once_per_turn`：`FirstOverflowLLM` 第一次 chat 抛 ContextTooLong → 主循环本轮只做 1 次兜底重发（成功请求共 2 次视图记录），run 正常走完并过护栏；`overflow_retries==1`、`failures==0`、无 compact_fail 事件 | （本次） |
| V4.41 | PASS | `test_v4_41_ptl_dumps_oldest_groups_each_retry`：4 组历史，第 1 次溢出投影到最近 1 组（保留含 c3 的组），第 2 次溢出再丢最早组（0 组、est_tokens 严格下降）；`test_reactive_overflow_degrades_then_gives_up` 复核重试上限 3 次后抛 ContextTooLong | （本次） |
| V4.42 | PASS | `test_v4_42_circuit_breaker_refuses`：`AlwaysTooLongLLM` 下 run 以 `status=refuse` 结束，拒答原因含"熔断"，compact_fail 事件数 == compact_fail_limit（config 读取），API 调用恰为 2×limit（每轮原始+1 次兜底），熔断后不再尝试 | （本次） |
| V4.43 | PASS | `re.findall(r'(?<![\w.])\d{4,}', agent/context.py)` → `[]`：L5/PTL 新代码不含任何写死的 4 位以上数值阈值 | （本次） |

**回归**：`pytest -q` → **119 passed**；G6/G7 grep 无输出。V4.44（真实 LLM 16K 窗口冒烟）见下方 live 记录。

### V4.44 live 冒烟记录（16K 窗口 / 长任务归因题 / agnes-3.0-flash，2026-10-01，共 4 次尝试）

| 次 | 冒烟配置 | 结果 |
|---|---|---|
| 1 | steps 20, keep 2 | refuse（步数耗尽）；L3×4/L4×4，无 API 报错、无熔断 |
| 2 | steps 30, keep 2 | 30 步 refuse；L3×6/L4×17/L5×2；一次 final_answer 参数错误（claims.5.value 非数值）；L4 投影后模型反复复核 r2/r3 烧预算 |
| 3 | steps 40, keep 3, 打回 2 | 9 步收敛→answered；打回耗尽 verified=False（`≈0%` 需精确 0 的 claim、z 写 0.09 vs 存储 0.0913 超 0.5% 相对容差）；无 API 报错 |
| 4 | 打回上限 4 | 38 步 answered；L3×7/L4×30/L5×2（L5 两次生成核验失败→按设计退回仅账本）；正文 30 个数字仅 7 条 claim，其中 2 条数值在任何结果中不存在（124.639 vs 最近 126.06；置信度 0.85 非数据数字）→护栏按设计拦截→verified=False |

**标准逐条判定**：①trace ≥1 次 L3：PASS（4 次均稳定）；②无 API 报错：PASS（4 次均成立）；③最终答案通过 verifier：FAIL（4 次）。③的根因是**模型 claim 纪律**（正文↔claims 不闭合、引用不存在的数），非压缩/循环缺陷；执行者不得改检查项与通过标准（verify 0.3），可调冒烟参数已试完。**V4.44 记 待 Q10**（docs/questions.md 四选一，含 P8 prompt 实验路径），不自行判 PASS。

证据：`docs/evidence/V4.44_output.txt` / `V4.44_trace.jsonl` / `V4.44_config_used.yaml` 为第 4 次运行；第 1–3 次证据被后续运行覆写，结论如上表。

## P4.6 v1 正式评测与阶段全局检查（V4.45–V4.47 + G1–G8，2026-10-01）

| 检查 ID | 状态 | 证据 |
|---|---|---|
| V4.45 | PASS（复跑） | 首轮 run 20261001-212749（commit 见 eval/reports/20261001-212749.md）：L1 0.92/L2 0.39，**低于 v0 → FAIL**；逐 trial 归因（eval/badcases.md）后两项 agent 侧修复：①working_capital 单期/非相邻期静默退化为期末口径 → 自动向前补一期（回归测试 test_working_capital_single_period_keeps_avg_basis）；②system prompt 铁律 3：claim.value 用题面单位。复跑 run 20261001-215744（commit 17a181c）：L1 0.92±0.14、L2 1.00±0.00，总 29/30=0.967 ≥ v0 26/30=0.867；compare 判 L2 +0.39 显著改善、L1 −0.08 显著回退（单 trial 单位漂移，badcases 已归类）。通过标准"总正确率不低于 v0 + 失败题全部归类有根因"满足 |
| V4.46 | PASS | eval/reports/leaderboard.md 含 v1 行（0.92/1.00，run 20261001-215744，commit 17a181c） |
| V4.47 | 待 HUMAN | `python scripts/export_v447_evidence.py 20261001-215744 42` 生成 docs/evidence/V4.47.md：固定种子抽 3 条 trace（l2_0003t3/l1_0002t1/l1_0001t1），每条 claim → rid → 工具参数 → 当前库重放 SQL 与结果行齐全。人工批阅点：数字→SQL→结论链条是否完整可信（执行者不自判） |
| V4.44 | **待 Q10** | 见上方 live 冒烟记录：L3/无 API 报错两条稳定 PASS，"通过 verifier"4 次 FAIL（模型 claim 纪律），四选一裁定等人工 |

**P4 全局检查（范围 fa8a322..HEAD）**：
| ID | 状态 | 记录 |
|---|---|---|
| G1 | PASS | 离线 `pytest -q` → **123 passed**（0 failed/0 error，无联网测试） |
| G2 | PASS | `pytest --collect-only` → 123 tests ≥ P3 关闭时 40 |
| G3 | PASS | `git diff fa8a322..HEAD -- tests \| grep skip/xfail` → 无输出 |
| G4 | BLOCKED-待人工（沿 Q8） | ①.env 未跟踪 ✓；②真实密钥值全库 git grep 无命中 ✓；③通用模式命中仅为 docs 历史证据/引述文本（Q8 已登记，无新增） |
| G5 | PASS | 本提交后 `git status --porcelain` 空；`git log fa8a322..HEAD --format=%s` 全部 `[P4.x]`/`[P4.x-y]` 前缀 |
| G6 | PASS | grep eval/datasets\|scenarios\|gold → 无输出（含 P4.6 新文件） |
| G7 | PASS | grep eval(/exec(/subprocess → 无输出 |
| G8 | **未关闭** | 本阶段未决问题：**Q9**（V4.2 用例数字矛盾）、**Q10**（V4.44 verifier 项）、V4.15/V4.47 HUMAN 批阅。P4 不打 phase-P4-done tag，等人工裁定 |

**P4 成果摘要**：v1 = 单循环 14 工具 + Result Store 证据链 + hooks/verifier 护栏 + L1–L5 压缩（含 reactive/PTL/熔断），L1+L2 总正确率 0.967（v0 0.867），失败根因全部归档 eval/badcases.md。

## P4 收尾（2026-10-02，六步授权清单执行记录，review_20261002.md）

| 步骤 | 状态 | 证据 |
|---|---|---|
| 步骤 1：ETL/运行时口径同步 | 完成 | fincalc 勾稽拆分为 check_identities_detail（结构化数值行）+ IDENTITY_TREES_HP（HP 的 TL 分项不含 dr_nc）；NaN 分项/NaN 合计显式违例（不得静默按 0 通过）；真实数据回归 test_hp_tl_no_drnc_double_count（HP 全部 39 期 TL 分项闭合，反向破坏 +1473 被抓）+ test_identity_nan_not_silent |
| 步骤 2：attribution skill 修订 | 完成 | 标签判定优先级（reclassification > mix_shift > industry_wide > company_specific > seasonal/no_anomaly）；第二步仅收集证据、不得提前结束；no_anomaly（|z|<2 且分位<95%）与 seasonal（超典型波动但符合历年规律）可判定条件；观察性分类表述纪律；七步骤与 no_anomaly 原则保留 |
| 步骤 3：claims 闭合与精度 | 完成 | system prompt 铁律重编号并新增 2/7 两条（claims 与正文闭合、数字精度足量）；final_answer 参数说明统一（一一对应、元数字不需要数据 claim）；未新增任何绕过核验的白名单 |
| 步骤 4：V4.47 导出增强 | 完成 | scripts/export_v447_evidence.py：完整正文/全部 claims/核验结果与打回记录/原 run rid 与模型所见结果（未截断）/完整 SQL 与完整输出行/派生公式/data_version；原 run 证据与当前库重放分区，重放 rid 加 replay: 前缀；working_capital / check_identities 工具保留底层取数 SQL（ResultStore 可查）；check_identities 工具输出结构化数值列（可被 claim 引用）；已用相同 seed=42 从 run 20261001-215744 重新生成 docs/evidence/V4.47.md |
| 步骤 5：重跑 | 部分完成 | ①V4.44 冒烟两次（run5/run6，独立证据文件 V4.44_output_20261002*.txt + 带时间戳 trace，未覆写失败记录）：均 40 步预算耗尽**诚实拒答**（无 API 报错、L3≥1 满足、无编造）。run5 根因：calc×13/run_sql×6（含幻觉表名）；prompt/skill 修补后 run6：数据收集已高效（query_metric×3/variance×6），但 **recall×29**——16K 窗口 + keep_last=3 下工作集放不下，模型全程在"取数→被压缩→recall"循环。此为冒烟参数结构性问题（keep_last/步数/窗口），超出 Q10-B（prompt/skill 修复）授权，**待人工裁定**；②L1/L2 三次评测重跑进行中 |
| 步骤 6：再交批阅 | 部分 | 本表即 V4.15 修订与 V4.47 材料的提交说明；V4.44 未达标、两项批阅未回前不打 phase-P4-done |

其他：verifier 勾稽反馈改用结构化数值（含 A-L-E 命名兼容、缺失清单）；result digest 排除布尔列；G4 allowlist 机制按 Q8-CLOSED 执行（G4_example_allowlist.json + 整行 SHA256）。回归：pytest 125 passed（新增 HP 口径回归/反向/NaN 三测试 + skills 新断言）。

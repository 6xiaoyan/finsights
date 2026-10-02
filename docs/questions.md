# 需要人工确认的问题

> **最新裁定（2026-10-02，用户授权 Codex 批阅）**：Q8 选 B，具体历史示例行按路径及 SHA256 豁免，全库扫描保留；Q9 选 A，负向用例改 6250、0.5% 容差不变；Q10 选 B，现在修复并重跑原冒烟，验收标准不变。三项决策均 CLOSED。V4.15/V4.47 已批阅为 FAIL-待修复，V4.44 仍 FAIL；不存在继续等待 Q8/Q9/Q10 决策的阻塞。V1.17 按用户已看过并取消第二轮抽检的明确答复，PASS-USER-CONFIRMED。详细理由及 GLM 执行顺序见 [授权批阅与开发交接](evidence/review_20261002.md)。以下原问题与历史状态保留，不代表最新状态。

> 按 plan 第 0 节规则 3：执行者遇到 ⚠️ 标记时，把问题和建议记录在这里，等人工确认后再继续。

---

## 已查证

### Agnes AI（Agens）模型信息（2026-10-01 查证，现行选型）

| 项 | 结论 | 来源 |
|---|---|---|
| 准确模型名 | `agnes-3.0-flash`（旗舰，限时免费）；`agnes-2.5-flash` 同为免费。社区与部分资料将厂商写作 "Agens"，官方拼写为 **Agnes AI**（新加坡 Sapiens AI 旗下），为同一服务商 | [模型页](https://wiki.agnes-ai.com/en/docs/agnes-30-flash.md)、[定价页](https://wiki.agnes-ai.com/en/docs/pricing.md) |
| 上下文长度 | 512K tokens，最大输出 65,536 tokens | [模型页](https://wiki.agnes-ai.com/en/docs/agnes-30-flash.md) |
| 价格 | 当前推广期 input / output / cached input 全部 $0；list 价 $0.05 / $0.15 / $0.005 每百万 token。**注意是限时推广**，评测报告需记录运行日期，推广终止时按 plan 变更流程决定是否切回 DeepSeek | [定价页](https://wiki.agnes-ai.com/en/docs/pricing.md) |
| 工具调用 | 支持 OpenAI 风格 `tools` / `tool_choice`；文档描述了多步工具编排。**未提及并行工具调用** → 与 DeepSeek 结论一致：执行采用串行，代码兼容多个 tool_calls | [模型页](https://wiki.agnes-ai.com/en/docs/agnes-30-flash.md) |
| 缓存 | cached input 单独计价（list 价的 10%），说明存在自动前缀缓存；但触发机制未文档化。plan 6.6.2 "修改历史会使前缀缓存失效、应攒批" 的假设保留，待 V4.44 冒烟测试用实际账单/命中率验证 | [定价页](https://wiki.agnes-ai.com/en/docs/pricing.md) |
| 限速 | 免费档约 **20 RPM**（第三方汇总，以控制台为准）。影响：eval runner 并发 4 × 每题 3 次可能触顶；`agent/llm.py` 的 429 指数退避重试可吸收，runner 设计长任务并发时需加客户端限速 | [Free-LLM-Collection](https://github.com/for-the-zero/Free-LLM-Collection) |
| 端点与密钥 | **base_url 用中国站 `https://api.agnes-ai.cn/v1`（2026-10-01 实测该 key 在此端点对话正常）**；国际站 `apihub.agnes-ai.com/v1` 对该 key 返回 401 "Invalid token"（此前约 20 次诊断请求排除了请求形式问题，见 verification_log 历史）。开发者文档未公开（集成在登录后的控制台），模型资料另见 Hugging Face `Agnes-AI/Agnes-3.0-Flash` | [wiki](https://wiki.agnes-ai.com/) |

### P0 能力探针结论（2026-10-01，两次运行一致，证据 `docs/evidence/P0_capability_probe_output.txt`）

`agnes-3.0-flash` 五项探针 3 通过 / 2 失败。**通过**：中文连通、工具调用（正确产出 get_weather + 北京参数）、company_specific 归因（JSON 字段与标签全对、证据引用得当）。**失败 ×2，且是同一种失败模式——倾向生成"公司特定"的自信叙事**：

1. **同行共振场景判错**：三家同季存货同向跳升（z 均 >4），模型盯着"联想 z=4.7 > 同行 4.1"的排名，无视"三家同向同量级"的模式，判成 `company_specific`（第二次运行甚至明确写"所有公司 Q3 z 均高，差异源于公司特定波动"——看到了事实仍然推错）。
2. **错误前提不拒绝**：数据是存货 +18.2%（上升），问"为什么下降"，模型不指出矛盾，反而把存货数据误读成营收，再顺着"下降"前提编出五点归因长文（AI PC 换机潮、"国补"等，均无出处）。

**设计启示（供 P4 实现 attribution skill 与 system prompt 时落实，P5 的 L3 评测定量验证）**：
- attribution skill 的同行对比步骤要写成机械规则：**判定 industry_wide 看"三家是否同向 + 量级相近（幅度差异 < 30% 或均超各自历史区间）"，不看 z 值排名**。
- system prompt 铁律加一条（呼应 plan 6.10 第 5 条）：**"前提与数据矛盾时必须 refuse/clarify，禁止改写数据以迎合前提"**。
- 本探针正好是 PRD 4.3"确认偏误"论断的实证：便宜模型会"合理化任何现象"。这反而是本项目卖点（可信度设计）的好素材，面试可讲。
- L3 评测（P5）若显示脚手架救不回来（top-1 显著低于随机），再按 PRD 4.6 批注的口子评估切回 DeepSeek。

**附带观察（更新 Q2）**：两次探针运行响应均很快、正文无 reasoning 内容，usage 只有 prompt/completion 两项，默认疑似未开 thinking；Q2 结论"不显式传参"维持，留待 V4.44 冒烟测试再确认。

### DeepSeek V4.1 Flash 模型信息（已被 2026-10-01 的模型切换取代，留档备查）

**DeepSeek V4.1 Flash 模型信息**（2026-10-01 查证，来源均为官方文档）：

| 项 | 结论 | 来源 |
|---|---|---|
| 准确模型名 | `deepseek-flash`（即 DeepSeek-V4.1-Flash；旧名 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 仍被接受，但实际由 V4.1-Flash 服务并按 Flash 价格计费）。另一个可用 id：`deepseek-v4-pro` | [api-docs.deepseek.com](https://api-docs.deepseek.com/)、[定价页](https://api-docs.deepseek.com/quick_start/pricing) |
| 上下文长度 | 1M tokens，最大输出 384K | [定价页](https://api-docs.deepseek.com/quick_start/pricing) |
| 并行工具调用 | 请求体中**没有** `parallel_tool_calls` 参数；`tool_choice` 支持 `none` / `auto` / `required` / 具名函数（thinking 模式下 `required` 和具名选择报 400）；有 beta 的 `strict` 标志（默认 false）。**结论：官方未承诺单次响应返回多个 tool_calls，按 plan 风险表 #3，工具执行采用串行**；代码保留对多个 tool_calls 的兼容（循环处理），但不做并发优化 | [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion) |
| 前缀缓存规则 | 自动上下文缓存，按 cache hit / cache miss 分开计价（flash：hit 均价约 $0.003–0.006 / 1M，miss $0.15–0.3 / 1M，off-peak 为 peak 一半）。无 cache_edits 类接口 → **对历史消息的任何修改都会使其后前缀缓存失效**，plan 6.6.2"攒批、一次性修改"的设计成立 | [定价页](https://api-docs.deepseek.com/quick_start/pricing) |

补充：定价分 peak / off-peak（peak 为 UTC 周一至周五 01:00–04:00 与 06:00–10:00，周末与中国法定假日全天 off-peak，off-peak 价格减半）。**大批量评测尽量安排在 off-peak 时段跑，成本减半。** deepseek-flash 支持 vision 与 thinking 模式，本项目暂不使用 vision。

---

## 待人工确认

### Q1（P0 提出）：verify.md 中 G3/G4/G6/G7 的 grep 正则失效

`verify.md` 第 1 节 G3/G4/G6/G7 的模式串里用 `\|` 表示"或"，例如：

```
G4: git grep -nE "sk-[A-Za-z0-9]{16,}\|api_key\s*=\s*['\"][^'\"]+"
```

在 `-E`（ERE）语法下 `\|` 是**字面竖线字符**，不是"或"。这四条检查实际只会匹配同时含有竖线分隔两段的整行，等于形同虚设（例如新增 `skip` 不会被 G3 发现，密钥不会被 G4 发现）。

**建议**：把这几条里的 `\|` 改为 `|`（例如 `grep -nE "^\+.*(skip|xfail)"`）。按 verify 0.3 规则 1，执行者不能擅自修改检查项，请人工确认后修改 verify.md 并在变更记录中留档。

**影响**：不影响任何已实现功能；若确认修改，从 P0 起即可用正确正则执行全局检查。

> **人工答复（2026-10-01）：确认，已修改。** 问题的根源是：表格单元格中的 `|` 必须写成 `\|`，被你当成了命令原文。现在 G2–G7 的命令移到了 verify.md 第 1 节表格上方的代码块中，以代码块为准。G4 也改为检查 `.env` 中的实际密钥值（Agnes 的 key 不是 `sk-` 开头）。G7 改为不会误报 `ast.literal_eval`。另外新增了 `G-selftest`：第一次执行时，先在临时分支上故意制造违规，确认命令有输出。变更已记入 verify.md 第 12 节。**状态：已关闭。**

### Q2（P0 提出）：agnes-3.0-flash 的 thinking 模式默认状态与使用策略

官方文档确认可通过 `chat_template_kwargs: {"enable_thinking": true}` 开启 thinking；但**默认是否开启**未文档化，开启后 tool_calls 响应结构是否变化也未说明。

**建议**：agent 主循环与 judge 均不显式传 thinking 相关参数（用 API 默认值）；V0.5 ping 与能力探针运行时观察响应里是否出现 reasoning 内容，把结果补记到这里，再决定是否需要显式关闭（若默认开启，需评估成本与延迟影响）。

**影响**：P0 不阻塞；只影响 `agent/llm.py` 是否显式传参。

> **人工答复（2026-10-01）：不要依赖默认值，显式关闭 thinking。**
> - `config.yaml` 增加 `llm.enable_thinking`，agent 和 judge 分别配置，**都设为 false**；`agent/llm.py` 每次请求都显式传这个参数。
> - 原因：①默认值没有文档，以后可能悄悄改变，影响评测的可复现性；②版本对比时需要控制变量；③thinking 增加延迟和 token，20 RPM 的限速下评测会更慢；④开启后 tool_calls 的响应结构没有文档。
> - 如果响应中出现 reasoning 内容，**只写进 trace，不写回 messages**。
> - 能力探针中增加一项：分别用 true/false 跑一次带工具的请求，记录响应结构和延迟，作为证据。
> - thinking 开或关作为 P8 的一个对照实验（v1 + thinking），用评测数据决定是否开启。
> - 对应的检查项：verify.md 新增的 V0.9。**状态：已关闭。**

### Q3（P0 提出）：V0.6 真实工具调用测试的实现位置

verify.md V0.6 要求"测试 `tests/test_llm.py::test_tool_call`"用真实 LLM 验证工具调用。但如果把它放进 pytest 收集，会带来两个冲突：① G1 要求 `pytest -q` 全过，而真实调用依赖网络与有效 key，离线/限速（20 RPM）时必然失败或超时；② 往套件里加 skip/xfail 会违反 0.3 规则 2。

**建议**：真实工具调用测试以**独立探针脚本**实现（`docs/evidence/P0_capability_probe.py`，含 V0.6 的 get_weather/北京用例），运行输出存为 `docs/evidence/P0_capability_probe_output.txt` 作为 V0.6 证据；`tests/test_llm.py` 保留全部离线 mock 测试（工具调用的解析逻辑由 mock 覆盖）。即 V0.6 的证据形式从 pytest 改为脚本，请人工确认是否接受。

> **人工答复（2026-10-01）：接受，并推广为通用规则。** verify.md 新增 0.5 节"联网检查"：所有需要真实 LLM 或网络的检查（V0.5、V0.6、V4.44、所有评测运行）都放在 `scripts/live/<检查ID>_*.py` 中单独运行，输出存为 `docs/evidence/<检查ID>_output.txt`；pytest 中全部是离线测试。G1 改为"离线运行 pytest -q 全部通过"。请把现有的 `docs/evidence/P0_capability_probe.py` 移到 `scripts/live/V0.6_capability_probe.py`（证据目录只放输出，不放代码）。**状态：已关闭。**

---

## 待人工确认（P1）

### Q4（P1 提出）：Dell 有 6 个"核心科目"缺口，无法从合规来源补齐

| 缺口 | 原因 | 选项 |
|---|---|---|
| Dell FY2024 Q2/Q3 的 accounts_receivable、accounts_payable（共 4 条） | 这两份 10-Q（accn 0001571996-23-000032 / -46）的资产负债表未用 `AccountsReceivableNetCurrent` / `AccountsPayableCurrent` 报送（R2 文件确认存在标准标签，但 companyfacts 无对应非维度事实，推断为维度上下文或扩展标签，companyfacts 不收录扩展标签）；FY25 文件的比较期也未重述 | A. 人工从两份 10-Q 主文档抄 4 个数，经 data/manual CSV 入库（V1.16 可溯源）；B. 接受缺口（已由 other_* 残差吸收，勾稽闭合不受影响） |
| Dell FY2017Q4 的 revenue、cogs（共 2 条） | Q4 倒算需要同财年 Q1–Q3，但 Dell FY2017 的 Q1–Q3 期末都在 2017-01-01 之前，按 plan 时间窗口规则未入库 → 无法倒算 | A. 接受缺口（仅影响 Dell FY17Q4 的收入/成本/毛利）；B. 为倒算输入放宽窗口（会违反窗口规则，不建议） |

V1.9 按现状为 BLOCKED（6 条）；其余 109 期核心科目齐全。

### Q5（P1 提出）：V1.10 营收区间上限被 Dell 增长击穿

V1.10 预期"每家公司的季度营收都在 5,000–40,000（百万美元）"。实测 Dell 季度营收最大 43,842（FY2027Q1，2026 年 5 月），最小 18,000——区间是为抓"单位错 1000 倍"设计的，Dell 是真实增长越过了上界。**建议**：把 V1.10 的上界改为 50,000 或改为"对数数量级正确（10³–10⁵）"。未改前 V1.10 记 PASS-with-note。

### Q6（P1 提出）：三个派生口径确认（当前实现如下，如无异议即为定稿）

1. **HP total_liabilities** = total_assets − total_equity（HP 不报送 Liabilities 标签），is_derived=**FALSE**（V1.11 要求资产负债表全部 FALSE；语义上它是推导值，留档于此）。
2. **SEC gross_profit** 一律 = revenue − cogs（is_derived=TRUE），不取公司报送的 GrossProfit 标签——不同年份标签口径混杂会导致恒等式破裂；联想取报表"毛利"行（V1.11 的 Q4 检查不受影响）。
3. **other_\* 残差** is_derived=FALSE，语义仅指"Q4 倒算"。吸收项 >5% 总资产的期间已在 `data/identity_report.md` 列出，主要为 Dell 并购 EMC 后的真实递延所得税/应计项目，非映射错误。

---

## 人工答复：Q4–Q6（2026-10-01，由 Claude 代为答复）

### Q4 答复
**(1) Dell FY2024 Q2/Q3 的 AR/AP：选 A，补录。** 原因：缺口不只影响 DSO 和 DPO。缺失的金额被残差吸收后，other_current_assets 从 16,346 跳到 27,051，other_current_liabilities 从 594 跳到 21,221，**在数据里制造了一个假异常**，会污染 L3 归因题和异常候选列表。

已从 EDGAR 核实（`R2.htm`，合并资产负债表），可以直接录入 `data/manual/dell_patch.csv`：

| 期间 | period_end | 科目 | 值（百万美元） | 报表行原文 | accn | available_date |
|---|---|---|---|---|---|---|
| FY2024 Q2 | 2023-08-04 | accounts_receivable | 10,351 | Accounts receivable, net of allowance & Due from related party, net | 0001571996-23-000032 | 2023-09-12 |
| FY2024 Q2 | 2023-08-04 | accounts_payable | 19,969 | Accounts payable & Due to related party | 0001571996-23-000032 | 2023-09-12 |
| FY2024 Q3 | 2023-11-03 | accounts_receivable | 9,720 | 同上 | 0001571996-23-000046 | 2023-12-08 |
| FY2024 Q3 | 2023-11-03 | accounts_payable | 19,478 | 同上 | 0001571996-23-000046 | 2023-12-08 |

- 同一份 R2 中，AR 和 AP 的 XBRL 标签就是 `us-gaap_AccountsReceivableNetCurrent` / `AccountsPayableCurrent`，口径与相邻各期一致（都包含与 VMware 的关联方款项）。companyfacts 没有收录的原因不重要，按人工补录处理，`source='manual_edgar'`，`source_ref` 写 accn + "R2.htm"。
- **一并修正 FY2024 Q4**：FY24 的 10-K（accn 0001571996-24-000036，**2024-03-25 提交**）的 AR 9,343 和 AP 19,389 同样没有进入 companyfacts，现在库里的 original 版本取自 2024-06-11 的 10-Q 比较期，**available_date 晚了 2.5 个月**。数值不用改，把 available_date 改为 2024-03-25，source_ref 改为该 10-K。
- 补录之后，重算这几期的残差，确认假异常已经消失。

**(2) Dell FY2017 Q4 的营收和成本：不接受缺口，用 C 方案。** 窗口规则限制的是**入库的期间**，不限制**计算时用到的输入**。Q4 用"全年 − 前三季度累计（9 个月 YTD）"倒算即可，不需要 Q1–Q3 单季入库：

| 科目 | 全年（10-K，accn 0001571996-17-000004，2017-03-31 提交） | 9M YTD（10-Q，accn 0001571996-16-000021，2016-12-09 提交） | Q4 |
|---|---|---|---|
| revenue（`SalesRevenueNet`） | 61,642 | 41,568 | **20,074** |
| cogs（`CostOfRevenue`） | 48,683 | 33,140 | **15,543** |

- 两个输入都取**首次报送的版本**。之后 2018 年 8-K 和 2019 年 10-K 中的重述值（例如 cogs 48,515）不能混用。
- available_date = 2017-03-31。
- **建议推广**：所有 Q4 倒算都优先用"全年 − 9M YTD"。这样比减三个单季少用两个输入，也更不容易混入不同版本的数。
- 注意 Dell FY2017 是 53 周年，Q4 有 14 周（2016-10-29 至 2017-02-03，共 98 天）。见 Q7-2 的 days 问题。

### Q5 答复
同意修改，但不只是放宽上界。V1.10 改为两条：①季度营收在 5,000–100,000 之间（用于发现单位错 1000 倍）；②相邻季度营收的变化在 ±50% 以内（用于发现单位错 10 倍和 Q4 倒算错误）。超出 ±50% 的逐条说明原因。verify.md 已经修改。

### Q6 答复
1. **HP total_liabilities：不同意用 A − E 推导。** 用 A − E 推出 L 之后，"A = L + E"这条检查对 HP 就**恒成立**，等于没检查。改为：L = 负债各分项之和（流动负债合计 + 长期债务 + 其他非流动负债 + ……，用 HP 实际报送的标签），再和 `LiabilitiesAndStockholdersEquity − StockholdersEquity...` 比较，差异超过 0.5% 就排查。这样才能真正检验映射是否完整。
2. **SEC gross_profit = revenue − cogs：同意。** 另外加一个只做记录的比较：公司报送了 GrossProfit 标签的期间，计算它和推导值的差异，超过 0.5% 的写进 identity_report，不作为失败。
3. **other_\* 残差的 is_derived=FALSE：不同意。** 残差是用来补齐差额的"倒挤数"，必须能和真实报送的科目区分开。**新增 `derivation` 字段**（plan 附录 B 已经修改）：

   | derivation | 含义 |
   |---|---|
   | NULL | 直接报送 |
   | `q4_backout` | Q4 倒算 |
   | `sum_of_parts` | 分项加总（HP 负债） |
   | `rev_minus_cogs` | 毛利推导 |
   | `residual` | 残差 |
   | `manual_patch` | 人工补录 |

   `is_derived` 定义为 `derivation IS NOT NULL`。V1.11 改为检查"`q4_backout` 只出现在利润表的 Q4"，verify.md 已经修改。

   **你对 Dell 残差的解释不准确。** 我用原始 JSON 核对了 Dell FY2024 Q4：
   - other_noncurrent_liabilities 16,892 = `ContractWithCustomerLiabilityNoncurrent`（非流动递延收入）13,827 + `OtherLiabilitiesNoncurrent` 3,065，完全吻合。也就是说，它的主体是**非流动递延收入**，不是递延所得税或应计项目。
   - other_current_assets 15,616 中，约 4,643 是 `NotesAndLoansReceivableNetCurrent`（短期融资应收款），其余是 `OtherAssetsCurrent`。

   处理方式：
   - 附录 A 新增两个标准科目：`deferred_revenue_noncurrent` 和 `financing_receivables`（只放流动部分；非流动部分仍计入 other_noncurrent_assets）。HP 和联想有对应科目的也要映射过去；没有报送的不入库，**不能填 0**。
   - 新规则：任何一期的残差超过总资产 5% 时，必须在 identity_report 中**按报送标签拆开说明**残差由哪些项目构成，不能只写一句定性的解释。

---

## Q7（人工抽检时发现，2026-10-01）：P1 还有 5 个问题需要修复后才能打 tag

抽检方法：我用和你不同的解析引擎（xpdf 的 `pdftotext -table`，你用的是 pdfplumber）独立提取文本，逐条比对。然后用整库交叉核对，覆盖抽样之外的数据。

**抽检结论：151/151 正确，0 错误**，明细见 `docs/evidence/V1.17_claude_check.csv`。
- 137 条自动匹配通过；
- 4 条是 cogs 的符号：PDF 以括号负数列示，库中按正数存储，约定一致；
- 10 条人工复核后确认一致：科目名跨两行、脚本误匹配到现金流量表、附註编号干扰；
- 另外渲染了 1 页图片，目视核对一致。

**整库核对**：联想资产负债表 740 个 (期间, 科目) 汇总值与 CSV 完全一致；利润表 169 个值（含 Q4 倒算）全部一致。

**但发现了 5 个系统性问题：**

1. **联想 fiscal_year 的写法不统一**：联想存的是 18–27，HP 和 Dell 存的是 2017–2027。必须统一为 4 位数（联想 FY18 = 截至 2018-03 的财年，记为 2018，与 Dell 的命名规则一致）。否则后面按 fiscal_year 关联和出题都会出错。
2. **`days` 字段全部少 1 天**（115 期都错）：例如联想 FY18 Q4（2018-01-01 至 03-31）实际是 90 天，库里是 89；Dell 13 周的季度是 91 天，库里是 90。原因在 `load_db.py`：起始日 = 上期期末 + 1，天数 = 期末 − 起始日，少算了 1 天。正确的算法是 **days = 本期期末 − 上期期末**。窗口边缘的第一期不要用 91 天近似，用窗口外那一期的期末日来算（Dell FY2017 Q4 = 98 天）。这个字段直接影响 DIO、DSO、DPO 的计算。
3. **联想应付账款在 FY23 有口径断点**：
   - FY18–FY22 的"應付票據"（6–21 亿美元）映射到了 other_current_liabilities；
   - FY23 起报表合并成"應付貿易賬款及票據"，映射到了 accounts_payable。
   
   结果是 accounts_payable 在 FY23 Q1 凭空多出最多约 15%，这会在 DPO 和归因题里制造假异常。修复方法：FY18–FY22 的應付票據也映射到 accounts_payable（应收侧已经是这样处理的，应收票據映射到 accounts_receivable，做法一致）。
   
   联想 FY26 起的"應收貿易賬款、租賃款及票據"多了租赁应收款，这是**真实的口径变化**，无法拆分。写进 `filing_notes` 表即可，正好可以作为 L3 中重分类类型的真实样本。
4. **联想 net_income 大量缺失**：37 期中只有 23 期有，Q4 一期都没有。FY19 Q4 的 operating_income 在库里存在，但源 CSV 中凑不齐 Q1–Q3，不清楚是怎么倒算出来的。请补齐 net_income，或者从标准科目中去掉它（它不是核心科目），并解释 FY19 Q4 operating_income 的来源。
5. **检查漏洞**：上面 2、3 两个问题，现有的 V1.x 检查都发现不了。verify.md 已新增以下检查，请执行：
   - V1.18：fiscal_year 统一为 4 位数；
   - V1.19：days = 本期期末 − 上期期末；
   - V1.20：口径连续性，列出每个科目在不同年份映射的报表行，以及环比变化超过 3σ 的跳变，并逐条解释；
   - V1.21：SEC 数据的 available_date = 该期间本身的 10-Q 或 10-K 的提交日期。

**状态**：Q4–Q6 已关闭；Q7 待执行者修复。修复完成、V1.x 全部通过后，打 `phase-P1-done`。V1.17 的 Claude 部分已完成；另一个模型独立抽检完成后，由用户确认最终状态。

---

## 待人工确认（P2）

### Q7 执行侧状态补记（2026-10-01）
Q7 五项修复已在 P1.7 落实并验证（见 verification_log"P1 批阅修复"表，V1.18–V1.22 全 PASS）；V1.17 第二模型独立抽检与最终 tag `phase-P1-done` 仍待人工确认。本条仅补记状态，不自行关闭。

### Q8（P2 提出）：G4③ 通用密钥模式命中 docs/ 下的历史证据文本（非真实密钥）

G4 第③条（`git grep -nE "sk-[A-Za-z0-9]{16,}|api_key\s*=\s*['\"][^'\"]+"`）在**代码区无输出**、`.env` 真实密钥值全库无匹配（②通过），但命中 3 处文档：
- `docs/evidence/G-selftest.txt`（两处）：G-selftest 时写入的假密钥 `sk-SELFTEST0123456789ABCDE` 与对 `api_key="test-key"` 的引述；
- `docs/verification_log.md`（P0 期记录）：转述同一假 key。

这三处都是**检查过程本身的留档**，不是密钥。按 verify 0.3 规则执行者不能修改检查项，证据文件也不应事后改写，所以无法在不违反规则的前提下让③变为无输出。注意 P0 关闭时 G4 记为"无输出"，推测当时扫描未覆盖已提交的证据文件（本阶段按同一命令如实执行才暴露）。

**建议（三选一，请人工裁定）**：
A. G4③ 的扫描范围限定为代码与配置（`-- ':!docs'`），docs 留档豁免；
B. 维持全库扫描，把"SELFTEST/test-key 形态"作为已登记的例外写入 verify.md；
C. 人工重写上述证据措辞（执行者不自行删改证据）。

**影响**：不阻塞 P2 功能与提交；只影响 `phase-P2-done` 的 G4 行结论。在此之前 G4 记 BLOCKED-待人工。

---

## 待人工确认（P4）

### Q9（P4 提出）：V4.2 第三用例的数字与"0.5% 容差"口径自相矛盾

verify V4.2 规定用例"存储 6123.4 (usd_mn)，claim 6150 (usd_mn) → 找不到（超出 0.5%）"。
实际算术：|6150 − 6123.4| / 6123.4 = **0.434%**，并未超出 0.5%——在任何 0.5% 相对容差实现下该 claim 都会被判"找到"。用例的期望与括号里的理由互相矛盾（其余两例与 V3.4–V3.6 都自洽）。

**建议（三选一，请人工裁定）**：
A. 把该用例的 claim 值改为 6250（偏离 2.06%，明确超出 0.5%），期望"找不到"不变；
B. 把数字核验容差从 0.5% 收紧到 0.4%（注意 V3.6 的 1.004 用例恰好在 0.4% 边界，需同时约定边界含等号）；
C. 接受现状：该用例按 0.5% 容差执行、期望改为"找到"（等于承认 verify 笔误）。

**执行者处理**：在裁定前，`tests/test_tools.py::test_v4_2_outside_tolerance` 用 6250 验证"超出容差→找不到"这一规则本身，并对 6150 如实断言"0.43%<0.5%→找到"，V4.2 记 **待 Q9**（不自行判 PASS）。

### Q10（P4.5d 提出）：V4.44 长任务归因题——压缩机制全过，模型 claim 纪律不足，已 4 次 live 仍未 PASS

**现象**（证据：docs/evidence/V4.44_output.txt 为第 4 次；前三次结论记录在 verification_log）：
- 第 1–2 次（20/30 步）：步数耗尽拒答；压缩本身正常（L3/L4 大量触发、无 API 报错、无熔断）。
- 第 3 次（40 步、keep=3、打回上限 2）：9 步内 answer 通过收敛，但 verifier 打回 2 次后耗尽：正文含 `≈ 0%`（按规则需一条 value 恰为 0 且能在引用结果中找到的 claim）、z 值写作 0.09（存储 0.0913，0.5% 相对容差判定不通过）。
- 第 4 次（打回上限调至 4）：38 步、L3×7/L4×30/L5×2（L5 两次生成均因摘要引用含数字的错误文本而核验失败→按设计退回仅账本），最终答案内容质量高、结论正确（no_anomaly），但 **30 个正文数字只有 7 条 claim**，其中 2 条 claim 的数值在任何结果中不存在（分部贡献 124.639 与存储最接近值 126.06 相差 1.1%；置信度 0.85 非数据数字）→ verified=False。

**判定**：V4.44 的三条通过标准中「≥1 次 L3」「无 API 报错」已稳定满足；「最终答案通过 verifier」连续 4 次未满足。失败根因是**模型行为**（claim 与正文不闭合、引用不存在的数），不是压缩/循环缺陷；且第 4 次中 verifier 恰好抓住了模型幻觉数字，属护栏按设计工作。执行者不能改检查项或通过标准（verify 0.3），冒烟可调的参数（步数/时间/打回次数/投影强度）已全部试完。

**建议（四选一，请人工裁定）**：
A. 把它作为已知 bad case 记入 V4.44 为 FAIL-待 P8：P8 做 prompt/skills 实验（强制"正文每个数字都建 claim、置信度等元数字白名单、claim 必须给 rid"），实验后重跑冒烟；V4.45–V4.47 照常先跑（L1/L2 短任务不涉长归因）。
B. 允许我现在就在 attribution skill/系统提示中加"claims 闭合"纪律并重跑冒烟（属正常开发调优，不算弱化检查）。
C. 人工复核 trace 后认为检查过严，修改 verify 的 V4.44 标准（执行者无权自改，需人工明确指示）。
D. 换一道更短的归因题冒烟（改变检查输入，需人工确认这不构成放宽）。

**执行者处理**：V4.44 记 **待 Q10**（不自行判 PASS）；继续 P4.6 的 V4.45–V4.47（独立检查项），运行数据不受裁定影响。

---

## Q11（P5 提出）：V5.11 真实事件候选的 z 阈值 2.5 数学上不可达

plan 7.4 要求以"真实 |z| > 2.5"筛真实事件候选。但 z 以历年同季度环比分布为基准，样本只有 7–8 个（2018–2025），**n 个样本下 |z| 的理论上限是 (n-1)/√n**：n=8 时 ≈ 2.47，因此 2.5 永远筛不出任何候选（实测 0 条）。

**已做**：候选清单按 |z| > 2.0 生成（docs/evidence/V5.11.csv，2 条），文件头注明阈值偏离。

**请人工裁定**：A. 确认 2.0 并写入 plan；B. 改用稳健排序法（如按 |z| 排序取前 K 个）；C. 扩大样本（纳入 2016–2017 或用月度数据重算 σ）。
**状态：待裁定**（不阻塞 L3 live run，只影响真实事件标注的输入范围）。

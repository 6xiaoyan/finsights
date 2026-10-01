# 需要人工确认的问题

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
| 端点与密钥 | base_url `https://apihub.agnes-ai.com/v1`；密钥在 `platform.agnes-ai.com` 申请。2026-10-01 用户提供的首把 key 被网关拒绝（401 Invalid token），已请用户核对 | [wiki](https://wiki.agnes-ai.com/) |

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

### Q2（P0 提出）：agnes-3.0-flash 的 thinking 模式默认状态与使用策略

官方文档确认可通过 `chat_template_kwargs: {"enable_thinking": true}` 开启 thinking；但**默认是否开启**未文档化，开启后 tool_calls 响应结构是否变化也未说明。

**建议**：agent 主循环与 judge 均不显式传 thinking 相关参数（用 API 默认值）；V0.5 ping 与能力探针运行时观察响应里是否出现 reasoning 内容，把结果补记到这里，再决定是否需要显式关闭（若默认开启，需评估成本与延迟影响）。

**影响**：P0 不阻塞；只影响 `agent/llm.py` 是否显式传参。

### Q3（P0 提出）：V0.6 真实工具调用测试的实现位置

verify.md V0.6 要求"测试 `tests/test_llm.py::test_tool_call`"用真实 LLM 验证工具调用。但如果把它放进 pytest 收集，会带来两个冲突：① G1 要求 `pytest -q` 全过，而真实调用依赖网络与有效 key，离线/限速（20 RPM）时必然失败或超时；② 往套件里加 skip/xfail 会违反 0.3 规则 2。

**建议**：真实工具调用测试以**独立探针脚本**实现（`docs/evidence/P0_capability_probe.py`，含 V0.6 的 get_weather/北京用例），运行输出存为 `docs/evidence/P0_capability_probe_output.txt` 作为 V0.6 证据；`tests/test_llm.py` 保留全部离线 mock 测试（工具调用的解析逻辑由 mock 覆盖）。即 V0.6 的证据形式从 pytest 改为脚本，请人工确认是否接受。

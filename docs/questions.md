# 需要人工确认的问题

> 按 plan 第 0 节规则 3：执行者遇到 ⚠️ 标记时，把问题和建议记录在这里，等人工确认后再继续。

---

## 已查证（P0 ⚠️ 项，无需再确认，留档备查）

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

### Q2（P0 提出）：deepseek-flash 的 thinking 模式默认状态与使用策略

官方文档确认 flash 支持 thinking 模式（`thinking`、`reasoning_effort` 参数），但未查到**默认是否开启**，以及开启后 tool_calls 响应结构的变化。plan 4.6 只定了模型未定 thinking 策略。

**建议**：agent 主循环与 judge 均不显式传 `thinking` 参数（用 API 默认值）；V0.5 ping 测试时观察响应里是否出现 `reasoning_content` 字段，把结果补记到这里，再决定是否需要显式关闭（若默认开启，需评估成本影响）。

**影响**：P0 不阻塞；只影响 `agent/llm.py` 是否显式传参。

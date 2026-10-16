| 区域 | 来源 | 规范化字段 | 状态 | 说明 |
|---|---|---|---|---|
| package | `run_meta.json:run_id` | `run_id` | available |  |
| package | `调用方 --case-id` | `case_id` | available | 运行包本身不含 case_id，由评测侧绑定 |
| package | `--run-dir` | `run_dir` | available |  |
| package | `run_meta.json:status` | `final_status` | available | 以 run_meta 为准；trace 末行 result 仅作交叉核对 |
| package | `answer.md（仅 status==answered）` | `final_answer` | derived | 未提交时为 None，不填补 |
| package | `run_meta.json:verified` | `verified` | available |  |
| package | `analysis_state.json:contract` | `contract` | available | 仅 multi_agent 有 |
| package | `analysis_state.json:latest_revision` | `latest_revision` | available |  |
| package | `analysis_state.json:plans` | `plans` | available | 按 revision 字符串键 |
| package | `analysis_state.json:artifacts` | `artifacts` | available | version 字段不落盘，见 artifact_version() |
| package | `analysis_state.json:reviews` | `reviews` | available |  |
| package | `analysis_state.json:answer_candidate` | `answer_candidate` | available |  |
| package | `trace.jsonl:subagent_start/end` | `role_calls` | available | 按 agent_call_id 配对；缺 end 的项 token/耗时为 None |
| package | `analysis/query_trace.jsonl 或 results.json` | `query_records` | derived | 无 query_trace.jsonl 时从 results.json 派生；派生记录只覆盖成功路径（仅成功路径落库） |
| package | `run_meta.json:usage` | `usage` | available | 缺失时为空字典，不填 0 |
| package | `run_meta.json` | `run_meta` | available |  |
| package | `本适配器计算` | `diagnostics` | derived | result 行数、状态不一致等 |
| package | `本适配器计算` | `unknown_fields` | derived | 缺失的埋点文件清单 |
| role_call | `trace.jsonl:subagent_start.role` | `role_calls[].role` | available |  |
| role_call | `trace.jsonl:subagent_start.node_id` | `role_calls[].node_id` | available | 仅 worker 有 |
| role_call | `trace.jsonl:subagent_start.target_*` | `role_calls[].target_version` | available | 仅 reviewer 有 |
| role_call | `trace.jsonl:subagent_end` | `role_calls[].latency_ms` | available | 缺 end 时为 None |
| role_call | `subagent_context 事件` | `role_calls[].context_refs` | new_instrumentation | 历史包为 None |
| query | `query_trace.jsonl:query_trace_id` | `query_records[].query_trace_id` | new_instrumentation | 调用级 id；历史包为 None（不可用 rid 代替，rid 会复用） |
| query | `query_trace.jsonl:attempts/extractor_events` | `query_records[].attempts` | new_instrumentation | 历史包只有 run_meta.extraction_calls 总数 |
| query | `query_trace.jsonl:plan` | `query_records[].plan` | new_instrumentation | ok 路径可从 results.json[].args.plan 派生；非 ok 路径历史包不可恢复 |
| query | `results.json[].sql` | `query_records[].sql_chars` | derived | 只记长度，全文留在 results.json；未产生 SQL 时为 None |
| query | `results.json[].rows` | `query_records[].columns` | derived | 取首行键；无行时为空表 |
| query | `trace.jsonl 子 tool_result.query_trace_id` | `query_records[].linkage` | new_instrumentation | 历史包恒为 unknown，不按顺序/文本猜归属 |
| query | `results.json[].id` | `query_records[].result_ids` | available | 非 ok 路径为空；不伪造 rid |
| query | `（历史包不存在）` | `query_records[].status` | unknown_legacy | 派生记录只来自成功路径，故按 control-flow 推为 ok；失败调用在历史包中无任何记录 |

状态含义：`available` 今天的运行包里已有；`derived` 由包内数据推导；
`new_instrumentation` 本次补的埋点才产生（历史包为 unknown）；
`unknown_legacy` 历史包必然缺失，只能记 unknown。

**同名字段辨析**：`query_records[].query_trace_id` 是**调用级** id（`q1`、`q2`…，
运行内唯一、每次调用必有）；`query_records[].plan_digest` 才是 executor 的
**内容寻址** id（`sha256(sql + plan)[:12]`，非 ok 路径为 null）。
两者同名不同义，不能互换：同计划复用 rid 时，多次调用共享同一个 plan_digest，
但各有自己的 query_trace_id。

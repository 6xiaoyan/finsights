# P1/P3 阶段关闭证据汇总（dashboard_design.md §8.2）

日期：2026-10-05。原则（§8.2）：P1/P3 可补齐后补打；执行者汇总有效全局证据、指定准确 commit；P4 不打 tag。
本文件汇总截至 commit 7e3910d（工作区含看板实现，tag 在其后的看板提交上，见 tag 对象说明） 的 G1–G8 有效证据（全部修复为累计包含，tag 打在当前 HEAD）。

## phase-P1-done

| 检查 | 证据摘要 |
|---|---|
| G1 | pytest 190 passed（0 failed，离线；含 P1–P5 与看板测试） |
| G2 | 测试数较各前一阶段只增不减（collect-only 记录于日志） |
| G3 | 无新增 skip/xfail（git diff 各阶段 tag..HEAD 无命中） |
| G4 | Q8 CLOSED：G4_example_allowlist.json（路径+SHA256 豁免）+ 全库扫描 0 真实命中 |
| G5 | 提交格式 [P<n>.<m>]；工作区提交后干净 |
| G6 | agent 不读取 gold（grep 无命中；L3 gold 在 eval/datasets/l3_gold，不进 agent） |
| G7 | 无 eval/exec/subprocess 入口（G-selftest 验证过正则有效） |
| G8 | Q1–Q11 全部关闭（Q11 按 solution.md 双路径方案 CLOSED） |
| V1.1–V1.22 | verification_log P1 各行 PASS/BLOCKED 已按 Q4/Q7/Q11 处置（Dell 补录、日期修正、口径统一、V5.11 双路径待实现） |
| V1.17 | PASS-USER-CONFIRMED（用户人工确认，取消第二轮 AI 抽检） |
| 指定 commit | 见 tag 对象（含全部必要修复的最新提交） |

## phase-P3-done

| 检查 | 证据摘要 |
|---|---|
| V3.1–V3.11 | verification_log P3 各行 PASS；评测集 30 题、runner/compare/leaderboard、v0 live 基线 L1 1.00/L2 0.78 |
| V3.12 | 人工抽检通过（用户确认） |
| G1–G8 | 同上（同一 HEAD）；旧 Q8/V1.17 依赖已解决 |
| 指定 commit | 见 tag 对象 |

## phase-P4-done（明确不打）

V4.44 维持 FAIL（§8.1 选 C）；V4.15/V4.47 无新的通过裁定。P4 不打 tag，不因 HEAD 通过 P1/P3 检查而顺延。

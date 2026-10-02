# MVP 基线固定与首批 bad case 清单（2026-10-02）

## 1. 基线状态

- MVP 交付链：自由问题 → 工具取数/计算/检索披露 → 带来源结论与限制 → 数字/引用核验 → 运行包落盘。
  已由真实端到端运行验证（V7.3 六问，§3 证据）。
- 基线 commit：以本文件所在提交为 MVP 基线（`git log --oneline -5` 可见 P5.10 收尾、MVP CLI、P7.3 三批）。
- 工作区：提交后 clean；`data/*.duckdb`、`runs/`、`.env` 按 .gitignore 不入库。
- 数据版本：`data/finsights.duckdb` sha256 前 12 位 `860558522f16`，`data_version`
  `2633728-1790844911120430000-d728e5137cbb`（每次运行包 run_meta.json 都自带这两项 + dirty 标志，
  复现不依赖仅 HEAD）。
- 回归：`pytest -q` 182 passed（全离线）；G3/G4/G6/G7 干净。
- **不构成**：`phase-P5-done`/`phase-P6-done`（V4.15/V4.47/V4.44 FAIL 保留、V5.12/Q12 待人工）；
  准确率无 100% 承诺；本文件不是任何检查项的通过证据。

## 2. 使用命令（README.md 同步）

```powershell
# 单问题（结论+核验状态+运行包路径打印到终端）
.venv\Scripts\python -m agent.cli --question "联想 FY2025Q2 的营运资本相比上一季是改善还是恶化？"
# 时点隔离（快照库+披露过滤）
.venv\Scripts\python -m agent.cli --question "..." --as-of 2024-06-30
# 批量评测
.venv\Scripts\python -m eval.runner --agent v1 --datasets l1,l2
```

输出样例：`runs/cli-20261002-193108/`（简单取数，核验通过，数字与库分毫不差）、
`runs/cli-20261002-193217/`（归因题，核验未被冒充成功，exit=2）。
查引用：answer.md 的 `[rN]` → results.json 同 rid 的 rows/SQL/digest。

## 3. 首批 bad case 清单（等待用户选案例，按交接 §5 逐例人工主导）

| # | 案例（证据位置） | 现象 | 已知归因线索 | 性质 |
|---|---|---|---|---|
| B1 | l3_002 t1、l3_021 t1–t3、l3_023 t2（run 20261002-183423；badcases V5.10 重跑节） | 同行比较只看原始环比 %，"幅度相差悬殊"否定/肯定 industry_wide；阴性题把 |z|<0.3 的自然同向波动误报为行业异常 | % vs z 口径是**待讨论假设**，未自动写死（交接 §5）；注入按 σ 设计导致原始 % 天然不同 | agent 推理 |
| B2 | l3_003 t1–t3（同上 run） | mix_shift 题全部答 company_specific：从未查询对冲科目（oca/total_current_assets 在 query_metric 可见） | 归因缺"总量/比率分解+配对核对"动作；候选通用分析动作见交接 §5 | agent 推理 |
| B3 | runs/cli-20261002-193217（e2e_attrib，真实事件归因） | 长文叙述大量裸数字未入 claims/rid，18 步修正穷尽仍核验未过（拒冒充成功） | 数字可溯源 vs 叙述简洁的平衡；提示/skill/verifier 三处候选落点 | agent 输出纪律 |
| B4 | l3_002 t3 refuse（同上 run）；e2e_attrib 18 步/39.7 万 token/275s | 归因类题步数/成本逼近预算，耗尽即拒答（行为正确、代价高） | 与压缩/步数效率相关（V4.44 FAIL 族，暂缓项） | 成本/预算 |
| B5 | l3_022/023/027/028/030 题面方向（本轮已修）；阴性题在新题面下未复验 | 旧题面前提与数据矛盾（none 恒写"上升"），修正后 l3_022 的 3 条 clarify/refuse trial 判无效 | 生成器缺陷已修+测试增强；**是否复验 3×5 由用户排期**（不自动长跑） | harness（已修） |
| B6 | robust_fy_ambiguity（cli-20261002-193850） | 财年/自然年歧义时选择"声明口径后作答"而非先澄清——结果正确、策略是偏好问题 | 留人工裁定期望行为 | 待裁定偏好 |

## 4. 停止点

MVP 已固定，自动扩展停止：L4 冒烟/V6.10 全量 live、P7.1/P7.2、P8 自动优化、V4.44 重试、
bank 重建（B1 设计张力若裁定改注入模式才触发）均等待用户选择案例后按 §5 流程逐轮进行。
git push 仍需用户单独授权（本地领先 origin 多个提交，未推送）。

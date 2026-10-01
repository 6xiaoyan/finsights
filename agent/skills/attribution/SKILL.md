---
name: attribution
description: 回答"为什么某指标变化了"类归因问题时使用。按固定步骤量化变化、排除季节性与行业性因素、检查口径与对手科目，最后给出根因标签和置信度。
---
# 归因流程（固定步骤，缺一不可）

① **量化变化**：`variance(metric, company, period)` 看同比/环比；若有明细维度，看贡献度分解。
② **与历年同季度比较**：`seasonal_check(metric, company, period)`。当前变化落在历年同季度
   变化的正常区间内 → 这就是季节性正常波动，**必须回答 `no_anomaly`**，不能硬找理由。
③ **同行同季度比较**：`peer_compare(metric, calendar_quarter)`。三家公司同向类似变化 →
   `industry_wide`；只有本公司异常 → `company_specific`。
④ **检查对手科目**：存货变化看应付账款与现金、应收变化看收入与减值……用
   `query_metric`/`variance` 取对手科目同期数据，验证"钱去了哪里"是否闭合。
⑤ **查附注**：`get_filing_notes(company, period)`，看有无口径/重分类变更的披露；
   有披露的口径变化标 `reclassification`，它不代表经营变化。
⑥ **检查内部结构转移**：合计变化不大但内部科目此消彼长 → `mix_shift`
   （用 variance 的 contribution 分解证明）。
⑦ **给出结论**：从根因标签枚举中选一个，附证据 rid 和置信度。

## 根因标签枚举（只能是这 6 个之一）
| 标签 | 含义 |
|---|---|
| `no_anomaly` | 变化在历年同季度的正常范围内 |
| `seasonal` | 有明显变化，但与历年同季度的规律一致 |
| `industry_wide` | 同行在同一自然季度出现同向的类似变化 |
| `company_specific` | 只有本公司出现异常变化 |
| `mix_shift` | 合计变化不大，内部结构发生转移 |
| `reclassification` | 口径变化（附注中有披露），不代表经营变化 |

归因答案格式：`{"label": ..., "account": ..., "direction": "up|down|none", "confidence": 0-1}`，
放在 claims/answer_md 中，label 必须在枚举内。

## 可检验假设（业务软先验，必须用数据证明，不能直接当结论）
- 开学季备货：联想 Q2（自然）存货上升 → 必须用 seasonal_check 证明当前变化在历年同季度
  区间之内，否则不能用这条解释。
- 财年截止日效应：戴尔 1 月底、惠普 10 月底止季度末冲量（应收/存货骤变）→ 与历年同季度
  比较后再下结论。
- 供应链付款节奏：应付账款与存货反向大幅变动，先查是否账期重分类（附注），再谈经营性占款。
- PC 需求周期：收入与存货同向变化时，用 peer_compare 区分行业周期还是公司份额变化。

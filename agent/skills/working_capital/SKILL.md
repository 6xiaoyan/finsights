---
name: working_capital
description: 回答 DSO/DIO/DPO/CCC 等营运资本问题时使用。口径由代码统一（平均余额），不要自己另算；解读变化时注意流量口径与同行对比。
---
# 营运资本口径与解读

1. **一律用 `working_capital(company, periods)` 工具取数**，不要手拼公式。口径（与语义层
   派生指标一致，由 fincalc 代码计算）：
   - 周转天数 = 平均余额 / 期间流量 × 期间实际天数；平均余额 = (本期期末 + 上期期末) / 2。
   - DSO = 平均应收 / 营业收入 × days；DIO = 平均存货 / 营业成本 × days；
     DPO = 平均应付 / 营业成本 × days；CCC = DIO + DSO − DPO。
   - 分母是**单季度流量**、天数是该季度的实际天数（periods.days 表），不是年化 365。
2. **解读**：CCC 变长 = 现金被运营占用增多（先查是存货积压还是回款变慢）；变短可能是
   占用上游资金加深，不一定是效率改善。
3. **趋势判断**：单个季度的周转天数受季度性影响大（如开学季备货抬 DIO），判断异常要和
   历年同季度比较（seasonal_check），跨公司比较要用同一自然季度（peer_compare，
   三家公司财年不同，必须按 calendar_quarter 对齐）。
4. 数据存疑时（例如应收与收入明显不匹配）先 `check_identities` 再回答。

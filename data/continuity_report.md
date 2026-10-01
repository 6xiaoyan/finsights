# 口径连续性报告 continuity_report

## ① 映射来源随年份变化（每条需说明：真实口径变化 / 映射修复）

- Dell / deferred_revenue_current：2017 用 ['DeferredRevenueCurrent'] → 2018 用 ['ContractWithCustomerLiabilityCurrent', 'DeferredRevenueCurrent']。说明：标签演化：ContractWithCustomerLiabilityCurrent（606 后）与 DeferredRevenueCurrent（606 前，回退）
- Dell / deferred_revenue_current：2018 用 ['ContractWithCustomerLiabilityCurrent', 'DeferredRevenueCurrent'] → 2019 用 ['ContractWithCustomerLiabilityCurrent']。说明：标签演化：ContractWithCustomerLiabilityCurrent（606 后）与 DeferredRevenueCurrent（606 前，回退）
- Dell / deferred_revenue_noncurrent：2017 用 ['DeferredRevenueNoncurrent'] → 2018 用 ['ContractWithCustomerLiabilityNoncurrent', 'DeferredRevenueNoncurrent']。说明：同上（非流动侧）
- Dell / deferred_revenue_noncurrent：2018 用 ['ContractWithCustomerLiabilityNoncurrent', 'DeferredRevenueNoncurrent'] → 2019 用 ['ContractWithCustomerLiabilityNoncurrent']。说明：同上（非流动侧）
- HP / deferred_revenue_current：2018 用 ['ContractWithCustomerLiabilityCurrent', 'DeferredRevenueCurrent'] → 2019 用 ['ContractWithCustomerLiabilityCurrent']。说明：ASC 606 过渡年（FY2018）：不同季度分别用 ContractWithCustomerLiabilityCurrent 与 DeferredRevenueCurrent 报送（回退机制按季度启用）
- HP / deferred_revenue_noncurrent：2018 用 ['ContractWithCustomerLiabilityNoncurrent', 'DeferredRevenueNoncurrent'] → 2019 用 ['ContractWithCustomerLiabilityNoncurrent']。说明：标签演化：ContractWithCustomerLiabilityNoncurrent（606 后）与 DeferredRevenueNoncurrent（606 前，回退）
- HP / long_term_debt：2018 用 ['LongTermDebtAndCapitalLeaseObligations'] → 2019 用 ['LongTermDebtAndCapitalLeaseObligations', 'LongTermDebtNoncurrent']。说明：标签演化：FY17–FY22Q3 用 LongTermDebtAndCapitalLeaseObligations，之后 LongTermDebtNoncurrent（回退机制）
- HP / long_term_debt：2019 用 ['LongTermDebtAndCapitalLeaseObligations', 'LongTermDebtNoncurrent'] → 2020 用 ['LongTermDebtNoncurrent']。说明：标签演化：FY17–FY22Q3 用 LongTermDebtAndCapitalLeaseObligations，之后 LongTermDebtNoncurrent（回退机制）
- HP / total_equity：2017 用 ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'] → 2018 用 ['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest']。说明：标签演化：StockholdersEquity 与 …IncludingPortionAttributableToNoncontrollingInterest 随 NCI 存废切换（回退机制，非口径变化）
- HP / total_equity：2021 用 ['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'] → 2022 用 ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest']。说明：标签演化：StockholdersEquity 与 …IncludingPortionAttributableToNoncontrollingInterest 随 NCI 存废切换（回退机制，非口径变化）
- HP / total_equity：2022 用 ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'] → 2023 用 ['StockholdersEquity']。说明：标签演化：StockholdersEquity 与 …IncludingPortionAttributableToNoncontrollingInterest 随 NCI 存废切换（回退机制，非口径变化）
- Lenovo / accounts_receivable：2025 用 ['應收貿易賬款及票據'] → 2026 用 ['應收貿易賬款、租賃款及票據']。说明：真实列报变更：FY2026 起报表行含租赁应收款，无法拆分，见 filing_notes
- Lenovo / net_income：2018 用 ['期内(虧損)/溢利', '期内溢利'] → 2019 用 ['期内溢利', '期内溢利/(虧損)']。说明：标签变体修复：期內/年內、內/内 字形变体（Q7-4）
- Lenovo / net_income：2019 用 ['期内溢利', '期内溢利/(虧損)'] → 2020 用 ['期内溢利']。说明：标签变体修复：期內/年內、內/内 字形变体（Q7-4）
- Lenovo / net_income：2026 用 ['期内溢利'] → 2027 用 ['期内(虧損)/溢利']。说明：标签变体修复：期內/年內、內/内 字形变体（Q7-4）
- Lenovo / operating_income：2018 用 ['經營(虧損)/溢利', '經營溢利'] → 2019 用 ['經營溢利', '經營溢利/(虧損)']。说明：标签变体修复：經營(虧損)/溢利 与 經營溢利/(虧損) 等排列随盈亏方向变化（Q7-4）
- Lenovo / operating_income：2019 用 ['經營溢利', '經營溢利/(虧損)'] → 2020 用 ['經營溢利']。说明：标签变体修复：經營(虧損)/溢利 与 經營溢利/(虧損) 等排列随盈亏方向变化（Q7-4）

## ② 环比超历年同季度 3σ 的跳变（每条需说明）

（无）

## ③ V1.10②：相邻季度营收变化超 ±50%

（无）

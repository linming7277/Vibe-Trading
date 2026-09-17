# 功能模块盘点审计（2026-09-16，audit-v7 附册）

## 审计视角说明

audit-v5~v7 只审了数据与运行健康（管线/调度/新鲜度/泄漏），功能模块层自 audit-v5 的
DEAD 名单后无人再盘。本册补上：前端 41 页、后端 77 包、导航/路由/数据库业务量四方核对。

## 总判断

系统当前真正活着的是一条链：**通达信采集 → L3 龙头池 → 公司研究/论点 → CIO 报告 + 晨报 → 飞书**（数据新鲜至 09-15/16）。
而规划中的另一条链——**双线引擎 → 信号 → 委员会审批 → 模拟盘**——自 08-12/18 后整体冻结，
且从未走通一次全流程（committees=0、paper_orders=0、信号快照冻结在 08-12）。

## 四张清单

### 1. 前端 41 页分类
- **A 生产使用**（约 15 页）：价值线四页、公司研究、市场行情三页、宏观双页、数据中心、AI 会话/设置、模拟页框架
- **B 深链可达**（约 7 页）：公司财务、行业明细、回测详情、筛选器（无菜单）、基金（无菜单但功能完整）、方法论（无链接指向）
- **C 孤儿**（5 个）：Home.tsx（上游遗留落地页，无路由）、Portfolio.tsx（功能完整但 portfolios=0 行）、
  TradePlans.tsx（无路由——决策链第 5 步断头）、ValueControlCenter.tsx（已被机会与风险取代）、/today（数据冻结 08-12）
- **D 死壳**：ValueStrategy.tsx（3 行，路由 index 立即重定向，永久不可见）
- 9 月以来 41 页中 **33 页从未变更**——开发火力全部集中在价值线 8 页。

### 2. 后端 77 包分类
- **A 活跃**：31 包（价值线全链 + 宏观三件套 + CIO + 调度 + 公司研究栈 + scheduled_research + swarm 活跃非死码）
- **C 内部支撑**：约 30 包（config/providers/session/governance/strategy_store 等；quantlib 被 AlphaZoo bench 消费、
  cycle_profit_scenario/normalized_earnings 被 CIO 消费——均非死码）
- **B 只通管道没有水**：paper_trading（7 个端点全通、4 个种子账户、**0 订单 0 成交**）
- **D 确认死**：fine_tracks（空目录+5 张 0 行表）、shadow_account（仅测试引用）、ValueControlCenter、ValueStrategy 壳
- **建成未上电**：live + trading + live_routes（mandate/kill-switch 全套就绪，唯一未挂载的路由模块）——产品红线，维持

### 3. 功能缺口（按投入产出排序）
| # | 缺口 | 现状 | 成本 |
|---|---|---|---|
| 1 | **双线引擎断供**：引擎无任何调度器，只能页面手动触发；输入实时拉 tdx（采集本身是好的） | 情绪线 08-12 起空转 | 小-中 |
| 4 | **决策链第 5 步断头**：TradePlans 无路由，"形成买卖点"CTA 落到不处理参数的页面 | trade_plans=0 | 小 |
| 7 | **研究报告库停摆**：reports 表冻结 08-18，/ai/reports 展示死库；新 CIO 链不落 reports 表 | 886 条旧记录 | 中 |
| 2 | **信号→委员会→模拟盘闭环未通**： committees=0、paper_orders=0，批准后无人提交模拟单 | API 全在 | 中 |
| 3 | **持仓/组合视图缺失**：个人投资者最需要的"我的公司怎么样了"没有页面 | Portfolio 完整实现却无路由 | 中 |
| 8 | **定时研究仅试点**：1 个 playbook、research_tasks=0 | 昨日刚试点 | 小-中 |
| 11 | /today 双线总览无入口（页面质量不差，但进来就是 8 月旧数据） | 前提=先修缺口 1 | 小 |
| 5/6 | 回测与主线脱钩、Alpha Zoo 462 因子无出口（bench 进程内存态、无因子→策略通路） | 重资产 | 中-大/大 |

### 4. 重复/冲突（10 处）
公司详情三层入口（QuickView→全页→财务子页）；机会中心三胞胎（ValueControlCenter 死/ValueFocusSelection/ValueFocusPage）；
Today vs StrategyWorkspace 同源双展示；宏观双页边界模糊；**组合三概念并存**（Portfolio 记账/paper_trading 模拟/trade_plans 计划）；
委员会新旧两套体系（都是 0 行）；筛选四兄弟互不相通；Compare vs Correlation 交叠；DataCenter vs ValueLineDataRequirements 重复；
23 个文件残留 react-i18next 双语框架（上游遗留）。

## 建议路线（按投入产出）

1. **小成本先修**：决策链第 5 步（TradePlans 路由或撤 CTA）、方法论页加链接、基金页入导航、reports 表接 CIO 落库
2. **引擎调度修复**（小-中）：在 automation 挂引擎触发 + 复用 tdx 快照——前提是先决定情绪线去留
3. **闭环决策**（需老板拍板）：信号→委员会→模拟盘要不要走通；组合三概念合并成"我的持仓"一个视图
4. **删除批**：fine_tracks、Home、ValueControlCenter、ValueStrategy、shadow_account、（或）Portfolio
5. **中期**：回测/因子出口评估

# 2026-09-15 「全球策略」页改版为「宏观总览」+ 跨市场序列 API（二轮：充实内容）

- 日期：2026-09-15
- 性质：前端页面重定位 + 新增只读 API + 二轮按老板反馈充实内容（变化量、走势、国内读数、SHADOW 前瞻）。不改任何研究/调度逻辑，不新增数据源。
- 状态：已实现 + 测试通过 + 17:55 重启后端实测通过。未 commit。

---

## 二轮充实（老板反馈：页面空洞看不出什么）

在环境卡 + 序列卡基础上新增四块实质内容，全部来自既有本地存储，零新增数据源：

1. **环境轴「较前值」变化**：读最近两份 `macro_snapshots`，每轴显示分值 + 与上一份快照的分值差（↑/↓）与状态变化（前值 X）；`axes_trend` 字段。
2. **序列卡升级**：每张卡新增「较前值」变化（带正负色）与 **40 点迷你走势线**（SVG，按观测日排序降采样）；`change` / `prev_value` / `sparkline` 字段。
3. **国内宏观读数表**（新增章节）：CPI、PPI、PMI、M1、M2、社融增量、出口同比、LPR 1Y/5Y、GDP、A股20日宽度、风险偏好共 12 项——最新值、较前值、数据日期、新鲜度徽标；`domestic_series` 字段。
4. **下一交易日前瞻卡**（新增章节）：最新 `macro_market_forecasts` 记录——方向中文（复用 `DIRECTION_CN`：震荡/偏强/偏弱/暂不判断）、目标交易日、**影子运行标记**、以及已渲染的 boss 版 `narrative_md`（react-markdown + remark-gfm 渲染，明确注明"影子模式仅供参考"）；`forecast` 字段。

## 页面结构（/global，导航组「宏观」）

1. 宏观环境卡（regime + 五轴含变化量）
2. 下一交易日前瞻（SHADOW，方向 + 完整叙述）
3. 跨市场关键序列 7 张卡（值 + 变化 + 走势 + 新鲜度）
4. 国内宏观读数表 12 项
5. 港美股市场快照（原内容保留为次级章节）
6. 数据边界说明卡

导航：组「全球策略」→「宏观」；子项「全球全景」→「宏观总览」；子项「宏观环境」（/macro）不变。

## 后端

- `GET /api/value/macro-overview`（只读，require_auth）：`src/value_strategy/macro_overview.py`——
  - 投影：复用 `get_macro_sector_projection`；
  - 序列：`CrossMarketStore.read_rows` 全历史 → 最新值/前值/变化/降采样走势；
  - 轴趋势与前瞻：对 research.db 只读 SQL（`macro_snapshots` 最近 2 份、`macro_market_forecasts` 最新 1 条），方向中文复用 `macro_forecast.engine.DIRECTION_CN`；
  - 中文标签优先取 `MACRO_SERIES_CATALOG` 冻结 `name_zh`，跨市场序列用页面级覆盖表；未注册新鲜度规则的序列（shibor）按 3 日历日兜底；缺失显式 MISSING。

## 实测（17:55，重启后）

五轴趋势齐全；前瞻 2026-09-16 SHADOW「震荡」+ 1080 字叙述；跨市场 7 序列全部带 40 点走势与变化（美债 +0.01/+0.02、WTI +3.05）；国内 12 项读数（8 月值按官方节奏，部分按注册规则标 STALE 属正常披露滞后）；SHIBOR 09-11 STALE lag=4 如实暴露。

## 测试

- 后端 `agent/tests/test_macro_overview.py` 5 个：组合+趋势+前瞻透传、无前瞻缺省、STALE 滞后、禁词、注册表标签一致性——sqlite 读侧全部 patch 隔离；`test_macro_sector_projection.py` 11 个无回归；ruff 干净。
- 前端 vitest：Layout 29 个全过（导航改名已同步）；416 passed / 2 failed 为本次改动前就存在的遗留（ValueLeaderMethodology 链接 /value→/value/focus、SourceReferenceCard WATCH 文案），与改版无关。`npm run build` 通过。

## 未动 / 后续候选

- /macro 宏观环境页未动；价值线/调度/推送链路未动。
- 后续：SHIBOR 抓取接进 07:35 调度（当前 STALE 的根因）；`policy_sectors` 目前投影为空，若后续有数据页面可直接展示。



---

## 三轮（2026-09-16）：前瞻卡换成「最近 3 天要闻」

- 老板决定：宏观总览页不放预测，改为浏览最近的公开快讯。
- 移除页面上的「下一交易日前瞻」章节（预测引擎本身照常每晚生成，数据仍在库，后续可按需回加）。
- 新增 `GET /macro/recent-news?days=3`：读 `morning_flash_items`，取最近 N 个自然日（发布时间优先、抓取时间兜底归日）；质量规则与 08:00 卡一致（去推广/软文/纯日历占位、同事件跨天只留最新一条）；按国内/海外分区（强制词+海外词规则），每组上限 40 条，时间倒序。
- 页面：按日分块（今天/昨天/前天 + 星期），国内/海外两栏，条目带时间戳、点击跳原文；空日显示"暂无快讯"。
- 实现：`morning_macro_brief.recent_flash_news()`（复用卡片的质量过滤与分区规则，窗口解耦）；路由在 `research_routes.py`。
- 测试：`test_morning_flash_cache.py` 新增 3 个（分组/分区/窗口、过滤与跨天去重、分组上限），11 个全过；`npm run build` 通过。
- 实测（09-16 10:13）：3 天 89 条（09-16 40+2、09-15 40+7、09-14 空——收集器 09-15 才上线，随天数自然填满）。


---

## 四轮（2026-09-16）：要闻区新增 LLM 简要分析

- 老板反馈：新闻有点乱，加一段 AI 简要分析。这是项目的**受控 LLM 点位**（AGENTS.md 铁律 5），与 CIO 综合同级管理。
- 新模块 `investment_research_supervisor/news_analysis.py`，三原则：
  1. **缓存优先、页面不阻塞**：分析按内容指纹缓存（research.db `morning_news_analysis` 表），6 小时内复用；过期由页面请求触发后台线程重生成——首个请求拿 `generating`，刷新可见；
  2. **fail-closed**：输出先过交易用语禁词审查（与 CIO 同语义），不合格即丢弃不落库；分析不可用时整块隐藏，新闻本体不受影响；
  3. **明确标注**：卡片固定带「AI 生成 · 仅供参考」。
- 模型配置复用 `research_lead` 角色（与 CIO 综合同源，研究员设置页可调），phase=`MACRO_NEWS_ANALYSIS`，超时 120s。
- 提示词约束：3-6 条要点、150-300 字、先国内后海外、只概括标题真实信息、禁交易表述与方向预测、全部中文。
- 路由：`/macro/recent-news` 响应新增 `analysis` 字段（ready/generating）。
- 测试：`test_news_analysis.py` 7 个（指纹、6 小时缓存窗、ready 存储、禁词丢弃、模型失败、缓存命中不派发、后台派发与进行中去重），全过；`npm run build` 通过。
- 实测（10:34，真实模型）：generating → 45 秒后 ready，321 字 6 条要点，禁词零命中，内容与快讯标题逐一对应。


---

## 五轮（2026-09-16）：吸附式目录推广到价值投资全部页面

- 宏观总览页的内联目录条抽取为共享组件 `components/workspace/WorkspaceUI.tsx` 的 `PageToc`（含「本页目录」标签、主色描边、平滑滚动），宏观总览页改为复用。
- 价值投资四个多区块页面接入：机会与风险（数据总览/最近变化/重点研究/继续观察/暂缓优先/日终任务）、行业龙头（筛选说明/行业候选列表/指标词典）、方法论（全流程/逐步说明/怎样使用）、低估龙头池（今日变化/低估龙头列表）。
- 未接入：持续研究、公司研究、财务分析（单列表/工作台结构，无多区块可跳转，加目录反而多余）。
- 验证：`npm run build` 通过；价值相关页面测试 33 过 / 1 失败为既有遗留（ValueLeaderMethodology 链接断言）。

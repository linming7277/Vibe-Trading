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


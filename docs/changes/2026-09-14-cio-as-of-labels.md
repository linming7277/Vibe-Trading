# 2026-09-14 公司研究页展示 CIO 报告「价格截至 / 叙述截至」

- 日期：2026-09-14
- 性质：前端只读展示。后端零改动（`price_as_of` / `narrative_as_of` 由同日「读路径 PRICE 补刷」已在 `get_report` 返回中提供）；未改 Focus A=10、价值线、早盘文案、position_label 六句、发送闸。
- 约束遵守：未 commit/push、未发飞书、未开 CIO worker、打开页面不触发 LLM（GET 只读 + 后端 refresh_cio_block 为确定性路径）。

---

## 改动文件

| 文件 | 改动 |
|---|---|
| `frontend/src/lib/api.ts` | 新增 `CioReportSummary` 类型（`price_as_of? / narrative_as_of? / research_as_of?` 等可选字段）+ `api.getCioReport(stockCode, asOf?)` → `GET /api/research/cio/{code}` |
| `frontend/src/components/value/CioReportAsOfCard.tsx`（新增） | 取 CIO 报告摘要，渲染固定原文两行：「价格截至 YYYY-MM-DD」「叙述截至 YYYY-MM-DD」；字段缺失不渲染对应行；请求失败显示「CIO 报告暂时读取失败。」；无任何报告显示「暂无 CIO 报告存档。」 |
| `frontend/src/pages/CompanyResearch.tsx` | 「估值」页签价格区结论卡（`ValuePriceZoneConclusionCard`）后挂载 `<CioReportAsOfCard stockCode={stockCode} />` |

渲染规则：后端字段**逐字使用**；`price_as_of` 缺失就不出现「价格截至」行，`narrative_as_of` 缺失就不出现「叙述截至」行——两行独立，不合并成「已更新」。

## 数据来源

- `GET /api/research/cio/{stock_code}`（`agent/src/api/cio_report_routes.py:21` → `cio_report/service.py:45` `get_report`）。
- `price_as_of` = PRICE 块 `close_as_of`（打开报告时的确定性补刷日期，缺省回落 valuation `as_of`）；
- `narrative_as_of` = 报告行 `created_at` 日（全文叙述最后落库日）。

## 测试

- `npx vitest run src/components/value/__tests__/CioReportAsOfCard.test.tsx` → **4 passed**：
  - a. 两字段都有 → 出现「价格截至 2026-09-11」「叙述截至 2026-09-07」；
  - b. 字段缺失 → 不出现「价格截至」/「叙述截至」（不编造）；
  - （附）请求失败 → 「暂时读取失败」且不编日期；
  - c. 正文无买入/卖出/加仓/减仓/止盈/止损。
- `npx tsc --noEmit` → 通过（无类型错误）。

## 没改什么

后端全部公式与接口（`get_report` 的 PRICE 补刷为此前已交付逻辑，本任务只消费）、Focus A=10、价值线入池、`position_label` 六句、早盘文案与开关、发送闸。未 commit/push、未发飞书、未开 CIO worker。

## 风险

1. 页面打开会对该股触发一次后端 PRICE 块确定性检查（当日首次且价格变化时写一行新版本报告）——无 LLM、幂等；高频打开不放大写入。
2. `narrative_as_of` 是全文存档日：PRICE 块当日刷新后叙述可能仍是旧基准日——两行日期分开展示正是为了让这一点可见。

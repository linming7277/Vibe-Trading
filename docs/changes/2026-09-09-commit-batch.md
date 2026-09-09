# 2026-09-09 提交批次记录（commit batch）

- 日期：2026-09-09
- 结论：**无可拆分的未提交改动**。执行 `git status --short --branch` 与 `git diff --stat` 时工作树为空（clean）；四个主题的改动**已被更早的三个批量提交（信息均为「1」）入库**，且 `main` 与 `origin/main` 完全同步（0/0，已推送）。
- 因此本任务按约定**不重写已推送历史**（重拆需 rebase/reword + force push），以下为「要求主题 → 实际入库提交」的映射记录。

---

## 一、要求主题 → 实际提交映射

### 1. fix(brief): bitable 空源拒绝删表
- 实际提交：**`d6c97c02`**（信息为「1」）
- 文件：`agent/src/investment_research_supervisor/daily_brief_bitable_service.py`、`agent/tests/test_bitable_empty_source.py`、`docs/changes/2026-09-09-bitable-empty-source.md`
- 与要求主题**完全一致**（仅提交信息不符合中文主题格式）。

### 2. fix(value): 价格落点区分低于低估带与未落入
- 实际提交：**`b09a3bf6`**（「1」）中的相应文件：
  - `agent/src/value_price_zones/service.py`
  - `agent/src/investment_research_supervisor/daily_brief_service.py`（position_label 相关部分）
  - `agent/tests/test_price_position_labels.py`、`agent/tests/test_investment_research_daily_brief.py`（落点断言）
  - `docs/changes/2026-09-09-price-position-labels.md`
- ⚠️ 该提交同时混入了第 3 主题（前端 api.ts / ValuePriceZoneConclusionCard / 其测试 / price-position-label-ui 文档）与**非本任务改动**（ValueFocusPage / ValueFocusSelection / router / ValueLeaderMethodology / Layout.test / focus-a-extract 文档）。

### 3. fix(ui): 公司研究页直显 position_label
- 实际提交：**`b09a3bf6`**（同上，前端部分文件）：
  - `frontend/src/lib/api.ts`、`frontend/src/components/value/ValuePriceZoneConclusionCard.tsx`、`frontend/src/components/value/__tests__/ValuePriceZoneConclusionCard.test.tsx`、`docs/changes/2026-09-09-price-position-label-ui.md`

### 4. feat(brief): 涨停定增事件入表并按成交额展示
- 实际提交：**`253b7486`**（「1」）：
  - `agent/src/value_strategy/market_events.py`、`agent/src/tdx_data/day_file.py`（read_lday_tail）
  - `agent/src/value_workspace/automation.py`（MARKET_EVENTS_READY）
  - `agent/src/investment_research_supervisor/daily_brief_service.py`（事件合并）
  - `agent/tests/test_market_events_p1.py`
  - `docs/changes/2026-09-09-limitup-placement-events.md`、`docs/changes/2026-09-09-limitup-event-sort.md`
- ⚠️ 混入非本主题：`frontend/src/pages/ValueFocusPage.tsx` 及其测试改动。

## 二、未纳入提交的文件 / 遗留

- 当前工作树 clean，无未提交源码。
- **遗留问题 1**：`tmp/ensure_cio_0908.py` 被误提交进 `253b7486`（tmp 目录脚本不应入库）。已在 .gitignore 覆盖 tmp 之前入库，属历史遗留；建议后续加 `tmp/` 忽略规则并从工作区删除该文件（不改本次历史）。
- **遗留问题 2**：`b09a3bf6` 主题混杂（价值落点 + 前端 UI + Focus 页 + 无关文档），回溯定位需按文件路径检索；后续提交请按主题拆分并使用中文主题行。
- 本记录文件 `docs/changes/2026-09-09-commit-batch.md` 本身**有意不提交**（docs/* 默认忽略，未加白名单）。

## 三、没改什么

- 本次仅做只读核查与本文档，**未改任何业务逻辑**、未重写/未 reword 任何已推送提交、未 push、未注册 live_routes、未触碰 Focus/低估池/L3/价格区。

## 四、测试（提交前状态）

四个主题各自的测试在早前执行且全绿（详情见对应文档）：
- bitable 空源：`tests/test_bitable_empty_source.py` 4 passed
- 价格落点：`tests/test_price_position_labels.py` 11 passed、日报套件 32/32（含已知秒级 flake 单跑即绿）
- 市场事件：`tests/test_market_events_p1.py` 10 passed、09-08 真数抽查涨停 74 / 定增 0

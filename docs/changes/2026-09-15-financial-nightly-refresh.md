# 2026-09-15 专业财务全量采集进调度（financial_nightly）

- 日期：2026-09-15
- 性质：新增 tdx 刷新模块 + 调度槽位 + 指纹跳过门。修 AUDIT V5 Blocker #4 前半（财务全量停 08-24）。不改数据语义、不改 value_line 手动刷新、不改下游任何消费方。
- 状态：已实现 + 测试通过；**已随 15:15 backend 重启生效**（同批加载早盘快讯收集器与 CIO 游标修复，即 AUDIT V5 的 S1）。未 commit。

---

## 根因（为什么停在 08-24）

- `financial_history`（PIT 专业财务，150,267 行 / 5,543 只）全市场采集**从未进任何调度**，只能从数据需求页/`/strategy/value/refresh` 手动触发。
- 08-24 那次手动全量成功（09:16→09:57，约 41 分钟）；08-19 两次尝试均因服务重启被判 `service_restarted_before_completion` 而作废（全量采集只在最后一次性原子替换，中途死=全丢）。
- 此后再无人手动触发。EOD 链 `FINANCIAL_READY` 只对 Focus 池做增量 `prepare`（`collect_incremental` 单只 upsert），覆盖不了全市场。
- 数据源本身是新鲜的：gpcw 包今早 06:40 刚被通达信客户端更新（指纹已从 `f891be51…` 变为 `d7093f9a…`），库内缓存 max announcement_date 停在 2026-08-31。

## 改动

### 1. `agent/src/tdx_data/service.py`
- `MODULES` 新增 `financial_history`（专业财务历史）→ `/tdx/status`、数据中心页自动出现该模块卡片，可手动更新；`REFRESH_PROFILES["all"]` 应急全量也包含它。
- `REFRESH_PROFILES` 新增 `financial_nightly = ("financial_history",)`；`MODULE_DATASETS["financial_history"] = ("financial_history",)`。
- 新采集器 `_collect_financial_history`：包状态就绪校验 → 指纹跳过门 → securities 全量 universe（回退 get_stock_list）→ `FinancialHistoryService.collect()`（85% 覆盖门槛 + 末尾原子替换）。在 `_run_job` 的 `snapshot_context` 内运行，150k 行先写暂存区，`publish_snapshot` 原子提升——**采集中读者始终看到上次完整缓存**。
- `_financial_skip_recollect`：仅当「当前包指纹 == 缓存行内 raw_version」且「上次成功在 7 天内」（`FINANCIAL_RECOLLECT_MAX_AGE`）才跳过；指纹缺失/包变化/无成功状态/超 7 天一律重采。专业财务行只随包重下而变化，指纹相等即重采必得同行；7 天下限兜底指纹误判的最坏情况。
- `_module_coverage` 对 financial_history 返回真实 coverage（供 dataset_snapshots 记录）。

### 2. `agent/src/tdx_data/automation.py`
- `due_profiles` 新增工作日 **21:30** → `["financial_nightly"]`。时点依据：20:30 history_nightly（约 1 分钟）与周一 20:45 fundamental_weekly（约 3 分钟）释放桥之后；远离 16:45 价值线 EOD 链；不与早盘收集器（纯 HTTP）竞争。全量约 40 分钟（5,571 只 ÷ 100/批 × ~44 秒）。

### 3. `agent/src/tdx_data/financial_history.py`
- 新增 `cached_raw_version()`：读缓存最新行 payload 的 raw_version（而非 module_state 元数据），作为跳过门的对照值——value_line 手动刷新与 tdx 夜更都写同一数据集，行本身才是真源。

## 失败语义（全部沿用既有机制）

- 采集失败/覆盖不足 → 模块 failed、**保留上次成功缓存**（"更新失败，已保留上次成功缓存"）。
- 调度器 `_eligible`：failed/partial 10 分钟后重试，当日最多 3 次；completed 当日不重跑。
- 进程重启杀掉运行中任务 → 与现状一致由重启对账判 `service_restarted_before_completion`；**区别是下一晚自动补上**，不再依赖人记得点按钮。

## 与 value_line 手动刷新的关系

- `/strategy/value/refresh`（数据需求页）路径不动，仍可手动触发全量（无指纹门）。
- 两条路径写同一 module_state 与同一数据集；夜更引入后手动路径退居应急。

## 测试

`agent/tests/test_tdx_refresh_pipeline.py` 新增 6 个：
- 槽位归属与 all-profile 包含；
- due_profiles 矩阵（21:30 financial_nightly；20:30/20:45 原槽位不变；周六不采）；
- 调度器 21:30 启动 + 当日 completed 抑制重跑；
- 跳过门五分支矩阵（相等+新鲜=跳过；包变/空指纹/无状态/超 7 天=采）；
- 采集器跳过路径（不触桥、不 collect）与变化路径（seeds securities、coverage 透传 `_module_coverage`）。

结果：`test_tdx_refresh_pipeline.py` 13 passed；`test_tdx_data_service.py` 28 passed（无回归）。

## 上线与观察

- **15:15 已重启 backend 生效**（`/tdx/status` 已含 financial_history 模块与 financial_nightly 计划；启动器 restart 在本会话内挂起，改用与 ServiceHost 等价的 detached 启动并补写 `.launcher/backend.json`）。
- 首次全量：今晚 21:30（包指纹今晨 `d7093f9a…` ≠ 缓存 raw_version `f891be51…` → 必然全量，约 40 分钟）；数据中心「专业财务历史」卡片可见进度。
- 次日起包未变 → 跳过（message「专业财务包未变化」），包更新（披露季通常每晚）→ 全量。
- 明早验证点：`/tdx/status` 中 financial_history last_success_at 为昨晚；库内 max announcement_date 前移；EOD 链 CIO_FINANCIAL_BLOCKS 输入更新。
- 15:17 复核：快讯收集器已在新进程入库（morning_flash_items 50→100 行）。
- 未动：Focus A=10、v3 席位、value_line 手动路径、collect_incremental、`check_finance_source_freshness`、MCP 只读闸。

# 2026-09-11 CIO 报告「分块按数据新鲜度更新」第一期：PRICE 块

- 日期：2026-09-11
- 范围：新增 `cio_report/blocks.py`（块契约）、`CioReportService.refresh_cio_block / refresh_cio_price_blocks`、价值 EOD 挂钩、测试。**未改** Focus A=10、入池规则、风险 UNKNOWN 口径、全文 synthesis 路径。
- 约束遵守：未 commit/push、未发飞书、未整池重跑 LLM（PRICE 刷新 0 次 LLM 调用）、未开早盘开关。

---

## 池范围与只数（确认结论）

- 代码里的龙头池 = **`l3_leader_pool` 最新 COMPLETED as_of 的成员**（`level3_leaders/store.py:239` `current_pool`）。当前最新池 as_of=**2026-09-04**，成员 **676 只**。
- CIO 现有 A/B/C 档（`service.py:389` `ensure_focus_tier_reports`）只覆盖 Focus A(10)/B/C；**PRICE 块批量刷新的 universe 按任务书取龙头池 676 只**，与 A/B/C 档互不影响。
- 注意：低估池（company_low_value_leader_pool，181 只）是另一条链，不在本刷新范围。

## 块契约（代码常量，`cio_report/blocks.py`）

```python
CIO_BLOCK_CONTRACT = {
  "PRICE":     {"phase": 1, "section_types": ("valuation",),
                "input_keys": ("code","close_as_of","close","zone_low","zone_high","position_label"),
                "hash_column": "valuation_hash",
                "source": "ValuePriceZoneService.get_price_zones（确定性，无 LLM）"},
  "FINANCIAL": {"phase": 0, "section_types": ("financial_path","latest_quarter","normalized_earnings"), ...},
  "BUSINESS":  {"phase": 0, ...}, "RISK": {"phase": 0, ...}, "THESIS": {"phase": 0, ...},
}
```

- 块指纹 = `sha256(规范 JSON{block, code, *input_keys})[:16]`（`block_fingerprint`）。
- 指纹存放：对应节 `structured_payload["block_fingerprints"][block_id]`（PRICE 同时写报告行 `valuation_hash` 列）。未登记视为过期，首次刷新补登记。
- 过期判定：新算指纹 ≠ 存档指纹 → 刷新；相同 → `REUSED` 零写入。

## 新增入口

- `refresh_cio_block(market, code, block_id, as_of=None)`：
  - PRICE：确定性重建 `valuation` 节（`CioSectionBuilder.build_valuation`，纯读），payload 附加 `position_label / zone_low / zone_high / close_as_of / block_fingerprints`；
  - 只替换该节，**其它节原样保留上一份存档、`narrative_report_md` 全文存档保留、不调 synthesis LLM**；
  - 新报告行 `research_as_of=刷新日`，`previous_report_id` 链式指向上一份；
  - 无上一份报告：PRICE 节照写，其余节标 `MISSING` 占位，narrative 用确定性模板拼接（`BLOCK_ONLY_TEMPLATE`），**不趁机全文 LLM**；
  - 未启用块（phase=0）返回 `NOT_ENABLED`。
- `refresh_cio_price_blocks(as_of=None, universe="leader_pool")`：逐只调上者，fail-soft 单只隔离，返回 built/reused/failed。

## EOD 挂钩

`value_workspace/automation.py`：`CIO_PRICE_BLOCKS_READY` 阶段（`built刷+reused复用/count`），置于行情/价格区 READY 之后、`CIO_FOCUS_TIER` 全文任务之前，fail-soft。

## 003012.SZ 真机结果（as_of=2026-09-11）

| | 旧（09-07 存档） | 新（PRICE 刷新后） |
|---|---|---|
| research_as_of | 2026-09-07 | 2026-09-11 |
| valuation 现价 | 4.71 | **4.57** |
| position_label | （无此字段） | **低于低估关注区** |
| zone_low / zone_high | （无） | **4.55 / 4.69** |
| valuation 节指纹 | b143399c9017 | ba49f7628ec1 |
| 其余 16 节正文 | 09-07 | **逐字保持 09-07** |
| narrative_report_md 全文 | 09-07 存档 | **原样保留** |
| previous_report_id | — | **49（链回旧报告）** ✓ |

刷新返回 `REFRESHED / fingerprint=c024f8e461ed1356 / report_id=286 / previous_report_id=49`。

## 测试

`pytest agent/tests/test_cio_price_block.py -q` → **4 passed**：
- a. 只改 close → valuation 节变、business 节正文与全文存档逐字保持、previous_report_id 链保留；
- b. 指纹相同再跑 → `REUSED`，报告行数不变（0 次无意义写入）；
- c. 3 只池夹具批量 → 全部 REFRESHED；
- d. 源码检查：两个 refresh 函数体内无 `chatllm / analyze( / business_research / _synthesize / run_macro_forecast / invoke`。

既有 CIO 测试（report/incremental_synthesis/quick_brief/narrative/routing）全绿。`test_cio_block_worker.py` 4 个失败为**另一并行会话的未完成半成品**（untracked `block_worker.py` + `store.py` 的 `cio_block_jobs` 队列方法，失败于其自身的 `dict(sqlite3.Row)` 用法），与本实现无关、互不冲突；未代为修改。

## 顺带修复（重要）

发现工作区 `agent/src/cio_report/service.py` 已被历史事故整体覆盖为 `strategy_engines/macro_data.py` 的副本（导致 `import src.cio_report.service` 即 `ModuleNotFoundError: src.cio_report.common`，backend 一旦重启 CIO 路由即挂）。已用 git HEAD 版本恢复并验证 import 与 `ensure_focus_tier_reports` 可用；全 src 扫描确认副本污染仅此一处。

## 没改什么

Focus A=10 与 A/B/C 档策略、价值线入池规则、价格带公式（`value_price_zones` 未动）、风险 UNKNOWN 口径、全文 synthesis 路径、`load_trading_days`、早盘开关（off）。未 commit/push、未发飞书、未重启 backend。

## 风险

1. PRICE 块刷新后 `narrative_report_md` 全文仍是旧存档（按规格保留）——老板若读全文叙述，价格段是旧的；结构化节（payload/页面取数）是新的。第二期可考虑只对价格段做确定性模板替换。
2. 龙头池 676 只每晚 EOD 各刷一次 PRICE 块 = 每晚最多 676 行新报告（指纹不变则 REUSED 零写入；价格变动日按变动只数写）。
3. 与并行会话的 block_worker 半成品存在概念重叠（同在打"分块"），后续需二选一收敛，避免双轨。
4. backend 未重启，EOD 新阶段今晚 16:45 由运行中进程执行时**不会**包含 `CIO_PRICE_BLOCKS_READY`（旧代码无此阶段）；重启后生效。

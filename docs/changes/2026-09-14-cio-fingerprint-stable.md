# 2026-09-14 CIO 指纹稳定化核验（同日同数据不再全文重建）

## 结论
**未改代码。** 指纹稳定性由工作区既有修复承担并实测通过：
- `cio_report/builder.py` `_fingerprint_safe` + `_FP_VOLATILE_KEY_RE`
  （quality fix §9）：节指纹哈希前剔除 `_?as_of` / `_?updated_at` / `data_dates`
  等镜像研究时钟的字段（build_valuation payload 的 `as_of` 因此不入哈希）。
- `cio_report/blocks.py` `block_fingerprint`：PRICE 契约只哈希
  `code / close_as_of / close / zone_low / zone_high / position_label`
  （input_keys 白名单），`price_block_inputs` 只产这些字段。
- `cio_report/builder.py` / `blocks.py` 全文 **无 datetime.now / time.time /
  utc_now**（grep 零匹配）。

## 实测验证（真库，2026-09-11 基准日）
1. 同股连续两次 `build_all_sections`：19 节 `input_fingerprint` **零漂移**。
2. `build_report` 同日两连发：两次 `idempotent_reuse=True`，报告行 **+0**。
3. `ensure_focus_tier_reports` A 档两连发：第一轮 A 建 0/复用 10，
   第二轮 A 建 0/复用 10，报告行 **+0**（不再出现 116 轮空转）。
4. `refresh_cio_block` PRICE 两连发：两次 **REUSED**，cio_block_jobs **+0**。
5. 价格真变 → REFRESHED：既有测试 `test_price_changes_but_business_kept` 覆盖。

## 测试
- `pytest tests/test_cio_price_block.py tests/test_cio_default_as_of.py` → 6 passed
  （含 TDX 未就绪 + as_of 空 → build/ensure/batch/block 四入口全部
  RESEARCH_DATE_UNAVAILABLE 且**报告 0 写入**）。
- `pytest tests/test_cio_incremental_synthesis.py` → 6 passed。
- 真库核验：见上。

## 未改什么
未改价值线筛选、Focus A=10、低估值池入池规则、价格带计算、
fiscal_year_flows、MCP、bitable、live_routes；未 commit、未发飞书、
未开 worker、未开重综合、未对 A 档 10 家跑任何 LLM。

## 风险
1. `plain_summary`（ zones 的自由文本）仍参与哈希——若上游模板改写措辞
   会触发该节重建（数据未变时属于可接受的重建，非空转）。
2. 18–23 时的 116 轮空转发生在本轮核验之前；当时进程内存里的代码版本
   无法回溯审计。当前磁盘版本已实测稳定。

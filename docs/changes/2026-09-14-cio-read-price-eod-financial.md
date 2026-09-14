# 2026-09-14 CIO 报告：打开先补 PRICE 块 + EOD 刷 FINANCIAL 块

- 日期：2026-09-14
- 性质：读路径增强 + EOD 财务块阶段 + 测试。未改 Focus A=10、价值线入池、早盘文案、发送开关。未 commit、未发飞书。
- 前置：PRICE/FINANCIAL 块契约见 `cio_report/blocks.py`；`refresh_cio_block` 语义见同日 cio-price-block / cio-block-worker 文档。

---

## 一、打开报告：先补 PRICE

**读入口**：`agent/src/api/cio_report_routes.py:21` `GET /api/research/cio/{stock_code}` → `agent/src/cio_report/service.py:45` `CioReportService.get_report(market, stock_code, as_of)`。

新行为（`as_of` 未显式指定＝"最新"读取时）：
1. `_default_research_as_of()` 取 TDX 最新合格收盘日；**拿不到 → 不写库，直接返回旧存档**（fail-closed，绝不落宏观序列日/08-19）；
2. 就绪 → 调 `refresh_cio_block(..., "PRICE")`：指纹变了才写（确定性重建 valuation 节 + 块指纹登记），没变 → REUSED 零写入；**路径上无 ChatLLM/analyze/全文 build_report**；
3. 返回前附加两个日期字段：`price_as_of`（PRICE 块 `close_as_of`，缺省回落 valuation `as_of`）、`narrative_as_of`（全文存档所属日期 = 报告行 created_at 日）。
4. 显式传历史 `as_of` 的读取 → 不刷新，纯读存档（PIT 语义不变）。

## 二、新财报：EOD 刷 FINANCIAL

- **新阶段名**：`CIO_FINANCIAL_BLOCKS_READY`（`value_workspace/automation.py`，置于 `FINANCIAL_READY` 之后），显示 `N处理/M队/池只数`。
- 入口：`block_worker.py` 新增 `refresh_financial_blocks(as_of, store)`——同步 enqueue + 立即消费（`CioBlockWorker.process_queued`），**不需要 worker 线程**（`HZ_CIO_BLOCK_WORKER` 保持 off 也是效）。
- 指纹对比：财务指纹 = 财务快照 `historical_cutoff@updated_at`；`latest_block_fingerprint` 相同 → 0 入队 0 写入。
- 消费语义：确定性重建 `financial_path`/`latest_quarter` 节，`keep_narrative=True` → 叙述标 **STALE** 保留原文，零 LLM。
- TDX 未就绪 → `TDX_NOT_READY`，0 写库。
- PRICE 不入 worker 队列（EOD 由 `CIO_PRICE_BLOCKS_READY` 直接刷，已有）；enqueue FINANCIAL 保留（worker on 时消费同一队列）。

## 003012 验证（真机一次，读路径）

```
research_as_of: 2026-09-11 | price_as_of: 2026-09-11 | narrative_as_of: 2026-09-11
valuation 现价 4.57 | position_label 低于低估关注区 | zone 4.55–4.69
PRICE 块指纹 c024f8e461ed1356（与登记一致 → REUSED，零新行）
business 节正文保持 ✓
```

## 测试

- `pytest tests/test_cio_read_price_financial.py -q` → **6 passed**：
  - a. 现价变 → 返回新价、业务节原文不变、全文存档保持、previous_report_id 链；
  - b. 块指纹已登记且现价没变 → REUSED，报告行数不增加；
  - c. TDX 未就绪 → 零写入、返回旧报告；
  - d. 新报告期 → 财务节 `historical_cutoff` 更新、freshness=STALE、叙述保留、零 LLM；
  - e. 同期次再跑 → 0 入队、0 写入；
  - f. 池外票不刷（universe 过滤）。
- 回归：`test_cio_block_worker.py(7) + test_cio_price_block.py(4) + test_cio_default_as_of.py(2) + test_cio_read_price_financial.py(6)` → **19 passed**。

## 没改什么

Focus A=10、价值线入池规则、早盘文案与判断规则、发送闸、`HZ_MORNING_MACRO=on`（保持）、`HZ_CIO_BLOCK_WORKER`（保持 off，EOD 财务刷新不依赖 worker 线程）、收盘日报模板与 hermes 通道。未 commit/push、未发飞书。

## 风险

1. 读路径 PRICE 补刷在**每次打开报告**时可能触发一次确定性写（当日首次且价格变化）——写入量与打开频率无关（当日第二次起指纹命中 REUSED）。
2. `get_report` 现在依赖 TDX 服务可用性：不可用时返回旧存档（fail-soft），页面看到的可能是昨日价格——已通过 `price_as_of` 字段如实标注。
3. FINANCIAL 块的"确定性节重建"在快照缺失时输出 gap 节并标 STALE——诚实降级，全文重建前老板看到的财务叙述可能滞后。
4. EOD `CIO_FINANCIAL_BLOCKS_READY` 今晚首次真实运行；若财务快照 `historical_cutoff` 粒度问题导致全池"报告期更新"误判，写入量会偏大（仍为零 LLM、确定性），需观察今晚 stage 值。

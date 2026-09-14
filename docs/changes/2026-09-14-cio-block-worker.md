# 2026-09-14 CIO 分块后台 Worker V1（收编半成品，PRICE/FINANCIAL）

- 日期：2026-09-14
- 性质：收编既有半成品 `block_worker.py` + `cio_block_jobs` 队列（**没有再写第二套**），补 TDX 未就绪闸、PRICE 消费统一到 `refresh_cio_block`、EOD 去双写。backend 保持运行但 **worker 默认 off**（`HZ_CIO_BLOCK_WORKER` 未设，本任务未打开、未 commit、未发飞书）。

---

## 已有与收编（文件:行）

| 组件 | 位置 | 处置 |
|---|---|---|
| `block_worker.py:31` `pool_universe` | 最新 l3_leader_pool 在席成员 | 原样收编 |
| `block_worker.py:51/70` 价格/财务指纹 | 廉价聚合查询 | 原样收编 |
| `block_worker.py:91` `enqueue_eod_block_jobs` | EOD 入队 | **改造**：加 TDX 合格收盘闸；移除 PRICE 入队（见下） |
| `block_worker.py:119` `CioBlockWorker` | 30 秒单线程消费 QUEUED，单只失败继续 | PRICE 消费改为调 `service.refresh_cio_block` |
| `block_worker.py:189` `start_cio_block_worker` | env 闸默认 off | 原样（实测 `started: False`） |
| `store.py` `cio_block_jobs` 队列方法 | enqueue 去重（QUEUED/SUCCESS 按 code+block+fingerprint）/latest/queued/finish | 原样收编 |
| `service.py` `refresh_cio_block` / `refresh_cio_price_blocks` | 本会话早前实现 | PRICE 消费统一入口 |

## 关键设计

1. **去双写（PRICE）**：EOD 主路径 = `refresh_cio_price_blocks` 直接刷（确定性、块指纹幂等）；`enqueue_eod_block_jobs` **不再入队 PRICE**（PRICE 入队仅保留 `enqueue_price_job` 给非 EOD 场景显式调用，如盘后补数）。worker 消化 PRICE 时也走同一个 `refresh_cio_block`——指纹相同即 REUSED 零写入，两条路径天然不重复写报告。
2. **TDX 未就绪闸**：`enqueue_eod_block_jobs` 入队前先取 `_qualified_close_date()`（TDX 最新合格收盘日）；拿不到 → `logger.error` + 返回 `TDX_NOT_READY`，**0 入队、0 写库**，绝不发明日期。`as_of` 缺省时用该收盘日。
3. **FINANCIAL**：财务指纹 = 财务快照 `historical_cutoff@updated_at`；仅当报告期比已处理指纹**更新**才入队（旧期次不入）。消费为确定性重建 `financial_path`/`latest_quarter` 节、叙述标 **STALE**（`keep_narrative=True`），零 LLM。
4. **范围**：universe = 最新 l3_leader_pool 在席成员（当前 **683 只**）；池外丢弃。
5. **启动**：`api_server.py:194` 调 `start_cio_block_worker()`，env 未设实测 `{'started': False}`；启动不做任何全池扫描/全文生成。

## 真机验证（003012.SZ，一次 PRICE）

- 003012 在最新池内 → `enqueue_price_job` QUEUED → worker 消费 1 条 → 指纹与已登记一致 → **报告行 2 → 2（零新写）**。
- 未对 676/683 只真跑任何 LLM。

## 测试

`pytest tests/test_cio_block_worker.py tests/test_cio_price_block.py tests/test_cio_default_as_of.py -q` → **13 passed**：
- a. `_qualified_close_date=None` → `TDX_NOT_READY`、0 入队、`cio_block_jobs` 0 行；
- b. PRICE 任务消费 → valuation 更新（现价/position_label/块指纹登记）、financial_path 节保持、previous_report_id 链保留；
- c. 指纹重复 → `DUPLICATE` 不入队（0 新任务）；
- d. FINANCIAL 新报告期入队、同期次/旧期次不入队；
- e. 池外票不入队；
- f. 默认 env → worker 线程不创建；
- g. worker 模块源码无 `ChatLLM/_synthesize/analyze(/run_macro_forecast/build_report`。

## 没改什么

Focus A=10、价值线入池规则、风险 UNKNOWN 口径、早盘开关（`HZ_MORNING_MACRO` 保持 on 且 scheduler 已在运行）、EOD 其余阶段、`_default_research_as_of`（未就绪拒绝语义保留）。未 commit/push、未发飞书、backend 保持运行（worker 未开）。

## 风险

1. worker 默认 off：打开（`HZ_CIO_BLOCK_WORKER=on` + 重启）后才有后台消费；EOD 的 PRICE 刷新不依赖 worker，不受影响。
2. FINANCIAL 的"确定性节重建"在快照缺失时输出为 gap 节（`_gap_section`），叙述标 STALE——属诚实降级，但老板看到的财务段会滞后至下一次全文重建。
3. PRICE 的价格指纹取自 `adjusted_daily_bars` 最新日线；该表若有滞后（此前审计发现 bulk 停在 09-04 附近、主链走 .day 文件），会导致价格变化漏触发——若发现漏触发，改用 .day 尾价或 quotes 作为指纹源（未做，属数据源切换）。

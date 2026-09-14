# 2026-09-11 CIO 分块后台 worker（V1：PRICE + FINANCIAL）

## 表与挂点
- 新表 `cio_block_jobs`（research.db，CioReportStore._ensure_table）：
  `job_id, code, block_id, trigger, as_of, fingerprint, status(QUEUED/SUCCESS/FAILED/DISCARDED), created_at, updated_at`
- 入队/去重/取队/指纹：`cio_report/store.py`（enqueue_block_job / latest_block_fingerprint /
  queued_block_jobs / finish_block_job / update_report_section / get_report_sections）
- EOD 触发挂点：`value_workspace/automation.py`（SW1/估值序列回填之后，fail-soft，
  stage=`CIO_BLOCK_JOBS_QUEUED`）
- worker 启动：`api_server.py` 启动序列 `start_cio_block_worker()`；
  env `HZ_CIO_BLOCK_WORKER=on` 才拉起，**默认 off**
- 实现：`src/cio_report/block_worker.py`（CioBlockWorker + enqueue_eod_block_jobs +
  pool_universe / price_fingerprints / financial_fingerprints）

## PRICE / FINANCIAL 触发条件与写回
- **PRICE**：行情 READY 后对池内全员取廉价指纹（最新日线收盘+日期，一次聚合查询）；
  指纹与该 (code, PRICE) 最近 QUEUED/SUCCESS 不同才入队。worker 消费时经
  `CioSectionBuilder.build_valuation` 确定性重建 valuation 节（现价/价格区/position_label），
  连叙述一起替换，freshness=REFRESHED。禁止 ChatLLM。
- **FINANCIAL**：财务快照的 historical_cutoff（=最新报告期口径）与已处理指纹比较，
  **仅当报告期更新才入队**；旧期次不入队。worker 只重建 financial_path/latest_quarter
  的 structured_payload（数字表），保留旧叙述并标 `freshness_status=STALE`。
- universe = 最新 l3_leader_pool 在席成员；池外丢弃。写回为**原地 UPDATE**
  （company_cio_report_sections 按 (report_id, section_type) 替换），保留
  previous_report_id 链，不新建报告行。

## 003012 验证（真数据，确定性、零 LLM）
- 003012.SZ 在池（683 家），报告 id=286（research_as_of=2026-09-11）
- PRICE 刷新：UPDATED；valuation 节 payload 含现价 4.57、价值区间 18.89–29.67、
  股息率 5.18；financial_path 的 input_fingerprint 原样未动（节隔离验证 ✓）

## 测试
`pytest tests/test_cio_block_worker.py` → 6 passed：
a. 价格变 → 只 PRICE 入队且只 valuation 节更新、financial_path 节原样；
b. 指纹未变（上次 SUCCESS）→ 0 新任务；
c. 新报告期 → FINANCIAL 入队；同期次/旧期次 → 不入队；
d. 池外代码 → 丢弃不入队；
e. 默认 env（未设）→ start_cio_block_worker 不创建线程；
f. worker 模块源码与命名空间无 ChatLLM/ProviderModelRuntime。
回归：`pytest tests/test_value_l3_automation.py tests/test_cio_report.py tests/test_cio_quick_brief.py` → 52 passed。

## 未改什么
未改 Focus A=10、低估值池入池规则、L3 评选、价格带计算、fiscal_year_flows、
MCP、bitable、live_routes；未接新闻触发；未对全池跑 LLM；未 commit、未 push、未发飞书、未开早盘。

## 风险
1. worker 默认 off：启用需 env HZ_CIO_BLOCK_WORKER=on 并重启 backend；未启用时
   队列会由 EOD 触发器持续入队但无人消费（可在页面/日志从 CIO_BLOCK_JOBS_QUEUED 观察）。
2. PRICE 块刷新依赖 ValuePriceZoneService 的已存研究数据；该公司无价格区数据时该任务
   记 FAILED（不重试到无限）。
3. FINANCIAL 块的 STALE 叙述沿用旧文字，数字以 structured_payload 为准——页面若只渲染
   narrative 需同步提示（当前 Quick Brief 读 structured 字段，不受影响）。

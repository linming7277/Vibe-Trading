# 2026-09-14 CIO 读取容错：缺 profit_forecast_detail 不再失败

## 现象与定位
`get_cio_report` 读 601886（旧 17 节/BLOCK_ONLY 报告，无 10a profit_forecast_detail 节）
曾失败。逐一核查读取路径后确认：**生产读取链路本身已三层容错**——
- `cio_report/service.py:43-85` `get_report`：按 section_type 组装，缺节如实缺席，
  无对该键的硬访问；
- `cio_report/narrative.py:679-686` `_raw_section`：缺节回退「本节资料不足」；
- `agent/mcp_server.py:1017-1064` MCP `get_cio_report`：泛化透传 sections +
  异常守卫；前端无对该节的任何引用。

本次真正的风险点是 `get_report` 的**节排序**：
`cio_report/service.py`（原 74 行）`sorted(key=...SECTION_TITLES.index(...))` 对
未知 section_type 会抛 ValueError（内部错误 500）。

## 改动
1. `cio_report/service.py`（get_report 排序）：未知 section_type 排到末尾，
   绝不因排序抛内部错误。
2. `cio_report/service.py`（get_report 读路径顺序修正）：先查报告，**无报告直接
   返回 None**（quick-brief §12 不静默生成）；PRICE 确定性补刷只对已存报告执行，
   且补刷后重取。修复「读未研究公司 → 读路径凭空创建 BLOCK_ONLY 报告 →
   quick-brief 不再报 NOT_FOUND」的契约违反。
3. `tests/test_cio_report.py`：新增回归测试——缺 profit_forecast_detail 的报告
   读取正常（缺节如实缺省）；含该节的报告原样返回（synthesis_status 保留）。

## 验证
- 601886 只读 GET：**HTTP 200**（报告与 quick-brief 双端点），17 节旧报告同样可读。
- `pytest tests/test_cio_report.py tests/test_cio_quick_brief.py
  tests/test_cio_default_as_of.py tests/test_cio_price_block.py
  tests/test_cio_incremental_synthesis.py tests/test_cio_narrative.py
  tests/test_cio_routing.py tests/test_next_session_outlook.py
  tests/test_pbc_social_financing.py` → **全绿（45+）**。

## 读取路径保证（重申）
GET 读取不调用 build_report / _synthesize / 全文 refresh；PRICE 补刷为确定性
原地更新，且只为已存报告执行；不为无报告公司创建任何报告。

## 未改什么
Focus A=10、低估值池入池规则、价格带计算、fiscal_year_flows、MCP、bitable、
live_routes、早盘开关；worker 保持 off；未 commit、未发飞书、未对 601886
以外的任何股票运行 LLM。

# 2026-09-15 CIO 08-19 错批清除（第二批）+ 财务指纹游标修复

- 日期：2026-09-15
- 性质：数据清理（DELETE，先 SELECT 锁范围）+ 一处代码修复（游标物化）。未 commit、未发飞书、未开/关任何开关、未重启 backend。

---

## 阶段 1：08-19 错批删除

- **SELECT 锁范围**（先查后删）：
  - 行数 **683**（与预期一致）；
  - created_at 全部落在 **2026-09-14T07:36:16 ~ 07:36:52 UTC（北京 09-14 15:36）**，按小时仅 07 时一档 683 行；
  - **重启（北京 09-14 16:28 = UTC 08:28）之后的 08-19 新行 = 0** → 未发生「闸复发」，允许删除。
- **DELETE**（先节后报告，单事务，条件同上）：
  - `company_cio_report_sections` 删 **12,903 行**；
  - `company_cio_research_reports` 删 **683 行**；
- 删除后 `research_as_of='2026-08-19'` = **0**；
- 抽查：601886.SH / 600216.SH 的最新报告 research_as_of = **2026-09-14**（完好，非 08-19）。

### 为什么昨天删过还会出现

昨天（09-14）的删除条件是 `created_at >= '2026-09-11T07:55:00'`，只覆盖了 **16:07–16:08（北京）**那批；本批 683 行写入于**更早的 15:36（北京）**，不在删除范围内。两批同构（542/636 块刷 + 99/98 LLM 全文），是同一失控路径的两次运行。今天的删除条件按 `research_as_of + 早于重启时刻` 锁定，已覆盖全部残留。

## 阶段 2：游标修复

- **`block_worker.py` `financial_fingerprints`**：`conn.execute(...)` 返回的 cursor 未物化即 `conn.close()`，之后 `for row in rows` 抛 `sqlite3.ProgrammingError: Cannot operate on a closed database`——即 09-14 EOD `CIO_FINANCIAL_BLOCKS_READY=FAILED` 的直接原因。已改为 close 前 `.fetchall()`。
- **`block_worker.py` `pool_universe`**：return 表达式里迭代 cursor、finally 才关连接——求值顺序上当前不抛，但属同族脆弱写法，已一并先 `.fetchall()` 再构建集合。
- **`store.py` 排查**：`queued_block_jobs / latest_report / sections_for` 等均已 `.fetchall()` 物化，无同族问题，未改。
- 全文件核查：`block_worker.py` 现存全部 `execute` 调用均为 fetchone/fetchall 物化后使用。

## 测试

`pytest tests/test_cio_block_worker.py tests/test_cio_price_block.py tests/test_cio_default_as_of.py -q` → **14 passed**，其中新增回归 `test_financial_fingerprints_iterates_after_close`：以临时 VIBE_TRADING_HOME 建真实 sqlite 快照表，断言关连接后遍历返回正确指纹（旧实现下该用例抛 ProgrammingError，可区分新旧）。TDX 未就绪 → 0 入队用例保持通过。

## 没改什么

Focus A=10、价值线入池、风险口径、`_default_research_as_of` 拒绝语义、worker 默认 off（未设 `HZ_CIO_BLOCK_WORKER`）、backend（未重启）。未 commit/push、未发飞书。

## 风险

1. 两批 08-19（15:36 与 16:07）都源自重启窗口期 TDX 未就绪的批量调用——`_default_research_as_of` 已于 09-14 收紧（未就绪返回 None、入口拒绝），**理论上不会再生成新的 08-19 批**；今晚 EOD 后可再核一次 08-19 计数确认。
2. `CIO_FINANCIAL_BLOCKS_READY` 修复需 backend 重启后才在 EOD 生效（当前运行中进程仍是旧代码）；未重启，按指令等待。

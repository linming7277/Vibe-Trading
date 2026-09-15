# 2026-09-15 早盘快讯缓存表 + 「后台收集、早盘只读库」

- 日期：2026-09-15
- 性质：新表 + 收集器/调度器 + 构建主源切换。未改收盘日报、价值线、Focus、价格区、outlook、CIO、早盘文案规则。未 commit、未真发飞书、未重启。
- 开关：`HZ_MORNING_MACRO=on`（保持）——收集器与 08:00 发送**同开关**，未新增第二个环境变量；`HZ_CIO_BLOCK_WORKER` 未设（worker off 不变）。

---

## 表结构（research.db，同 morning 其它研究数据）

```sql
CREATE TABLE IF NOT EXISTS morning_flash_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,        -- eastmoney / cls / jin10 …
    title TEXT NOT NULL,
    url TEXT NOT NULL,           -- 无链接不写表
    published_at TEXT,           -- ISO 含时分；未知时分则存日期（构建从宽）
    region TEXT,                 -- ingest 不填，构建时跑分区规则
    raw_key TEXT UNIQUE,         -- source|url，同 url 重跑 0 新增
    fetched_at TEXT,
    as_of_date TEXT              -- 写入时北京日历日，清扫依据
)
```

- 只补缺 upsert（INSERT OR IGNORE），已存在行**不覆盖标题**。
- 每次 ingest 末尾删除 `as_of_date` 早于 14 天前的行。

## 收集器

`ingest_morning_flash(now, research_db_path, em_fetcher, rss_fetcher)` → `{fetched, inserted, skipped, errors}`：
- 复用现有 `fetch_eastmoney_flash`（东财 url/uniqueUrl 字段已接）与 `fetch_rss_items`（RSS `<link>` 已解析）——不新爬虫、不抓正文；
- 无 url 的条丢弃并记日志（不写表）；单源失败 `errors += 1` 不抛；
- 末尾清扫 14 天前旧行。

## 调度

`MorningFlashScheduler`（60s 看钟，距上次 ≥10 分钟才拉）+ 窗口判定 `in_collect_window`：
- **工作日**：15:05 → 次日 08:00 拉取（08:00 整点那一轮覆盖构建前快讯）；08:00–15:05 白天不拉；
- **周末**：全天继续拉（选定方案，覆盖周五 15:05 → 周一 08:00 的长隔夜）；
- 挂点：`api_server.py` 与 `start_morning_macro_scheduler()` 同一处调用 `start_morning_flash_scheduler()`；`HZ_MORNING_MACRO=off` 两都不跑。**未注册 live_routes**。

## 构建器（08:00 主路径）

- `build_morning_macro_brief(news=None)` → `_load_flash_items(as_of)`：只读 `morning_flash_items`，窗口 `published_at ∈ (上一交易日 15:00, 当日 08:00]`（上一交易日按本地交易日历回退）；**日期级（无时分）条目从宽**——上一交易日或当日均可（如实降级，不假装 00:00）；
- 无 url 的行跳过（宁可少一条）；
- 仍走既有 `_filter_news`（软文/日历/同事件去重）/ 国内强制词 / `_split_and_cap` / 解读 / L1 / 判断 / 闸——全部复用；
- 行渲染：`- HH:MM 标题` + **独立链接行（https://…）**；
- 窗口内有效带链接新闻 0 → 「【隔夜要闻】资料不足。」+ `SKIPPED_EMPTY`，网关 0 次。

## 测试

- 新增 `tests/test_morning_flash_cache.py` → **8 passed**：
  1. 假抓 3 条（2 有 url、1 无）→ inserted=2、skipped=1；重跑 inserted=0；
  2. 窗口：昨 16:00、今 07:50 进；今 10:11、昨 14:50 不进；
  3. 构建读表 → text 含 2 条链接行、无「纳指/数字对照/中间价」；
  4. 空窗 → SKIPPED_EMPTY、FakeSender 0 次；
  5. 调度窗口矩阵（周一 15:06 ✓ / 14:00 ✗ / 周二 07:00 ✓ / 09:00 ✗ / 周日 ✓ / 周六凌晨 ✓）+ 10 分钟间隔（5 分钟内不重复、11 分钟后可再拉）；
  6. AST：模块不引用 focus_selection / low_value_leader_pool / level3_leaders。
- `pytest tests/test_morning_macro_brief.py -q` → **30 passed**（旧「现场抓东财填要闻」断言已改为读表 mock；其余全保持）。
- 合计 **38 passed**。

## 没改什么

收盘日报、价值线、Focus、价格区、outlook、CIO 全部；早盘文案规则（分区/过滤/解读/L1/判断）原样复用；`read_ds_lday`/`fetch_overseas_indices` 保留（不再被 build 调用）；`HZ_CIO_BLOCK_WORKER` 保持未设。未 commit/push、未真发飞书、未重启。

## 运维依赖（重要风险）

**backend 必须从「上一交易日 15:00 前」持续运行到「当日 08:00」**，缓存表才有完整隔夜数据：
- 若 backend 在 15:05–次日 08:00 之间停机/重启，停机期间的快讯即缺失（东财接口只给最新若干条，无法回补）；
- 明早 08:00 卡的质量取决于「昨 15:05 起收集器是否持续在跑」——今晚起 backend 保持运行即可；
- 收集器与 08:00 发送同开关（`HZ_MORNING_MACRO`）：关掉开关两者同时停。

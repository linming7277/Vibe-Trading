# 2026-09-09 前瞻宽度行 P2（20日新高 + 涨停家数 + SHADOW 前缀）

- 日期：2026-09-09
- 性质：下一交易日前瞻【资金】段后新增宽度一句。不改价值线、Focus、价格区、涨停 10%/20% 识别口径、outlook 走势/资金/板块规则；未 commit；未注册 live_routes；未新增页面/观察器框架。

---

## 一、改了什么 / 为什么

老板需要一眼看到「市场在追什么有多热」，而不是荐股。前瞻原四段（走势/资金/板块/Focus 声明）缺一句市场宽度。本批在 **【资金】行后紧跟一行宽度句**（不设【宽度】大段标题，不算第五大段），总前瞻 5 行 ≤12 行。

**正典句式（结构固定，数字可变）**：
```
SHADOW｜20日新高 N 家，涨停 M 家，成交 0.87x，成交平淡，方向不明
```
- 前缀 `SHADOW｜` 仅在预测处于 SHADOW 时出现（非 SHADOW 无前缀）；
- 第 4 段沿用资金段既有 status 措辞（0.8–1.2 分档规则零改动）；
- 任何一段缺数写「数据不足」，不编；全部缺数时整行省略。

## 二、N 与 M 怎么算

| 量 | 口径 | 来源 |
|---|---|---|
| N（20日新高家数） | P 日收盘 ≥ 过去 20 个交易日收盘最大值（≥ 含持平）；**宇宙与涨停扫描同一套**：沪深非北交（.BJ/4/8 前缀排除）、名称非 ST/*ST、P 日非零成交（停牌排除）、.day 末根对齐 P 日（不足 21 根不计入） | `market_events.day_tail_universe(as_of, count=21)` → 本机 `.day` 末 21 根 |
| M（涨停家数） | 事件表 `LIMIT_UP` research_as_of=P 日条数优先；**表缺失/当日 0 条 → 现场调 `scan_limit_ups` 同一套函数计数**（绝不另写第二套涨停定义） | `value_strategy_state_events` → 兜底 `.day` 扫描 |
| 成交 0.87x | 既有资金段量比：沪深300当日额 / 20 日均额（`_flow` 新增返回 `volume_ratio`，分档规则零改动） | forecast_index_bars |

**盘中污染修复（关键）**：通达信会在 T+1 盘中往 `.day` 追加 P+1 的盘中根——按「末根==P 日」过滤会在次日盘中全军覆没（实测从 74→0）。`day_tail_universe` 现在读 `count+2` 根后**裁掉所有 >P 日的根**，再要求末根==P 日：历史日口径在任何时刻重跑都稳定（09-08 在 09-09 盘中重跑仍得 74 条涨停）。

## 三、涉及文件

- `agent/src/value_strategy/market_events.py`：抽公共 `day_tail_universe()`（涨停扫描与新高计数共用同一宇宙与排除口径）；`scan_limit_ups` 改用该宇宙（语义等价，10/10 既有测试通过）
- `agent/src/tdx_data/day_file.py`：（早前批次已加 `read_lday_tail`，本批未再改）
- `agent/src/investment_research_supervisor/next_session_outlook.py`：`count_20d_new_highs` / `count_limit_up_events`；`_flow` 返回值新增 `volume_ratio`（additive）；`build_next_session_outlook` 新增宽度行 + 投影字段 `breadth_new_highs` / `breadth_limit_ups` / `breadth_line`；签名新增 `new_highs` / `limit_up_count` / `tdx_home` / `tdx_db_path` / `research_db_path` 注入参数（测试与后续调用可显式传入）
- `agent/tests/test_outlook_breadth_p2.py`（新增 5 用例）

## 四、怎么测（全部通过）

```
pytest tests/test_outlook_breadth_p2.py tests/test_next_session_outlook.py -q
→ 13 passed（5 新增：3 只股票 2 只创新高 → N=2；前瞻宽度行含「20日新高 2 家」
  且带 SHADOW 前缀、总行 ≤12；缺 .day → 「20日新高 数据不足」；
  无买入/卖出/加仓等交易词；既有 outlook 8 用例回归通过）

pytest tests/test_market_events_p1.py -q → 10 passed（宇宙抽取重构后涨停口径不变）
```
隔离 grep：`breadth_new_highs` / `count_20d_new_highs` / `day_tail_universe` 在 `focus_selection/`、`low_value_leader_pool/`、`level3_leaders/` **零引用**——宽度统计不进任何筛选链。

## 五、2026-09-08 实跑（完整前瞻，今日盘中重跑口径稳定）

```
【走势】基准偏弱（把握低·影子）。沪深300收4558.74，近1日-0.36%、近5日-1.14%、近20日-2.25%。作废：沪深300收盘高于P日收盘价4558.74且成交额高于P日。
【资金】成交平淡，方向不明——沪深300成交额为20日均量的0.87倍。
SHADOW｜20日新高 1048 家，涨停 74 家，成交 0.87x，成交平淡，方向不明
【板块】相对最强：农林牧渔+7.66%、传媒+6.48%、房地产+4.81%；最弱：煤炭-1.11%、银行-1.08%、计算机-0.88%。强板块是放量。
以上不改今天 Focus 名单。
```
N=1048 / M=74：涨停 74 家的放量热点日，13.6% 个股处于 20 日收盘通道顶部，数字如实（09-09 盘中重跑结果一致）。

## 六、没改什么

- 价值线（L3/低估池/Focus）及其测试；涨停 10%/20% 口径；outlook 走势/资金/板块措辞规则与 0.8–1.2 量比分档；日报其它段落；MCP；live_routes；git（未 commit/push）。
- `grep` 证实宽度统计零引用进 Focus/低估池/L3。

## 七、已知风险

1. **`.day` 前复权/除权**：.day 为不复权原始价，个股除权除息日历史高点偏高 → 新高判定偏保守（方向安全，可能漏数，不会多数）。
2. **盘中重跑**：T+1 盘中 `.day` 会含 P+1 盘中根，本实现已裁剪；若通达信对历史根做前复权重算（罕见），历史口径会有小幅漂移。
3. **M 兜底口径**：事件表当日 0 条时现场扫描计数——若上游 ingest 未跑，M 仍准确（同一函数），但会多一次全市场扫描（秒级）。

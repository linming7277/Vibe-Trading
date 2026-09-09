# 2026-09-09 涨停/定增事件 P1（市场事件接入既有事件表）

- 日期：2026-09-09
- 性质：P1 瘦身扩展。复用既有 `value_strategy_state_events` 表，未建第二套事件体系；未改价值线/Focus/价格区/outlook 公式；未 commit；未注册 live_routes；未用 baostock。

---

## 一、改了什么 / 为什么

老板日报此前只有「价格条件」与「研究变化」，缺市场级事件（涨停、定增公告）。本批把两类事件写入**既有**事件表，并让日报在 8 行预算内消费：

1. **涨停扫描（`scan_limit_ups`）**
   - K 线源：**本机通达信 `vipdoc/{sh,sz}/lday/*.day`**（`read_lday_tail` 只读每文件末 2 根，追加式落盘、不会被次日覆盖）。选它的原因：`adjusted_daily_bars` 当日通常只有池内补数（09-08 仅 1 行），不满足全市场口径。
   - 涨停口径：收盘 ≥ 前收×(1+幅度)×**0.995**（贴板 0.5% 内）。
   - 幅度：代码前缀 **300/301/688 → 20%**；其余沪深主板 → **10%**。
   - 成交额门槛：当日全市场有成交股票**成交额中位数 × 0.2**（常量 `LIMIT_UP_AMOUNT_MEDIAN_FRACTION`，相对量纲避免元/万元单位歧义）。
   - 排除：北交所（bj 目录不扫 + 4/8 开头前缀防御排除）、名称含 ST/*ST（`snapshot_records dataset='securities'` 全市场名单）、停牌/成交额≤0、无前收。
   - 同一股票 P 日至多 1 条（event_key 天然去重）。
2. **定增（`fetch_private_placements`）**
   - 源：`company_action_events`，`event_type='PRIVATE_PLACEMENT'`（表内既有枚举，非自造）且 `announcement_date=P 日`（公告日口径，非实施日/股价反应日）。
3. **写入（`ingest_market_events`）**
   - 表：`value_strategy_state_events`（既有）。upsert 键 = `event_key = "{event_type}:{stock_code}:{as_of}"`，重跑 0 新增；已存在行不覆盖（保留 ACKNOWLEDGED/CLOSED 状态）。
   - 行内容：category=MARKET_EVENT、severity=INFO、status=OPEN、trigger_dimension=market_event、details 含公司名/收盘/额/幅度（涨停）或标题/公告日（定增）；公司名来自 `records dataset='security_details'`。
   - 失败隔离：扫描/写入异常只记 error 日志并返回 FAILED，绝不打断 EOD 整链；EOD 新增 `MARKET_EVENTS_READY` 阶段（fail-soft）。
4. **日报消费（`_price_condition_digest`）**
   - 新增 `merge_market_event_lines(price_lines, event_lines)`：**价格条件行先占位**，事件行最多再占 **3** 行，合计仍 ≤ **8**；事件源空 → 不追加任何行（事件段整段省略），bitable 空源门照旧（不删表）。
   - 文案：`涨停：名称 代码` / `定增：名称 代码 标题前40字`；禁词（买入/卖出/追涨/打板等）由既有 `_TRADING_TERMS` 检查兜底。
   - 飞书卡片沿用 `max_lines=3` 既有预算（价格条件先占位），Excel 未新增 sheet。

## 二、事件表与 type 枚举

- 表：`value_strategy_state_events`（research.db，既有；UNIQUE(event_key)）
- 本次新增 event_type 值：`LIMIT_UP`、`PRIVATE_PLACEMENT`（后者与 `company_action_events.event_type` 既有枚举同名，非自造体系）；category=`MARKET_EVENT`；其余枚举不变。

## 三、涉及文件

- `agent/src/value_strategy/market_events.py`（新增：scan/抓取/写入/日报行读取）
- `agent/src/tdx_data/day_file.py`（新增 `read_lday_tail`，只读文件尾部 N 根；既有函数未改）
- `agent/src/value_workspace/automation.py`（EOD 新增 `MARKET_EVENTS_READY` 阶段，fail-soft）
- `agent/src/investment_research_supervisor/daily_brief_service.py`（digest 尾部并入事件行 + `merge_market_event_lines` 纯函数）
- `agent/tests/test_market_events_p1.py`（新增 8 用例）

## 四、怎么测（已执行，全绿）

```
pytest tests/test_market_events_p1.py -q
→ 8 passed（主板贴板+额门槛→1 条；300xxx +19% 不算/+20.2% 算；
  ST、北交所、零成交→0 条；定增公告日=P→1 条/≠P→0 条；
  重跑 0 新增；事件源空→无事件行；6 价格+5 事件→总 8、事件 2；
  定增 5 条→事件行上限 3）

pytest tests/test_market_events_p1.py tests/test_price_position_labels.py \
  tests/test_bitable_empty_source.py tests/test_investment_research_daily_brief.py \
  tests/test_value_strategy_events.py tests/test_macro_forecast_v28_integration.py -q
→ 116 passed, 1 failed（唯一失败为已知秒级重发 flake
  test_notify_resends_card_when_brief_is_rebuilt_after_send，与本改动无关，单跑即绿）

ruff（触碰文件）→ All checks passed
```

## 五、2026-09-08 真数抽查（只读识别）

- **涨停：74 条**。前 5 码：000011.SZ、000020.SZ、000059.SZ、000523.SZ、000560.SZ（样例：000011.SZ 收盘 9.49，涨停价参考 9.493，额 3.23 亿）。
- **定增：0 条**（`company_action_events` 中 announcement_date=2026-09-08 的 PRIVATE_PLACEMENT 公告为 0——真实为空，非缺数据）。
- 注意：首次抽查走 `adjusted_daily_bars` 得 0 条，原因是该表 09-08 仅 1 行（池内补数）——已按 §一切换为 `.day` 全市场源后得到 74 条。

## 六、没改什么

- 价值线（L3/低估池/Focus）及全部相关表与算法；价值线相关测试未被改到失败（116 passed 含其回归）
- 价格区/position_label/sw1 板块/outlook 走势公式
- 日报正文其它段落、bitable 列结构与空源门、MCP、live_routes（未注册）
- 涨停结果不用于过滤价值名单；301/北交所/ST 如实标注未计入
- git（未 commit/push）

## 七、已知风险

1. **全市场日 K 依赖本机通达信 `.day`**：换机/未开通达信收盘下载时，涨停通道当日为 0 条（事件段省略），属数据缺失而非口径错误。
2. **前复权价口径**：涨停判定用前复权收盘比值；个股在 P 日除权除息时幅度会失真（可能漏判/极少数误判）——后续可引入除权因子表修正，本版未做。
3. **定增覆盖依赖 company_action_events 的公告入库**：公告源未抓到的定增不会出现；识别不了类型时该通道为 0 条（不冒充）。
4. **事件行不进 bitable**：老板表仍只同步低估值龙头池；事件仅日报正文/卡片可见。

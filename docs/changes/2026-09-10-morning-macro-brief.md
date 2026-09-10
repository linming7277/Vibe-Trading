# 2026-09-10 早盘宏观速览 V1（morning_macro_brief）

- 日期：2026-09-10
- 性质：新增独立产品「早盘宏观速览」。不改收盘 EOD 逻辑、价值线（L3/低估池/Focus）、价格区、outlook 走势/资金/板块规则、收盘日报模板；未 commit/push；未注册 live_routes；**未真发飞书测试卡**。

---

## 一、改了什么 / 为什么

老板在早盘（08:00）缺一份宏观速览：隔夜外盘、国内政策/公告、对当日环境的定性。收盘投研日报 16:45 才生成，时间与内容都不匹配早盘场景。本批新增独立构建器 + 独立卡片 + 独立调度，与收盘日报**三重隔离**（构建器独立、卡片标题独立、delivery channel 独立）。

## 二、产品与实现

- **触发**：`MorningMacroScheduler`（`src/investment_research_supervisor/morning_macro_brief.py:434` 起）——60s 轮询线程，北京时间 **08:00** 触发，工作日才发；非交易日（本地交易日历判定）→ `SKIPPED_NOT_TRADING_DAY`；周末 → `SKIPPED_WEEKEND`；同日只处理一次（`_handled_dates`）。启动挂点：`api_server._run_startup_preflight` 内 `start_morning_macro_scheduler()`（`api_server.py:185-188`，紧随既有调度器之后）。**下次 backend 重启后生效**（当前运行中进程仍是旧代码）。
- **构建**：`build_morning_macro_brief(as_of_date, as_of_time="08:00+08:00")` → `{text, items, sentiment, macro_shadow, missing}`。固定版式 ≤12 行：
  - 【隔夜外盘】标普/纳指/道指/恒生 各写 `收盘(±x.xx%)`；单个指数缺 → 该指数「资料不足」；**全失败 → 整段「【隔夜外盘】资料不足。」**（测试锁定）
  - 人民币中间价：`macro_series.usd_cny_official_mid` 最新 ≤ 当日（官方口径，本地读取）
  - 【国内要闻】≤5 条：`policy_events`（政策·来源）+ `company_action_events`（公司公告），日期∈{P-1, P}；0 条整段省略；published_at 为日级粒度，无法锚定 15:00 的条目按日期收，正文展示日期
  - 【对今日环境】偏暖/偏冷/中性/资料不足 + 半句原因（隔夜外盘均值方向）；宏观预测 SHADOW → 追加「；宏观预测把握低」
  - 【缺数】只列影响判断的缺口
- **文案纪律**：交易词（买入/卖出/加仓等 9 词）构建器内直接抛错；不出现 M1/A1 因子码；不推荐行业进名单。
- **发送**：独立 channel `feishu_morning_macro` + 独立标题「早盘宏观速览 · 日期」——**不可能覆盖收盘日报卡**（delivery 键与卡片标题双重隔离）；幂等（同日 SENT → REUSED）；发送失败记 FAILED delivery + error 日志，不重试风暴、不碰 bitable。
- **数据源边界**：外盘 = 项目内 Yahoo 加载器（`backtest.loaders.yahoo_loader`）；要闻 = 既有表；中间价 = 既有 macro_series。未接路透/彭博，未用 baostock，未造新爬虫。

## 三、怎么测（新增 `tests/test_morning_macro_brief.py`，10 用例全绿）

```
pytest tests/test_morning_macro_brief.py -q → 10 passed
pytest tests/test_outlook_breadth_p2.py tests/test_next_session_outlook.py \
  tests/test_investment_research_daily_brief.py tests/test_bitable_empty_source.py -q
→ 55 passed（回归无红）
ruff（模块/测试/api_server）→ All checks passed
```
覆盖：外盘有数含百分号；外盘全失败 → 整段「资料不足」不编数字；要闻 0 条省段；无交易词、无 M1/A1；**构建模块 import 纯净**（AST 检查不触 focus_selection/low_value_leader_pool/level3_leaders）；调度周末/非交易日 SKIPPED、到点发一次、未到点不发；发送幂等（REUSED 不重复调用网关）；卡片标题=「早盘宏观速览 · 日期」。

## 四、9/10 实盘预览（构建打印，未发送）

```
【隔夜外盘】资料不足。
人民币中间价 6.7804
【对今日环境】资料不足（隔夜外盘数据不足）。
【缺数】标普、纳指、道指、恒生（影响隔夜外盘与环境判断）
```
如实： Yahoo 对指数符号当前被限流/失败（YFRateLimitError，实测），外盘段按约定整段「资料不足」——中间价（本地）正常。盘中 Yahoo 恢复后自动补齐。

## 五、没改什么

- 收盘 EOD 逻辑（16:45 流水线零改动）；价值线 L3/低估池/Focus；价格区与 position_label；收盘日报模板与卡片
- outlook 宽度行/白名单口径；涨停口径；MCP；live_routes
- git（未 commit/push）

## 六、已知风险

1. **调度生效需重启 backend**：当前运行中进程仍是旧代码（09-08 17:49 加载），重启后 08:00 调度才会启动——下次重启时间由你决定。
2. **外盘源稳定性**：Yahoo 对指数符号当前被限流（实测 09-09/09-10 均 EMPTY）——早盘 08:00 时段若持续限流，隔夜外盘段会长期「资料不足」；备选方案（eastmoney 指数链路）留待 P2。
3. **要闻粒度**：published_at 日级，无法严格切「昨日 15:00 后」；已按日期窗口 + 明示日期处理。
4. **老板群真实发送**：调度默认启用——下一次 backend 重启后，第一个工作日 08:00 将真发。若需先观察，可在重启前设 env 关闭（未实现开关，需一句配置，告知即加）。

# 2026-09-14 飞书通道拆分：早盘与收盘各自独立

- 日期：2026-09-14
- 性质：早盘发送通道改造 + 配置回滚 + 测试。未改价值线、Focus、早盘文案、新闻源、发送闸。未 commit、未发飞书测试卡。

---

## 背景

昨天（09-13→14）为让早盘发出，启用了 `channels.feishu_supervisor.enabled=True`——但该开关是**共享**的：`DailyBriefNotificationSettings.from_channels_config()` 对收盘与早盘解析同一条配置，enabled=True 会使**收盘**也改走应用内 `feishu_supervisor` 频道（违背 "Hermes owns the bot lifecycle" 的设计注释，且发送身份变了）。本次拆开。

## 拆线结果

| | 收盘投研日报 | 早盘宏观速览 |
|---|---|---|
| 生成/发送入口 | `automation.py:552` → `DailyBriefNotificationService.notify()` | `morning_macro_brief.py` `send_morning_macro_brief()` |
| 通道 | **`hermes_feishu_supervisor`**（Hermes 凭证，08-26 至今每交易日 SENT，未变） | **`feishu_morning_macro`**（独立幂等键，走 Hermes 凭证短命 client 发送） |
| 凭证 | `HermesSupervisorFeishuCredentials`（Hermes profiles env） | 同一套凭证（复用 `ShortLivedFeishuBriefSender`） |
| 依赖 `channels.feishu_supervisor.enabled` | **否**（enabled=False 时自动落 hermes 线） | **否**（已移除 `ExistingFeishuSupervisorSender` 唯一路径） |

## 改动清单

1. `morning_macro_brief.py` `send_morning_macro_brief`：sender 由 `ExistingFeishuSupervisorSender()` 改为 **`ShortLivedFeishuBriefSender()`**（Hermes 凭证请求级 SDK client；与 Bitable publisher 同法）。投递表仍写 `channel=feishu_morning_macro`，与收盘的 `hermes_feishu_supervisor` 幂等键隔离。
2. `agent.json`：`channels.feishu_supervisor.enabled` **True → False**（回滚昨天的启用；备份链完整：`agent.json.bak-20260914-pre-supervisor` 之后又一版）。
3. 测试：`test_morning_macro_brief.py` 新增 3 条（见下）。

## 目标群变化（需要知道）

- 昨天 09:40 的早报发到了 supervisor 嵌套配置的 `oc_24ff0076…`；
- 切线后早盘 target = Hermes home 频道 `oc_b9580ece…`（`FEISHU_HOME_CHANNEL`）。两 open_id 不同——**明早起早报落在 Hermes 机器人群**。若需指回原群，改 hermes profiles env 的 `FEISHU_HOME_CHANNEL` 即可（不动代码）。

## 测试

`pytest tests/test_morning_macro_brief.py -q` → **43 passed**，含新增 3 条：
- a. mock `supervisor.enabled=False` → 收盘 `settings.delivery_channel` 含 `hermes`（`load_channels_config`/hermes load 均 mock）；
- b. 早盘 send 在 enabled=False 时经 mock Hermes sender 成功 `SENT`（不再报 "channel is not running"），投递行 `channel=feishu_morning_macro`；
- c. 早盘 `feishu_morning_macro` ≠ 收盘 `hermes_feishu_supervisor`。
关联回归：`test_cio_price_block.py / test_cio_default_as_of.py / test_cio_block_worker.py` → 12 passed。

## 没改什么

`from_channels_config` 本体（收盘 fallback 逻辑原样）、收盘 notify/sender 选择代码、价值线、Focus、早盘文案（要闻/要点/判断/环境/数字对照）、发送闸（要闻 0 → SKIPPED_EMPTY）、`HZ_MORNING_MACRO=on`（保持）、`trading_dates` 数据。未 commit、未 push、未发测试卡。

## 风险

1. 早报目标群从 `oc_24ff…` 变为 Hermes home `oc_b9580e…`——老板若只看原群，需要把 `FEISHU_HOME_CHANNEL` 指回（env 改动即可）。
2. `ShortLivedFeishuBriefSender` 每次发送用请求级 SDK client（无长连接），发送频率低（每日一次）无影响。
3. backend 运行中（09:36 起的进程加载的是拆分前代码）——拆线代码与 enabled=False 回滚将于**下次重启**一并生效；生效前运行的进程内 `feishu_supervisor.enabled=True`（内存配置）仍走应用内线，功能不受影响。

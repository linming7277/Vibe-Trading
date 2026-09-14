# 2026-09-14 早盘宏观速览上线（HZ_MORNING_MACRO=on）

- 日期：2026-09-14
- 性质：开关上线 + 三处阻塞修复。未 commit、未改 Focus/价值线/收盘日报。

---

## 上线动作（时序）

1. **开关持久化**：`C:\Users\Administrator\.vibe-trading\.env` 写入 `HZ_MORNING_MACRO=on`（dotenv 首选候选路径；文件在 home 下、已被 gitignore）。模拟下次启动验证 `morning_macro_enabled()==True`。
2. **交易日历数据修复**：本地 `trading_dates` 停在 2021-08（`_collect_history` 的 `get_trading_dates(count=5000)` 自 1990 年起被截断）→ 经 TDX 接口补入 2023 年至今 **897 个交易日**（含 20260914）。
3. **`_is_trading_day` 三态判定**（`morning_macro_brief.py`）：原实现「今天 ∈ 日历」才发，而日历只含已实现交易日——08:00 时「今天」永远不在 → 按原逻辑此产品一天都不会发。改为：日历内确认 → True；覆盖区间内缺失（节假日）→ False；超出覆盖 → 周一~周五 True、周六日 False。测试 `test_is_trading_day_calendar_states` 覆盖三态。
4. **启用投递频道**：`agent.json` 的 `channels.feishu_supervisor.enabled: False → True`（凭证 app_id/app_secret 原本已在；先备份 `agent.json.bak-20260914-pre-supervisor`）。此前 supervisor 频道未启用，早报首次自动补发失败（`Feishu supervisor channel is not running`）。
5. **重启生效**（launcher restart 两次：09:36 修渠道前 / 09:40 修渠道后）。

## 首发结果

- `investment_research_daily_brief_deliveries`：channel=`feishu_morning_macro`、research_as_of=**2026-09-14**、**SENT**（09:40:58 北京），无 error。
- 报告内容即当日预览版：要闻 8 条（东财主源，含链接）、要点 3、判断「中性」（新未落地规则生效——「美联储加息悬念待揭晓」不再误判偏冷）、环境句、中间价 6.7743、数字对照。
- 明起每个交易日 **08:00 自动生成并发送**。

## CIO PRICE BLOCK 首战同步确认（09-11 EOD 落盘值）

`CIO_PRICE_BLOCKS_READY=10刷+673复用/683`，0 失败；`CIO_FOCUS_TIER=A:10建`（Focus A 10 只以 09-11 全部新建全文）。08-19 错批残留保持 0。

## 没改什么

Focus A=10、价值线、收盘日报模板、新闻源、发送闸（要闻 0 仍 SKIPPED_EMPTY）、判断规则（当日已按独立任务改为「未落地先行」）。未 commit、未 push。

## 风险

1. 法定节假日：`_is_trading_day` 超出日历覆盖时按「周一~周五=交易日」推断——长假首日早报仍会发（内容为最新存档数据），日历数据每晚 TDX 更新后次日自愈。
2. `trading_dates` 的 count=5000 截断问题仍在采集代码里（未改代码）；若未来重跑全量采集会再次截断，本次以数据补入方式修复。建议后续把 `_collect_history` 的窗口改为近年增量。
3. supervisor 频道启用后，收盘日报等其它 supervisor 渠道投递同时恢复（此前 08-28 后该渠道实际未投递）。

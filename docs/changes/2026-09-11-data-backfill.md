# 2026-09-11 数据补齐：45 只风险 UNKNOWN 排查 + 宏观序列刷新 + 预测断档 + 定增日期

- 日期：2026-09-11
- 性质：数据操作 + 只读诊断，**未改任何代码文件**。全程未 commit/push、未发飞书、未设 `HZ_MORNING_MACRO`、未注册 live_routes、未重启 backend。
- 授权范围遵守：未动 Focus 限额/筛选规则/价格带公式/L3/低估池口径；未动 quotes 覆盖式快照架构；未碰两融/北向（已决定不做）；未编造任何数据。

---

## 阶段 1：45 只 overall_risk=UNKNOWN（关键反转）

**分拣结果（通达信专业财务源 cw vs 库内 financial_history）：**

| 分类 | 只数 |
|---|---|
| CAN_BACKFILL（源有 ≥2023 期而库没有） | **0** |
| SOURCE_EMPTY（源也没有） | **0** |
| UNCLEAR（源读不到） | **0** |
| ALREADY_SYNCED（库与源一致） | **45** |

45 只抽样核对：库内最晚报告期 = 源最晚报告期 = **2026-06-30（中报）**，各 31 期、近年 14 期。**financial_history 无一需要补，未写入任何行。**

> 审计修正：此前「45 只财务停在 2021-2022」的结论是 `ORDER BY updated_at DESC LIMIT 12` 抽样偏差（upsert 后旧行与新行同批被更新），本轮逐只全量比对后推翻。

**重算验证**：`refresh_company_snapshot`（`low_value_risk_snapshot/service.py:120`）试算 prep 材料双 READY 的 000719.SZ（as_of=2026-09-10）→ **仍 UNKNOWN**。财务输入无变化，其余 44 只重算结果可确定同理（均为同函数同输入），为避免 45 次无意义重写未批量执行。

**UNKNOWN 真实根因**（`risk_research/service.py:832`：有 missing 项即判 UNKNOWN）：45 只的 `data_quality.missing` 均含 `BUSINESS_CHANGE / CUSTOMER_CONCENTRATION / PRODUCT_REVENUE_SHARE / MARKET_SHARE / THESIS`——缺的是**业务研究件与研报观点的风险装配**，不是财务数据。佐证：`company_business_research_snapshots` 对 45 只覆盖 45/45、prep 表 business READY 45/45，但风险引擎仍报 business=MISSING——属于风险引擎输入装配/口径问题，**不在本任务授权范围（禁止改风险口径），留给后续专项**。

**UNKNOWN 补前 → 补后：45 → 45**（无一可由财务补数改变）。

## 阶段 2：宏观高频序列（`run_refresh(mode='forward')` + `MacroDataService.refresh`）

| 序列 | 刷新前 | 刷新后 | 通道 |
|---|---|---|---|
| usd_cny_official_mid | 2026-09-08 | **2026-09-11（6.7743）** | chinamoney CCPR（inserted 3） |
| us_treasury_2y | 2026-09-04 | **2026-09-10** | treasury.gov（inserted 6） |
| us_treasury_10y | 2026-09-04 | **2026-09-10（4.95）** | treasury.gov |
| wti_spot | 2026-09-01 | **2026-09-01（未动）** | FRED 通道 BLOCKED：`FRED_API_KEY` 未配置 |

主 refresh 另 upsert 9,809 行 akshare 系列并重建 2026-09-11 快照（partial，regime 中性）。环境句复验无变化（中性 + 缺社融）。

## 阶段 3：社融增量 —— **FAILED_NO_SOURCE**

适配器已存在且已接入主 provider（`agent/src/strategy_engines/macro_data.py:124` `_fetch_pboc_social_financing`，Tushare `sf_month`），但 `TUSHARE_TOKEN` 未配置（app 配置链实测为空），refresh 返回 error row「TUSHARE_TOKEN is required」。**未编造序列**。配置 token 后主 refresh 自动带上，无需改代码。

## 阶段 4：宏观预测断档（09-10/09-11 无 bundle/forecast）

**卡点定位**：EOD stage `FORECAST_BARS_READY=READY`、`FORECAST_BUNDLE_READY=SKIPPED`、`MACRO_NEXT_FORECAST_READY=PARTIAL`。`prepare_forecast_inputs_step`（`macro_forecast/eod_steps.py:92`）中 `bundle_status` 唯一为 SKIPPED 的路径是 `resolve_next_target` 返回空。实测复现：`resolve_next_target('20260911')` → `''`。

**根因**：`load_trading_days`（`macro_forecast/service.py:41`）合并本地日历 + TDX `get_trading_dates`，而 TDX 日历接口**只返回已实现的交易日**（实测窗口 08-27~09-26 仅返回到 20260911），本地 `trading_dates` 缓存同样只到 09-11 → `next_trading_day` 永远找不到"严格晚于今天"的日 → SKIPPED。此为设计缺陷，不是漏跑；09-09 之前的 bundle 是 EOD 外人工脚本时代产物。

**补生成**：09-10/09-11 目标日的前瞻窗口已过（须在前一日 16:45~当日 08:00 生成），不补。**为下一交易日 09-14 用现成函数补齐**：`build_next_bundle(target_date='20260914')` → **BUILT**（`mfib_20260914_5517b7417c8e`，FACTS_ONLY，gaps 如实含 SOCIAL_FINANCING_MISSING 等 5 项）；`run_macro_forecast(mode='shadow', target_date='20260914')` → **`mmf_20260914_5e90af347cfb`（SHADOW / ABSTAINED，已落库）**。未发老板面、未动实验臂。

**根治建议**（未实施，需授权改代码）：resolve 失败时按「工作日推算 + 本地日历校验」兜底，或 TDX 日历外补一个含未来交易日的源。

## 阶段 5：定增 announcement_date 全 NULL

- 原因：写入路径硬编码——`company_actions/service.py:77` 与 `:98` 均写 `"announcement_date": None`，`pit_status=PIT_LIMITED`，是有意设计：TDX 分红/股本源**没有公告日字段**（refs raw 实测只有 `Date`=除权/事件日，已入 `event_date`）。
- 可无损回填：**0 行**。把 event_date 填进 announcement_date 等于把除权日当公告日，属造假，未填。
- PRIVATE_PLACEMENT：`company_action_events` 里 0 行——TDX 路径只采分红/送转/配股/股本变动，**定增根本没有采集器**；`market_events.py` 的定增扫描读的正是这张空表（且要求 announcement_date=P 日）。修复需接公告源（新数据源），超出本任务，未做。

## 测试

`pytest agent/tests/test_morning_macro_brief.py agent/tests/test_macro_forecast_pit.py -q` → **47 passed**（本任务未改代码，为"未碰坏"确认）。

## 写库清单（全部走现成函数）

- macro_series：treasury 6 行、chinamoney 3 行、主 refresh upsert 9,809 行（覆盖 shibor/lpr/CPI/PMI 等全部 akshare 系列与快照重建）
- forecast_input_bundles：+1（mfib_20260914）；macro_market_forecasts：+1（mmf_20260914）
- company_low_value_risk_snapshots：000719.SZ 一行重算覆写（UNKNOWN→UNKNOWN，同判）
- financial_history：**0 行**（无需补）

## 没改什么

Focus 名单与 A=10、价值线筛选规则、价格带公式、L3/低估池口径与重算、风险研究规则代码、quotes 快照架构、两融/北向、morning/收盘日报模板、`HZ_MORNING_MACRO`（保持 off）。未 commit/push、未发飞书、未注册 live_routes、未重启 backend。

## 风险

1. **45 只 UNKNOWN 依旧**：真实缺口（风险引擎的业务/研报输入装配）未修——这是风险口径改动，需单独授权的专项；在修复前这 45 只将持续「资料不足」，其中若有权 value trap 无法识别。
2. **WTI 停在 09-01**：FRED 通道因无 key 整体 BLOCKED（美债 _fred 通道同步停摆，但 treasury.gov 主通道已顶上）。配 key 前早盘数字对照里 WTI 持续过期。
3. **预测断档会复发**：本周期只手工补了 09-14 一天；resolve_next_target 的日历缺陷不修，09-14 EOD 之后还会断（09-14 收盘后 EOD 对 09-15 又拿不到目标日）。
4. **社融/定增**：分别等 TUSHARE_TOKEN 配置与公告源接入，当前环境句将持续提示「资料还缺社融增量」，定增事件线持续为空。
5. 本机 refresh 与运行中 backend（并发写库）未冲突，但若今晚 16:45 EOD 重跑主 refresh 属正常幂等路径。

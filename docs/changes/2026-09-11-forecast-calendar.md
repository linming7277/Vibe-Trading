# 2026-09-11 修复宏观预测交易日历：EOD 可解析「今天之后的下一交易日」

- 日期：2026-09-11
- 范围：仅宏观预测日历解析（`macro_forecast` 四个文件）+ 新增测试。价值线、Focus、价格带、风险口径、SHADOW 策略、早盘开关均未改动。
- 约束遵守：未 commit/push、未发飞书、未把实验臂发老板面、未编社融、未把除权日当公告日。

---

## 旧逻辑为什么返回空（文件:行）

1. `agent/src/macro_forecast/service.py:41` `load_trading_days`：合并本地 `trading_dates` 缓存 + TDX `get_trading_dates` 实时窗口。两者都**只含已实现的交易日**（实测 TDX 窗口 08-27~09-26 只返回到 20260911；本地缓存同样止于 09-11）。
2. `agent/src/macro_forecast/eod_steps.py:62`（旧）`resolve_next_target`：`next_trading_day(anchor, days)`。
3. `agent/src/macro_forecast/contracts.py:114` `next_trading_day`：要求「严格晚于 anchor」的日历内日期——日历不含未来日 → 恒为 None → 返回 `""`。
4. `agent/src/macro_forecast/eod_steps.py:92` `prepare_forecast_inputs_step`：target 为空 → `bundle_status` 保持初始值 `"SKIPPED"` → 每天每晚间档断链。

## 新目标日算法（优先级 A→B→C→D）

**A. 日历内确认**：合并日历中存在严格晚于 as_of 的日期 → 直接取第一个（现状不变，TDX `get_trading_dates` 继续负责"已走过的交易日"）。

**B. 惯例投影**（新，`contracts.py` 纯函数）：
- `project_future_trading_days(days, *, after, holidays=(), horizon_days=10)`：以日历内最后已知交易日为基准，向后推 10 个自然日，跳过周六日与 `holidays` 集合，结果严格晚于 `after`（绝不包含当天，满足 C）。项目内无论码节假日表、库内 `trading_dates` 亦不含未来日——故当前 `holidays=()`，即**只跳周末**，法定节假日不验证。
- `projected_next_trading_day(day, days, ...)`：先走 A，A 落空才投影取首个候选。

**缺口标注**：目标日晚于日历末已知交易日时，`build_timeline` 本就给出 `CALENDAR_UNVERIFIED_NEXT_SESSION`（"候选 T，正式计分预测前须复核"§4.3，机制未动）；`prepare_input_bundle` 在该状态下向 `payload["gaps"]` 增补 **`CALENDAR_HOLIDAY_UNVERIFIED`**（`service.py:160` 附近），提示"该 T 由跳周末投影得出，法定节假日未验证"。若未来接入节假日表，把它传进 `holidays` 即可自动收紧。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/src/macro_forecast/contracts.py` | 新增 `project_future_trading_days` / `projected_next_trading_day` 纯函数；`timedelta` 导入 |
| `agent/src/macro_forecast/eod_steps.py` | `resolve_next_target`：`next_trading_day` → `projected_next_trading_day`（A→B 回退） |
| `agent/src/macro_forecast/service.py` | `prepare_input_bundle` 自动解析同样回退；`CALENDAR_UNVERIFIED_NEXT_SESSION` 时 gaps 增补 `CALENDAR_HOLIDAY_UNVERIFIED` |
| `agent/src/macro_forecast/forecast_service.py` | `run_macro_forecast(target_date=None)` 自动解析同样回退；移除失效 import |
| `agent/tests/test_macro_forecast_calendar.py` | 新增 8 条纯函数/mock 测试 |

未动：`build_timeline`/`next_trading_day` 原语义、预测模型公式、SHADOW 策略、实验臂投递、节假日接口。

## 测试

`pytest agent/tests/test_macro_forecast_calendar.py agent/tests/test_macro_forecast_pit.py agent/tests/test_macro_forecast_v28_integration.py -q` → **46 passed**（新增 8 条：周五投影跳周末、绝不含当天、节假日表排除、日历有未来日优先用日历、mock 日历止于 2026-09-11 时 resolve=20260914、周五结果 ∉ {09-11,09-12,09-13} 等；既有 PIT/V28 集成全绿，不打 TDX 接口）。

## 09-11 解析结果（真机）

```
resolve_next_target('20260911') → '20260914'
```

09-14 的输入包已于本日早间生成（`mfib_20260914_5517b7417c8e`，FACTS_ONLY，gaps 含 `SOCIAL_FINANCING_MISSING` 等如实项）+ SHADOW/ABSTAINED 预测 `mmf_20260914_5e90af347cfb` 落库；今晚 16:45 EOD 将首次自动走通 BUILT 路径（幂等/按世界指纹出新版本），forecast 阶段保持 SHADOW。

## 没改什么

价值线全链（L3/低估池/Focus A=10/价格带）、风险 UNKNOWN 口径、预测模型公式与 prompt、SHADOW 策略与实验臂投递路径、`load_trading_days` 原签名与既有调用、早盘开关 `HZ_MORNING_MACRO`（保持 off）。未 commit/push、未发飞书、未重启 backend。

## 风险

1. **法定节假日未验证**：投影只跳周末。国庆/春节等连休期间，resolve 会把休市首日当目标日；bundle 会如实带 `CALENDAR_HOLIDAY_UNVERIFIED` + `CALENDAR_UNVERIFIED_NEXT_SESSION` 警告，forecast 端 deterministic gates 与 ABSTAINED 机制兜底，但**方向预测可能围绕不存在的 T 日生成**——建议尽快接一个含未来交易日的日历源（或静态节假日表传入 `holidays`）。
2. 投影窗口仅 10 个自然日：超长连休（如春节 8 天）末端可能再次落空，届时返回空 → SKIPPED（与旧行为一致，fail-soft 不阻塞 EOD）。
3. `run_macro_forecast` 自动路径行为变化：此前 `target_date=None` 在日历无未来日时直接 CALENDAR_UNAVAILABLE 返回；现在会投影出候选 T 继续走 gates——gates 与 ABSTAINED 不变，属预期修复面。

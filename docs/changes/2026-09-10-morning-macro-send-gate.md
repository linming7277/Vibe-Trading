# 2026-09-10 早盘宏观速览发送闸与总开关

- 日期：2026-09-10
- 性质：防事故加固。不改构建文案、不改外盘/要闻数据源、不改价值线/Focus/价格区、不改收盘日报；未 commit/push；未注册 live_routes；**未真发飞书**。

---

## 一、改了什么 / 为什么

两个空卡外泄路径被堵死：

1. **总开关（默认 off）**：`HZ_MORNING_MACRO=on` 才启动调度线程；未设/off → `start_morning_macro_scheduler()` 直接返回（日志一条），线程不存在——backend 重启后不会在第一个工作日 08:00 意外真发。
2. **发送前质量门（无条件）**：`send_morning_macro_brief` 在调网关前检查——外盘有效指数（change_pct 非空）**≥2** 或 **有国内要闻** 才允许发；否则返回 `SKIPPED_EMPTY` + **error 级日志**（含 usable/news 计数），**网关调用 0 次、不写 SENT delivery**。构建出的空文本（Yahoo 限流时「资料不足」版）到不了老板群。

## 二、涉及文件

- `agent/src/investment_research_supervisor/morning_macro_brief.py`：
  - `morning_macro_enabled()`（env 总闸，未设/off=False，不回显值）
  - `MorningMacroScheduler.start()` 总闸检查
  - `send_morning_macro_brief()` 质量门（SKIPPED_EMPTY + error 日志）

## 三、怎么测（`tests/test_morning_macro_brief.py`，14 用例全绿）

```
pytest tests/test_morning_macro_brief.py -q → 12 passed（原 10 + 新 2）：
  - 默认（delenv）→ start 后线程不启动
  - env=on → 线程启动（stop 清理）
pytest tests/test_market_events_p1.py tests/test_outlook_breadth_p2.py -q → 全绿（回归）
ruff（模块 + 测试）→ All checks passed
```
发送质量门测试：注入 3 指数 + 1 要闻的非空 brief → SENT（网关 1 次调用）；`overseas=[] news=[]` → **SKIPPED_EMPTY、FakeSender.calls == 0**。

## 四、没改什么

- 构建文案与版式（≤12 行、六句正典、SHADOW 前缀规则）
- 收盘日报/卡片/bitable 空源门；价值线 Focus/价格区/L3；涨停口径；外盘数据源
- git（未 commit/push）

## 五、已知风险

1. **启用动作是显式的**：想真发需设 `HZ_MORNING_MACRO=on` 后重启 backend（重启时机由你定）。默认 off 状态下调度完全静止——早盘速览不会出现，属预期行为而非故障。
2. 质量门只挡「空卡」；若外盘仅 2 个指数可用且方向同侧，卡片仍会发（内容薄但真实）——如需更高门槛（如 ≥3）改一个常量即可。

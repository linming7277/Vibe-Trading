# 2026-09-09 Bitable 空源保护（fail-closed）

- 日期：2026-09-09
- 性质：防数据丢失加固。不改表格列结构、不改日报内容、不改 outlook、未连真实飞书 API、未 commit/push、未注册 live_routes。

---

## 一、改了什么 / 为什么

**问题**：`DailyBriefBitablePublisher.publish()` 在源表（`low_value_leader_table`）为空时，仍按空集执行增量同步——`current_codes=∅` 导致所有受管行/遗留快照行全部进入 `deletes`，`batch_delete` 把老板飞书表里的存量行**整表清空**。一次空简报（上游生成失败/空池）就等于一次删库。

**修复（fail-closed）**：
1. `publish()` 在**任何 gateway 调用之前**（字段创建、list_records 之前）加空源门：源无实质内容 → 直接返回 `status=SKIPPED_EMPTY_SOURCE` + error 说明「已拒绝删除/覆盖飞书表，表保持原样」，并打 **error 级日志**（含 research_as_of）。飞书表保持原样。
2. 空源判定 `_source_has_substance()`：`None`、`{}`、**每行关键业务列全空**（公司/现价/合理价值范围/相对中位值差距——研究日期、同步来源、日报版本属元数据列恒非空，不参与判定）都算空源。
3. 源行数 > 0：维持既有增量同步语义（更新/新建/删除过期受管行），一行未改。
4. `SKIPPED_EMPTY_SOURCE` **不写 SENT delivery**、不计数成功发送——它不是成功，也不触发重试风暴（与既有 credentials/max-attempts 两个 SKIPPED 路径同风格）。

## 二、涉及文件

- `agent/src/investment_research_supervisor/daily_brief_bitable_service.py`
  - `publish()` 空源门（`source_rows` 计算后、gateway 调用前）
  - 新增 `_source_has_substance()` 静态判定
  - 模块级 `logger`
- `agent/tests/test_bitable_empty_source.py`（新增，4 用例）

## 三、怎么测

```
pytest tests/test_bitable_empty_source.py -q
→ 4 passed

pytest tests/test_bitable_empty_source.py tests/test_investment_research_daily_brief.py \
       tests/test_macro_forecast_v28_integration.py -q
→ 60 passed, 1 failed（唯一失败为已知秒级时间戳 flake
  test_notify_resends_card_when_brief_is_rebuilt_after_send，与本改动无关；
  该测试 + 既有 bitable 同步测试 + 新用例单独重跑 → 6 passed）

ruff check（两文件）→ All checks passed
```

用例覆盖：① 源 `[]` → `SKIPPED_EMPTY_SOURCE`、gateway.deleted/created/updated 全空、无 SENT delivery；② 源 `None` → 同上；③ 行存在但关键业务列全空 → 同上；④ 源 2 行 → READY、过期受管行仍被增量删除、SENT delivery 正常（现有同步语义不变）。全部走注入 Gateway 替身，0 网络。

## 四、没改什么

- 表格列结构（`_FIELD_NAMES`/`_RETIRED_FIELD_NAMES`/字段类型适配逻辑零改动）
- 日报内容与 outlook（`daily_brief_service`/`next_session_outlook` 未触碰）
- MCP、live_routes（未注册、未修改）
- Focus / 低估池 / L3（未触碰）
- 既有增量同步语义（源有数据时逐行为与修复前一致，既有测试 `test_bitable_publisher_syncs_current_pool_and_keeps_manual_rows` 原样通过）

## 五、已知风险

1. **PENDING 性行为**：`SKIPPED_EMPTY_SOURCE` 不写 delivery 记录——上游若有按 delivery 轮询重试的机制不会因此重试（符合「不写成成功发送」要求，但也没有自动补发；恢复依赖下一次正常简报）。
2. **关键列口径**：目前判定列=公司/现价/合理价值范围/相对中位值差距。若未来源表新增关键业务列，需同步加入 `_source_has_substance` 的判定键，否则"半空"行可能被误判为有实质。
3. **秒级 flake**：`test_notify_resends_card_when_brief_is_rebuilt_after_send` 存在与本改动无关的重发时间戳粒度 flake（详见 `2026-09-09-mainline.md` §五.6），批量跑偶发红、重跑即绿。

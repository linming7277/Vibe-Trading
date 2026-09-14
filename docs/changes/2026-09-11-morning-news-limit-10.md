# 2026-09-11 早盘要闻上限：国内 3+海外 3 → 国内 5+海外 5（合计 ≤10）

- 日期：2026-09-11
- 范围：仅 `agent/src/investment_research_supervisor/morning_macro_brief.py` + `agent/tests/test_morning_macro_brief.py`。收盘日报、价值线、Focus、L3、低估池、价格区均未改动。
- 约束遵守：未 commit；未发飞书；未设 `HZ_MORNING_MACRO`（保持 off）；未重启 backend。

---

## 改了什么

1. **要闻上限**：`_classify_and_cap` 由 `domestic[:3] + overseas_news[:5]` 改为 `domestic[:5] + overseas_news[:5]`，合计最多 10 条；`resolve_news` docstring 同步更新。
2. **取消 16 行裁剪**：删除 `_LAYOUT_MAX_LINES` 常量与 build 里的预算/裁剪逻辑（`_total`/`reserve`/reversed 丢链接行），所有带 url 的条目无条件渲染「链接:」行。
3. 其余版式不变：组内时间新→旧；每条 `HH:MM 来源 标题` + 有 url 才跟「链接: URL」；【要点】仍最多 3 条（≤30 字）；【判断】仍 1 句；发送闸口径不变。

## 新旧上限

| | 旧 | 新 |
|---|---|---|
| 国内 | ≤3 | **≤5** |
| 海外 | ≤5 | ≤5 |
| 合计 | ≤6 | **≤10** |
| 16 行裁剪 | 有（丢最早链接行） | **无** |
| 版式最大行数 | 16 | 无硬上限（10 条全带链接约 27 行） |

## 测试

`pytest tests/test_morning_macro_brief.py -q` → **28 passed**。关键用例：

- `test_resolve_news_total_10_from_12_candidates`：12 条候选（国内 7 + 海外 5）→ 10 条（国内 5、海外 5），组内新→旧；
- `test_text_cap_10_titles_from_12_candidates`：同样 12 候选走 resolve+build → text 里标题行共 10（国内 5、海外 5）；
- `test_resolve_news_domestic_capped_at_five`：7 条国内候选 → 5；
- `test_resolve_news_overseas_capped_at_five`：7 条海外候选 → 5；
- `test_no_line_trimming_all_links_render`：6 条全带 url → 6 行链接全保留（不裁剪）；
- 其余（要点 3 条、判断规则、链接校验、发送闸、调度）回归通过。

## 真机 smoke（只 build，未 notify）

2026-09-11 08:00 还原：窗内实际命中 8 条（国内 5 + 海外 3），8 条链接行全部保留，组内新→旧，24 行；要点取国内前 3，判断「偏冷。要闻出现「油价大涨」。」。发送闸不受影响。

## 没改什么

- 收盘日报、价值线、Focus、L3、低估池、价格区、涨停/定增口径
- 发送闸（`morning_gate_decision` 语义不变）、交付渠道（`feishu_morning_macro`）
- 东财/RSS/政策三级源与时间窗（前一日 15:00 → now）、组内排序规则
- 【要点】≤3、【判断】1 句、链接 http(s) 严校验（不编链接）
- `HZ_MORNING_MACRO` 保持 off；未 commit/push；未发飞书；未重启

## 风险

1. 版式上限放开后卡片最长约 27 行（10 条全带链接），飞书卡片可容纳但视觉变长。
2. 东财首页 `page_size=20` 未同步上调：若噪音条目偏多，窗内有效候选可能不足 10 条（fail-closed 显示实际条数，不编造）。
3. backend 未重启，运行中进程仍是旧代码。

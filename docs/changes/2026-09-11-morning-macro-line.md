# 2026-09-11 早盘宏观速览接入【环境】一行（日报同源宏观快照）

- 日期：2026-09-11
- 范围：仅 `agent/src/investment_research_supervisor/morning_macro_brief.py` + `agent/tests/test_morning_macro_brief.py`。收盘日报模板、价值线、Focus、L3、价格区均未改动；未重算宏观模型。
- 约束遵守：未 commit；未设 `HZ_MORNING_MACRO`（保持 off）；未发飞书；未重启 backend；8 点路径不调用宏观 refresh（只读已落库快照）。

---

## 改了什么

1. **`load_macro_environment_line(as_of_date, *, summary_fn=None)`**：只读已落库宏观快照的一句话环境，返回 `{text, snapshot_as_of, missing}` 或 `None`。默认走 `_default_macro_summary` → `InvestmentResearchDailyBriefService._macro_environment_text(as_of)`（与收盘日报「当前研究环境」完全同源，含收缩/滞胀附加句）。
2. **`build_morning_macro_brief` 插入【环境】行**：位置在【判断】之后、人民币中间价/【数字对照】之前；快照缺失（available=False）、句子为空或取数异常 → 整段省略，不编「中性」。新增可选参数 `macro_environment` 供测试注入。
3. 判定规则：`available` 即「status 可用」；缺数半句「资料还缺…，判断宜保守。」由快照文案自带（`compose_macro_owner_text`），渲染原句不加工；`missing` 字段仅从原句正则提取作 debug 标注，不参与渲染逻辑。

## 用的快照函数（文件:行）

- 收盘日报侧包装：`agent/src/investment_research_supervisor/daily_brief_service.py:1261` `InvestmentResearchDailyBriefService._macro_environment_text(research_as_of)`（staticmethod，fail-soft）
- 共享投影本体：`agent/src/macro_line/refresh.py:147` `get_macro_line_summary(as_of)`——零网络、零 LLM，读 `MacroDataService.build_snapshot(as_of)`（as-of 查询 ≤as_of 最新观察值）+ `MacroEventStore.events_for_summary` + `check_macro_source_freshness` 缺数标签
- 缺数半句拼装处：`agent/src/macro_line/refresh.py:138`

## 回退规则

无需逐日回退：`build_snapshot(as_of)` 是 as-of 语义查询（≤as_of 的最新序列观察值），「当日尚未采集」天然由该查询覆盖，返回的就是最新已落库视图；`summary.as_of` 记入 `snapshot_as_of`（实测为 2026-09-11）。仅当底层完全无数据（`available=False`）或句子为空时返回 `None` → 整段【环境】省略。取数异常 fail-soft 省略，绝不阻塞卡片、绝不编快照。

## 09-11 预览中的【环境】行（真机只读，未 notify）

```
【环境】经济和资金面没有明显方向（中性）。当前松紧差在信用偏冷、金融条件偏暖。资料还缺社融增量，判断宜保守。环境无变化，不据此调整研究名单。
```

- snapshot_as_of = 2026-09-11；missing = 「社融增量」
- 与【判断】（偏冷，来自「油价大涨」关键词）不一致——产品允许：判断只看要闻+外盘，环境是已落库宏观快照原文
- 整卡 25 行；人民币中间价行保持在【环境】之后、【数字对照】之前（原位置未动）

## 测试

`pytest tests/test_morning_macro_brief.py -q` → **34 passed**。新增 6 条（全 mock，不外网、不重跑宏观采集）：

- a. mock 快照返回「经济和资金面没有明显方向（中性）。…」→ text 含【环境】且含该句，并断言版式顺序【判断】→【环境】→ 中间价 →【数字对照】
- b. mock 快照 None / 空白句 → text 不含【环境】
- c. mock 快照带「资料还缺社融、M1，判断宜保守。」→ 环境段含「社融」与「保守」
- d. 发送闸不因环境句放行：`morning_gate_decision(0,0)`/`(1,0)` 仍 `SKIPPED_EMPTY`
- e. 源码 grep：`load_macro_environment_line` + `build_morning_macro_brief` 不含 `focus_selection`/`low_value_leader_pool`/`level3_leaders`/写库语句
- 另有 `load_macro_environment_line` 形状用例（available=False/空句/None→None；missing 提取）

## 没改什么

- 收盘日报模板与生成逻辑（只读其 `_macro_environment_text`，未动它）
- 价值线、Focus、L3、价格区、涨停/定增口径；环境句不作为任何筛选条件
- 宏观 refresh / 调度（早盘 8 点不触发任何宏观采集，只读已落库）
- 发送闸阈值、交付渠道、三级新闻源、要点/判断规则
- `HZ_MORNING_MACRO` 保持 off；未 commit/push；未发飞书；未重启 backend

## 风险

1. 环境句与【判断】可能方向不一致（本例：判断偏冷 vs 环境中性）——已在产品设计内，老板两行对照阅读；若认为困惑可后续在环境行尾加「（宏观快照口径）」注。
2. `_default_macro_summary` 懒导入 `daily_brief_service`（重模块），首次调用有一次导入开销；api_server 进程内该模块本就已加载，无实际影响。
3. 快照文案由宏观侧维护，若措辞调整（如「资料还缺」句式变化），`missing` 提取会失配——仅影响 debug 字段，不影响渲染（渲染的是原句本身）。
4. backend 未重启，运行中进程仍是旧代码。

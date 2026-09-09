# 2026-09-09 涨停事件按成交额降序排序（日报事件位）

- 日期：2026-09-09
- 性质：只改日报里涨停事件的**排序**。不改识别口径、价值线、Focus、outlook；未 commit；未连飞书。

---

## 一、改了什么 / 为什么

09-08 全市场扫到 **74 条涨停**。旧实现 `list_market_event_lines` 按 (event_type, stock_code) 排序后截 3 行——8 行预算里的 3 个事件位会被 000011 这类**小代码号**占据，看不出市场资金在追什么。

**改法**：`list_market_event_lines` 内部分两段排序后再拼接、截断：
1. **涨停**：按事件明细里的 `amount`（成交额，元）**降序**；缺 amount 的排最后（键值缺省按 0 处理）。
2. **定增**：保持原顺序（stock_code 序），排在涨停之后。
3. 合并后按 `max_lines`（日报预算 3）截断；总行仍 ≤8（价格条件先占位的既有合并逻辑 `merge_market_event_lines` 不变）。

## 二、涉及文件

- `agent/src/value_strategy/market_events.py`（`list_market_event_lines`：涨停按 `after_state.amount` 降序 → 定增原序 → 拼接截断）
- `agent/tests/test_market_events_p1.py`（新增 2 用例 + 修正 1 处手写 INSERT 的占位符/列数）

## 三、怎么测

```
pytest tests/test_market_events_p1.py -q
→ 10 passed，其中新增：
  ① 3 条涨停额 1 亿 / 10 亿 / 3 亿 → 事件行顺序 000020.SZ(10亿) → 000059.SZ(3亿) → 000011.SZ(1亿)，
     第一行是 10 亿那只
  ② 缺 amount 的涨停排最后（有额 000011.SZ 在前，缺额 999999.SZ 在尾）
```

## 四、没改什么

- 涨停识别口径（幅度/贴板/额门槛/排除规则）——排序只影响日报展示顺序，不影响哪些票算涨停
- 价值线 / Focus / L3 / 价格区 / outlook
- 定增通道（仍公告日口径、保持原顺序、排涨停之后）
- 预算规则（事件 ≤3、总行 ≤8、价格条件先占位）、bitable 空源门
- git（未 commit/push）

## 五、已知风险

1. **缺额行**：details 无 amount 的涨停排最后（正常 ingest 写入的行都带额；手工直插才可能出现）。
2. **展示 ≠ 重要性**：按额排序只反映资金关注度，不代表该事件对研究名单的影响权重。

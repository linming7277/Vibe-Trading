# 2026-09-10 早盘外盘主源切换为本机通达信扩展行情 .day

- 日期：2026-09-10
- 性质：早盘宏观速览外盘数据源切换。不改涨停口径、价值线/Focus/价格区、outlook 其它规则；未 commit/push；未设 HZ_MORNING_MACRO=on；未真发飞书。

---

## 一、改了什么 / 为什么

早盘外盘原走 Yahoo——实测对指数符号持续限流（YFRateLimitError），外盘段长期「资料不足」。核查发现本机通达信 `vipdoc/ds/lday/` 有**扩展行情 .day**（自动更新，今天 11:46 仍在落盘），覆盖纳指/纳指100/恒生/美元兑人民币四条序列。本批：

1. `day_file.py` 新增 `read_ds_lday(path, count=None)`——`ds/lday` 扩展行情专用读取器：32 字节/根，**OHLC 为 float32**（偏移 4/8/12/16），amount f32、volume u32、保留 u32。与 A 股 int÷100 布局**不同，禁止复用 `read_lday` 读 ds**（会得到垃圾价，如 USDCNY「10878111」）。校准：IXIC 末根 = 2026-09-09 收 26253.34 ✓。
2. `morning_macro_brief.fetch_overseas_indices` 改为：
   - **主源**：ds `.day`——纳指←`12#A_IXIC.day`、纳指100←`12#A_NDX.day`、恒生←`27#HSI.day`
   - **标普/道指**：ds 无对应文件 → Yahoo 兜底（当前限流时如实「资料不足」）
3. **日期口径**：只用 `date < as_of_date` 的最后一根完整日线（早盘日当日盘中根裁掉；.day 若已追加 T+1 盘中根同样裁掉）——09-10 08:00 简报：美股用 09-09、恒生用 09-09（09-10 港股盘中根不冒充隔夜收盘）。涨跌幅 = 该根收盘 / 再前一根收盘 − 1。
4. **人民币兜底行**：官方中间价缺失时，用 `10#USDCNY.day` 末根（≤as_of）写「美元兑人民币（扩展行情） X.XX」——**显式标注口径，绝不冒充官方中间价**；有官方中间价时此行不出现。

## 二、涉及文件

- `agent/src/tdx_data/day_file.py`：`_DS_BAR` 结构 + `read_ds_lday`
- `agent/src/investment_research_supervisor/morning_macro_brief.py`：`fetch_overseas_indices` 主源切换（ds → Yahoo 按指数兜底）+ `load_usdcny_ds` + build 人民币行 ds 兜底 + build/cny 签名增加 `tdx_home`
- `agent/tests/test_morning_macro_brief.py`：ds 夹具用例 + 断言更新

## 三、怎么测

```
pytest tests/test_morning_macro_brief.py -q → 12 passed（含 ds 读取已知值、
  as_of 裁剪、Yahoo 兜底注入、人民币标签、质量门）
pytest tests/test_sw1_index_bars.py -q → 通过（A 股 read_lday 回归不坏）
ruff → All checks passed
```

## 四、2026-09-10 08:00 预览 text（真实 ds 文件，未发送）

```
【隔夜外盘】标普 资料不足，纳指 26253(-0.64%)，道指 资料不足，恒生 25317(-0.38%)，纳指100 29422(+0.05%)
人民币中间价 6.7804
【对今日环境】偏冷（隔夜外盘偏跌）。
【缺数】标普、道指
```
（09-10 早盘口径：美股/恒生各取 09-09 收盘；标普/道指无 ds 文件 → 如实「资料不足」，Yahoo 限流时不编数。）

## 五、没改什么

- 涨停 10%/20% 口径与额门槛（`day_tail_universe` 只做「读 ds」能力扩展的同族重构，10/10 涨停用例全绿）
- 价值线（L3/低估池/Focus）、价格区、position_label、收盘日报
- git（未 commit/push）

## 六、已知风险

1. **ds 为扩展行情口径**：10#USDCNY（6.71）与官方中间价（6.7804）有 ~1% 差（疑似离岸/不同定价源），前端已显式标注「扩展行情」，不得与官方混写。
2. **盘中根污染**：通达信盘中即写当日根——已按 `date < as_of` 裁剪；但若通达信对历史根做复权重算，历史口径会有小幅漂移（与 A 股 .day 同族风险）。
3. **标普/道指长期缺 ds 文件**：除非通达信开通对应扩展行情，否则这两行持续「资料不足」（Yahoo 兜底恢复时除外）。

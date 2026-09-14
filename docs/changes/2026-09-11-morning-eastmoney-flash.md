# 2026-09-11 早盘宏观速览新闻主源：东财快讯接入

- 日期：2026-09-11
- 范围：仅 `agent/src/investment_research_supervisor/morning_macro_brief.py` + 对应测试。收盘日报、价值线、Focus、L3、低估池、价格区均未改动。
- 约束遵守：未 commit；未设 `HZ_MORNING_MACRO`（保持 off）；未发飞书（预览只走 build，不调 notify）。

---

## 问题

上一版「新闻为主」改造只写了抓取函数，管线没接通，属于死代码：

1. `fetch_eastmoney_flash` 调的是错误端点 `getFastNews`（无参数返回 0 条），正确端点是 `getNewsByColumns`（column=350 财经快讯）。
2. `resolve_news` 根本没调用 `fetch_eastmoney_flash`；`send_morning_macro_brief` 也没调用 `resolve_news` → 生产路径要闻恒为空。
3. `fetch_rss_items` 收了 `window_start/window_end` 却不过滤时间窗，旧闻会混入。
4. RSS 条目带 `published`(datetime)，build 期望 `time`，形状不一致渲染为空。

## 修复

- **`fetch_eastmoney_flash`**：改用 `getNewsByColumns?client=web&biz=web_724&column=350`，解析 `showTime`（`%Y-%m-%d %H:%M[:%S]`）为北京时间 `published`；标题缺失回退 `summary` 去【】前缀；解析失败/无标题条目丢弃；网络失败返回空（备源兜底）。
- **`fetch_rss_items`**：真正应用时间窗过滤（`window_start <= published <= window_end`）。
- **`resolve_news`**：三级合并 东财快讯 > RSSHub 财联社/金十 > policy_events/公司公告；统一时间窗（as_of 前一日 15:00 → now）；标题去重；丢噪音（涨停/龙虎榜/打板/广告/加微信/定增/增发 + 带 6 位代码的利好/大涨/跌停标题）；返回扁平列表 `domestic[:3] + overseas[:5]`（国内在前、海外在后），条目统一 `{time, source, title}` 或 `{date, source, title}`。
- **`build_morning_macro_brief`**：要闻行渲染恢复 `time or date` 回退（政策/公告条目只有 date）；删除无作用的 sign 分支。
- **`send_morning_macro_brief`**：接入 `news = resolve_news(...)` 再 build——生产路径要闻真正有数据。
- 清理死常量：`_NEWS_DROP_MARKS`、重复的 `_OVERSEAS_KW`、未使用的 `_EQUITY_PREFIXES`。

## 测试

`agent/tests/test_morning_macro_brief.py` **15 passed**（新增 4 条全 mock 用例：em 解析/坏时间丢弃、网络失败返回空、时间窗+噪音过滤+归一化、国内 ≤3 封顶）。含 import 纯净度子进程用例。

## 真机只读预览（2026-09-11 08:00 还原，未发送）

`resolve_news("2026-09-11", now=08:00)`（预览侧拉 50 条补偿已被第一页顶出的隔夜条目）→ 6 条（国内 3 + 海外 3，全部东财快讯命中）；`build` 文本：

```
【隔夜要闻】
05:21 东财快讯｜金融强国建设"十五五"规划出台！四部门详解
07:44 东财快讯｜更好发挥金融市场促进新质生产力发展的作用
07:42 东财快讯｜"十五五"时期金融将加力支持科技创新
07:57 东财快讯｜殷勇会见美国标普全球集团总裁兼首席执行官
07:27 东财快讯｜界面早报 | 新一批国家组织高值医用耗材集采纳入23个品种；欧洲央行将三大关键利率上调25个基点
07:00 东财快讯｜【早报】油价大涨，黄金、白银大跌；美股存储板块，集体回落；燧原科技，今日上市；甲骨文，业绩超预期
人民币中间价 6.7804
【数字对照】纳指 26253.34(-0.64%)，纳指100 29421.55(-0.29%)，恒生 24924.46(-1.39%)
【对今日环境】偏冷。
```

要闻 6 条 + 有效外盘 3 个 → 发送闸满足。RSS 源仍失败（自动降级为空，不阻塞）。

已知小瑕疵：要闻排序沿东财接口原始次序（05:21 在 07:44 前），未做组内新→旧排序；如需可加一行组内 sort。

## 后续（未做，需授权）

- 运行中的 backend（PID 16064）需重启才会加载本模块新代码；`HZ_MORNING_MACRO=on` 由用户决定何时设置。

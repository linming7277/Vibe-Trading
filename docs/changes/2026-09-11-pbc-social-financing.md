# 2026-09-11 社融增量接入人民银行免费表

## 数据源
- 目录页：`https://www.pbc.gov.cn/diaochatongjisi/116219/116319/5570903/5570885/index.html`
- 实际表页（本次解析）：`https://www.pbc.gov.cn/diaochatongjisi/attachDir/2026/08/2026081417010772070.htm`
  （《社会融资规模增量统计表》，单位亿元，发布日 2026-08-14）

## 解析规则
1. 目录页先按 `<a>` 锚点找「社会融资规模增量统计表」链接；目录是 JS 壳时退回扫描
   静态脚本内嵌的 attachDir 链接；并把已核实的 2026-08-14 样例 URL 作为保底候选。
   候选按 URL 路径日期取最新，逐个尝试。
2. 表页两种形态都兼容：
   - 普通表格（th 表头含「社会融资规模增量」）→ pandas.read_html 按表头取列；
   - Excel 导出裸表格（`<td>2026.07</td><td>14017</td>`，无表头文字）→ 扫描
     「月单元格 → 下一个数值单元格」。
3. 月列形如 2026.07/2026.7；数值可含逗号；空单元格（未来月份如 2026.08）、
   非数字一律丢弃，绝不写 0。
4. 防接错表：页面含「存量统计表」「社会融资规模存量」「万亿元」→ 直接放弃。
5. observation_date = 该月 1 日；release_date = 页面发布日（从 attachDir 路径日期
   解析，如 2026-08-14）；release_status = first_observed_only；source = PBOC；
   source_url = 实际 htm。HTTP 失败/解析失败 → 空列表，不阻断其它宏观序列。

## 接线
`MacroDataService._fetch_pboc_social_financing` 重写：央行表优先 → akshare 兜底；
`_fetch_akshare` 跳过社融（不再触发商务部 SSL 失败，也避免双源重复）。
Tushare 从社融链路移除（不再作为必须）。

## 入库与验证
- 真机解析 7 条（2026-01 至 2026-07），最近 3 个月：2026-05 = 20,293 亿、
  2026-06 = 33,671 亿、**2026-07 = 14,017 亿**（与任务书已核实样例一致）。
- refresh 后库内 `social_financing_increment` 最新 observation_date = **2026-07-01**，
  value = **14017.0**。
- 环境句（get_macro_line_summary("2026-09-11")）刷新后：
  「经济和资金面没有明显方向（中性）。当前松紧差在信用偏冷、金融条件偏暖。环境无变化…」
  ——**不再写「资料还缺社融增量」**。

## 测试
`pytest tests/test_pbc_social_financing.py` → 4 passed（全部 mock HTML，不打外网）：
1. 增量表 2026.07=14,017、2026.08 空 → 只入库 7 月；
2. 存量表目录/存量口径页面 → 0 条（防接错表）；
3. HTTP 失败 → 空列表，refresh 其它宏观序列不受影响；
4. 序列存在时 freshness missing 不含「社融增量」，序列为空时含（对照）。

## 未改什么
未改价值线筛选、Focus、价格带、早盘开关、日报链路、MCP、bitable；
Tushare 社融镜像代码已从主链移除；未 commit、未发飞书。

## 风险
1. 央行目录页为 JS 渲染，静态 HTML 只内嵌最早两份表；之后的新表若继续
   JS 化，本抓取只能拿到「已知样例 URL」这一份，需要后续找到其列表 JSON
   接口才能自动追新（接口已隔离在 `_fetch_pbc_social_financing_flow` 一处）。
2. 社融为月度数据且发布滞后（央行 8/14 发 7 月表），freshness 的月份窗口
   判断沿用现有 macro_series 机制，未单独调整。
3. Excel 变体的列序假设是「月列后第一列为增量」——与央行增量统计表固定
   版式一致，但若央行改版需同步调整扫描逻辑。

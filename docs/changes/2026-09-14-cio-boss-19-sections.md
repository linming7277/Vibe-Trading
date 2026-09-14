# 2026-09-14 老板版深度分析对齐 CIO 19 节

## 问题
飞书/MCP「深度分析流程」按旧 14/15 节大纲重写输出，10a 未来三年利润预估明细
与 05c 财报隐藏信息扫描被丢弃或并入别的标题。老板要的是 19 节原结构。

## 正式 19 节（SECTION_TITLES 顺序，cio_report/builder.py:19-38）
01 公司与产业位置 / 02 龙头质量与同行优势 / 03 多年财务路径 / 03b 最新季度边际变化 /
04 当前经营阶段 / 05 盈利质量与财务风险 / 06 经营与业务结构 / 07 竞争优势 /
08 资本配置 / 09 当前估值 / 09b 正常化盈利参考 / 10 财务情景预测 /
10a 未来三年利润预估明细 / 05c 财报隐藏信息扫描 / 10b 周期利润情景 /
11 为什么值得继续研究 / 12 为什么需要谨慎 / 13 核心逻辑、证伪条件与验证点 /
14 CIO 最终研究结论

## 改动
1. `cio_report/narrative.py`
   - `BOSS_SECTIONS` 对齐 SECTION_TITLES（19 节正式名单同序），不再是大纲合并视图；
   - `render_boss_report` 逐节输出：标题用官方节名，正文 = 该节确定性叙述原文
     （BossRenderer 组合或节叙述），缺节输出「本节资料不足」；
     禁止合并改写成旧大纲；不触发任何刷新/LLM。
2. `agent/mcp_server.py`
   - `get_cio_report` docstring「unified 14-section」→ 19-section；
   - ask 工具「full 14-section deep report」→ 19-section。
3. `hermes-skills/investment-research-supervisor/SKILL.md`
   - 「14 节持久化报告」→「19 节持久化报告」；「（14 节标题保留）」→「（19 节标题保留）」。
4. `cio_report/builder.py` 模块 docstring 14-section → 19-section；narrative.py 两处旧注释同步。

## 渲染与降级规则（重申）
- 有节：输出该节确定性叙述原文（不做二次改写）。
- 缺节：标题仍在，正文「本节资料不足」——绝不为缺节触发
  refresh_cio_report / build_report / _synthesize。
- 读取路径保持零创建（2026-09-14 已修：无报告直接返回 None）。

## 测试
- `pytest tests/test_cio_narrative.py tests/test_cio_report.py
  tests/test_cio_quick_brief.py tests/test_cio_incremental_synthesis.py
  tests/test_cio_default_as_of.py tests/test_cio_price_block.py` → **58 passed**。
- 三个旧大纲契约测试更新为新契约：19 节顺序与标题、10a/05c 标题必须在、
  05/13 节承载核心矛盾事实内容。

## 未改什么
Focus A=10、价值线入池、fiscal_year_flows、价格带、MCP 只读闸、bitable、
live_routes、早盘开关、worker（保持 off）；未 commit、未 push、未发飞书；
未对全池重建、未跑任何新 LLM。

## 风险
1. 存量旧报告（17 节）在下次重建前于老板端显示为 17 节（缺 10a/05c 两节，
   显示「本节资料不足」）——EOD 维护或手动重刷后自愈。
2. 分节增量综合的分节预算（每节 120–300 字 × 19 节）比 14 节版本长约 35%，
   端点配额紧张时全篇 LLM 叙述完成时间相应变长（缓存与降级机制不变）。

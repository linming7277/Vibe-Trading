# 2026-09-11 45 只风险 UNKNOWN 修复（路 A：业务研究重跑 + 快照重算）

- 日期：2026-09-11
- 性质：数据生产（LLM 业务研究 45 只）+ 风险快照重算。**未改任何引擎代码/口径**。未 commit/push、未发飞书、未动 Focus/筛选规则/SHADOW/早盘开关。
- 背景：前端「风险复核：资料不足」= `overall_risk=UNKNOWN` 的固定文案（`low_value_risk_snapshot/service.py:33`；显示端 `_risk_label`，`investment_research_supervisor/service.py:133`）。此前判定根因是风险引擎缺「可确认的业务风险事实 + 研报观点」，不是财务数据（财务已核实齐全，见同日 data-backfill 文档）。

---

## 做了什么

1. **单只闭环验证**（000719.SZ）：`BusinessResearchService.analyze(force=True)` 重跑业务研究 → 新 claims 命中「前五名客户…占…」判定点 → `get_risk_research` 实时判定翻案 **MEDIUM**（BUSINESS_CUSTOMER_CONCENTRATION）。
2. **批量 45 只**（`tmp/backfill45_business_research.py`，逐只 `analyze(force=True, as_of='2026-09-11')` + `refresh_company_snapshot(source_as_of='2026-09-11')`，单只失败继续）：全部 COMPLETED，无 error 行。claims 质量：除 3 只（600874/601318/601921，claims=0，无可用披露文本）外，每只 4–8 条、带引用 3–7 条。

## 结果数字

| 指标 | 补前 | 补后 |
|---|---|---|
| 45 只 overall_risk | 45 UNKNOWN | **18 MEDIUM / 27 UNKNOWN** |
| 翻案率 | — | 40%（18/45） |

18 只翻案均产出 `BUSINESS_CUSTOMER_CONCENTRATION`（MEDIUM/WATCH）或业务变化类风险，带年报引用。

## 生效路径

- **公司研究页**（`/api/value/companies/{code}/risk-research`，`as_of=None` 实时判定）：读最新业务研究件重算——18 只刷新页面即显示「有风险需要复核」；27 只仍「资料不足」。
- **投研日报**：按 `research_as_of` 读快照行。09-10 的历史行保持 UNKNOWN（历史如实）；已写 09-11 新行，明晚池日推进后 EOD 自然接用。

## 剩余 27 只 UNKNOWN 的机理（诊断实证）

1. **LLM 输出非确定性 × 关键词判定**：000719.SZ 两次分析对照——第一版 claims 含「前五名客户合计销售金额占…」→ 命中判定 → MEDIUM；批量重跑版同票 claims 换措辞、无客户集中度内容 → 落空回 UNKNOWN。业务研究 prompt 的主题枚举只有 `MAIN_BUSINESS/PRODUCT/BUSINESS_MODEL/BUSINESS_CHANGE`（`business_research/service.py:30`），**没有客户集中度/收入占比主题**，风险引擎（`risk_research/service.py:634-640`）靠文本关键词「前五名客户/占营业收入」碰瓷式命中。
2. **披露源本身缺失**：部分票年报无前五名客户/分业务收入披露（prep 表 `official_disclosure_sources` 相应 MISSING）——重跑也不会有。
3. **7 只无 thesis**、`MARKET_SHARE` 判定恒缺（引擎设计内诚实缺口）。

## 根治建议（未实施，需授权——属口径/prompt 改动）

- 业务研究 prompt 强制要求输出「客户集中度 / 收入结构占比」两个主题的带引用 claim（年报有披露时）；或
- `_business_risks` 判定关键词扩展 + 将 `CUSTOMER_CONCENTRATION`/`PRODUCT_REVENUE_SHARE` 纳入 `BUSINESS_TOPICS`。

## 涉及文件

- 写库：`company_business_research_snapshots` +45 行（重分析）、`company_low_value_risk_snapshots` +45 行（source_as_of=2026-09-11）
- 新增脚本：`tmp/backfill45_business_research.py`（可复用；勿入库，tmp 历史有被误 commit 前科）
- 代码：**0 行改动**

## 没改什么

Focus 名单与 A=10、筛选规则、价格带、风险引擎判定代码、SHADOW/实验臂、早盘开关（off）、日报模板。未 commit/push、未发飞书、未重启 backend。

## 风险

1. 27 只仍 UNKNOWN（60%）：机理如上，纯数据重跑的边际收益已尽，继续重骰 LLM 输出不可控。
2. LLM 重分析为单飞租约保护，但输出随模型温度波动——同一票多次结果可能不同（本次实证）。风险结论因此存在抖动可能。
3. 3 只（600874/601318/601921）claims=0：本地披露文本不足，需先补披露材料才能有据产出。
4. 09-11 快照行与 09-10 历史行并存：日报回看 09-10 仍显示 UNKNOWN，属 PIT 如实行为。

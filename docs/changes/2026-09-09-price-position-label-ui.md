# 2026-09-09 价格落点前端展示接入 position_label

- 日期：2026-09-09
- 性质：前端展示修正（公司研究页价格区）。不改后端公式、不改估值/Focus/风险算法、不改 outlook、未 commit/push、未连飞书。

---

## 一、改了什么 / 为什么

后端 `get_price_zones` 已返回正典六句 `position_label`（见 `2026-09-09-price-position-labels.md`），但公司研究页（`/company/:market/:symbol`）的价格区结论卡仍由前端按「现价在不在结构带内」自拼文案——低于低估关注区下沿的深度便宜股会落到「现价未落入关注/观察/复核带」，老板误读为「还没到便宜」。

**修复**：`describePricePosition` 改为**优先直显后端 `position_label`（逐字，不自造）**；无该字段（旧后端）时降级为原逻辑，降级路径保持「现价低于合理价值带下限」的诚实表述（本就不写「未落入」，已加测试锁定）。

## 二、涉及文件

- `frontend/src/lib/api.ts`：`ValuePriceZones` 接口新增 `position_label?: string`
- `frontend/src/components/value/ValuePriceZoneConclusionCard.tsx`：`describePricePosition` 正典优先 + 降级路径语义保持
- `frontend/src/components/value/__tests__/ValuePriceZoneConclusionCard.test.tsx`：新增 3 个正典落点用例（合计 11 用例）

## 三、怎么测

```
npx vitest run src/components/value/__tests__/ValuePriceZoneConclusionCard.test.tsx
→ 11 passed（含新增：
  ① position_label=「低于低估关注区」→ 逐字直显
  ② position_label=「未落入（偏贵，尚未进入观察带）」→ 逐字直显
  ③ 降级（无 position_label、现价 11 < fair_value_low 12 且不在任何结构带）
     → 「现价低于合理价值带下限」，断言不含「未落入」）

npx vitest run src/pages/__tests__/CompanyResearch.test.tsx
  src/components/value/__tests__/ValuePriceZoneConclusionCard.test.tsx
→ 2 个测试文件、12 tests 全过

npx tsc --noEmit → 无类型错误
```

## 四、没改什么

- 后端估值公式、价格带边界、`position_label` 六句集合（本次只做前端消费）
- Focus 排序、低估池、L3、风险快照
- `LeaderCompanyQuickView` 的数据说明弹窗（它不拼「未落入」，走的是中枢距离/支撑关系文案）
- `ValueFocusPage`（未拼落点文案）
- 日报卡片与 Excel（后端已直用 `position_label`，本任务不碰）
- git（未 commit/push）

## 五、已知风险

1. **双后端窗口期**：生产 backend 在重启前仍返回无 `position_label` 的旧响应——页面自动走降级旧文案（诚实、无「未落入」误标低于下沿的情况）；重启后自动切正典六句。
2. **降级文案非正典**：无 `position_label` 时显示「现价低于合理价值带下限」（锚点是合理价值下限，不是低估关注区下沿）——与正典第五句口径不同但方向一致、无误导；正典只由有字段的响应承载。
3. **LeaderCompanyQuickView 的数据说明弹窗**未接 `position_label`（它展示中枢距离与支撑关系，不拼落点句）；如需同源可后续小改。

# 2026-09-09 价格落点标签正典化（低于低估关注区 ≠ 未落入）

- 日期：2026-09-09
- 性质：文案/投影修正。不改估值公式与带宽计算、不改 Focus 排序（A=10）、不改风险等级算法、不 commit/push、未连飞书。

---

## 一、改了什么 / 为什么

**问题**：产品约定「未落入 = 现价在所有观察/关注带**上方**，还没跌进带」。但旧落点句 `_price_position_sentence` 的第 5 分支把「带存在但现价不在任何带内」一律写成 **「现价未落入关注/观察/复核带」**——现价**低于**低估关注区下沿的深度便宜股也落到这个分支。2026-09-08 Focus A 十家其中 6 家（现价低于低估关注区下沿）因此被标成「未落入」，老板会误读成「还没到便宜」，实际是「比便宜带更便宜」。

**修复（正典六句，逐字固定，不得自造）**：
1. 现价高于高估复核区上沿 → `未落入（偏贵，尚未进入观察带）`
2. 现价在高估复核区 → `高估复核区`
3. 现价在中性带 → `中性`
4. 现价在低估关注区 → `低估关注区`
5. 现价低于低估关注区下沿 → `低于低估关注区`
6. 没有可用区间或停牌/价格无效 → `资料不足`

**实现**：
- `src/value_price_zones/service.py` 新增正典函数 `price_position_label(current_price, valuation_zones=None)`：按 `_valuation_zones` 阶梯（深度低估区/较高安全边际区/**低估关注区**/合理区/**偏高区/明显偏高区**，None 边界=开放端）顺序归带；带内→按带名映射（复核/中性/低估关注/低于低估关注）；带间空隙或无带→「资料不足」（不发明第七句）。`get_price_zones` 返回新增 **`position_label`** 字段（additive，随 API 自动下发公司研究页）。
- `src/investment_research_supervisor/daily_brief_service.py`：`_price_position_sentence` 重写为正典六句委托（入参改 `valuation_zones`）；日报价格条件段改从同一 `position_label` 取文案——**日报价格条件、飞书卡片、Excel、公司研究页（API 消费方）全部同源**。旧结构带句子（落在关注/观察/复核带内、未落入关注/观察/复核带、低于合理价值带下限、带不完整）退役。
- 昨日 `2026-09-08-focus-a-extract.md` 的「未落入」为提取脚本按带包含关系错误归带所致（现价低于全部估值带时被误标），非产品代码路径——该文档由本记录更正。

## 二、涉及文件

- `agent/src/value_price_zones/service.py`（+`price_position_label` 正典函数、`get_price_zones` 返回新增 `position_label`；顺带拆一处既有 E702 分号行，零逻辑变化）
- `agent/src/investment_research_supervisor/daily_brief_service.py`（`_price_position_sentence` 正典化；digest 改传 `valuation_zones`；清理因此失效的 `valuation` 死变量；更新过时 v27 注释）
- `agent/tests/test_price_position_labels.py`（新增，11 用例）
- `agent/tests/test_investment_research_daily_brief.py`（两处旧标签断言改写为正典六句 + 600216.SH 对照夹具）

## 三、怎么测

```
pytest tests/test_price_position_labels.py -q
→ 11 passed（低于低估关注区 / 低估关注区 / 高于全部有限上沿→未落入 /
  无带无价→资料不足 / 高估复核区与中性 / 未知带名不发明句子 /
  五参数化只允许六句正典之一）

pytest tests/test_investment_research_daily_brief.py -q
→ 31 passed + 1 已知秒级 flake（test_notify_resends_card_when_brief_is_rebuilt_after_send，
  与本改动无关，单跑即绿，见 2026-09-09-mainline.md §五.6）

pytest tests/test_value_price_zones.py -q → 9 passed（估值带公式零改动佐证）
ruff（四文件）→ All checks passed
```

关键对照（夹具=600216.SH 2026-09-08 真实数字）：现价 **12.95**，合理价值 21.03–77.30，低估关注区下沿 16.82（=21.03×0.8）→ **「低于低估关注区」**，不再是「未落入」。

## 四、没改什么

- 合理价值与带宽计算公式（`_valuation_zones` 的折扣/缓冲参数、`_structure` 支撑阻力算法零改动）
- Focus 排序与 A=10 名额；风险等级算法
- 估值/风险快照与各表（0 写库；提取脚本在 `agent/tmp/`）
- git（未 commit/push）、飞书、live_routes
- 前端公司研究页展示代码（后端 `position_label` 已随 API 下发，前端消费另行跟进）

## 五、已知风险

1. **前端展示跟进**：公司研究页若仍从旧字段拼接文案，需一次前端小改才能显示新落点（后端字段已就绪）。
2. **「高估复核区」聚合了两条带**（偏高区/明显偏高区）——正典六句不区分二者；若未来需要细分，属于六句集的扩展决策，不在本次。
3. **带间空隙**：合成夹具人为留洞时落「资料不足」；生产阶梯连续，不触发。
4. **旧文案残留**：2026-09-08 已外发卡片为旧文案，不重写历史；09-09 起的日报/卡片/Excel 均为新落点。

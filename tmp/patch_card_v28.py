"""V28 卡片重排：市场复盘 → 预测复盘 → 宏观+前瞻 → 变化（含价格摘要压缩）→ Focus 压缩 → 观察 → 按钮。"""
from pathlib import Path

p = Path('src/investment_research_supervisor/daily_brief_notification_service.py')
text = p.read_text(encoding='utf-8')

# ---- 新增三个块构建函数（插在 _macro_environment_block 前） ----
anchor_fn = "def _macro_environment_block(brief: dict[str, Any]) -> list[dict[str, Any]]:"
new_fns = '''def _market_review_block(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """§四-一：今日市场复盘（指数 + 行业强弱 Top/Bottom3）。"""
    payload = dict(brief.get("brief_payload") or brief)
    review = dict(payload.get("market_review") or {})
    if not review.get("available"):
        return [{"tag": "markdown", "content": "**今日市场复盘**\\n暂无可靠数据（今日收盘行情未入库）。"}]
    rows: list[dict[str, Any]] = [{"tag": "markdown", "content": "**今日市场复盘**"}]
    benchmark = dict(review.get("benchmark") or {})
    parts = []
    for index in review.get("indices") or []:
        if index.get("status") != "READY":
            continue
        ret = index.get("ret_1d")
        parts.append(f"{index['name']} {ret * 100:+.2f}%" if ret is not None else f"{index['name']} 暂无可靠数据")
    if benchmark.get("market_class"):
        rows.append({"tag": "markdown", "content":
            f"沪深300 **{benchmark['market_class']}**（{parts[0] if parts else '—'}）"})
    if parts[1:]:
        rows.append({"tag": "markdown", "content": "　".join(parts[1:4])})
    strong = list(review.get("strong_industries") or [])[:3]
    weak = list(review.get("weak_industries") or [])[:3]
    if strong or weak:
        strong_text = "、".join(f"{i['name']} {i['rr'] * 100:+.1f}pp" for i in strong) or "—"
        weak_text = "、".join(f"{i['name']} {i['rr'] * 100:+.1f}pp" for i in weak) or "—"
        rows.append({"tag": "markdown", "content":
            f"相对沪深300最强：{strong_text}\\n相对沪深300最弱：{weak_text}"})
    if review.get("one_liner"):
        rows.append({"tag": "markdown", "content": f"{review['one_liner']}"})
    return rows


def _forecast_review_block(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """§四-二：昨日预测复盘（官方优先；SHADOW 标注）。"""
    payload = dict(brief.get("brief_payload") or brief)
    review = dict(payload.get("forecast_review") or {})
    if not review.get("available"):
        return [{"tag": "markdown", "content":
            "**昨日预测复盘**\\n上一交易日未形成有效预测，本日无预测成绩可复盘。"}]
    rows: list[dict[str, Any]] = [{"tag": "markdown", "content":
        "**昨日预测复盘**" + ("（影子预测复盘）" if review.get("is_shadow") else "")}]
    market = dict(review.get("market") or {})
    if market.get("evaluation") in ("HIT", "MISS"):
        result = "命中" if market["evaluation"] == "HIT" else "未命中"
        predicted = {"STRONGER": "偏强", "RANGE_BOUND": "震荡", "WEAKER": "偏弱"}.get(
            market.get("predicted"), market.get("predicted") or "未生成")
        actual_ret = market.get("actual_return")
        rows.append({"tag": "markdown", "content":
            f"大盘：预测**{predicted}**，实际{market.get('actual_class')}（{actual_ret * 100:+.2f}%）→ **{result}**"})
    elif market.get("evaluation") == "ABSTAINED":
        rows.append({"tag": "markdown", "content": "大盘：预测暂不判断（不计入成绩）。"})
    else:
        rows.append({"tag": "markdown", "content": "大盘：数据不足无法评价。"})
    for side_label, key in (("相对看强", "strong_industries"), ("相对看弱", "weak_industries")):
        items = list(review.get(key) or [])[:3]
        if not items:
            continue
        text_parts = "、".join(
            f"{i['name']} {i['rr'] * 100:+.2f}pp {'命中' if i.get('evaluation') == 'HIT' else '未命中'}"
            if i.get("rr") is not None else str(i.get("name")) for i in items)
        rows.append({"tag": "markdown", "content": f"{side_label}：{text_parts}"})
    rows.append({"tag": "note", "elements": [{"tag": "plain_text",
                 "content": "当前预测样本仍少于20个交易日，暂不评价长期有效性。"}]})
    return rows


def _next_outlook_block(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """§四-三：宏观环境（复用现环境块）+ 下一交易日前瞻。"""
    rows: list[dict[str, Any]] = [*_macro_environment_block(brief)]
    payload = dict(brief.get("brief_payload") or brief)
    outlook = dict(payload.get("next_outlook") or {})
    if not outlook.get("available"):
        reason = str(outlook.get("reason") or "")
        rows.append({"tag": "markdown", "content":
            f"**下一交易日前瞻**\\n下一交易日前瞻暂未生成：{'模型运行失败' if 'MODEL' in reason.upper() or not reason else reason or '未生成'}。"})
        return rows
    if outlook.get("calendar_unverified"):
        rows.append({"tag": "markdown", "content":
            "**下一交易日前瞻**\\n下一交易日尚未完成日历确认，前瞻暂按候选交易日留档，不作为正式预测。"})
        return rows
    rows.append({"tag": "markdown", "content": "**下一交易日前瞻**"})
    if outlook.get("abstained"):
        rows.append({"tag": "markdown", "content":
            "大盘：暂不判断（当前证据不足，暂不形成明确方向判断。）"})
    else:
        direction = {"STRONGER": "偏强", "RANGE_BOUND": "震荡", "WEAKER": "偏弱"}.get(
            outlook.get("direction"), outlook.get("direction") or "未生成")
        summary = _short_text(outlook.get("summary"), limit=110)
        rows.append({"tag": "markdown", "content": f"大盘方向：**{direction}**\\n{summary}"})
    strong = list(outlook.get("strong_industries") or [])[:3]
    weak = list(outlook.get("weak_industries") or [])[:3]
    if strong:
        rows.append({"tag": "markdown", "content":
            "相对看强：" + "、".join(str(i.get("name")) for i in strong)})
    if weak:
        rows.append({"tag": "markdown", "content":
            "相对看弱：" + "、".join(str(i.get("name")) for i in weak)})
    invalidation = list(outlook.get("invalidation") or [])[:1]
    if invalidation:
        rows.append({"tag": "note", "elements": [{"tag": "plain_text",
                     "content": f"失效条件：{_short_text(invalidation[0], limit=90)}"}]})
    gaps = list(outlook.get("data_gaps") or [])[:3]
    if gaps:
        gap_cn = {"OVERSEAS_EQUITY_INDEX_UNAVAILABLE": "海外市场信息未纳入",
                  "USDCNY_STALE_SINCE_2021_05": "美元兑人民币数据缺失",
                  "SOCIAL_FINANCING_MISSING": "社融序列缺失",
                  "SCHEDULE_NO_RELIABLE_SOURCE": "未来事件日程无可靠来源"}
        rows.append({"tag": "note", "elements": [{"tag": "plain_text",
                     "content": "数据限制：" + "；".join(gap_cn.get(g, g) for g in gaps)}]})
    return rows


def _macro_environment_block(brief: dict[str, Any]) -> list[dict[str, Any]]:'''
assert anchor_fn in text, 'anchor fn'
text = text.replace(anchor_fn, new_fns, 1)

# ---- 卡片组装：新顺序 + 压缩（§五 价格摘要 ≤3 并入变化；§廿八 Focus 压缩） ----
old_card = '''    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": _summary_metrics(payload)},
        *_macro_environment_block(brief),
        *_price_condition_digest_block(brief),
    ]
    changes = [
        *_compact_strategy_changes(payload),
        *_compact_investment_changes(payload),
    ]
    if changes:
        elements.extend(changes)
    elements.extend([
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**重点研究 · {len(list(payload.get('executive_watchlist') or []))} 家**　*研究结论，不构成买卖建议*"},
        *_value_observation_table(brief),
    ])'''
new_card = '''    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": _summary_metrics(payload)},
        *_market_review_block(brief),
        *_forecast_review_block(brief),
        *_next_outlook_block(brief),
        {"tag": "hr"},
        {"tag": "markdown", "content": "**今日投资判断变化**"},
        *_compact_strategy_changes(payload),
        *_compact_investment_changes(payload),
        # §廿七：价格条件摘要压缩至 ≤3 条并入变化段，不再占据市场复盘位置
        *_price_condition_digest_block(brief, max_lines=3),
    ]
    elements.extend([
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**重点研究 · {len(list(payload.get('executive_watchlist') or []))} 家**　*研究结论，不构成买卖建议*"},
        *_value_observation_table(brief, compact=True),
    ])'''
assert old_card in text, 'card assembly anchor'
text = text.replace(old_card, new_card, 1)

# ---- 价格摘要块签名扩展 ----
old_price = '''def _price_condition_digest_block(brief: dict[str, Any]) -> list[dict[str, Any]]:'''
new_price = '''def _price_condition_digest_block(brief: dict[str, Any], *, max_lines: int = 8) -> list[dict[str, Any]]:'''
assert old_price in text
text = text.replace(old_price, new_price, 1)

# ---- Focus 观察表压缩（§廿八/廿九：每家 1 行 4 字段） ----
old_watch = '''def _value_observation_table(brief: dict[str, Any]) -> list[dict[str, Any]]:'''
new_watch = '''def _value_observation_table(brief: dict[str, Any], *, compact: bool = False) -> list[dict[str, Any]]:'''
assert old_watch in text
text = text.replace(old_watch, new_watch, 1)
p.write_text(text, encoding='utf-8')
print('card v28 rewired')

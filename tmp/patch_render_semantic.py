from pathlib import Path

p = Path('src/macro_forecast/render.py')
text = p.read_text(encoding='utf-8')

old = '''    lines.append("## 1. 大盘判断")
    lines.append("")
    if payload.get("model_error"):
        lines.append(f"方向：未生成（{STATUS_CN.get(_status_of(payload), '模型失败')}）")
        lines.append("")
        lines.append(f"说明：{model_error}。本次未生成大盘方向预测；不以上涨/下跌模板代替。")
    elif abstain_reason:
        lines.append("方向：暂不判断")
        lines.append("")
        lines.append(f"当前证据不足，暂不形成明确方向判断。原因：{abstain_reason}")
    elif output:
        lines.append(f"方向：{DIRECTION_CN.get(direction, direction)}")
        lines.append("")
        lines.append(f"一句话：{market.get('summary') or '—'}")
        lines.append(f"主要依据：{_keys_text(market.get('evidence_keys'), payload=payload)}")
        lines.append(f"反向因素：{_keys_text(market.get('counter_evidence_keys'), payload=payload)}")
        invalidations = market.get("invalidation_conditions") or []
        lines.append(f"失效条件：{'；'.join(str(item) for item in invalidations) or '—'}")'''
new = '''    semantic = payload.get("semantic") or {}
    summary_market = semantic.get("market_summary") or {}
    invalidation_sem = semantic.get("invalidation") or {}

    lines.append("## 1. 大盘判断")
    lines.append("")
    if payload.get("model_error"):
        lines.append(f"方向：未生成（{STATUS_CN.get(_status_of(payload), '模型失败')}）")
        lines.append("")
        lines.append(f"说明：{model_error}。本次未生成大盘方向预测；不以上涨/下跌模板代替。")
    elif abstain_reason:
        lines.append("方向：暂不判断")
        lines.append("")
        lines.append(f"当前证据不足，暂不形成明确方向判断。原因：{abstain_reason}")
    elif output:
        lines.append(f"方向：{DIRECTION_CN.get(direction, direction)}")
        lines.append("")
        # 语义守则：未经宽度类证据支持的声明按修正视图显示
        summary_text = summary_market.get("revised_summary") or market.get("summary") or "—"
        lines.append(f"一句话：{summary_text}")
        lines.append(f"主要依据：{_keys_text(market.get('evidence_keys'), payload=payload)}")
        lines.append(f"反向因素：{_keys_text(market.get('counter_evidence_keys'), payload=payload)}")
        invalidations = (invalidation_sem.get("kept")
                         if invalidation_sem.get("kept") is not None
                         else (market.get("invalidation_conditions") or []))
        lines.append(f"失效条件：{'；'.join(str(item) for item in invalidations) or '—'}")'''
assert old in text, 'market block anchor'
text = text.replace(old, new)

old2 = '''    lines.append("## 2. 相对看强行业（相对沪深300）")
    lines.append("")
    if strong:
        for index, entry in enumerate(strong, 1):
            lines.append(f"{index}. {entry.get('display_name')}：{entry.get('reason')}")
    else:
        lines.append("本次无相对看强行业结论。")
    lines.append("")

    lines.append("## 3. 相对看弱行业（相对沪深300）")
    lines.append("")
    if weak:
        for index, entry in enumerate(weak, 1):
            lines.append(f"{index}. {entry.get('display_name')}：{entry.get('reason')}")
    else:
        lines.append("本次无相对看弱行业结论。")'''
new2 = '''    semantic_industries = {item.get("display_name"): item for item in semantic.get("industries") or []}

    lines.append("## 2. 相对看强行业（相对沪深300）")
    lines.append("")
    if strong:
        for index, entry in enumerate(strong, 1):
            lines.append(f"{index}. {_industry_line(entry, semantic_industries)}")
    else:
        lines.append("本次无相对看强行业结论。")
    lines.append("")

    lines.append("## 3. 相对看弱行业（相对沪深300）")
    lines.append("")
    if weak:
        for index, entry in enumerate(weak, 1):
            lines.append(f"{index}. {_industry_line(entry, semantic_industries)}")
    else:
        lines.append("本次无相对看弱行业结论。")'''
assert old2 in text, 'industry block anchor'
text = text.replace(old2, new2)

old3 = '''    lines.append("## 6. 说明")
    lines.append("")
    lines.append("本报告为研究预测，后续将在收盘后按固定规则复盘。")
    return "\\n".join(lines)'''
new3 = '''    lines.append("## 6. 说明")
    lines.append("")
    lines.append("本报告为研究预测，后续将在收盘后按固定规则复盘。")
    notes = _semantic_notes(semantic)
    for note in notes:
        lines.append(f"（语义说明：{note}）")
    return "\\n".join(lines)


_BASIS_CN = {
    "PRICE_MOMENTUM": "近期相对走势",
    "MACRO_ALIGNMENT": "宏观环境方向一致",
    "MIXED": "相对走势与宏观环境同时一致",
}


def _industry_line(entry: dict[str, Any], semantic_industries: dict[str, dict[str, Any]]) -> str:
    """§六：老板正文自然表达依据来源，不出现后台枚举。"""
    audit = semantic_industries.get(entry.get("display_name")) or {}
    reason = audit.get("revised_reason") or entry.get("reason") or ""
    basis = entry.get("reason_basis") or audit.get("reason_basis")
    suffix = f"（依据：{_BASIS_CN[basis]}）" if basis in _BASIS_CN else ""
    return f"{entry.get('display_name')}：{reason}{suffix}"


def _semantic_notes(semantic: dict[str, Any]) -> list[str]:
    """§十三：确定性删除/降级的透明标注（不重写原记录，只注明修正视图）。"""
    notes: list[str] = []
    summary = semantic.get("market_summary") or {}
    if summary.get("breadth_claim") == "REMOVED":
        clauses = "、".join(f"『{clause}』" for clause in summary.get("breadth_claim_removed_clauses") or [])
        notes.append(f"原表述 {clauses or '宽度类表述'} 未获宽度类证据支持，已删除；"
                     "宽度指标需引用 MARKET_BREADTH 类证据。")
    invalidation = semantic.get("invalidation") or {}
    if invalidation.get("removed"):
        notes.append("原失效条件中的 T 日涨跌幅/跑赢跑输类语句属于收盘结果评价，已移出失效条件，"
                     "留待收盘复盘（Phase 3）。")
    if invalidation.get("used_fallback"):
        notes.append("原失效条件均不合法，已替换为标准条件：信息截止后若出现未纳入输入包的重大政策或跨市场冲击，本次判断需重新评估。")
    overstated = [item for item in semantic.get("industries") or [] if item.get("classification") == "OVERSTATED"]
    if overstated:
        names = "、".join(str(item.get("display_name")) for item in overstated)
        notes.append(f"{names} 的宏观措辞超出其引用证据（价格动量），已按动量口径降级表述。")
    return notes'''
assert old3 in text, 'tail anchor'
p.write_text(text.replace(old3, new3))
print('renderer semantic-aware')

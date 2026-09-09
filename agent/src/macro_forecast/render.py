"""老板中文报告渲染器（预测引擎 V1 §33-§34）：确定性、0 LLM。"""

from __future__ import annotations

from typing import Any

RENDERER_VERSION = "macro-forecast-boss-v1"

DIRECTION_CN = {
    "STRONGER": "偏强", "RANGE_BOUND": "震荡", "WEAKER": "偏弱", "ABSTAIN": "暂不判断",
    None: "未生成",
}
DATA_MODE_CN = {
    "FULL": "数据完整",
    "DOMESTIC_LIMITED": "当前主要基于境内数据",
    "FACTS_ONLY": "仅事实简报（核心输入不足）",
}
STATUS_CN = {
    "DRAFT": "草稿（未满足正式发布条件）",
    "SHADOW": "影子运行（不计入正式统计）",
    "OFFICIAL": "正式预测",
    "ABSTAINED": "已弃权（证据不足）",
    "MODEL_FAILED": "模型失败（预测未生成）",
    "INVALID_OUTPUT": "输出未通过校验（预测未采用）",
}
GAP_CN = {
    "OVERSEAS_EQUITY_INDEX_UNAVAILABLE": "海外市场信息当前未纳入",
    "USDCNY_STALE_SINCE_2021_05": "美元兑人民币数据缺失（来源停更）",
    "SOCIAL_FINANCING_MISSING": "社融序列缺失",
    "SCHEDULE_NO_RELIABLE_SOURCE": "未来事件日程无可靠来源",
    "P_DAY_CLOSE_PENDING": "上一交易日收盘数据尚未就绪",
    "BENCHMARK_P_CLOSE_NOT_READY": "基准收盘数据未就绪",
    "MACRO_GROUPS_BELOW_MINIMUM": "宏观方向输入组覆盖不足",
}


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def render_narrative(payload: dict[str, Any], bundle: dict[str, Any]) -> str:
    """由结构化 payload 确定性渲染老板中文报告（不再调用模型）。"""
    target = (bundle.get("target") or {})
    output = payload.get("model_output") or {}
    abstain_reason = payload.get("abstain_reason")
    model_error = payload.get("model_error")
    market = output.get("market") or {}
    direction = market.get("direction") if output else None
    benchmark = payload.get("market_context") or {}
    macro = payload.get("macro_context") or {}

    lines: list[str] = []
    lines.append("# 下一交易日宏观与市场前瞻")
    lines.append("")
    lines.append(f"预测日期：{target.get('target_date') or payload.get('input', {}).get('target_trade_date') or '—'}")
    lines.append(f"信息截止：{target.get('cutoff_at') or '—'}")
    lines.append(f"数据模式：{DATA_MODE_CN.get(bundle.get('data_mode'), bundle.get('data_mode') or '—')}")
    lines.append("")

    semantic = payload.get("semantic") or {}
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
        lines.append(f"失效条件：{'；'.join(str(item) for item in invalidations) or '—'}")
        if benchmark:
            lines.append("")
            lines.append(
                f"背景：沪深300 上一收盘 {benchmark.get('close')}，1日 {_fmt_pct(benchmark.get('ret_1d'))}，"
                f"5日 {_fmt_pct(benchmark.get('ret_5d'))}，20日波动 {benchmark.get('vol_20d') or '—'}。"
            )
    else:
        lines.append("方向：未生成")
    lines.append("")

    industries = output.get("industries") or {}
    validation = payload.get("validation") or {}
    valid_entries = validation.get("industry_entries") or []
    strong = [entry for entry in valid_entries if entry.get("side") == "RELATIVE_STRONG"]
    weak = [entry for entry in valid_entries if entry.get("side") == "RELATIVE_WEAK"]

    semantic_industries = {item.get("display_name"): item for item in semantic.get("industries") or []}

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
        lines.append("本次无相对看弱行业结论。")
    lines.append("")
    lines.append("说明：「相对」指相对于沪深300 的下一交易日表现；行业自身可能下跌，只要跌幅小于沪深300 仍属相对强。")
    lines.append("")

    lines.append("## 4. 当前宏观背景")
    lines.append("")
    if macro:
        regime = macro.get("regime")
        axes = macro.get("axes") or {}
        axis_text = "、".join(f"{_AXIS_CN.get(key, key)} {_AXIS_TIER.get(_tier(value), _tier(value))}" for key, value in sorted(axes.items())) or "—"
        lines.append(f"宏观状态：{regime or '—'}（五轴：{axis_text}）")
        events = macro.get("events") or []
        if events:
            for event in events[:3]:
                lines.append(f"- 事件：{event.get('text') or event}")
        if macro.get("pit_caveat"):
            lines.append(f"- 口径说明：{macro['pit_caveat']}")
    else:
        lines.append("宏观背景资料暂不可用。")
    lines.append("")

    lines.append("## 5. 数据限制")
    lines.append("")
    for gap in (payload.get("input") or {}).get("gaps") or bundle.get("gaps") or []:
        lines.append(f"- {GAP_CN.get(gap, gap)}")
    lines.append("- 概念/主题板块预测尚未启用")
    lines.append("- 本报告不含个股判断")
    lines.append("")

    lines.append("## 6. 说明")
    lines.append("")
    lines.append("本报告为研究预测，后续将在收盘后按固定规则复盘。")
    notes = _semantic_notes(semantic)
    for note in notes:
        lines.append(f"（语义说明：{note}）")
    return "\n".join(lines)


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
    return notes


def _status_of(payload: dict[str, Any]) -> str:
    return str(payload.get("status_hint") or "MODEL_FAILED")


def _keys_text(keys: Any, *, payload: dict[str, Any] | None = None) -> str:
    """短键证据 → 「描述[紧凑值]」；无 payload 时退化为键本身。"""
    items = [str(item) for item in (keys or []) if str(item).strip()]
    if not items:
        return "—"
    if not payload:
        return "、".join(items)
    info = payload.get("input") or {}
    alias, values, catalog = info.get("alias_map") or {}, info.get("evidence_values") or {}, info.get("evidence_catalog") or {}
    rendered = []
    for key in items:
        full = alias.get(key, key)
        description = catalog.get(full)
        value = values.get(key)
        if description:
            rendered.append(f"{description}[{value}]" if value is not None else description)
        else:
            rendered.append(key)
    return "、".join(rendered)


_AXIS_CN = {
    "growth": "增长", "inflation": "通胀", "liquidity": "流动性",
    "credit": "信用", "financial_conditions": "金融条件",
}


def _tier(value: Any) -> str:
    if value is None:
        return "资料不足"
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "资料不足"
    if score >= 60:
        return "偏暖"
    if score <= 40:
        return "偏冷"
    return "中性"


_AXIS_TIER = {"偏暖": "偏暖", "偏冷": "偏冷", "中性": "中性", "资料不足": "资料不足"}

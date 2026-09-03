"""Watchpoint V1 projection contract.  No scores, no target prices, no LLM."""

from __future__ import annotations

from typing import Any

FORMULA_VERSION = "value-watchpoint-projection-v1.0.0"

CATEGORIES = frozenset({"THESIS", "RISK", "FINANCIAL", "BUSINESS", "VALUATION", "MOAT", "CAPITAL"})
IMPORTANCE_TIERS = ("CRITICAL", "HIGH", "NORMAL", "LOW")
NEXT_REVIEW_ANCHORS = frozenset({
    "NEXT_QUARTER", "NEXT_ANNUAL_REPORT", "NEXT_DISCLOSURE", "CONTINUOUS", "MANUAL_REVIEW", "NONE",
})
CANONICAL_METRICS = (
    "REVENUE", "GROSS_MARGIN", "NET_MARGIN", "NET_PROFIT", "OCF", "RECEIVABLE", "INVENTORY",
    "DEBT", "INTEREST_BEARING_DEBT", "LIQUIDITY", "ROE", "CAPEX", "CUSTOMER_CONCENTRATION",
    "VALUATION_RELIABILITY", "THESIS_INVALIDATION", "THESIS_SUPPORT", "MOAT_TECHNOLOGY",
    "MOAT_DIMENSION", "CAPITAL_DIMENSION", "SEGMENT_REVENUE", "SEGMENT_MARGIN",
    "PRODUCT_VOLUME", "RISK_ITEM",
)

# Shared financial metrics merge across Risk / Financial / NE / Cycle / Capital.
MERGE_BY_METRIC = {
    "REVENUE", "GROSS_MARGIN", "NET_MARGIN", "NET_PROFIT", "OCF", "RECEIVABLE",
    "INVENTORY", "DEBT", "INTEREST_BEARING_DEBT", "LIQUIDITY", "ROE", "CAPEX",
}

# Distinct metrics that describe one research question.  Grouped items merge
# only when they also share the same review anchor, so a short-term solvency
# item never absorbs a long-term balance-sheet item.
CANONICAL_THEMES = {
    "DEBT": "LEVERAGE",
    "INTEREST_BEARING_DEBT": "LEVERAGE",
}
THEME_TITLES = {
    "LEVERAGE": "杠杆与带息债务是否继续抬升",
}

SOURCE_RANK = {
    "THESIS": 0,
    "RISK_HIGH": 1,
    "RISK_MEDIUM": 2,
    "FINANCIAL_CORE": 3,
    "NORMALIZED_EARNINGS": 4,
    "CYCLE_SCENARIO": 5,
    "BUSINESS": 6,
    "MOAT": 7,
    "CAPITAL": 8,
    "RISK": 2,
    "FINANCIAL": 3,
    "VALUATION": 3,
}

CATEGORY_LABELS = {
    "THESIS": "核心逻辑", "RISK": "风险观察", "FINANCIAL": "财务下一期",
    "BUSINESS": "经营下一期", "VALUATION": "估值资料", "MOAT": "竞争优势",
    "CAPITAL": "资本配置",
}
ANCHOR_LABELS = {
    "NEXT_QUARTER": "下一份季度报告",
    "NEXT_ANNUAL_REPORT": "下一份年报",
    "NEXT_DISCLOSURE": "下一次相关公告",
    "CONTINUOUS": "持续观察",
    "MANUAL_REVIEW": "人工复核",
    "NONE": "",
}
PRIMARY_ACTION_ORDER = {
    "THESIS_REVIEW": 0, "RISK_REVIEW": 1, "VALUATION_DATA_REVIEW": 2,
    "PRIORITY_RESEARCH": 3, "CONTINUE_OBSERVE": 4, "DEFER_RESEARCH": 5,
    "OUTSIDE_VALUE_SCOPE": 6,
}

FORBIDDEN_WATCHPOINT_KEYS = frozenset({
    "score", "watchpoint_score", "trigger_probability", "target_price",
    "confidence_percentage", "confidence_pct",
})

ANCHORS = NEXT_REVIEW_ANCHORS
TIER_QUOTA = {"A": 3, "B": 2, "C": 1, "NOT_APPLICABLE": 3}

RISK_TYPE_LABELS = {
    "FINANCIAL_REVENUE_DECLINE": "营业收入走弱",
    "FINANCIAL_PROFIT_DECLINE": "净利润走弱",
    "FINANCIAL_MARGIN_DECLINE": "毛利率下降",
    "FINANCIAL_ROE_DECLINE": "ROE 下降",
    "FINANCIAL_CASH_FLOW": "经营现金流走弱",
    "FINANCIAL_PROFIT_CASH_DIVERGENCE": "利润与经营现金背离",
    "FINANCIAL_DEBT_RATIO": "资产负债率上升",
    "FINANCIAL_INTEREST_DEBT": "带息债务上升",
    "FINANCIAL_RECEIVABLE": "应收账款压力",
    "FINANCIAL_INVENTORY": "存货压力",
    "FINANCIAL_LIQUIDITY": "流动性压力",
    "FINANCIAL_CASH_COVERAGE": "现金覆盖压力",
    "FINANCIAL_CAPEX_PRESSURE": "资本开支压力",
    "FINANCIAL_FORECAST_DOWNGRADE": "预测下调",
    "BUSINESS_CUSTOMER_CONCENTRATION": "客户集中度",
    "BUSINESS_OPERATION_CHANGE": "经营结构变化",
    "DISCLOSURE_DEBT_MATURITY": "债务到期披露",
    "DISCLOSURE_RECEIVABLES_COLLECTION": "应收账款催收披露",
    "THESIS_STATUS": "核心逻辑状态",
    "THESIS_CHALLENGE": "核心逻辑挑战证据",
    "THESIS_REVIEW_STALE": "核心逻辑复核过期",
    "VALUE_TRAP": "低估陷阱复核",
}

GENERIC_THESIS_MARKERS = (
    "盈利和现金流持续恶化", "主营业务持续收缩", "盈利、现金流和主营业务",
    "盈利或经营现金流连续恶化", "核心业务持续收缩或经营变化被证实为不利",
    "持续跟踪收入的同口径变化", "持续跟踪净利润的同口径变化",
    "持续跟踪经营现金流的同口径变化", "持续跟踪ROE的同口径变化",
    "持续跟踪毛利率的同口径变化", "持续跟踪应收账款的同口径变化",
    "持续跟踪存货的同口径变化", "持续跟踪债务的同口径变化",
)


def source_ref(*, module: str, formula_version: str, research_as_of: str | None = None,
               **extra: Any) -> dict[str, Any]:
    ref = {"module": module, "formula_version": formula_version}
    if research_as_of:
        ref["research_as_of"] = research_as_of
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            ref[key] = value
    return ref


def watchpoint(
    *,
    category: str,
    title: str,
    current_state: str,
    positive_condition: str,
    negative_condition: str,
    source_module: str,
    source_refs: list[dict[str, Any]],
    research_as_of: str | None,
    importance_tier: str,
    canonical_metric: str,
    semantic_key: str | None = None,
    next_review_anchor: str | None = None,
    cautions: list[str] | None = None,
    data_status: str = "READY",
    origin: str = "",
    generic: bool = False,
    direction: str = "",
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError(f"invalid watchpoint category: {category}")
    if importance_tier not in IMPORTANCE_TIERS:
        raise ValueError(f"invalid importance_tier: {importance_tier}")
    anchor = str(next_review_anchor or "").strip().upper() or None
    if anchor and anchor not in NEXT_REVIEW_ANCHORS:
        raise ValueError(f"invalid next_review_anchor: {next_review_anchor}")
    theme = CANONICAL_THEMES.get(canonical_metric)
    if theme:
        key = semantic_key or f"{theme}:{anchor or 'NONE'}"
    elif canonical_metric in MERGE_BY_METRIC:
        key = semantic_key or canonical_metric
    else:
        key = semantic_key or f"{category}:{canonical_metric}:{direction or 'WATCH'}"
    item = {
        "category": category,
        "title": title.strip(),
        "current_state": current_state.strip(),
        "positive_condition": positive_condition.strip(),
        "negative_condition": negative_condition.strip(),
        "next_review_anchor": anchor,
        "source_module": source_module,
        "source_refs": list(source_refs),
        "research_as_of": research_as_of,
        "formula_version": FORMULA_VERSION,
        "importance_tier": importance_tier,
        "cautions": list(cautions or []),
        "data_status": data_status,
        "canonical_metric": canonical_metric,
        "theme": theme,
        "submetrics": [canonical_metric],
        "semantic_key": key,
        "origin": origin or source_module,
        "generic": generic,
        "direction": direction or "WATCH",
    }
    return item


def data_gap(*, category: str, description: str, source_module: str,
             research_as_of: str | None = None) -> dict[str, Any]:
    return {
        "category": category,
        "description": description.strip(),
        "source_module": source_module,
        "research_as_of": research_as_of,
    }


def public_watchpoint(item: dict[str, Any]) -> dict[str, Any]:
    """Strip ranking-only fields from the boss-facing DTO."""
    return {
        "category": item["category"],
        "title": item["title"],
        "current_state": item["current_state"],
        "positive_condition": item["positive_condition"],
        "negative_condition": item["negative_condition"],
        "next_review_anchor": item.get("next_review_anchor"),
        "next_review_label": ANCHOR_LABELS.get(str(item.get("next_review_anchor") or ""), ""),
        "source_module": item["source_module"],
        "source_module_label": CATEGORY_LABELS.get(item["category"], item["source_module"]),
        "source_refs": item.get("source_refs") or [],
        "research_as_of": item.get("research_as_of"),
        "formula_version": item.get("formula_version") or FORMULA_VERSION,
        "importance_tier": item["importance_tier"],
        "cautions": item.get("cautions") or [],
        "data_status": item.get("data_status") or "READY",
        "submetrics": list(item.get("submetrics") or []),
    }

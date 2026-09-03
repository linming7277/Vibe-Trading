"""Stable semantic contract for Value Line's read-only strategy projection.

These constants describe research state only.  They deliberately contain no
orders, positions, stops, targets, or other execution semantics.
"""

ELIGIBILITY_LABELS = {
    "IN_VALUE_SCOPE": "当前属于低估龙头研究范围",
    "OUTSIDE_VALUE_SCOPE": "当前不属于低估龙头研究范围",
}

PRIORITY_LABELS = {
    "A": "重点研究",
    "B": "继续观察",
    "C": "暂缓优先研究",
    "NOT_APPLICABLE": "不适用",
}

PRICE_ATTENTION_LABELS = {
    "HIGH_ATTENTION": "价格条件高度值得关注",
    "ATTENTION": "价格条件值得关注",
    "WATCH": "价格条件继续观察",
    "WAIT": "当前价格条件等待",
    "BLOCKED": "当前研究条件存在阻断",
    "VALUATION_REVIEW_REQUIRED": "估值显示较大折价，但依据偏弱，先核验估值",
    "DATA_REVIEW_REQUIRED": "估值依据不足，先核验估值数据",
}

REVIEW_PRESSURE_LABELS = {
    "CRITICAL_REVIEW": "需要立即复核核心研究逻辑",
    "REVIEW": "需要重点复核",
    "WATCH": "存在复核事项",
    "NORMAL": "当前暂无明显复核压力",
}

PRIMARY_ACTION_LABELS = {
    "PRIORITY_RESEARCH": "优先开展研究",
    "CONTINUE_OBSERVE": "继续观察并补充资料",
    "RISK_REVIEW": "优先复核风险",
    "THESIS_REVIEW": "优先复核核心逻辑",
    "VALUATION_DATA_REVIEW": "先核验估值依据",
    "OUTSIDE_VALUE_SCOPE": "暂不属于当前价值研究范围",
    "DEFER_RESEARCH": "暂缓优先研究",
}

VALUATION_RELIABILITY_LABELS = {
    "RELIABLE": "可靠",
    "LIMITED": "有限",
    "WEAK": "偏弱",
    "INSUFFICIENT": "不足",
}

PRICE_STRUCTURE_FRESHNESS_LABELS = {
    "FRESH": "最新",
    "ACCEPTABLE": "可用",
    "STALE": "偏旧",
    "EXPIRED": "已过期",
    "UNKNOWN": "日期不足",
}

# Peer-sample thresholds are part of the auditable contract, not narrative.
PEER_SAMPLE_RELIABLE_MIN = 8
PEER_SAMPLE_LIMITED_MIN = 5
PEER_SAMPLE_WEAK_MIN = 3
EXTREME_FAIR_VALUE_HIGH_MULTIPLE = 5.0
EXTREME_FAIR_VALUE_LOW_MULTIPLE = 0.2
EXTREME_SMALL_PEER_MAX = 4


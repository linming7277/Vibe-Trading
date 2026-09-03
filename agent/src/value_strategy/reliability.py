"""Shared valuation-reliability contract (observational, rule-free versioning).

This module is a pure extraction of the reliability rules that
``ValueStrategyStateService.valuation_reliability`` has applied since the
Phase 1 projection.  Extracting it lets PIT snapshot writers persist the
reliability provenance without importing the whole strategy service graph.
The rules and thresholds are unchanged; the version constant only names the
existing behaviour so persisted snapshots can reference it.
"""

from __future__ import annotations

from typing import Any

from .semantics import (
    EXTREME_FAIR_VALUE_HIGH_MULTIPLE,
    EXTREME_FAIR_VALUE_LOW_MULTIPLE,
    EXTREME_SMALL_PEER_MAX,
    PEER_SAMPLE_LIMITED_MIN,
    PEER_SAMPLE_RELIABLE_MIN,
    PEER_SAMPLE_WEAK_MIN,
    VALUATION_RELIABILITY_LABELS,
)

# Version label for the *existing* peer-sample + extreme-guard rules.  It does
# not change any threshold; it only makes the persisted contract auditable.
RELIABILITY_FORMULA_VERSION = "valuation-reliability-v1.0.0"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in {float("inf"), float("-inf")} else None


def rank_reliability(sample_count: int) -> str:
    if sample_count >= PEER_SAMPLE_RELIABLE_MIN:
        return "RELIABLE"
    if sample_count >= PEER_SAMPLE_LIMITED_MIN:
        return "LIMITED"
    if sample_count >= PEER_SAMPLE_WEAK_MIN:
        return "WEAK"
    return "INSUFFICIENT"


def upgrade_reliability(status: str) -> str:
    return {"INSUFFICIENT": "WEAK", "WEAK": "LIMITED", "LIMITED": "RELIABLE", "RELIABLE": "RELIABLE"}[status]


def valuation_reliability(zones: dict[str, Any]) -> dict[str, Any]:
    """Persistable reliability provenance computed from one zones projection."""
    valuation = dict(zones.get("valuation") or {})
    peer_methods = [
        method for method in valuation.get("methods") or []
        if method.get("status") == "READY" and method.get("peer_count") is not None
    ]
    counts = [max(0, int(method.get("peer_count") or 0)) for method in peer_methods]
    method_count = len(counts)
    if not counts:
        status = "INSUFFICIENT"
    elif method_count == 1:
        status = rank_reliability(counts[0])
    else:
        # Two independent peer methods may lift confidence by one level,
        # but only from the weaker method's sample quality.
        status = upgrade_reliability(rank_reliability(min(counts)))

    current_price = _number(zones.get("current_price"))
    fair_mid = _number(valuation.get("fair_value_mid"))
    ratio = fair_mid / current_price if current_price and fair_mid else None
    flags: list[str] = []
    if ratio is not None and (ratio >= EXTREME_FAIR_VALUE_HIGH_MULTIPLE or ratio <= EXTREME_FAIR_VALUE_LOW_MULTIPLE):
        flags.append("EXTREME_FAIR_VALUE")
        if max(counts or [0]) <= EXTREME_SMALL_PEER_MAX and status in {"RELIABLE", "LIMITED"}:
            status = "WEAK"

    historical = dict(zones.get("historical_valuation") or {})
    historical_coverage = dict(historical.get("coverage") or {})
    reasons = []
    if counts:
        reasons.append("、".join(str(value) for value in counts) + " 家有效同行样本")
    else:
        reasons.append("缺少可用的同行估值方法")
    if "EXTREME_FAIR_VALUE" in flags:
        reasons.append("合理价值中枢与当前价格相差超过异常保护阈值")
    return {
        "status": status,
        "label": VALUATION_RELIABILITY_LABELS[status],
        "peer_method_count": method_count,
        "peer_sample_counts": counts,
        "peer_sample_count": max(counts or [0]),
        "historical_coverage": historical_coverage.get("coverage_status") or "INSUFFICIENT",
        "fair_value_to_price_ratio": round(ratio, 4) if ratio is not None else None,
        "flags": flags,
        "reasons": reasons,
        "formula_version": RELIABILITY_FORMULA_VERSION,
        "as_of": zones.get("as_of"),
    }

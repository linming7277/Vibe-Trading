"""Immutable Value Sector V2 formula for TDX 881xxx second-level industries."""

from __future__ import annotations

from ..common.scoring import WeightedScore, weighted_score

FORMULA_VERSION = "value-sector-v2.0.0"
WEIGHTS = {
    "momentum": .15,
    "earnings_momentum": .15,
    "valuation": .15,
    "capital_flow_proxy": .15,
    "macro_fit": .15,
    "policy_fit": .15,
    "risk_quality": .10,
}


def calculate(components: dict[str, float | None]) -> WeightedScore:
    if components.get("macro_fit") is None:
        coverage = sum(WEIGHTS[key] for key, value in components.items() if key in WEIGHTS and value is not None)
        return WeightedScore(None, round(coverage, 4), "macro_pending", {})
    available_dimensions = sum(components.get(key) is not None for key in WEIGHTS)
    if available_dimensions < 6:
        coverage = sum(WEIGHTS[key] for key in WEIGHTS if components.get(key) is not None)
        return WeightedScore(None, round(coverage, 4), "insufficient_data", {})
    return weighted_score(components, WEIGHTS, minimum_coverage=.80)

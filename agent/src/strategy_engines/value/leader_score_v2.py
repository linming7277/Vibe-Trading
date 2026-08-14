"""Immutable Value Leader V2 formula."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "value-leader-v2.0.0"
WEIGHTS = {
    "industry_position": .25,
    "profitability": .20,
    "growth_stability": .15,
    "cash_flow": .15,
    "valuation": .15,
    "governance_risk": .10,
}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=.80)

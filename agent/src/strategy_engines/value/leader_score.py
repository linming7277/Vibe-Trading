"""Industry-leader formula, intentionally distinct from daily market leaders."""

from __future__ import annotations

from ..common.missing_data import MIN_SIGNAL_COVERAGE
from ..common.scoring import weighted_score

FORMULA_VERSION = "value-leader-v1.0.0"
WEIGHTS = {
    "industry_position_proxy": 0.20,
    "profitability_quality": 0.20,
    "growth_stability": 0.15,
    "valuation_margin": 0.20,
    "cash_flow": 0.15,
    "governance_risk": 0.10,
}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=MIN_SIGNAL_COVERAGE)

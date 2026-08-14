"""Value entry timing formula."""

from __future__ import annotations

from ..common.missing_data import MIN_SIGNAL_COVERAGE
from ..common.scoring import weighted_score

FORMULA_VERSION = "value-timing-v1.0.0"
WEIGHTS = {"margin_of_safety": 0.25, "earnings_trend": 0.15, "price_trend": 0.15, "atr_pullback": 0.10, "liquidity": 0.10, "event_window": 0.10, "portfolio_capacity": 0.15}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=MIN_SIGNAL_COVERAGE)

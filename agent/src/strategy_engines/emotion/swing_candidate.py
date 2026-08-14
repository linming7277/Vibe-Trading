"""Swing-horizon candidate formula."""

from __future__ import annotations

from ..common.missing_data import MIN_SIGNAL_COVERAGE
from ..common.scoring import weighted_score

FORMULA_VERSION = "emotion-swing-v1.0.0"
WEIGHTS = {"relative_strength_20_60": 0.25, "persistent_flow": 0.20, "sector_trend": 0.15, "price_volume": 0.15, "fundamental_filter": 0.15, "valuation_risk": 0.10}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=MIN_SIGNAL_COVERAGE)

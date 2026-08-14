"""Short-horizon candidate formula."""

from __future__ import annotations

from ..common.missing_data import MIN_SIGNAL_COVERAGE
from ..common.scoring import weighted_score

FORMULA_VERSION = "emotion-short-v1.0.0"
WEIGHTS = {"sector_heat": 0.25, "relative_strength": 0.20, "capital_flow": 0.20, "price_volume": 0.15, "news_catalyst": 0.10, "microstructure": 0.10}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS, minimum_coverage=MIN_SIGNAL_COVERAGE)

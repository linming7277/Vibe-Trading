"""Market emotion score formula."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "emotion-market-v1.0.0"
WEIGHTS = {"breadth_limits": 0.25, "price_volume": 0.20, "capital_flow": 0.20, "news_event": 0.15, "leverage_speculation": 0.10, "volatility_overnight": 0.10}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS)

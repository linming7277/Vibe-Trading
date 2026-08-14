"""Emotion-line sector heat formula."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "emotion-sector-heat-v1.0.0"
WEIGHTS = {"relative_strength": 0.20, "turnover_increment": 0.15, "capital_flow": 0.15, "limit_structure": 0.15, "breadth": 0.15, "news_catalyst": 0.10, "persistence": 0.05, "crowding_risk": 0.05}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS)

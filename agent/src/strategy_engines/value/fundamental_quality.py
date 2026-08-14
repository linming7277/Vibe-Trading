"""Fundamental quality component helpers."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "value-fundamental-quality-v1.0.0"
WEIGHTS = {"roe": 0.25, "roic": 0.25, "earnings_stability": 0.20, "cash_conversion": 0.20, "leverage_safety": 0.10}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS)

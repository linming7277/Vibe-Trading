"""Value-line sector opportunity formula."""

from __future__ import annotations

from ..common.scoring import weighted_score

FORMULA_VERSION = "value-sector-v1.0.0"
WEIGHTS = {
    "prosperity": 0.25,
    "earnings_revision": 0.20,
    "macro_policy_fit": 0.15,
    "relative_strength": 0.15,
    "capital_flow": 0.10,
    "valuation": 0.10,
    "risk": 0.05,
}


def calculate(components: dict[str, float | None]):
    return weighted_score(components, WEIGHTS)

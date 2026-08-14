"""Candidate-pool ranking for TDX 881xxx second-level industries.

Macro and policy are deliberately retained as research context, not ranking
inputs.  The sector layer only narrows a wide universe before company-level
fundamental and valuation research takes over.
"""

from __future__ import annotations

from ..common.scoring import WeightedScore, weighted_score

FORMULA_VERSION = "value-sector-v2.1.0"
WEIGHTS = {
    "momentum": .20,
    "earnings_momentum": .25,
    "valuation": .20,
    "capital_flow_proxy": .20,
    "risk_quality": .15,
}
# These signals are shown alongside a sector, but never influence its rank.
CONTEXT_FIELDS = ("macro_fit", "policy_fit")


def calculate(components: dict[str, float | None]) -> WeightedScore:
    available_dimensions = sum(components.get(key) is not None for key in WEIGHTS)
    if available_dimensions < 4:
        coverage = sum(WEIGHTS[key] for key in WEIGHTS if components.get(key) is not None)
        return WeightedScore(None, round(coverage, 4), "insufficient_data", {})
    return weighted_score(components, WEIGHTS, minimum_coverage=.80)

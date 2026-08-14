"""Coverage-aware deterministic weighted scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .missing_data import MIN_REWEIGHT_COVERAGE


@dataclass(frozen=True)
class WeightedScore:
    score: float | None
    coverage: float
    status: str
    used_weights: dict[str, float]


def weighted_score(
    components: Mapping[str, float | None],
    weights: Mapping[str, float],
    *,
    minimum_coverage: float = MIN_REWEIGHT_COVERAGE,
) -> WeightedScore:
    if not weights or any(weight < 0 for weight in weights.values()):
        raise ValueError("weights must be non-empty and non-negative")
    total_weight = sum(weights.values())
    if total_weight <= 0:
        raise ValueError("weights must have positive total")
    available = {key: float(components[key]) for key in weights if components.get(key) is not None}
    coverage = sum(weights[key] for key in available) / total_weight
    if coverage < minimum_coverage:
        return WeightedScore(None, round(coverage, 4), "insufficient_data", {})
    available_weight = sum(weights[key] for key in available)
    used = {key: weights[key] / available_weight for key in available}
    score = sum(available[key] * used[key] for key in available)
    return WeightedScore(round(min(100.0, max(0.0, score)), 4), round(coverage, 4), "ready", used)

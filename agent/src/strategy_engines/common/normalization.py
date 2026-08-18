"""Deterministic cross-sectional and time-series normalization."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def percentile(values: Iterable[float], q: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        raise ValueError("values must not be empty")
    position = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def winsorize(values: Iterable[float | None], low: float = 0.025, high: float = 0.975) -> list[float | None]:
    materialized = [_finite(value) for value in values]
    finite = [value for value in materialized if value is not None]
    if not finite:
        return materialized
    floor, ceiling = percentile(finite, low), percentile(finite, high)
    return [None if value is None else min(ceiling, max(floor, value)) for value in materialized]


def cross_sectional_percentiles(
    rows: list[Mapping[str, float | None]],
    fields: Mapping[str, bool],
) -> list[dict[str, float | None]]:
    """Return 0-100 percentile ranks; field value True means higher is better."""
    result = [dict.fromkeys(fields, None) for _ in rows]
    for field, higher_is_better in fields.items():
        clipped = winsorize([row.get(field) for row in rows])
        present = sorted((value, index) for index, value in enumerate(clipped) if value is not None)
        if not present:
            continue
        if len(present) == 1:
            result[present[0][1]][field] = 50.0
            continue
        # Equal observations must not receive different cross-sectional ranks
        # merely because their source rows happened to be ordered differently.
        # Use the average rank for each tie group; when an entire field is
        # constant it has no discriminating power, so return a neutral 50.
        if present[0][0] == present[-1][0]:
            for _, index in present:
                result[index][field] = 50.0
            continue
        position = 0
        while position < len(present):
            value = present[position][0]
            end = position + 1
            while end < len(present) and present[end][0] == value:
                end += 1
            average_rank = (position + end - 1) / 2
            score = average_rank / (len(present) - 1) * 100
            normalized = round(score if higher_is_better else 100 - score, 4)
            for _, index in present[position:end]:
                result[index][field] = normalized
            position = end
    return result


def rolling_percentile(history: Iterable[float | None], current: float | None, *, minimum: int = 120) -> float | None:
    value = _finite(current)
    finite = [_finite(item) for item in history]
    clean = [item for item in finite if item is not None]
    if value is None or len(clean) < minimum:
        return None
    return round(sum(item <= value for item in clean) / len(clean) * 100, 4)

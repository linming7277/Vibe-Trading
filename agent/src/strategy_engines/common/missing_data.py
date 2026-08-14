"""Missing-data policy used by every deterministic strategy formula."""

from __future__ import annotations

from collections.abc import Mapping


MIN_REWEIGHT_COVERAGE = 0.70
MIN_SIGNAL_COVERAGE = 0.80


def present(value: object) -> bool:
    return value is not None and value != ""


def coverage(values: Mapping[str, object], expected: tuple[str, ...] | list[str]) -> float:
    if not expected:
        return 1.0
    return sum(present(values.get(name)) for name in expected) / len(expected)


def quality_status(value: float, *, signal: bool = False) -> str:
    threshold = MIN_SIGNAL_COVERAGE if signal else MIN_REWEIGHT_COVERAGE
    return "ready" if value >= threshold else "insufficient_data"

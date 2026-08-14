"""Versioned macro-to-sector fit score."""

from __future__ import annotations

FORMULA_VERSION = "value-macro-sector-v1.0.0"


def calculate(sensitivities: dict[str, float | None], macro_axes: dict[str, float | None]) -> float | None:
    pairs = [(float(sensitivities[key]), float(macro_axes[key])) for key in sensitivities if sensitivities[key] is not None and macro_axes.get(key) is not None]
    if not pairs:
        return None
    # Sensitivities are -1..1 and macro axes 0..100.
    value = sum(50 + sensitivity * (axis - 50) for sensitivity, axis in pairs) / len(pairs)
    return round(min(100.0, max(0.0, value)), 4)

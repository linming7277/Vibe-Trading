"""Valuation output with strict fallback instead of fabricated precision."""

from __future__ import annotations

FORMULA_VERSION = "value-valuation-v1.0.0"


def calculate(*, current_price: float | None, dcf_low: float | None = None, dcf_high: float | None = None, comparable_low: float | None = None, comparable_high: float | None = None) -> dict[str, object]:
    if current_price is None or current_price <= 0:
        return {"method": "unavailable", "status": "insufficient_data", "fair_low": None, "fair_high": None, "margin_of_safety": None}
    if dcf_low is not None and dcf_high is not None:
        method, low, high = "dcf", dcf_low, dcf_high
    elif comparable_low is not None and comparable_high is not None:
        method, low, high = "comparables", comparable_low, comparable_high
    else:
        return {"method": "unavailable", "status": "insufficient_data", "fair_low": None, "fair_high": None, "margin_of_safety": None}
    midpoint = (float(low) + float(high)) / 2
    return {"method": method, "status": "ready", "fair_low": float(low), "fair_high": float(high), "margin_of_safety": round((midpoint - current_price) / midpoint * 100, 4) if midpoint else None}

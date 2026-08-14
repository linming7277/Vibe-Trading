"""Objective market-emotion feature derivation."""

from __future__ import annotations


def breadth_score(*, up: int, down: int, limit_up: int = 0, limit_down: int = 0) -> float | None:
    total = up + down
    if total <= 0:
        return None
    breadth = up / total * 100
    limit_adjustment = min(15.0, max(-15.0, (limit_up - limit_down) * 0.5))
    return round(min(100.0, max(0.0, breadth + limit_adjustment)), 4)

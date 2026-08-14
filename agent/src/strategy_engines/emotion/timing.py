"""Emotion timing and eligibility rules."""

from __future__ import annotations

FORMULA_VERSION = "emotion-timing-v1.0.0"


def eligible(*, is_st: bool = False, delisting_risk: bool = False, suspended: bool = False, liquid: bool = True, one_price_limit: bool = False, stale: bool = False, financial_red_flag: bool = False) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    if is_st:
        reasons.append("st")
    if delisting_risk:
        reasons.append("delisting_risk")
    if suspended:
        reasons.append("suspended")
    if not liquid:
        reasons.append("insufficient_liquidity")
    if one_price_limit:
        reasons.append("unfillable_one_price_limit")
    if stale:
        reasons.append("stale_data")
    if financial_red_flag:
        reasons.append("financial_red_flag")
    return not reasons, tuple(reasons)

"""Suspension inference for Value Line (V1 — inferred, no official flag).

There is no reliable suspension column in the TDX cache (audit recorded).
This module infers from bars only, using the same normalization and quote
flooring rules as :mod:`src.value_strategy.trading_calendar`.

Locked rules (V1):
- ``last_session`` = as_of itself when it is a trading day, otherwise the
  latest trading day <= as_of (same flooring as the trading calendar).
- No ``last_session`` (empty calendar) or no ``last_bar_date`` -> UNKNOWN.
- ``last_bar_date > last_session`` -> UNKNOWN (date soup is not suspension).
- ``last_bar_date == last_session``: volume==0 AND (amount is None or 0)
  -> SUSPENDED_INFERRED; otherwise TRADING (any non-zero volume trades).
- ``last_bar_date < last_session``: the stock has no tradable bar on the
  latest session -> SUSPENDED_INFERRED.  This deliberately also catches a
  cache that simply missed the newest bar — the reason string says so, and
  V1 accepts the false positive rather than back-filling.
- A pre-open quote with Now=0 is never evidence of suspension on its own;
  the optional same-day quote volume only reinforces an already-inferred
  suspension, it never creates one.
"""

from __future__ import annotations

from typing import Any

from .trading_calendar import normalize_day, _floor  # noqa: PLC2701 - same normalization contract


TRADING = "TRADING"
SUSPENDED_INFERRED = "SUSPENDED_INFERRED"
UNKNOWN = "UNKNOWN"

REASON_TRADING = "最近交易日有正常成交"
REASON_SUSPENDED_ZERO = "最近交易日成交量为零，按停牌推断（沿用停牌前收盘，不是程序坏了）"
REASON_SUSPENDED_MISSING_BAR = "缺最近交易日K线，按停牌推断（沿用停牌前收盘，不是程序坏了）"
REASON_UNKNOWN = "资料不足，无法判断停牌状态"


def infer_suspension(
    *,
    as_of: str | None,
    last_bar_date: str | None,
    last_bar_volume: Any = None,
    last_bar_amount: Any = None,
    trading_days: list[str] | None = None,
    quote_volume: Any = None,
    quote_amount: Any = None,
) -> dict[str, Any]:
    """Return ``{status, reason, last_session, last_bar_date}`` per locked rules."""
    days = sorted({normalize_day(item) for item in (trading_days or []) if normalize_day(item)})
    as_of_key = normalize_day(as_of)
    bar_key = normalize_day(last_bar_date)
    if not days or not as_of_key or not bar_key:
        return {"status": UNKNOWN, "reason": REASON_UNKNOWN, "last_session": None, "last_bar_date": last_bar_date}
    last_session = as_of_key if as_of_key in days else _floor(days, as_of_key)
    if last_session is None:
        return {"status": UNKNOWN, "reason": REASON_UNKNOWN, "last_session": None, "last_bar_date": last_bar_date}
    if bar_key > last_session:
        return {"status": UNKNOWN, "reason": REASON_UNKNOWN, "last_session": last_session, "last_bar_date": last_bar_date}
    if bar_key == last_session:
        volume = _to_float(last_bar_volume)
        amount = _to_float(last_bar_amount)
        zero_volume = volume == 0
        zero_amount = amount is None or amount == 0
        quote_zero = _to_float(quote_volume) == 0 and (quote_amount is None or _to_float(quote_amount) == 0)
        if zero_volume and zero_amount:
            return {
                "status": SUSPENDED_INFERRED,
                "reason": REASON_SUSPENDED_ZERO + ("；当日盘口也无成交" if quote_zero else ""),
                "last_session": last_session, "last_bar_date": last_bar_date,
            }
        return {"status": TRADING, "reason": REASON_TRADING, "last_session": last_session, "last_bar_date": last_bar_date}
    # bar_key < last_session: no tradable bar on the latest session.
    return {
        "status": SUSPENDED_INFERRED,
        "reason": REASON_SUSPENDED_MISSING_BAR,
        "last_session": last_session, "last_bar_date": last_bar_date,
    }


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result

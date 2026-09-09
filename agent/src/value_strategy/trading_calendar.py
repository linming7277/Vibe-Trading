"""Trading-day gap arithmetic for research freshness (reliability V1 Part C).

Pure functions over an already-collected list of trading-day keys.  No TDX
I/O happens here — callers pass ``days`` (typically the process-cached
``trading_dates`` dataset keys) so freshness classification stays testable.

Gap definition (locked): on the ascending trading-day list, ``gap = index(quote)
- index(last_bar)``.  Both dates must resolve to a list entry; a quote date
that is not a trading day snaps to the latest trading day ``<= quote`` (a
holiday quote is compared against the previous close).  When either date
cannot be resolved — empty list, read failure, or a ``last_bar`` outside the
list — the caller falls back to calendar days and reports
``CALENDAR_DAYS_FALLBACK``.
"""

from __future__ import annotations

from datetime import date

TRADING_DAYS = "TRADING_DAYS"
CALENDAR_DAYS_FALLBACK = "CALENDAR_DAYS_FALLBACK"


def normalize_day(value: object) -> str:
    """YYYY-MM-DD / YYYYMMDD / datetime-ish text -> YYYYMMDD (empty on junk)."""
    text = str(value or "").strip().replace("-", "")[:8]
    try:
        date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    except ValueError:
        return ""
    return text


def trading_days_between(last_bar: object, quote: object, days: list[str]) -> tuple[int | None, str]:
    """Return ``(gap_trading_days, semantics)`` per the locked definition."""
    normalized = sorted({normalize_day(item) for item in days if normalize_day(item)})
    if not normalized:
        return None, CALENDAR_DAYS_FALLBACK
    last_key = normalize_day(last_bar)
    quote_key = normalize_day(quote)
    if not last_key or not quote_key:
        return None, CALENDAR_DAYS_FALLBACK
    if last_key not in normalized:
        return None, CALENDAR_DAYS_FALLBACK
    # Snap the quote to the latest trading day <= quote (holiday intraday
    # quotes compare against the previous close).
    quote_effective = quote_key if quote_key in normalized else _floor(normalized, quote_key)
    if quote_effective is None or quote_effective < last_key:
        return None, CALENDAR_DAYS_FALLBACK
    return normalized.index(quote_effective) - normalized.index(last_key), TRADING_DAYS


def _floor(sorted_days: list[str], key: str) -> str | None:
    floor = None
    for item in sorted_days:
        if item > key:
            break
        floor = item
    return floor


def next_trading_day(day: object, days: list[str]) -> str | None:
    """严格晚于 day 的下一个交易日键；日历未覆盖返回 None（不按 weekday 猜测）。"""
    key = normalize_day(day)
    if not key:
        return None
    for item in sorted({normalize_day(item) for item in days if normalize_day(item)}):
        if item > key:
            return item
    return None


def previous_trading_day(day: object, days: list[str]) -> str | None:
    """严格早于 day 的最近交易日键；无更早日期返回 None。"""
    key = normalize_day(day)
    if not key:
        return None
    return _floor(sorted({normalize_day(item) for item in days if normalize_day(item)}), key)


def is_trading_day(day: object, days: list[str]) -> bool | None:
    """日历内确认返回 True/False；日历缺失或未覆盖返回 None。"""
    key = normalize_day(day)
    normalized = sorted({normalize_day(item) for item in days if normalize_day(item)})
    if not key or not normalized:
        return None
    return key in normalized


_CACHE: list[str] | None = None
_CACHE_LOADED = False


def cached_trading_dates(loader=None) -> list[str]:
    """Process-wide one-shot read of the TDX ``trading_dates`` dataset.

    Read failures return ``[]`` (callers then stay on the calendar-day
    fallback) and are retried on the next call.
    """
    global _CACHE, _CACHE_LOADED
    if _CACHE_LOADED and _CACHE is not None:
        return _CACHE
    if loader is None:
        def loader() -> list[str]:  # noqa: E731 - local default keeps the module pure otherwise
            from src.tdx_data.store import TdxDataStore

            store = TdxDataStore()
            try:
                rows = store.list_records("trading_dates", limit=5000)["items"]
            finally:
                store.close()
            return [str(row.get("key") or "") for row in rows]
    try:
        _CACHE = sorted({normalize_day(item) for item in loader() if normalize_day(item)})
        _CACHE_LOADED = True
    except Exception:  # noqa: BLE001 - freshness must degrade, never fail
        _CACHE = []
    return _CACHE

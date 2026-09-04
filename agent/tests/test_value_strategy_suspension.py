"""Suspension inference V1 (inferred, never fabricated as an official flag)."""

from __future__ import annotations

from src.value_strategy.suspension import (
    SUSPENDED_INFERRED,
    TRADING,
    UNKNOWN,
    infer_suspension,
)

DAYS = [
    "2026-08-26", "2026-08-27", "2026-08-28",  # 周三四五
    "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
]


def test_trading_when_last_session_has_volume() -> None:
    result = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=1000, last_bar_amount=500, trading_days=DAYS)
    assert result["status"] == TRADING


def test_zero_volume_and_amount_on_last_session_is_suspended_inferred() -> None:
    result = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=0, last_bar_amount=0, trading_days=DAYS)
    assert result["status"] == SUSPENDED_INFERRED
    assert "停牌" in result["reason"] and "推断" in result["reason"]


def test_zero_volume_with_missing_amount_is_suspended_inferred() -> None:
    result = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=0, last_bar_amount=None, trading_days=DAYS)
    assert result["status"] == SUSPENDED_INFERRED


def test_small_volume_still_trading() -> None:
    result = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=1, last_bar_amount=0.1, trading_days=DAYS)
    assert result["status"] == TRADING


def test_missing_latest_bar_is_suspended_inferred_with_explicit_reason() -> None:
    result = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-02", last_bar_volume=1000, last_bar_amount=500, trading_days=DAYS)
    assert result["status"] == SUSPENDED_INFERRED
    assert "缺最近交易日K线" in result["reason"]


def test_weekend_as_of_floors_to_friday_and_zero_volume_weekend_is_not_suspension() -> None:
    # 2026-09-05 是周六：last_session 落到 09-04（周五）。K 线在周五且有量 → 正常。
    result = infer_suspension(as_of="2026-09-05", last_bar_date="2026-09-04", last_bar_volume=800, last_bar_amount=300, trading_days=DAYS)
    assert result["status"] == TRADING
    assert result["last_session"] == "20260904"


def test_bar_newer_than_last_session_is_unknown_not_suspension() -> None:
    result = infer_suspension(as_of="2026-09-01", last_bar_date="2026-09-03", last_bar_volume=100, trading_days=DAYS)
    assert result["status"] == UNKNOWN


def test_empty_calendar_or_missing_dates_is_unknown() -> None:
    assert infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=0, last_bar_amount=0, trading_days=[])["status"] == UNKNOWN
    assert infer_suspension(as_of=None, last_bar_date="2026-09-03", trading_days=DAYS)["status"] == UNKNOWN
    assert infer_suspension(as_of="2026-09-03", last_bar_date=None, trading_days=DAYS)["status"] == UNKNOWN


def test_quote_volume_reinforces_but_never_creates_suspension() -> None:
    # 有量 K 线 + 盘前 0 量报价 → 不得停牌（Now=0 只是缺报价）
    trading = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=100, last_bar_amount=10, trading_days=DAYS, quote_volume=0, quote_amount=0)
    assert trading["status"] == TRADING
    # 0 量 K 线 + 0 量报价 → 停牌推断，reason 提到盘口
    suspended = infer_suspension(as_of="2026-09-03", last_bar_date="2026-09-03", last_bar_volume=0, last_bar_amount=0, trading_days=DAYS, quote_volume=0, quote_amount=0)
    assert suspended["status"] == SUSPENDED_INFERRED
    assert "盘口也无成交" in suspended["reason"]


def test_yyyymmdd_and_iso_inputs_both_normalized() -> None:
    assert infer_suspension(as_of="20260903", last_bar_date="20260903", last_bar_volume=0, last_bar_amount=0, trading_days=DAYS)["status"] == SUSPENDED_INFERRED

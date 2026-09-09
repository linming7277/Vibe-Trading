"""宏观预测 V1 时间契约：T/P/C、日历、自然语言解析（任务卡1；主规格 §4，T01）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.macro_forecast.contracts import (
    CALENDAR_CONFIRMED, CALENDAR_NOT_TRADING_DAY, CALENDAR_UNAVAILABLE,
    CALENDAR_UNVERIFIED_NEXT_SESSION, CUTOFF_CLOCK, PUBLISH_DEADLINE_CLOCK, SHANGHAI,
    build_timeline, is_trading_day, next_trading_day, previous_trading_day, require_aware,
    resolve_reference,
)

# 覆盖 2026 年清明/五一/端午与普通周末的人工日历（周五 0501 休市等）。
DAYS = [
    "20260330", "20260331", "20260401", "20260402", "20260403", "20260407", "20260408",
    "20260430", "20260504", "20260505", "20260506", "20260601", "20260602",
]


def test_t01a_next_trading_day_skips_holiday_weekend() -> None:
    """T01：0403（周五）之后的下一交易日是 0407（周一），不是 0404。"""
    assert next_trading_day("20260403", DAYS) == "20260407"


def test_t01b_calendar_uncovered_future_returns_none() -> None:
    assert next_trading_day("20260602", DAYS) is None


def test_t01c_is_trading_day_three_state() -> None:
    assert is_trading_day("20260403", DAYS) is True
    assert is_trading_day("20260404", DAYS) is False
    assert is_trading_day("20270101", DAYS) is None


def test_t01d_previous_trading_day() -> None:
    assert previous_trading_day("20260407", DAYS) == "20260403"
    assert previous_trading_day("20260330", DAYS) is None


def test_timeline_builds_aware_shanghai_cutoff() -> None:
    timeline = build_timeline("20260602", DAYS)
    assert timeline.calendar_status == CALENDAR_CONFIRMED
    assert timeline.previous_date == "20260601"
    assert timeline.cutoff_at.tzinfo is not None
    assert timeline.cutoff_at.utcoffset().total_seconds() == 8 * 3600
    assert (timeline.cutoff_at.hour, timeline.cutoff_at.minute) == (CUTOFF_CLOCK.hour, CUTOFF_CLOCK.minute)
    assert (timeline.publish_deadline.hour, timeline.publish_deadline.minute) == (
        PUBLISH_DEADLINE_CLOCK.hour, PUBLISH_DEADLINE_CLOCK.minute)


def test_timeline_empty_calendar_records_unavailable() -> None:
    timeline = build_timeline("20260602", [])
    assert timeline.calendar_status == CALENDAR_UNAVAILABLE
    assert timeline.previous_date == ""


def test_timeline_target_covered_but_not_trading_day() -> None:
    # 日历覆盖 0602 之后（无）——改用能构造出"覆盖未来但缺当日"的用例：
    days = ["20260401", "20260402", "20260403", "20260407"]
    timeline = build_timeline("20260404", days)
    assert timeline.calendar_status == CALENDAR_NOT_TRADING_DAY
    assert timeline.previous_date == "20260403"


def test_timeline_unverified_next_session() -> None:
    # 日历只到 0403，目标 0406 尚未入库（生产常态：日历止于昨收）
    days = ["20260401", "20260402", "20260403"]
    timeline = build_timeline("20260406", days)
    assert timeline.calendar_status == CALENDAR_UNVERIFIED_NEXT_SESSION
    assert timeline.previous_date == "20260403"


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        require_aware(datetime(2026, 6, 2, 8, 50), "cutoff")
    with pytest.raises(TypeError):
        require_aware("2026-06-02T08:50", "cutoff")


def test_resolve_today_trading_day() -> None:
    resolution = resolve_reference("今天", "20260602", DAYS)
    assert resolution.target_date == "20260602"
    assert resolution.calendar_status == CALENDAR_CONFIRMED
    assert not resolution.holiday_noted


def test_resolve_today_holiday_points_next_with_note() -> None:
    resolution = resolve_reference("今天", "20260404", DAYS)
    assert resolution.target_date == "20260407"
    assert resolution.holiday_noted
    assert "休市" in resolution.explanation


def test_resolve_tomorrow_strictly_next() -> None:
    resolution = resolve_reference("明天", "20260403", DAYS)
    assert resolution.target_date == "20260407"
    latest = resolve_reference("最新", "20260404", DAYS)
    assert latest.target_date == "20260403"


def test_resolve_unknown_keyword_honest() -> None:
    resolution = resolve_reference("后天", "20260602", DAYS)
    assert resolution.target_date is None
    assert resolution.calendar_status == CALENDAR_UNAVAILABLE

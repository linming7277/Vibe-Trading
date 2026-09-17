"""Tests for the daily macro-series refresh scheduler (07:35 weekday slot)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.macro_data.scheduler import (
    MacroSeriesRefreshScheduler,
    due_for_refresh,
    run_forward_refresh,
)

ZONE = ZoneInfo("Asia/Shanghai")


def test_due_only_at_the_weekday_morning_slot() -> None:
    assert due_for_refresh(datetime(2026, 9, 15, 7, 35, tzinfo=ZONE), last_fire_date=None)
    # 调度线程 60s 相位唤醒：07:35 分内的任意一秒都必须触发。
    assert due_for_refresh(datetime(2026, 9, 15, 7, 35, 47, tzinfo=ZONE), last_fire_date=None)
    assert not due_for_refresh(datetime(2026, 9, 15, 7, 34, 47, tzinfo=ZONE), last_fire_date=None)
    assert not due_for_refresh(datetime(2026, 9, 15, 7, 36, 0, tzinfo=ZONE), last_fire_date=None)
    assert not due_for_refresh(datetime(2026, 9, 19, 7, 35, tzinfo=ZONE), last_fire_date=None)


def test_same_day_refire_is_suppressed() -> None:
    fired = due_for_refresh(datetime(2026, 9, 15, 7, 35, tzinfo=ZONE), last_fire_date=None)
    assert fired
    again = due_for_refresh(
        datetime(2026, 9, 15, 7, 35, tzinfo=ZONE), last_fire_date=__import__("datetime").date(2026, 9, 15)
    )
    assert not again
    # Next weekday fires again.
    assert due_for_refresh(
        datetime(2026, 9, 16, 7, 35, tzinfo=ZONE), last_fire_date=__import__("datetime").date(2026, 9, 15)
    )


def test_tick_skips_and_fires_once_per_slot(monkeypatch) -> None:
    calls: list[str] = []

    def fake_refresh() -> dict:
        calls.append("run")
        return {"status": "REFRESHED"}

    monkeypatch.setattr("src.macro_data.scheduler.run_daily_refresh", fake_refresh)
    scheduler = MacroSeriesRefreshScheduler()

    skipped = scheduler.tick(datetime(2026, 9, 15, 7, 34, tzinfo=ZONE))
    assert skipped["status"] == "SKIP"
    assert calls == []

    fired = scheduler.tick(datetime(2026, 9, 15, 7, 35, tzinfo=ZONE))
    assert fired["status"] == "REFRESHED"
    assert calls == ["run"]
    assert calls == ["run"]

    again = scheduler.tick(datetime(2026, 9, 15, 7, 35, tzinfo=ZONE))
    assert again["status"] == "SKIP"
    assert calls == ["run"]


def test_forward_refresh_loads_dotenv_before_run(monkeypatch) -> None:
    order: list[str] = []

    def fake_dotenv() -> None:
        order.append("dotenv")

    def fake_run_refresh(*, mode: str) -> dict:
        order.append(f"run:{mode}")
        return {"overall": "PARTIAL", "sources": [{"source": "fred", "status": "READY"}]}

    monkeypatch.setattr("src.providers.llm._ensure_dotenv", fake_dotenv)
    monkeypatch.setattr("src.macro_data.run_refresh", fake_run_refresh)

    summary = run_forward_refresh()

    assert summary["overall"] == "PARTIAL"
    assert order == ["dotenv", "run:forward"]


def test_daily_refresh_runs_forward_and_domestic_independently(monkeypatch) -> None:
    calls: list[str] = []

    def fake_forward() -> dict:
        calls.append("forward")
        return {"overall": "READY"}

    def fake_domestic() -> dict:
        calls.append("domestic")
        raise RuntimeError("domestic source down")

    monkeypatch.setattr("src.macro_data.scheduler.run_forward_refresh", fake_forward)
    monkeypatch.setattr("src.macro_data.scheduler.run_domestic_refresh", fake_domestic)
    from src.macro_data.scheduler import run_daily_refresh
    result = run_daily_refresh()

    assert result["forward"]["overall"] == "READY"
    assert result["domestic"]["status"] == "FAILED"


def na_run_daily():
    from src.macro_data.scheduler import run_daily_refresh
    return run_daily_refresh()

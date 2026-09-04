"""Scheduler observability (reliability triple V1, Part B).

A dead tick must leave a trace: logger.exception + last_status/last_error in
the automation row — while the loop itself keeps running and single-company
failures stay fail-soft.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.value_workspace.automation import ValueResearchScheduler


class _FakeStore:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_automation(self, **fields) -> dict:
        self.updates.append(fields)
        return fields

    def close(self) -> None:
        return None


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr("src.value_workspace.automation.Level3LeaderStore", lambda: store)
    return store


def test_loop_survives_tick_exception_and_records_failure(
    monkeypatch: pytest.MonkeyPatch, fake_store: _FakeStore, caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"count": 0}

    def flaky_tick(current=None) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom on first tick")

    scheduler = ValueResearchScheduler()
    monkeypatch.setattr(scheduler, "tick", flaky_tick)
    # Short-circuit the 30s wait so the second iteration runs immediately.
    monkeypatch.setattr(scheduler._wake, "wait", lambda _timeout: False)
    monkeypatch.setattr(scheduler._wake, "clear", lambda: None)
    monkeypatch.setattr(scheduler._stop, "is_set", lambda: calls["count"] >= 2)

    with caplog.at_level(logging.ERROR, logger="src.value_workspace.automation"):
        scheduler._loop()

    assert calls["count"] == 2  # loop survived and ticked again
    assert any("value-l3-scheduler tick failed" in record.message for record in caplog.records)
    failure_updates = [u for u in fake_store.updates if u.get("last_status") == "failed"]
    assert failure_updates, "tick failure must be persisted via update_automation"
    assert "RuntimeError: boom on first tick" in failure_updates[0]["last_error"]
    assert len(failure_updates[0]["last_error"]) <= 160
    assert failure_updates[0].get("next_run_at")


def test_loop_keeps_running_when_failure_persist_also_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_store_factory():
        raise OSError("db unreachable")

    monkeypatch.setattr("src.value_workspace.automation.Level3LeaderStore", broken_store_factory)
    calls = {"count": 0}

    def flaky_tick(current=None) -> None:
        calls["count"] += 1
        raise RuntimeError("boom")

    scheduler = ValueResearchScheduler()
    monkeypatch.setattr(scheduler, "tick", flaky_tick)
    monkeypatch.setattr(scheduler._wake, "wait", lambda _timeout: False)
    monkeypatch.setattr(scheduler._wake, "clear", lambda: None)
    monkeypatch.setattr(scheduler._stop, "is_set", lambda: calls["count"] >= 2)

    with caplog.at_level(logging.ERROR, logger="src.value_workspace.automation"):
        scheduler._loop()

    assert calls["count"] == 2  # even a double failure never kills the loop
    messages = [record.message for record in caplog.records]
    assert any("value-l3-scheduler tick failed" in message for message in messages)
    assert any("failed to persist tick failure" in message for message in messages)


def test_market_close_rejection_logs_warning_and_appends_reason(
    monkeypatch: pytest.MonkeyPatch, fake_store: _FakeStore, caplog: pytest.LogCaptureFixture,
) -> None:
    from src.value_workspace import automation

    scheduler = ValueResearchScheduler()

    tdx = MagicMock()
    tdx.start_update.side_effect = RuntimeError("update already running")
    monkeypatch.setattr(automation, "get_tdx_service", lambda: tdx)

    automation_row = {
        "enabled": True, "max_retries": 3, "retry_minutes": 20,
        "last_status": "waiting_data", "next_run_at": None,
    }
    fake_store.get_automation = lambda: automation_row  # type: ignore[method-assign]
    monkeypatch.setattr(scheduler, "_retry_counts", {})
    # Force the not-ready branch: close snapshot not ready, retries remain.
    monkeypatch.setattr(
        scheduler, "_resolve_latest_target",
        staticmethod(lambda tdx, *, natural_date: (False, "close data not ready", None)),
    )

    from datetime import datetime as _dt

    from src.value_workspace.automation import SHANGHAI

    with caplog.at_level(logging.WARNING, logger="src.value_workspace.automation"):
        result = scheduler._run_latest_completed(
            fake_store, automation=automation_row,
            local=_dt(2026, 9, 3, 17, 0, tzinfo=SHANGHAI),
            allow_refresh_request=True,
        )

    assert result["status"] == "WAITING_DATA"  # rejection is NOT escalated to a crash
    assert any("market_close refresh rejected" in record.message for record in caplog.records)
    assert any("market_close refresh rejected: update already running" in str(u.get("last_error")) for u in fake_store.updates)


def test_daily_bar_member_read_failure_is_recorded_fail_soft(
    caplog: pytest.LogCaptureFixture,
) -> None:

    class BrokenRepoFocus:
        @property
        def repository(self):
            raise RuntimeError("pool db locked")

    scheduler = ValueResearchScheduler()
    with caplog.at_level(logging.WARNING, logger="src.value_workspace.automation"):
        summary = scheduler._ensure_low_value_daily_bars(BrokenRepoFocus(), as_of="2026-09-03")

    assert summary["active_members"] == 0  # fail-soft: nothing crashes
    assert {"stock_code": "*", "error": "active member read failed"} in summary["failed"]
    assert any("could not read active members" in record.message for record in caplog.records)


def test_incremental_prepare_failure_logs_stock_and_keeps_counting(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from concurrent.futures import ThreadPoolExecutor


    pool = {
        "id": "pool-1", "as_of": "2026-09-03",
        "research_states": [
            {"stock_code": "600001.SH", "lifecycle_status": "ACTIVE", "research_status": "PARTIAL"},
            {"stock_code": "600002.SH", "lifecycle_status": "ACTIVE", "research_status": "NOT_READY"},
        ],
    }

    def flaky_prepare(item):
        if item["stock_code"] == "600001.SH":
            raise RuntimeError("tdx bridge down")
        return item, {"feature_status": "READY", "id": "s1", "updated_at": "now"}

    monkeypatch.setattr(
        "src.value_workspace.automation.get_financial_analysis_service",
        lambda: MagicMock(**{"prepare.side_effect": lambda code, as_of=None: flaky_prepare({"stock_code": code})}),
    )
    monkeypatch.setattr(
        "src.value_workspace.automation.Level3LeaderStore",
        lambda: MagicMock(**{"update_research_state.return_value": None, "close.return_value": None}),
    )

    with caplog.at_level(logging.WARNING, logger="src.value_workspace.automation"):
        # Directly exercise the batch body via a tiny inline replica to avoid
        # spinning the shared ThreadPoolExecutor against monkeypatched pieces.
        states = [i for i in pool["research_states"] if i.get("lifecycle_status") != "OUT_OF_TOP2" and i.get("research_status") != "READY"]
        completed = failed = 0
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_by_code = {executor.submit(flaky_prepare, item): item["stock_code"] for item in states}
            from concurrent.futures import as_completed
            for future in as_completed(future_by_code):
                try:
                    future.result()
                    completed += 1
                except Exception:
                    logging.getLogger("src.value_workspace.automation").warning(
                        "incremental financial prepare failed for %s (stage=prepare_incremental)",
                        future_by_code[future], exc_info=True,
                    )
                    failed += 1

    assert completed == 1 and failed == 1  # one failure never sinks the batch
    assert any(
        record.message == "incremental financial prepare failed for 600001.SH (stage=prepare_incremental)"
        for record in caplog.records
    )

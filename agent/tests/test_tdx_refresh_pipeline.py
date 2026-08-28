from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.tdx_data import automation
from src.tdx_data.automation import DataRefreshScheduler, due_profiles
from src.tdx_data.service import TdxDataService
from src.tdx_data.store import TdxDataStore


def test_staged_records_are_invisible_until_snapshot_is_published(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    store.replace_dataset("quotes", [{"key": "600000.SH", "payload": {"price": 10}}])

    with store.snapshot_context("snapshot-new"):
        store.replace_dataset("quotes", [{"key": "600000.SH", "payload": {"price": 11}}])
        assert store.get_record("quotes", "600000.SH")["payload"]["price"] == 11

    # Other requests keep reading the last published cache while the worker is
    # still collecting the rest of its dependent datasets.
    assert store.get_record("quotes", "600000.SH")["payload"]["price"] == 10
    assert store.publish_snapshot("snapshot-new") == ["quotes"]
    published = store.get_record("quotes", "600000.SH")
    assert published and published["payload"]["price"] == 11
    assert published["snapshot_id"] == "snapshot-new"


def test_db_refresh_lock_excludes_another_process(tmp_path: Path) -> None:
    path = tmp_path / "tdx.db"
    first = TdxDataStore(path)
    second = TdxDataStore(path)
    assert first.acquire_refresh_lock("tdx:CN", "first", expires_at="2999-01-01T00:00:00+00:00")
    assert not second.acquire_refresh_lock("tdx:CN", "second", expires_at="2999-01-01T00:00:00+00:00")
    first.release_refresh_lock("tdx:CN", "first")
    assert second.acquire_refresh_lock("tdx:CN", "second", expires_at="2999-01-01T00:00:00+00:00")


def test_close_snapshot_requires_all_datasets_and_quote_coverage(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    snapshot_id = "cn-20260819-close"
    store.create_refresh_run(
        "run-close", profile="market_close", market="CN", market_date="2026-08-19",
        snapshot_id=snapshot_id, modules=("quote", "rank", "index", "sector"),
    )
    for dataset in ("quotes", "ranks", "indices", "sectors", "sector_members"):
        store.record_dataset_snapshot(
            snapshot_id=snapshot_id, refresh_run_id="run-close", dataset=dataset, market="CN",
            market_date="2026-08-19", source="tdx", coverage=0.95 if dataset == "quotes" else 1.0,
            item_count=100, expected_count=100, status="ready",
        )
    store.update_refresh_run("run-close", status="completed", completed_at="2026-08-19T08:00:00+00:00")
    service = TdxDataService(store=store)
    ready, reason, snapshot = service.close_snapshot_ready("2026-08-19")
    assert ready and not reason
    assert snapshot and snapshot["snapshot_id"] == snapshot_id

    # A new failed/partial run cannot replace the completed close snapshot.
    store.create_refresh_run(
        "run-bad", profile="market_close", market="CN", market_date="2026-08-19",
        snapshot_id="cn-20260819-bad", modules=("quote",),
    )
    store.update_refresh_run("run-bad", status="failed")
    assert service.close_snapshot_ready("2026-08-19")[0]


def test_close_snapshot_rejects_a_preclose_manual_run(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    snapshot_id = "cn-20260819-preclose"
    store.create_refresh_run(
        "run-preclose", profile="market_close", market="CN", market_date="2026-08-19",
        snapshot_id=snapshot_id, modules=("quote", "rank", "index", "sector"),
    )
    for dataset in ("quotes", "ranks", "indices", "sectors", "sector_members"):
        store.record_dataset_snapshot(
            snapshot_id=snapshot_id, refresh_run_id="run-preclose", dataset=dataset, market="CN",
            market_date="2026-08-19", source="tdx", coverage=0.95 if dataset == "quotes" else 1.0,
            item_count=100, expected_count=100, status="ready",
        )
    store.update_refresh_run("run-preclose", status="completed", completed_at="2026-08-19T04:59:00+00:00")

    ready, reason, _snapshot = TdxDataService(store=store).close_snapshot_ready("2026-08-19")
    assert not ready
    assert "早于 15:05" in reason


def test_latest_qualified_close_falls_back_past_an_incomplete_newer_run(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")

    def add_close(run_id: str, market_date: str, completed_at: str) -> None:
        snapshot_id = f"cn-{market_date}-{run_id}"
        store.create_refresh_run(
            run_id, profile="market_close", market="CN", market_date=market_date,
            snapshot_id=snapshot_id, modules=("quote", "rank", "index", "sector"),
        )
        for dataset in ("quotes", "ranks", "indices", "sectors", "sector_members"):
            store.record_dataset_snapshot(
                snapshot_id=snapshot_id, refresh_run_id=run_id, dataset=dataset, market="CN",
                market_date=market_date, source="tdx", coverage=0.95 if dataset == "quotes" else 1.0,
                item_count=100, expected_count=100, status="ready",
            )
        store.update_refresh_run(run_id, status="completed", completed_at=completed_at)

    add_close("run-qualified", "2026-08-26", "2026-08-26T08:00:00+00:00")
    add_close("run-preclose", "2026-08-27", "2026-08-27T04:59:00+00:00")

    ready, reason, snapshot = TdxDataService(store=store).latest_qualified_close_snapshot()

    assert ready and not reason
    assert snapshot and snapshot["market_date"] == "2026-08-26"


def test_scheduled_market_close_replaces_a_preclose_manual_run(tmp_path: Path, monkeypatch) -> None:
    zone = ZoneInfo("Asia/Shanghai")
    store = TdxDataStore(tmp_path / "tdx.db")
    store.create_refresh_run(
        "run-preclose", profile="market_close", market="CN", market_date="2026-08-19",
        snapshot_id="cn-20260819-preclose", modules=("quote",),
    )
    store.update_refresh_run("run-preclose", status="completed", completed_at="2026-08-19T04:59:00+00:00")
    store.update_refresh_automation("CN", enabled=True)
    started: list[str] = []

    class Service:
        def start_update(self, profile: str) -> None:
            started.append(profile)

    monkeypatch.setattr(automation, "get_tdx_service", lambda: Service())
    DataRefreshScheduler(store=store).tick(datetime(2026, 8, 19, 16, 0, tzinfo=zone))
    assert started == ["market_close"]


def test_scheduler_has_bounded_profiles_and_respects_pause(tmp_path: Path, monkeypatch) -> None:
    zone = ZoneInfo("Asia/Shanghai")
    assert due_profiles(datetime(2026, 8, 19, 16, 0, tzinfo=zone)) == ["market_close"]
    assert "all" not in due_profiles(datetime(2026, 8, 19, 16, 0, tzinfo=zone))

    store = TdxDataStore(tmp_path / "tdx.db")
    scheduler = DataRefreshScheduler(store=store)
    started: list[str] = []

    class Service:
        def start_update(self, profile: str) -> None:
            started.append(profile)

    monkeypatch.setattr(automation, "get_tdx_service", lambda: Service())
    store.update_refresh_automation("CN", enabled=False)
    scheduler.tick(datetime(2026, 8, 19, 16, 0, tzinfo=zone))
    assert started == []

    store.update_refresh_automation("CN", enabled=True)
    scheduler.tick(datetime(2026, 8, 19, 16, 0, tzinfo=zone))
    assert started == ["market_close"]

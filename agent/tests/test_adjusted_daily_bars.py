from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.adjusted_daily_bars.service import AdjustedDailyBarService
from src.api import adjusted_daily_bar_routes
from src.api.adjusted_daily_bar_routes import register_adjusted_daily_bar_routes
from src.tdx_data.service import TdxDataService
from src.tdx_data.store import TdxDataStore


SYMBOL = "000001.SZ"


def _payload(symbol: str, bars: int, *, start: date = date(2023, 8, 1), discontinuity: bool = False) -> dict:
    data = {field: [] for field in ("Open", "High", "Low", "Close", "Volume", "Amount", "ForwardFactor")}
    for index in range(bars):
        close = 10 + index * .02
        if discontinuity and index == bars // 2:
            close *= 2
        stamp = (start + timedelta(days=index)).isoformat() + " 00:00:00"
        values = {"Open": close - .05, "High": close + .1, "Low": close - .1, "Close": close,
                  "Volume": 10_000 + index, "Amount": close * 10_000, "ForwardFactor": 1.0}
        for field, value in values.items():
            data[field].append({"index": stamp, symbol: value})
    return {"code": symbol, "period": "1d", "dividend_type": "front", "data": data}


class FakeClient:
    available = True

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls: list[dict] = []

    def call(self, method: str, *args, **kwargs):
        assert method == "get_market_data"
        self.calls.append(kwargs)
        return self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]["data"]


class FakeLeaderService:
    def ensure_current_pool(self) -> dict:
        return {"members": [
            {"stock_code": "000001.SZ", "lifecycle_status": "NEW"},
            {"stock_code": "000002.SZ", "lifecycle_status": "ACTIVE"},
            {"stock_code": "000003.SZ", "lifecycle_status": "REENTERED"},
            {"stock_code": "000004.SZ", "lifecycle_status": "OUT_OF_TOP2"},
            {"stock_code": "000001.SZ", "lifecycle_status": "ACTIVE"},
        ]}


def _service(tmp_path: Path, payloads: list[dict]) -> tuple[AdjustedDailyBarService, TdxDataStore, FakeClient]:
    store = TdxDataStore(tmp_path / "tdx.db")
    client = FakeClient(payloads)
    tdx = TdxDataService(store, client)
    return AdjustedDailyBarService(tdx_store=store, tdx_service=tdx, leader_service=FakeLeaderService()), store, client


def test_initial_backfill_pit_incremental_and_idempotency(tmp_path: Path) -> None:
    first, second = _payload(SYMBOL, 260), _payload(SYMBOL, 265)
    service, store, client = _service(tmp_path, [first, second, second])
    try:
        initial = service.refresh_company("CN", SYMBOL)
        assert initial["initial_backfill"] is True
        assert initial["coverage_status"] == "READY" and initial["bar_count"] == 260
        cutoff = "2023-10-01"
        assert all(item["trade_date"] <= cutoff for item in service.get_daily_bars("CN", SYMBOL, as_of=cutoff))
        incremental = service.refresh_company("CN", SYMBOL)
        assert incremental["initial_backfill"] is False
        assert incremental["bar_count"] == 265 and incremental["changed_count"] == 5
        assert client.calls[1]["start_time"]
        repeat = service.refresh_company("CN", SYMBOL)
        assert repeat["changed_count"] == 0 and repeat["bar_count"] == 265
        assert service.status("CN", SYMBOL)["coverage_status"] == "READY"
    finally:
        store.close()


def test_coverage_levels_and_adjustment_continuity_signal(tmp_path: Path) -> None:
    service, store, _ = _service(tmp_path, [_payload(SYMBOL, 80), _payload("000002.SZ", 20), _payload("000003.SZ", 80, discontinuity=True)])
    try:
        assert service.refresh_company("CN", SYMBOL)["coverage_status"] == "PARTIAL"
        assert service.refresh_company("CN", "000002.SZ")["coverage_status"] == "INSUFFICIENT"
        discontinuous = service.refresh_company("CN", "000003.SZ")
        assert discontinuous["continuity"]["status"] == "REVIEW"
        # The cache records the provider's explicit front-adjusted request.
        assert service.status("CN", SYMBOL)["adjustment_type"] == "front"
    finally:
        store.close()


def test_current_pool_is_deduplicated_and_explicitly_bounded(tmp_path: Path) -> None:
    payloads = [_payload("000001.SZ", 260), _payload("000002.SZ", 260), _payload("000003.SZ", 260)]
    service, store, _ = _service(tmp_path, payloads)
    try:
        assert service.current_l3_symbols() == ["000001.SZ", "000002.SZ", "000003.SZ"]
        result = service.refresh_current_l3_daily_bars(limit=3)
        assert result["processed"] == 3 and result["ready"] == 3
        with pytest.raises(ValueError, match="1–20"):
            service.refresh_current_l3_daily_bars(limit=21)
    finally:
        store.close()


def test_full_pool_backfill_skips_ready_resumes_and_retries_failures(tmp_path: Path) -> None:
    first = _payload("000001.SZ", 750)
    second = _payload("000002.SZ", 750)
    recovered = _payload("000003.SZ", 750)
    service, store, _ = _service(tmp_path, [first, second, {"data": {}}, recovered])
    try:
        # Seed one existing fully-covered company; the run must not fetch it again.
        assert service.refresh_company("CN", "000001.SZ", as_of="2025-08-19")["coverage_status"] == "READY"
        started = service.backfill_current_l3_pool(
            as_of="2025-08-19", batch_size=2, max_batches=1, throttle_seconds=0,
        )
        assert started["total_count"] == 3 and started["skipped"] == 1 and started["ready"] == 1
        assert started["pending"] == 1 and started["next_offset"] == 2
        failed = service.backfill_current_l3_pool(
            resume_run_id=started["run_id"], max_batches=1, throttle_seconds=0,
        )
        assert failed["failed"] == 1 and failed["failures"][0]["stock_code"] == "000003.SZ"
        completed = service.backfill_current_l3_pool(
            resume_run_id=started["run_id"], retry_failed=True, max_batches=1, throttle_seconds=0,
        )
        assert completed["status"] == "COMPLETED"
        assert completed["ready"] == 2 and completed["skipped"] == 1 and completed["failed"] == 0
        assert completed["ready_coverage_rate"] == round(2 / 3 * 100, 2)
    finally:
        store.close()


def test_daily_bar_api_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, store, _ = _service(tmp_path, [_payload(SYMBOL, 260)])
    app = FastAPI(); register_adjusted_daily_bar_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(adjusted_daily_bar_routes, "get_adjusted_daily_bar_service", lambda: service)
    try:
        client = TestClient(app)
        assert client.post(f"/api/value/companies/{SYMBOL}/daily-bars/refresh?market=CN").status_code == 200
        response = client.get(f"/api/value/companies/{SYMBOL}/daily-bars/status?market=CN")
        assert response.status_code == 200 and response.json()["coverage_status"] == "READY"
        with sqlite3.connect(tmp_path / "tdx.db") as connection:
            before_compact_read = connection.execute("SELECT COUNT(*) FROM adjusted_daily_bars").fetchone()[0]
        compact = client.get(f"/api/value/companies/{SYMBOL}/daily-bars/compact?market=CN&limit=120&as_of=2024-04-01")
        assert compact.status_code == 200
        payload = compact.json()
        assert payload["adjustment_type"] == "front"
        assert payload["coverage_status"] == "PARTIAL"
        assert len(payload["bars"]) == 120
        assert payload["data_as_of"] <= "2024-04-01"
        assert all(item["date"] <= "2024-04-01" for item in payload["bars"])
        with sqlite3.connect(tmp_path / "tdx.db") as connection:
            after_compact_read = connection.execute("SELECT COUNT(*) FROM adjusted_daily_bars").fetchone()[0]
        assert after_compact_read == before_compact_read
        assert client.get(f"/api/value/companies/{SYMBOL}/daily-bars/compact?market=CN&limit=119").status_code == 422
        assert client.post("/api/value/daily-bars/current-l3/refresh?limit=21").status_code == 422
        batch = client.post("/api/value/daily-bars/current-l3/backfill?as_of=2025-08-01&batch_size=2")
        assert batch.status_code == 200 and batch.json()["processed"] == 2
        run_id = batch.json()["run_id"]
        assert client.get(f"/api/value/daily-bars/current-l3/backfill/{run_id}").status_code == 200
    finally:
        store.close()


def test_unseen_company_status_is_read_only(tmp_path: Path) -> None:
    service, store, _ = _service(tmp_path, [_payload(SYMBOL, 260)])
    try:
        with sqlite3.connect(tmp_path / "tdx.db") as connection:
            before = connection.execute("SELECT COUNT(*) FROM adjusted_daily_bar_coverage").fetchone()[0]
        result = service.status("CN", SYMBOL)
        with sqlite3.connect(tmp_path / "tdx.db") as connection:
            after = connection.execute("SELECT COUNT(*) FROM adjusted_daily_bar_coverage").fetchone()[0]
        assert result["coverage_status"] == "INSUFFICIENT" and result["error"] == "not_cached"
        assert before == after == 0
    finally:
        store.close()

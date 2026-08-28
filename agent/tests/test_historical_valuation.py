from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import historical_valuation_routes
from src.api.historical_valuation_routes import register_historical_valuation_routes
from src.historical_valuation.service import HistoricalValuationService
from src.tdx_data.store import TdxDataStore


SYMBOL = "000001.SZ"


def _financials() -> list[dict]:
    # The fifth row is deliberately announced later.  It must not change PE/PB
    # for earlier valuation dates.
    return [
        {"report_date": "2022-03-31", "announcement_date": "2022-04-20", "net_profit": 100.0, "parent_equity": 1_000.0},
        {"report_date": "2022-06-30", "announcement_date": "2022-08-20", "net_profit": 100.0, "parent_equity": 1_000.0},
        {"report_date": "2022-09-30", "announcement_date": "2022-10-20", "net_profit": 100.0, "parent_equity": 1_000.0},
        {"report_date": "2022-12-31", "announcement_date": "2023-04-20", "net_profit": 100.0, "parent_equity": 1_000.0},
        {"report_date": "2023-03-31", "announcement_date": "2023-09-01", "net_profit": 1_000.0, "parent_equity": 2_000.0},
    ]


def _payload(symbol: str, count: int = 300) -> dict:
    rows = []
    start = date(2023, 4, 21)
    for index in range(count):
        rows.append({"index": (start + timedelta(days=index)).isoformat() + " 00:00:00", symbol: 10 + index * .01})
    return {"code": symbol, "period": "1d", "dividend_type": "none", "data": {"Close": rows}}


class FakeClient:
    def call(self, method: str, *args, **kwargs):
        if method == "get_gb_info_by_date":
            start = date(2023, 4, 21)
            return [{"Date": int((start + timedelta(days=index)).strftime("%Y%m%d")), "Zgb": 100.0} for index in range(300)]
        if method == "get_divid_factors":
            return [{"Date": "2023-06-01T00:00:00", "Type": "1", "Bonus": 10.0}]
        raise AssertionError(method)


class FakeTdxService:
    def __init__(self) -> None:
        self.client = FakeClient()

    def fetch_kline(self, symbol: str, **_: object) -> dict:
        return _payload(symbol)


class FakeFinancialHistory:
    def query(self, symbol: str, *, as_of: str | None = None) -> dict:
        return {"items": _financials()}


class FakeLeaderService:
    def ensure_current_pool(self) -> dict:
        return {"id": "pool", "as_of": "2024-02-14", "members": [
            {"stock_code": "000001.SZ", "lifecycle_status": "NEW"},
            {"stock_code": "000002.SZ", "lifecycle_status": "ACTIVE"},
            {"stock_code": "000003.SZ", "lifecycle_status": "OUT_OF_TOP2"},
        ]}


def _service(tmp_path: Path) -> tuple[HistoricalValuationService, TdxDataStore]:
    store = TdxDataStore(tmp_path / "tdx.db")
    return HistoricalValuationService(
        tdx_store=store, tdx_service=FakeTdxService(), financial_history=FakeFinancialHistory(), leader_service=FakeLeaderService(),
    ), store


def _seed_ready(store: TdxDataStore, symbol: str) -> None:
    start = date(2019, 1, 1)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for index in range(2_200):
        trade_date = (start + timedelta(days=index)).isoformat()
        rows.append({
            "market": "CN", "stock_code": symbol, "trade_date": trade_date, "close": 10.0,
            "pe_ttm": 10.0, "pb_mrq": 1.0, "dividend_yield": None, "market_cap": 1_000.0,
            "financial_data_as_of": trade_date, "financial_source_id": "seed", "price_source_id": "seed",
            "source_type": "seed", "source_hash": f"{symbol}-{trade_date}", "quality_status": "READY",
            "created_at": now,
        })
    store.upsert_historical_valuation_series(rows)
    store.refresh_historical_valuation_coverage("CN", symbol)


def test_pit_rebuild_skips_future_financials_and_persists_positive_metrics(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    try:
        result = service.refresh_company("CN", SYMBOL, as_of="2024-02-14")
        assert result["coverage_status"] == "PARTIAL" and result["pe_count"] == 300 and result["pb_count"] == 300
        rows = service.tdx_store.get_historical_valuation_series("CN", SYMBOL)
        before_future = next(row for row in rows if row["trade_date"] == "2023-08-31")
        after_future = next(row for row in rows if row["trade_date"] == "2023-09-02")
        assert before_future["financial_data_as_of"] == "2023-04-20"
        assert after_future["financial_data_as_of"] == "2023-09-01"
        # No later announcement can appear in an earlier observation.
        assert before_future["pe_ttm"] == pytest.approx(before_future["close"] * 100 / 400)
        assert after_future["pe_ttm"] != before_future["pe_ttm"]
        assert before_future["pb_mrq"] is not None and before_future["dividend_yield"] is not None
    finally:
        store.close()


def test_percentiles_are_pit_safe_winsorized_and_dividend_direction_is_explicit(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    try:
        service.refresh_company("CN", SYMBOL, as_of="2024-02-14")
        earlier = service.get_valuation_history("CN", SYMBOL, as_of="2023-08-31")
        later = service.get_valuation_history("CN", SYMBOL, as_of="2024-02-14")
        assert earlier["coverage"]["last_date"] == "2023-08-31"
        assert len(service.tdx_store.get_historical_valuation_series("CN", SYMBOL, as_of="2023-08-31")) < len(service.tdx_store.get_historical_valuation_series("CN", SYMBOL))
        pe = later["historical_percentiles"]["pe_ttm"]
        dy = later["historical_percentiles"]["dividend_yield"]
        assert pe["status"] == "READY" and pe["winsorized"]["low_quantile"] == .01
        assert dy["direction"] == "higher_is_cheaper"
        assert dy["cheapness_percentile"] == pytest.approx(100 - dy["percentile"])
    finally:
        store.close()


def test_current_pool_stage_is_deduplicated_and_bounded(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    try:
        result = service.refresh_current_l3(limit=2, as_of="2024-02-14")
        assert result["processed"] == 2 and result["partial"] == 2 and result["failed"] == 0
        with pytest.raises(ValueError, match="1–20"):
            service.refresh_current_l3(limit=21)
    finally:
        store.close()


def test_full_pool_backfill_skips_ready_resumes_and_retries_failures(tmp_path: Path) -> None:
    class FullPoolLeader(FakeLeaderService):
        def ensure_current_pool(self) -> dict:
            return {"id": "full-pool", "as_of": "2024-02-14", "members": [
                {"stock_code": "000001.SZ", "lifecycle_status": "NEW"},
                {"stock_code": "000002.SZ", "lifecycle_status": "ACTIVE"},
                {"stock_code": "000003.SZ", "lifecycle_status": "REENTERED"},
                {"stock_code": "000004.SZ", "lifecycle_status": "OUT_OF_TOP2"},
                {"stock_code": "000001.SZ", "lifecycle_status": "ACTIVE"},
            ]}

    service, store = _service(tmp_path)
    service.leader_service = FullPoolLeader()
    _seed_ready(store, "000001.SZ")
    original = service.refresh_company
    failures = {"remaining": 1}

    def flaky(market: str, stock_code: str, *, as_of: str) -> dict:
        if stock_code == "000003.SZ" and failures["remaining"]:
            failures["remaining"] -= 1
            return {**store.historical_valuation_coverage(market, stock_code), "last_error": "ValueError: temporary"}
        return original(market, stock_code, as_of=as_of)

    service.refresh_company = flaky  # type: ignore[method-assign]
    try:
        started = service.backfill_current_l3_pool(
            as_of="2024-02-14", batch_size=2, max_batches=1, throttle_seconds=0,
        )
        assert started["total_count"] == 3 and started["skipped"] == 1 and started["partial"] == 1
        assert started["pending"] == 1 and started["next_offset"] == 2
        failed = service.backfill_current_l3_pool(
            resume_run_id=started["run_id"], max_batches=1, throttle_seconds=0,
        )
        assert failed["failed"] == 1 and failed["failures"][0]["stock_code"] == "000003.SZ"
        completed = service.backfill_current_l3_pool(
            resume_run_id=started["run_id"], retry_failed=True, max_batches=1, throttle_seconds=0,
        )
        assert completed["status"] == "COMPLETED" and completed["partial"] == 2 and completed["skipped"] == 1
        assert completed["usable_coverage_rate"] == 100.0 and completed["pe_usable"] == 3
        # A company without dividend data remains valuation-usable through
        # PE/PB and is not reported as failed.
        assert completed["dividend_usable"] == 2
    finally:
        store.close()


def test_non_positive_pe_pb_are_excluded_from_percentiles() -> None:
    rows = [{"pe_ttm": value, "pb_mrq": value, "dividend_yield": value} for value in range(1, 251)]
    rows.extend([{"pe_ttm": -10, "pb_mrq": 0, "dividend_yield": None}, {"pe_ttm": 0, "pb_mrq": -1, "dividend_yield": None}])
    pe = HistoricalValuationService._metric_percentile(rows, "pe_ttm", inverse=False)
    pb = HistoricalValuationService._metric_percentile(rows, "pb_mrq", inverse=False)
    assert pe["count"] == 250 and pb["count"] == 250
    assert pe["status"] == "READY" and pb["status"] == "READY"


def test_historical_valuation_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, store = _service(tmp_path)
    app = FastAPI(); register_historical_valuation_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(historical_valuation_routes, "get_historical_valuation_service", lambda: service)
    try:
        client = TestClient(app)
        refresh = client.post(f"/api/value/companies/{SYMBOL}/valuation-history/refresh?market=CN&as_of=2024-02-14")
        history = client.get(f"/api/value/companies/{SYMBOL}/valuation-history?market=CN&as_of=2024-02-14")
        assert refresh.status_code == 200 and history.status_code == 200
        assert history.json()["coverage"]["coverage_status"] == "PARTIAL"
        batch = client.post("/api/value/valuation-history/current-l3/backfill?as_of=2024-02-14&batch_size=2")
        assert batch.status_code == 200 and batch.json()["processed"] == 2
        run_id = batch.json()["run_id"]
        assert client.get(f"/api/value/valuation-history/current-l3/backfill/{run_id}").status_code == 200
    finally:
        store.close()

"""TDX professional-finance source preflight (Batch 1B dependency, 2026-09-02).

The check must judge staleness by observed local filing coverage, never by a
natural-day expectation about report periods.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.tdx_data.client import TdxClient
from src.tdx_data.financial_history import FinancialHistoryService
from src.tdx_data.store import TdxDataStore


class _HomeOnlyClient(TdxClient):
    """Never touches the real TDX bridge; only the package directory is used."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.user_dir = home / "PYPlugins" / "user"
        self.bridge_file = self.user_dir / "tqcenter.py"
        self._tq = None
        self._lock = __import__("threading").RLock()


def _service(tmp_path: Path, *, with_package: bool = True) -> FinancialHistoryService:
    home = tmp_path / "tdxhome"
    cw = home / "vipdoc" / "cw"
    cw.mkdir(parents=True, exist_ok=True)
    if with_package:
        (cw / "gpcw20260630.dat").write_bytes(b"x" * 2048)
    store = TdxDataStore(tmp_path / "tdx.db")
    return FinancialHistoryService(store=store, client=_HomeOnlyClient(home))


def _ingest(service: FinancialHistoryService, *, announcement_date: str, raw_version: str = "raw-1") -> None:
    service.store.upsert_records("financial_history", [{
        "key": f"600001.SH:2026-03-31:{announcement_date}",
        "category": "600001.SH", "name": "600001.SH",
        "payload": {
            "symbol": "600001.SH", "report_date": "2026-03-31",
            "announcement_date": announcement_date, "period_type": "q1",
            "raw_version": raw_version,
        },
    }])


def test_stale_when_local_filings_are_newer_than_ingested_source(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _ingest(service, announcement_date="2026-04-30")
    result = service.check_finance_source_freshness(
        as_of="2026-09-01", reference_latest_announcement_date="2026-08-30",
    )
    assert result["status"] == "STALE"
    assert result["latest_data_announcement_date"] == "2026-04-30"
    assert result["lag_days"] == 122
    assert "tdx_finance_source_stale" in result["reason"]


def test_ready_when_source_covers_local_filings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _ingest(service, announcement_date="2026-08-30")
    result = service.check_finance_source_freshness(
        as_of="2026-09-01", reference_latest_announcement_date="2026-08-30",
    )
    assert result["status"] == "READY"
    assert result["latest_package"] == "gpcw20260630.dat"
    assert result["package_report_period"] == "20260630"


def test_unknown_without_local_filing_reference(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _ingest(service, announcement_date="2026-04-30")
    result = service.check_finance_source_freshness(as_of="2026-09-01")
    assert result["status"] == "UNKNOWN"
    assert "no_local_filing_reference" in result["reason"]


def test_unknown_when_package_missing(tmp_path: Path) -> None:
    service = _service(tmp_path, with_package=False)
    _ingest(service, announcement_date="2026-04-30")
    result = service.check_finance_source_freshness(
        as_of="2026-09-01", reference_latest_announcement_date="2026-08-30",
    )
    assert result["status"] == "UNKNOWN"
    assert "needs_professional_finance" in result["reason"]


def test_collector_lag_is_advisory_not_stale(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _ingest(service, announcement_date="2026-08-30", raw_version="raw-old")
    result = service.check_finance_source_freshness(
        as_of="2026-09-01", reference_latest_announcement_date="2026-08-30",
    )
    assert result["status"] == "READY"
    assert result["collector_lag"] is True


def test_payload_never_leaks_error_objects(tmp_path: Path) -> None:
    service = _service(tmp_path)
    # Empty dataset with a valid package and reference must not crash.
    result = service.check_finance_source_freshness(
        as_of="2026-09-01", reference_latest_announcement_date="2026-08-30",
    )
    assert json.dumps(result)  # JSON-serialisable verdict for API/EOD use

from __future__ import annotations

from pathlib import Path

from src.tdx_data.financial_history import FINANCIAL_HISTORY_DATASET, FinancialHistoryService
from src.tdx_data.store import TdxDataStore


def _raw(report_date: str, announcement_date: str) -> dict[str, float | str]:
    return {
        "tag_time": report_date, "announce_time": announcement_date,
        "FN8": 100, "FN11": 80, "FN17": 20, "FN21": 300, "FN40": 600,
        "FN54": 150, "FN63": 350, "FN69": 200, "FN72": 250,
        "FN202": 30, "FN210": 58, "FN230": 400, "FN232": 40, "FN234": 30,
        "FN327": 35,
    }


class _FakeTdxClient:
    home = Path(".")

    def call(self, method: str, *, stock_list: list[str], **_kwargs):
        assert method == "get_financial_data"
        return {
            symbol: [_raw("20251231", "20260331")]
            for symbol in stock_list
        }


def test_incremental_financial_history_persists_each_completed_batch(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx_data.db")
    service = FinancialHistoryService(store=store, client=_FakeTdxClient())
    service.package_status = lambda: {"status": "ready", "raw_version": "test"}  # type: ignore[method-assign]
    observed: list[tuple[int, int]] = []

    result = service.collect_incremental(
        ["000001.SZ", "000002.SZ"], batch_size=1,
        progress=lambda done, _total, _label: observed.append((
            done, len(store.list_records(FINANCIAL_HISTORY_DATASET, limit=100)["items"]),
        )),
    )

    assert result["status"] == "ready"
    assert result["symbols"] == 2
    assert result["item_count"] == 2
    assert observed == [(1, 1), (2, 2)]
    store.close()

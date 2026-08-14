from __future__ import annotations

from datetime import date, timedelta

import pytest
import pandas as pd

from src.strategy_engines.history import HistoricalFeatureStore
from src.strategy_engines.value_market_history import ValueMarketHistoryService
from src.value_workspace.technical import TECHNICAL_FORMULA_VERSION, calculate_technical


def _rows(*, growth: float, count: int = 140, volume: float = 1_000_000) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    rows = []
    for index in range(count):
        close = 10 * ((1 + growth) ** index)
        rows.append({
            "data_as_of": (start + timedelta(days=index)).isoformat(),
            "open": close * .995, "high": close * 1.01, "low": close * .99, "close": close,
            "volume": volume * (1 + index / 1000), "amount": close * volume,
            "source": "TongDaXin",
        })
    return rows


def test_company_technical_uses_history_and_benchmark() -> None:
    result = calculate_technical(_rows(growth=.003), _rows(growth=.001))

    assert result["formula_version"] == TECHNICAL_FORMULA_VERSION
    assert result["status"] == "ready"
    assert result["bar_count"] == 140
    assert result["trend"] == "多头排列"
    assert result["metrics"]["return_20d"] > 0
    assert result["metrics"]["relative_strength_20d"] > 0
    assert result["metrics"]["support_20d"] < result["metrics"]["resistance_20d"]
    assert result["coverage"] >= .8


def test_company_technical_reports_insufficient_history() -> None:
    result = calculate_technical(_rows(growth=.001, count=10), [])

    assert result["status"] == "unavailable"
    assert result["coverage"] == pytest.approx(10 / 120)
    assert result["metrics"] == {}
    assert result["missing_fields"] == ["至少20个有效交易日日线"]


def test_market_history_reads_only_requested_symbols(tmp_path) -> None:
    store = HistoricalFeatureStore(root=tmp_path / "history", catalog_db=tmp_path / "research.db")
    for index, as_of in enumerate(("2026-08-11", "2026-08-12", "2026-08-13"), 1):
        store.write_partition(
            market="CN", dataset="value_ohlcv", data_as_of=as_of,
            frame=pd.DataFrame([
                {"symbol": "000001.SZ", "close": 10 + index, "volume": 1000, "source": "TongDaXin"},
                {"symbol": "600000.SH", "close": 20 + index, "volume": 2000, "source": "TongDaXin"},
            ]),
            provider="TongDaXin", coverage=1,
        )
    service = ValueMarketHistoryService(store=store, client=object())

    result = service.read_symbols(["000001.SZ"], as_of="2026-08-13", count=20)

    assert list(result) == ["000001.SZ"]
    assert [item["close"] for item in result["000001.SZ"]] == [11, 12, 13]

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import value_price_zone_routes
from src.api.value_price_zone_routes import register_value_price_zone_routes
from src.company_thesis.service import CompanyThesisService
from src.financial_analysis.store import FinancialAnalysisStore
from src.tdx_data.store import TdxDataStore
from src.value_price_zones.service import ValuePriceZoneService


SYMBOL = "000001.SZ"


def _kline_payload(symbol: str, bars: int = 140) -> dict:
    start = date(2026, 1, 1)
    rows = {key: [] for key in ("Open", "High", "Low", "Close", "Volume", "Amount")}
    for index in range(bars):
        # Repeated valleys and peaks make the expected cluster deterministic.
        close = 100 + 12 * math.sin(index / 7) + (index % 9) * 0.1
        day = (start + timedelta(days=index)).isoformat() + " 00:00:00"
        values = {"Open": close - 0.8, "High": close + 2.0, "Low": close - 2.0, "Close": close,
                  "Volume": 1_000 + (index % 8) * 100, "Amount": close * 10_000}
        for key, value in values.items():
            rows[key].append({"index": day, symbol: value})
    return {"code": symbol, "period": "1d", "dividend_type": "front", "data": rows}


def _setup(tmp_path: Path) -> tuple[TdxDataStore, FinancialAnalysisStore, CompanyThesisService, ValuePriceZoneService]:
    tdx = TdxDataStore(tmp_path / "tdx.db")
    financial = FinancialAnalysisStore(tmp_path / "research.db")
    thesis = CompanyThesisService(db_path=tmp_path / "research.db")
    tdx.upsert_records("klines", [{"key": f"{SYMBOL}:1d:front", "name": SYMBOL, "payload": _kline_payload(SYMBOL)}])
    fundamentals = [
        (SYMBOL, 20.0, 2.0), ("000002.SZ", 12.0, 1.2), ("000003.SZ", 16.0, 1.6),
        ("000004.SZ", 24.0, 2.4), ("000005.SZ", 28.0, 2.8),
    ]
    tdx.upsert_records("fundamentals", [{
        "key": code, "name": code, "payload": {"code": code, "pe_ttm": pe, "pb_mrq": pb},
    } for code, pe, pb in fundamentals])
    tdx.upsert_records("research_terminal_industry_members", [{
        "key": f"L3:{code}", "category": "L3", "name": code,
        "payload": {"industry_code": "L3", "stock_code": code},
    } for code, _, _ in fundamentals])
    snapshot, _ = financial.save_python_snapshot({
        "stock_code": SYMBOL, "stock_name": "测试公司", "as_of": "2026-05-20", "historical_cutoff": "2026-05-20",
        "financial_feature_version": "v1", "forecast_version": "v1", "feature_status": "READY", "forecast_status": "READY", "analysis_status": "NOT_RUN",
        "identity": {"level3_code": "L3", "market_valuation": {"pe": 20.0, "pb": 2.0}},
        "history": [{"period_type": "annual", "report_date": "2025-12-31", "net_profit": 100.0}],
        "feature": {}, "forecast": {"status": "READY", "scenarios": {"BASE": {"forecast": [{"year": "2026E", "net_profit": 120.0}]}}},
        "data_gaps": [], "source_hash": "zone-test",
    })
    assert snapshot["stock_code"] == SYMBOL
    thesis.create_initial_thesis(market="CN", stock_code=SYMBOL, title="测试", core_thesis="测试逻辑", status="WEAKENING", confidence="MEDIUM", invalid_conditions=[], created_by="HUMAN", source_data_as_of="2026-05-20")
    return tdx, financial, thesis, ValuePriceZoneService(tdx_store=tdx, financial_store=financial, thesis_repository=thesis.repository)


def _close(tdx: TdxDataStore, financial: FinancialAnalysisStore, thesis: CompanyThesisService, service: ValuePriceZoneService) -> None:
    service.close(); thesis.close(); financial.close(); tdx.close()


def test_price_zone_service_calculates_valuation_structure_confluence_and_is_read_only(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        with sqlite3.connect(tmp_path / "tdx.db") as conn:
            tdx_before = conn.execute("select count(*) from records").fetchone()[0]
        with sqlite3.connect(tmp_path / "research.db") as conn:
            research_before = conn.execute("select count(*) from company_theses").fetchone()[0]
        result = service.get_price_zones("CN", SYMBOL)
        with sqlite3.connect(tmp_path / "tdx.db") as conn:
            tdx_after = conn.execute("select count(*) from records").fetchone()[0]
        with sqlite3.connect(tmp_path / "research.db") as conn:
            research_after = conn.execute("select count(*) from company_theses").fetchone()[0]
        assert (tdx_before, research_before) == (tdx_after, research_after)
        assert result["valuation"]["status"] in {"DEEPLY_UNDERVALUED", "UNDERVALUED", "FAIR", "OVERVALUED", "DEEPLY_OVERVALUED"}
        assert result["valuation"]["fair_value_low"] is not None
        assert result["data_quality"]["historical_valuation"]["status"] == "INSUFFICIENT"
        assert result["data_quality"]["daily_history"]["status"] == "READY"
        assert result["support_zones"] and result["resistance_zones"]
        assert {zone["strength"] for zone in result["support_zones"]} <= {"LOW", "MEDIUM", "HIGH"}
        assert {zone["name"] for zone in result["valuation_zones"]} >= {"深度低估区", "低估关注区", "合理区", "偏高区"}
        assert result["thesis_status"] == "WEAKENING"
        assert "买入" not in result["plain_summary"] and "卖出" not in result["plain_summary"]
    finally:
        _close(tdx, financial, thesis, service)


def test_history_is_strictly_pit_and_future_bar_cannot_change_as_of_structure(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        cutoff = "2026-04-30"
        before_bars, _ = service._bars(SYMBOL, date.fromisoformat(cutoff))
        payload = _kline_payload(SYMBOL, bars=180)
        # Append a deliberately extreme future observation; the earlier PIT
        # bar set must stay identical and never read it.
        for field, value in (("Open", 999.0), ("High", 1_100.0), ("Low", 900.0), ("Close", 1_050.0), ("Volume", 99_999.0), ("Amount", 1_000_000.0)):
            payload["data"][field][-1][SYMBOL] = value
        tdx.upsert_records("klines", [{"key": f"{SYMBOL}:1d:front", "name": SYMBOL, "payload": payload}])
        after_bars, _ = service._bars(SYMBOL, date.fromisoformat(cutoff))
        assert after_bars == before_bars
        assert all(item["date"] <= cutoff for item in after_bars)
    finally:
        _close(tdx, financial, thesis, service)


def test_price_zone_prefers_unified_adjusted_daily_bar_cache(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        payload = _kline_payload(SYMBOL)
        rows = []
        for index, close_item in enumerate(payload["data"]["Close"]):
            stamp = str(close_item["index"])[:10]
            rows.append({
                "market": "CN", "stock_code": SYMBOL, "trade_date": stamp,
                "open": payload["data"]["Open"][index][SYMBOL], "high": payload["data"]["High"][index][SYMBOL],
                "low": payload["data"]["Low"][index][SYMBOL], "close": close_item[SYMBOL],
                "volume": payload["data"]["Volume"][index][SYMBOL], "amount": payload["data"]["Amount"][index][SYMBOL],
                "adjustment_type": "front", "source": "TongDaXin", "source_version": "test",
                "fetched_at": "2026-05-20T00:00:00+00:00", "source_hash": f"bar-{index}",
            })
        tdx.upsert_adjusted_daily_bars(rows)
        tdx.refresh_adjusted_daily_bar_coverage("CN", SYMBOL, source="TongDaXin", source_version="test")
        _bars, quality = service._bars(SYMBOL, None)
        assert quality["source"] == "adjusted_daily_bars" and quality["status"] == "PARTIAL"
        assert len(_bars) == 140
    finally:
        _close(tdx, financial, thesis, service)


def test_as_of_price_prefers_same_day_quote_over_stale_daily_bar(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        # The durable daily history deliberately stops before the research
        # date.  A same-day TDX quote must win; using the old bar would make a
        # daily report silently repeat a prior close.
        tdx.replace_dataset("quotes", [{
            "key": SYMBOL,
            "name": SYMBOL,
            "payload": {"price": 88.8, "data_as_of": "2026-08-20T15:00:00+08:00"},
        }])
        result = service.get_price_zones("CN", SYMBOL, as_of="2026-08-20")
        assert result["current_price"] == 88.8
        assert result["price_as_of"] == "2026-08-20T15:00:00+08:00"
        assert result["data_quality"]["price"] == {
            "status": "READY", "as_of": "2026-08-20T15:00:00+08:00", "source": "tdx_quote_cache", "message": "",
        }
    finally:
        _close(tdx, financial, thesis, service)


def test_as_of_price_uses_immutable_quote_snapshot_and_rejects_stale_price(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        snapshot_id = "cn-20260820-close"
        tdx.create_refresh_run(
            "run-close", profile="market_close", market="CN", market_date="2026-08-20",
            snapshot_id=snapshot_id, modules=("quote",),
        )
        with tdx.snapshot_context(snapshot_id):
            tdx.replace_dataset("quotes", [{
                "key": SYMBOL,
                "name": SYMBOL,
                "payload": {"price": 77.7, "data_as_of": "2026-08-20T15:00:00+08:00"},
            }])
        tdx.record_dataset_snapshot(
            snapshot_id=snapshot_id, refresh_run_id="run-close", dataset="quotes", market="CN",
            market_date="2026-08-20", source="tdx", coverage=1.0, item_count=1, expected_count=1, status="ready",
        )
        tdx.update_refresh_run("run-close", status="completed")
        tdx.replace_dataset("quotes", [{
            "key": SYMBOL,
            "name": SYMBOL,
            "payload": {"price": 99.9, "data_as_of": "2026-08-21T15:00:00+08:00"},
        }])

        historical = service.get_price_zones("CN", SYMBOL, as_of="2026-08-20")
        assert historical["current_price"] == 77.7
        assert historical["data_quality"]["price"]["source"] == "tdx_quote_snapshot"

        stale = service.get_price_zones("CN", SYMBOL, as_of="2026-08-22")
        assert stale["current_price"] is None
        assert stale["valuation"]["status"] == "INSUFFICIENT_DATA"
        assert stale["data_quality"]["price"]["status"] == "STALE"
    finally:
        _close(tdx, financial, thesis, service)


def test_intersections_and_missing_history_degrade_honestly(tmp_path: Path) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    try:
        intersections = service._intersections(
            [{"name": "低估关注区", "low": 72.0, "high": 80.0, "kind": "UNDERVALUED"}],
            [{"low": 74.0, "high": 78.0, "strength": "HIGH", "reasons": ["历史重要低点"]}],
            kind="UNDERVALUED", valuation_status="UNDERVALUED",
        )
        assert intersections == [{"low": 74.0, "high": 78.0, "valuation_status": "UNDERVALUED", "support_strength": "HIGH", "reasons": ["低估关注区与历史支撑区重叠", "历史重要低点"]}]
        upper = service._intersections(
            [{"name": "偏高区", "low": 100.0, "high": 110.0, "kind": "OVERVALUED"}],
            [{"low": 105.0, "high": 112.0, "strength": "MEDIUM", "reasons": ["历史重要高点"]}],
            kind="OVERVALUED", valuation_status="OVERVALUED",
        )
        assert upper[0]["low"] == 105.0 and upper[0]["high"] == 110.0
        tdx.replace_dataset("klines", [])
        tdx.upsert_records("quotes", [{
            "key": SYMBOL, "name": SYMBOL,
            "payload": {"code": SYMBOL, "price": 105.0, "data_as_of": "2026-05-20"},
        }])
        result = service.get_price_zones("CN", SYMBOL)
        assert result["support_zones"] == [] and result["resistance_zones"] == []
        assert result["data_quality"]["daily_history"]["status"] == "MISSING"
        # Missing bars must explain the materialization scope instead of a
        # bare "无缓存" that reads like an unexplained failure.
        assert "仅对三级行业龙头池与低估龙头池等研究范围物化日线" in result["data_quality"]["daily_history"]["message"]
        assert "历史估值分位暂缺" in result["plain_summary"]
        assert "仅研究范围内公司可用" in result["data_quality"]["historical_valuation"]["message"]
    finally:
        _close(tdx, financial, thesis, service)


def test_price_zone_api_uses_shared_read_only_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tdx, financial, thesis, service = _setup(tmp_path)
    app = FastAPI(); register_value_price_zone_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(value_price_zone_routes, "get_value_price_zone_service", lambda: service)
    try:
        client = TestClient(app)
        response = client.get(f"/api/value/companies/{SYMBOL}/price-zones?market=CN")
        rebuilt = client.post(f"/api/value/companies/{SYMBOL}/price-zones/rebuild?market=CN")
        assert response.status_code == 200 and rebuilt.status_code == 200
        assert response.json()["formula_version"] == rebuilt.json()["formula_version"]
    finally:
        _close(tdx, financial, thesis, service)

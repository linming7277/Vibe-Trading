from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import entry_research_routes
from src.api.entry_research_routes import register_entry_research_routes
from src.entry_research.service import EntryResearchService


SYMBOL = "000001.SZ"


def _zones(*, valuation: str = "DEEPLY_UNDERVALUED", historical: str = "CHEAP", confluence: list[dict] | None = None,
           supports: list[dict] | None = None, resistances: list[dict] | None = None, historical_quality: str = "READY",
           daily_quality: str = "READY") -> dict:
    return {
        "stock_code": SYMBOL, "as_of": "2026-08-19", "current_price": 74.0,
        "valuation": {"status": valuation, "fair_value_low": 80.0, "fair_value_mid": 90.0, "fair_value_high": 100.0},
        "valuation_zones": [
            {"name": "较高安全边际区", "low": 64.0, "high": 72.0, "kind": "UNDERVALUED"},
            {"name": "低估关注区", "low": 72.0, "high": 80.0, "kind": "UNDERVALUED"},
        ],
        "historical_valuation": {
            "historical_valuation_status": historical,
            "coverage": {"coverage_status": historical_quality},
            "historical_percentiles": {
                "pe_ttm": {"state": "CHEAP"}, "pb_mrq": {"state": "CHEAP"},
                "dividend_yield": {"state": "VERY_CHEAP"},
            },
        },
        "support_zones": supports if supports is not None else [{"low": 72.0, "high": 76.0, "strength": "HIGH"}],
        "resistance_zones": resistances or [],
        "confluence_zones": confluence if confluence is not None else [
            {"low": 72.0, "high": 76.0, "support_strength": "HIGH"},
        ],
        "data_quality": {"daily_history": {"status": daily_quality}},
    }


class FakeZones:
    def __init__(self, value: dict) -> None:
        self.value = value
        self.calls: list[tuple[str, str, str | None]] = []

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        self.calls.append((market, stock_code, as_of))
        return deepcopy(self.value)


class FakeThesis:
    def __init__(self, status: str = "STRENGTHENING", confidence: str = "HIGH", *, created_at: str = "2026-08-01T00:00:00+00:00", source_as_of: str = "2026-08-01") -> None:
        self.value = {"status": status, "confidence": confidence, "created_at": created_at, "source_data_as_of": source_as_of}
        self.reads = 0

    def get_current_thesis(self, market: str, stock_code: str) -> dict:
        self.reads += 1
        return deepcopy(self.value)


def _service(**kwargs: object) -> EntryResearchService:
    return EntryResearchService(price_zone_service=FakeZones(_zones()), thesis_repository=FakeThesis(), **kwargs)  # type: ignore[arg-type]


def test_deep_value_high_strength_confluence_scores_high_without_execution_language() -> None:
    service = _service()
    result = service.get_entry_research("CN", SYMBOL)
    assert result["entry_score"] == 95.0 and result["entry_level"] == "HIGH_ATTENTION"
    assert result["support_score"] == 100 and result["confidence"] == "HIGH"
    assert {"VALUATION_DEEPLY_UNDERVALUED", "VALUATION_SUPPORT_CONFLUENCE", "HIGH_SUPPORT", "THESIS_STRENGTHENING"} <= set(result["reason_codes"])
    assert result["focus_zones"]["focus_zone"] == {"label": "重点观察区", "low": 72.0, "high": 76.0, "kind": "FOCUS", "strength": "HIGH"}
    rendered = str(result)
    assert "BUY" not in rendered and "SELL" not in rendered and "买入" not in rendered and "卖出" not in rendered


def test_low_valuation_without_support_and_expensive_with_support_are_explainable() -> None:
    low_no_support = EntryResearchService(
        price_zone_service=FakeZones(_zones(valuation="UNDERVALUED", confluence=[], supports=[])), thesis_repository=FakeThesis("UNCHANGED"),
    ).get_entry_research("CN", SYMBOL)
    expensive_support = EntryResearchService(
        price_zone_service=FakeZones(_zones(valuation="OVERVALUED", historical="EXPENSIVE")), thesis_repository=FakeThesis("UNCHANGED"),
    ).get_entry_research("CN", SYMBOL)
    assert low_no_support["support_score"] == 20 and "NO_NEAR_SUPPORT" in low_no_support["reason_codes"]
    assert expensive_support["entry_score"] < low_no_support["entry_score"]
    assert "HISTORICAL_VALUATION_EXPENSIVE" in expensive_support["reason_codes"]


def test_thesis_safety_gates_cap_weakening_and_block_falsified() -> None:
    weakening = EntryResearchService(price_zone_service=FakeZones(_zones()), thesis_repository=FakeThesis("WEAKENING")).get_entry_research("CN", SYMBOL)
    falsified = EntryResearchService(price_zone_service=FakeZones(_zones()), thesis_repository=FakeThesis("FALSIFIED")).get_entry_research("CN", SYMBOL)
    assert weakening["entry_score"] > 85 and weakening["entry_level"] == "WATCH" and weakening["safety_gate"] == "WEAKENING_CAP"
    assert falsified["entry_level"] == "BLOCKED" and falsified["thesis_score"] == 0 and falsified["safety_gate"] == "FALSIFIED"


def test_data_quality_and_near_resistance_guard_attention() -> None:
    zone = _zones(historical_quality="INSUFFICIENT", resistances=[{"low": 74.0, "high": 76.0, "strength": "HIGH"}])
    result = EntryResearchService(price_zone_service=FakeZones(zone), thesis_repository=FakeThesis()).get_entry_research("CN", SYMBOL)
    assert result["confidence"] == "MEDIUM" and result["entry_level"] == "WATCH"
    assert result["support_score"] == 80 and "NEAR_RESISTANCE" in result["reason_codes"]
    assert result["data_gaps"] == ["HISTORICAL_VALUATION"]


def test_as_of_is_forwarded_and_future_thesis_is_not_used() -> None:
    zones, thesis = FakeZones(_zones()), FakeThesis(created_at="2026-08-20T00:00:00+00:00", source_as_of="2026-08-20")
    result = EntryResearchService(price_zone_service=zones, thesis_repository=thesis).get_entry_research("CN", SYMBOL, as_of="2026-08-19")
    assert zones.calls == [("CN", SYMBOL, "2026-08-19")]
    assert result["thesis_status"] is None and "THESIS" in result["data_gaps"]


def test_entry_research_api_is_read_only_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    app = FastAPI(); register_entry_research_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(entry_research_routes, "get_entry_research_service", lambda: service)
    client = TestClient(app)
    response = client.get(f"/api/value/companies/{SYMBOL}/entry-research?market=CN&as_of=2026-08-19")
    assert response.status_code == 200 and response.json()["entry_level"] == "HIGH_ATTENTION"

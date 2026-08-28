from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import exit_research_routes
from src.api.exit_research_routes import register_exit_research_routes
from src.exit_research.service import ExitResearchService


SYMBOL = "000001.SZ"


def _zones(*, valuation: str = "DEEPLY_OVERVALUED", historical: str = "VERY_EXPENSIVE",
           upper: list[dict] | None = None, resistances: list[dict] | None = None,
           historical_quality: str = "READY", daily_quality: str = "READY") -> dict:
    return {
        "stock_code": SYMBOL, "as_of": "2026-08-19", "current_price": 108.0,
        "valuation": {"status": valuation, "fair_value_low": 80.0, "fair_value_mid": 90.0, "fair_value_high": 100.0},
        "historical_valuation": {"historical_valuation_status": historical, "coverage": {"coverage_status": historical_quality}},
        "upper_review_zones": upper if upper is not None else [{"low": 105.0, "high": 110.0, "support_strength": "HIGH"}],
        "resistance_zones": resistances if resistances is not None else [{"low": 105.0, "high": 110.0, "strength": "HIGH"}],
        "data_quality": {"daily_history": {"status": daily_quality}},
    }


class FakeZones:
    def __init__(self, value: dict) -> None:
        self.value, self.calls = value, []

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        self.calls.append((market, stock_code, as_of))
        return deepcopy(self.value)


class FakeThesis:
    db_path = None

    def __init__(self, status: str = "WEAKENING", confidence: str = "HIGH", *, created_at: str = "2026-08-01T00:00:00+00:00", source_as_of: str = "2026-08-01") -> None:
        self.value = {"thesis_id": "thesis-1", "status": status, "confidence": confidence, "created_at": created_at, "source_data_as_of": source_as_of}

    def get_current_thesis(self, market: str, stock_code: str) -> dict:
        return deepcopy(self.value)


class FakeEvidence:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def list_active_evidence_for_thesis(self, thesis_id: str) -> list[dict]:
        return deepcopy(self.rows)


class FakeReview:
    def __init__(self, value: dict | None = None) -> None:
        self.value = value

    def get_latest_review(self, market: str, stock_code: str) -> dict | None:
        return deepcopy(self.value)


def _challenge(index: int, confidence: str = "HIGH", *, created_at: str = "2026-08-10T00:00:00+00:00") -> dict:
    return {"evidence_id": f"c{index}", "effect": "CHALLENGE", "confidence": confidence,
            "summary": f"挑战证据 {index}", "created_at": created_at, "data_as_of": "2026-08-10"}


def _service(*, zones: dict | None = None, thesis: FakeThesis | None = None, evidence: list[dict] | None = None, review: dict | None = None) -> ExitResearchService:
    return ExitResearchService(price_zone_service=FakeZones(zones or _zones()), thesis_repository=thesis or FakeThesis(),
                               evidence_repository=FakeEvidence(evidence), review_repository=FakeReview(review))  # type: ignore[arg-type]


def test_overvaluation_strong_resistance_weakening_and_challenges_require_critical_review() -> None:
    result = _service(evidence=[_challenge(1), _challenge(2, "MEDIUM"), _challenge(3, "LOW")]).get_exit_research("CN", SYMBOL)
    assert result["exit_score"] == 100.0 and result["exit_level"] == "CRITICAL_REVIEW"
    assert result["valuation_pressure"] == result["historical_valuation_pressure"] == result["resistance_pressure"] == 100
    assert result["thesis_risk"] == 100 and result["challenge_count"] == 3
    assert {"VALUATION_RESISTANCE_CONFLUENCE", "THESIS_WEAKENING", "MULTIPLE_CHALLENGES"} <= set(result["reason_codes"])
    assert result["focus_zones"]["focus_zone"]["low"] == 105.0
    assert "SELL" not in str(result) and "卖出" not in str(result)


def test_high_valuation_with_strengthening_thesis_does_not_become_critical_from_price_alone() -> None:
    result = _service(thesis=FakeThesis("STRENGTHENING")).get_exit_research("CN", SYMBOL)
    assert result["exit_level"] == "REVIEW" and result["exit_score"] == 75.0
    assert result["thesis_risk"] == 0 and result["thesis_status"] == "STRENGTHENING"


def test_falsified_is_critical_and_missing_data_caps_levels() -> None:
    falsified = _service(thesis=FakeThesis("FALSIFIED"), zones=_zones(valuation="UNDERVALUED", historical="CHEAP", upper=[], resistances=[])).get_exit_research("CN", SYMBOL)
    partial = _service(zones=_zones(historical_quality="INSUFFICIENT", daily_quality="MISSING", upper=[], resistances=[]), thesis=FakeThesis("WEAKENING")).get_exit_research("CN", SYMBOL)
    assert falsified["exit_level"] == "CRITICAL_REVIEW" and falsified["safety_gate"] == "FALSIFIED"
    assert partial["exit_level"] == "WATCH" and partial["confidence"] == "LOW"
    assert {"HISTORICAL_VALUATION", "RESISTANCE_HISTORY"} <= set(partial["data_gaps"])


def test_no_pressure_stale_review_and_challenge_sorting_are_explainable() -> None:
    review = {"review_id": "r1", "review_status": "PENDING", "recommended_status": "WEAKENING", "is_stale": True, "created_at": "2026-08-10T00:00:00+00:00"}
    evidence = [_challenge(1, "MEDIUM", created_at="2026-08-12T00:00:00+00:00"), _challenge(2, "HIGH", created_at="2026-08-11T00:00:00+00:00")]
    result = _service(zones=_zones(valuation="UNDERVALUED", historical="CHEAP", upper=[], resistances=[]), thesis=FakeThesis("UNCHANGED"), evidence=evidence, review=review).get_exit_research("CN", SYMBOL)
    assert result["resistance_pressure"] == 20 and "NO_NEAR_RESISTANCE" in result["reason_codes"]
    assert "REVIEW_STALE" in result["reason_codes"] and result["latest_review"]["is_stale"] is True
    assert [item["evidence_id"] for item in result["challenge_evidence"]] == ["c2", "c1"]


def test_as_of_forwards_to_price_zone_and_excludes_future_research_records() -> None:
    zones = FakeZones(_zones())
    service = ExitResearchService(price_zone_service=zones, thesis_repository=FakeThesis(created_at="2026-08-20T00:00:00+00:00", source_as_of="2026-08-20"),
                                  evidence_repository=FakeEvidence([_challenge(1, created_at="2026-08-20T00:00:00+00:00")]),
                                  review_repository=FakeReview({"review_id": "future", "created_at": "2026-08-20T00:00:00+00:00", "is_stale": True}))  # type: ignore[arg-type]
    result = service.get_exit_research("CN", SYMBOL, as_of="2026-08-19")
    assert zones.calls == [("CN", SYMBOL, "2026-08-19")]
    assert result["thesis_status"] is None and result["challenge_count"] == 0 and result["latest_review"] is None


def test_exit_research_api_is_read_only_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    app = FastAPI(); register_exit_research_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(exit_research_routes, "get_exit_research_service", lambda: service)
    response = TestClient(app).get(f"/api/value/companies/{SYMBOL}/exit-research?market=CN&as_of=2026-08-19")
    assert response.status_code == 200 and response.json()["exit_level"] == "CRITICAL_REVIEW"

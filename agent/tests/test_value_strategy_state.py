from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import value_strategy_routes
from src.api.value_strategy_routes import register_value_strategy_routes
from src.value_strategy.service import ValueStrategyStateService


class Pool:
    def __init__(self, active):
        self.rows = active
        self.db_path = None

    def active(self, market):
        return deepcopy(self.rows)


class Focus:
    def __init__(self, tier="A", code="605108.SH"):
        self.tier, self.code = tier, code

    def get_focus_selection(self, as_of=None):
        item = {"stock_code": self.code, "tier": self.tier, "focus_reasons": ["测试原因"]}
        return {"research_as_of": as_of or "2026-08-31", "A": [item] if self.tier == "A" else [], "B": [item] if self.tier == "B" else [], "C": [item] if self.tier == "C" else []}


class Result:
    def __init__(self, value): self.value = value
    def get_entry_research(self, *args, **kwargs): return deepcopy(self.value)
    def get_exit_research(self, *args, **kwargs): return deepcopy(self.value)
    def get_price_zones(self, *args, **kwargs): return deepcopy(self.value)
    def get_risk_research(self, *args, **kwargs): return deepcopy(self.value)
    def get_current_thesis(self, *args, **kwargs): return deepcopy(self.value)
    def get_profile(self, *args, **kwargs): return deepcopy(self.value)


def zones(peer_count=8, *, current=10, fair=20, last_bar="2026-08-31"):
    return {
        "as_of": "2026-09-01", "price_as_of": "2026-09-01", "current_price": current,
        "valuation": {"fair_value_mid": fair, "methods": [{"name": "PE", "status": "READY", "peer_count": peer_count}]},
        "historical_valuation": {"as_of": "2026-08-31", "coverage": {"coverage_status": "READY"}},
        "data_quality": {"daily_history": {"last_date": last_bar}},
    }


def service(*, code="605108.SH", tier="A", risk="MEDIUM", authority="HUMAN_CONFIRMED", thesis_status="FORMING", peer_count=8, current=10, fair=20, last_bar="2026-08-31", in_pool=True, entry_level="HIGH_ATTENTION"):
    pool_rows = [{"market": "CN", "stock_code": code, "company_name": "测试公司", "source_as_of": "2026-08-31", "leader_rank": 1, "industry_name": "测试行业"}] if in_pool else []
    return ValueStrategyStateService(
        pool_repository=Pool(pool_rows), focus_service=Focus(tier, code),
        entry_service=Result({"entry_level": entry_level, "entry_score": 88, "reason_codes": ["VALUATION_SUPPORT_CONFLUENCE"]}),
        exit_service=Result({"exit_level": "NORMAL", "exit_score": 20, "reason_codes": []}),
        price_zone_service=Result(zones(peer_count, current=current, fair=fair, last_bar=last_bar)),
        risk_service=Result({"overall_risk": risk, "value_trap_risk": "UNKNOWN", "as_of": "2026-08-31", "summary": "风险摘要"}),
        thesis_repository=Result({"status": thesis_status, "authority_status": authority, "source_data_as_of": "2026-08-31"}),
        leader_service=Result({"company": {"stock_name": "测试公司"}, "research_as_of": "2026-08-31", "leader_position": {"status": "READY", "rank": 1, "as_of": "2026-08-31", "level3": {"name": "测试行业"}}}),
    )


def test_outside_pool_hides_price_and_review_as_primary():
    state = service(code="600460.SH", in_pool=False, entry_level="WAIT").get_strategy_state("CN", "600460")
    assert state["eligibility"]["status"] == "OUTSIDE_VALUE_SCOPE"
    assert state["priority"]["tier"] == "NOT_APPLICABLE"
    assert state["primary_action"]["status"] == "OUTSIDE_VALUE_SCOPE"
    assert state["price_attention"]["primary"] is False
    assert state["review_pressure"]["primary"] is False


def test_focus_a_maps_to_priority_research():
    assert service().get_strategy_state("CN", "605108")["primary_action"]["status"] == "PRIORITY_RESEARCH"


def test_high_risk_overrides_focus_and_preserves_raw_attention():
    state = service(code="000544.SZ", tier="C", risk="HIGH").get_strategy_state("CN", "000544")
    assert state["primary_action"]["status"] == "RISK_REVIEW"
    assert state["price_attention"]["raw_level"] == "HIGH_ATTENTION"
    assert "高风险" in state["summary"]


def test_falsified_precedes_high_risk():
    state = service(risk="HIGH", thesis_status="FALSIFIED").get_strategy_state("CN", "605108")
    assert state["primary_action"]["status"] == "THESIS_REVIEW"


def test_insufficient_valuation_requires_data_review():
    state = service(peer_count=2).get_strategy_state("CN", "605108")
    assert state["price_attention"]["effective_status"] == "DATA_REVIEW_REQUIRED"
    assert state["primary_action"]["status"] == "VALUATION_DATA_REVIEW"


def test_unknown_risk_is_not_relabelled_low():
    state = service(code="600210.SH", tier="B", risk="UNKNOWN").get_strategy_state("CN", "600210")
    assert state["risk"]["overall"] == "UNKNOWN"
    assert state["primary_action"]["status"] == "CONTINUE_OBSERVE"
    assert "风险资料仍不完整" in state["summary"]


def test_thesis_authority_cautions_are_visible():
    provisional = service(authority="AI_PROVISIONAL").get_strategy_state("CN", "605108")
    legacy = service(authority="LEGACY_UNVERIFIED").get_strategy_state("CN", "605108")
    assert "AI初步研究" in provisional["thesis"]["caution"]
    assert provisional["thesis"]["strategy_role"] == "EXPLANATORY_ONLY"
    assert "历史核心逻辑" in legacy["thesis"]["caution"]


def test_human_rejected_blocks_positive_thesis_use():
    state = service(authority="HUMAN_REJECTED").get_strategy_state("CN", "605108")
    assert state["primary_action"]["status"] == "THESIS_REVIEW"


def test_small_peer_and_extreme_fair_value_are_capped_weak():
    reliable = ValueStrategyStateService.valuation_reliability(zones(8))
    weak = ValueStrategyStateService.valuation_reliability(zones(3, current=10, fair=100))
    assert reliable["status"] == "RELIABLE"
    assert weak["status"] == "WEAK"
    assert "EXTREME_FAIR_VALUE" in weak["flags"]


def test_two_peer_methods_can_improve_one_level_but_extreme_cap_applies():
    value = zones(3, current=10, fair=100)
    value["valuation"]["methods"].append({"name": "PB", "status": "READY", "peer_count": 3})
    result = ValueStrategyStateService.valuation_reliability(value)
    assert result["status"] == "WEAK"


def test_support_freshness_and_stale_support_gate():
    state = service(last_bar="2026-08-25").get_strategy_state("CN", "605108")
    assert state["freshness"]["price_structure"]["status"] == "EXPIRED"
    assert state["price_attention"]["effective_status"] == "WATCH"
    assert any("支撑数据已偏旧" in item for item in state["price_attention"]["cautions"])


def test_pool_and_quote_dates_remain_separate():
    state = service().get_strategy_state("CN", "605108")
    assert state["freshness"]["market_price_as_of"] == "2026-09-01"
    assert state["freshness"]["low_value_as_of"] == "2026-08-31"
    assert "日内行情已更新" in state["freshness"]["notice"]


def test_projection_does_not_mutate_upstream_outputs():
    original_zones = zones(3, current=10, fair=100)
    zone_source = Result(original_zones)
    instance = service(peer_count=3, current=10, fair=100)
    instance.price_zone_service = zone_source
    instance.get_strategy_state("CN", "605108")
    assert zone_source.value == original_zones


def test_strategy_state_api_returns_json(monkeypatch):
    instance = service()
    monkeypatch.setattr(value_strategy_routes, "get_value_strategy_state_service", lambda: instance)
    app = FastAPI()
    register_value_strategy_routes(app, require_auth=lambda: True)
    response = TestClient(app).get("/api/value/companies/605108/strategy-state?market=CN")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["eligibility"]["status"] == "IN_VALUE_SCOPE"

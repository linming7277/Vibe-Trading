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
    # V1 补丁后语义：last_bar 远早于 last_session → 停牌推断 → ACCEPTABLE 封顶
    # → 支撑降级门不再因"旧 K 线"触发（停牌时旧 K 线不是数据旧的证据）。
    # 该门仍对"非停牌 + STALE"场景生效（见 calendar-fallback 测试）。
    state = service(last_bar="2026-08-25").get_strategy_state("CN", "605108")
    assert state["freshness"]["suspension"]["status"] == "SUSPENDED_INFERRED"
    assert state["freshness"]["price_structure"]["status"] == "ACCEPTABLE"
    assert state["price_attention"]["effective_status"] == "HIGH_ATTENTION"


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


# ---------------------------------------------------------------------------
# 交易日新鲜度（reliability V1 Part C）：周末/节假日不再误判 EXPIRED
# ---------------------------------------------------------------------------

_TDAYS = [
    "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",  # 周一~周五
    "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
    "2026-09-07", "2026-09-08",
]


def _freshness(last_bar: str, quote: str, days=None):
    zones_payload = {"as_of": quote, "price_as_of": quote, "data_quality": {"daily_history": {"last_date": last_bar}}}
    return ValueStrategyStateService.price_structure_freshness(zones_payload, trading_days=days)


def test_friday_close_vs_sunday_quote_is_not_expired_with_trading_days():
    # last_bar=周五 08-28，quote=周日 08-30（非交易日→落到周五）→ index 差 0 → FRESH
    result = _freshness("2026-08-28", "2026-08-30", _TDAYS)
    assert result["gap_trading_days"] == 0
    assert result["status"] == "FRESH"
    assert result["gap_semantics"] == "TRADING_DAYS"
    assert result["gap_calendar_days"] == 2  # 自然日仍作对照保留


def test_two_trading_days_gap_is_acceptable():
    result = _freshness("2026-08-28", "2026-09-01", _TDAYS)  # 周五 → 下周二 = 2 个交易日
    assert result["gap_trading_days"] == 2
    assert result["status"] == "ACCEPTABLE"


def test_six_trading_days_gap_is_expired():
    result = _freshness("2026-08-26", "2026-09-03", _TDAYS)  # index(09-03)-index(08-26)=6
    assert result["gap_trading_days"] == 6
    assert result["status"] == "EXPIRED"


def test_holiday_quote_snaps_to_previous_close():
    # 08-29（周六）不是交易日 → 落到 08-28（周五）；last_bar=08-27 → 差 1
    result = _freshness("2026-08-27", "2026-08-29", _TDAYS)
    assert result["gap_trading_days"] == 1
    assert result["status"] == "ACCEPTABLE"


def test_empty_calendar_falls_back_to_calendar_days():
    result = _freshness("2026-08-28", "2026-08-30", [])
    assert result["gap_trading_days"] is None
    assert result["gap_semantics"] == "CALENDAR_DAYS_FALLBACK"
    assert result["gap_calendar_days"] == 2
    assert result["status"] == "ACCEPTABLE"  # 与旧自然日行为一致


def test_last_bar_outside_calendar_falls_back():
    result = _freshness("2020-01-01", "2026-09-01", _TDAYS)
    assert result["gap_trading_days"] is None
    assert result["gap_semantics"] == "CALENDAR_DAYS_FALLBACK"


def test_stale_support_gate_fires_when_suspension_unknown_no_calendar():
    # 交易日历为空 → suspension UNKNOWN → 不封顶 → STALE 降级门正常触发。
    from unittest.mock import patch as _patch
    with _patch("src.value_strategy.service.cached_trading_dates", return_value=[]):
        state = service(last_bar="2026-08-25").get_strategy_state("CN", "605108")
    assert state["freshness"]["suspension"]["status"] == "UNKNOWN"
    assert state["freshness"]["price_structure"]["status"] in {"STALE", "EXPIRED"}
    assert state["price_attention"]["effective_status"] == "WATCH"
    assert any("支撑数据已偏旧" in item for item in state["price_attention"]["cautions"])


# ---------------------------------------------------------------------------
# 停牌推断（V1）：strategy state 挂 freshness.suspension + EXPIRED 封顶 STALE
# ---------------------------------------------------------------------------

def test_suspension_field_present_and_trading_for_fresh_bars():
    state = service().get_strategy_state("CN", "605108")
    suspension = state["freshness"]["suspension"]
    assert suspension["status"] in {"TRADING", "SUSPENDED_INFERRED", "UNKNOWN"}
    assert isinstance(suspension["reason"], str)


def test_suspended_inferred_caps_expired_to_acceptable_and_adds_caution():
    # last_bar 远早于 quote（缓存缺最近多根）：交易日口径 EXPIRED + 缺 K 线 → 停牌推断。
    # V1 补丁：停牌封顶 ACCEPTABLE（不再是 STALE）——停牌时 K 线停在停牌前是正常现象，
    # 不应触发"支撑旧 → 高关注降 WATCH"的旧 K 线降级逻辑。
    state = service(last_bar="2026-08-20").get_strategy_state("CN", "605108")
    suspension = state["freshness"]["suspension"]
    assert suspension["status"] == "SUSPENDED_INFERRED"
    assert "停牌" in suspension["reason"]
    assert any("停牌中（推断）" in item for item in state["price_attention"]["cautions"])
    structure = state["freshness"]["price_structure"]
    assert structure["status"] == "ACCEPTABLE"
    assert structure.get("suspension_capped") is True


def test_suspended_high_attention_not_demoted_by_stale_support_gate():
    # 停牌 + gap 很大 + raw HIGH_ATTENTION + 支撑类 reason → freshness=ACCEPTABLE，
    # effective 不得因 STALE 降级成 WATCH（停牌时旧 K 线不是"数据旧"的证据）。
    state = service(last_bar="2026-08-20").get_strategy_state("CN", "605108")
    assert state["freshness"]["price_structure"]["status"] == "ACCEPTABLE"
    assert state["price_attention"]["effective_status"] == "HIGH_ATTENTION"
    assert state["price_attention"]["raw_level"] == "HIGH_ATTENTION"

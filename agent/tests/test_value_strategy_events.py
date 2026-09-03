from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import value_strategy_event_routes
from src.api.value_strategy_event_routes import register_value_strategy_event_routes
from src.value_strategy.event_service import ValueStrategyEventService, project_event_state
from src.value_strategy.event_store import ValueStrategyEventRepository


def state(**changes):
    result = {
        "market": "CN", "stock_code": "605108.SH", "stock_name": "同庆楼", "research_as_of": "2026-09-01",
        "eligibility": {"status": "IN_VALUE_SCOPE"}, "priority": {"tier": "B"},
        "primary_action": {"status": "CONTINUE_OBSERVE"},
        "risk": {"overall": "MEDIUM", "trap": "LOW"},
        "thesis": {"status": "FORMING", "authority": "AI_PROVISIONAL"},
        "leader": {"rank": 1, "as_of": "2026-09-01"},
        "price_attention": {"effective_status": "WATCH", "valuation_reliability": {"status": "RELIABLE"}},
        "review_pressure": {"effective_status": "NORMAL"},
        "freshness": {"market_price_as_of": "2026-09-01", "risk_as_of": "2026-09-01"},
        "reasons": ["原因"], "cautions": ["注意"], "formula_version": "phase1-unchanged", "read_only": True,
    }
    for path, value in changes.items():
        current = result
        parts = path.split("__")
        for part in parts[:-1]:
            current = current[part]
        current[parts[-1]] = value
    return result


class MutableStateService:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def get_strategy_state(self, market, code, research_as_of=None):
        self.calls += 1
        result = deepcopy(self.value)
        result["market"], result["stock_code"] = market, code
        if research_as_of:
            result["research_as_of"] = research_as_of
        return result


class Pool:
    def __init__(self, codes=("605108.SH",)): self.codes = codes
    def active(self, market): return [{"stock_code": code} for code in self.codes]


@pytest.fixture
def setup(tmp_path):
    repo = ValueStrategyEventRepository(tmp_path / "research.db")
    source = MutableStateService(state())
    service = ValueStrategyEventService(repository=repo, state_service=source, pool_repository=Pool())
    yield repo, source, service
    repo.close()


def event_types(service, before, after):
    return {item["event_type"] for item in service.detect_events(before, after)}


def test_initial_evaluation_creates_baseline_and_no_event(setup):
    repo, _source, service = setup
    result = service.evaluate_company("CN", "605108")
    assert result["status"] == "BASELINE_CREATED"
    assert result["created_event_ids"] == []
    assert repo.cursor_count() == 1
    assert repo.list_events() == []


def test_repeat_same_state_is_idempotent(setup):
    repo, _source, service = setup
    service.evaluate_company("CN", "605108")
    first = repo.get_cursor("CN", "605108.SH")["state_fingerprint"]
    service.evaluate_company("CN", "605108")
    assert repo.get_cursor("CN", "605108.SH")["state_fingerprint"] == first
    assert repo.list_events() == []


@pytest.mark.parametrize("before,after,expected", [
    ("OUTSIDE_VALUE_SCOPE", "IN_VALUE_SCOPE", "VALUE_SCOPE_ENTERED"),
    ("IN_VALUE_SCOPE", "OUTSIDE_VALUE_SCOPE", "VALUE_SCOPE_EXITED"),
])
def test_scope_transitions(setup, before, after, expected):
    assert expected in event_types(setup[2], state(eligibility__status=before), state(eligibility__status=after))


@pytest.mark.parametrize("before,after,direction,severity", [
    ("A", "B", "DOWNGRADE", "INFO"), ("B", "A", "UPGRADE", "INFO"),
    ("A", "C", "DOWNGRADE", "HIGH"), ("C", "A", "UPGRADE", "INFO"),
])
def test_priority_transitions(setup, before, after, direction, severity):
    event = next(item for item in setup[2].detect_events(state(priority__tier=before), state(priority__tier=after)) if item["event_type"] == "PRIORITY_CHANGED")
    assert (event["direction"], event["severity"]) == (direction, severity)


def test_priority_outside_is_not_event(setup):
    assert "PRIORITY_CHANGED" not in event_types(setup[2], state(priority__tier="NOT_APPLICABLE"), state(priority__tier="A"))


@pytest.mark.parametrize("before,after,direction", [
    ("LOW", "HIGH", "ESCALATED"), ("HIGH", "MEDIUM", "EASED"),
    ("UNKNOWN", "LOW", "DATA_RECOVERED"), ("MEDIUM", "UNKNOWN", "BECAME_UNKNOWN"),
])
def test_risk_transitions(setup, before, after, direction):
    event = next(item for item in setup[2].detect_events(state(risk__overall=before), state(risk__overall=after)) if item["event_type"] == "RISK_CHANGED")
    assert event["direction"] == direction


def test_risk_high_is_high_severity(setup):
    event = next(item for item in setup[2].detect_events(state(risk__overall="MEDIUM"), state(risk__overall="HIGH")) if item["event_type"] == "RISK_CHANGED")
    assert event["severity"] == "HIGH"


def test_value_trap_only_when_currently_in_scope(setup):
    service = setup[2]
    assert "VALUE_TRAP_CHANGED" in event_types(service, state(risk__trap="LOW"), state(risk__trap="HIGH"))
    assert "VALUE_TRAP_CHANGED" not in event_types(service, state(risk__trap="LOW"), state(risk__trap="HIGH", eligibility__status="OUTSIDE_VALUE_SCOPE"))


@pytest.mark.parametrize("field,value,event_type,severity", [
    ("thesis__status", "FALSIFIED", "THESIS_STATUS_CHANGED", "CRITICAL"),
    ("thesis__authority", "HUMAN_REJECTED", "THESIS_AUTHORITY_CHANGED", "CRITICAL"),
])
def test_thesis_critical_transitions(setup, field, value, event_type, severity):
    event = next(item for item in setup[2].detect_events(state(), state(**{field: value})) if item["event_type"] == event_type)
    assert event["severity"] == severity


@pytest.mark.parametrize("before,after,direction,severity", [
    (1, 2, "RANK_MOVED", "INFO"), (2, 3, "OUT_OF_TOP2", "MEDIUM"),
    (3, 2, "REENTERED", "MEDIUM"), (2, 1, "RANK_MOVED", "INFO"),
])
def test_leader_scope_transitions(setup, before, after, direction, severity):
    event = next(item for item in setup[2].detect_events(state(leader__rank=before), state(leader__rank=after)) if item["event_type"] == "LEADER_SCOPE_CHANGED")
    assert (event["direction"], event["severity"]) == (direction, severity)


@pytest.mark.parametrize("before,after,expected", [
    ("RELIABLE", "LIMITED", False), ("WEAK", "INSUFFICIENT", False),
    ("LIMITED", "WEAK", True), ("INSUFFICIENT", "RELIABLE", True),
])
def test_reliability_only_crosses_good_bad_boundary(setup, before, after, expected):
    actual = "VALUATION_RELIABILITY_CHANGED" in event_types(
        setup[2], state(price_attention__valuation_reliability={"status": before}),
        state(price_attention__valuation_reliability={"status": after}),
    )
    assert actual is expected


def test_price_attention_only_in_scope(setup):
    service = setup[2]
    assert "PRICE_ATTENTION_CHANGED" in event_types(service, state(), state(price_attention__effective_status="ATTENTION"))
    assert "PRICE_ATTENTION_CHANGED" not in event_types(service, state(), state(price_attention__effective_status="ATTENTION", eligibility__status="OUTSIDE_VALUE_SCOPE"))


def test_review_critical_is_high_severity(setup):
    event = next(item for item in setup[2].detect_events(state(), state(review_pressure__effective_status="CRITICAL_REVIEW")) if item["event_type"] == "REVIEW_PRESSURE_CHANGED")
    assert event["severity"] == "HIGH"


def test_primary_action_changed(setup):
    assert "PRIMARY_ACTION_CHANGED" in event_types(setup[2], state(), state(primary_action__status="RISK_REVIEW"))


def test_transition_batch_and_event_keys_are_deterministic(setup):
    first = setup[2].detect_events(state(), state(risk__overall="HIGH"), occurred_at="2026-09-01T10:00:00Z")
    second = setup[2].detect_events(state(), state(risk__overall="HIGH"), occurred_at="2026-09-01T11:00:00Z")
    assert [(e["event_key"], e["transition_batch_id"]) for e in first] == [(e["event_key"], e["transition_batch_id"]) for e in second]


def test_transaction_rolls_back_events_and_cursor(tmp_path):
    def fail(index, _event):
        if index == 1:
            raise RuntimeError("injected")
    repo = ValueStrategyEventRepository(tmp_path / "db.sqlite", event_insert_hook=fail)
    source = MutableStateService(state())
    service = ValueStrategyEventService(repository=repo, state_service=source, pool_repository=Pool())
    service.evaluate_company("CN", "605108")
    original = repo.get_cursor("CN", "605108.SH")["state_fingerprint"]
    source.value = state(risk__overall="HIGH", primary_action__status="RISK_REVIEW")
    with pytest.raises(RuntimeError, match="injected"):
        service.evaluate_company("CN", "605108")
    assert repo.get_cursor("CN", "605108.SH")["state_fingerprint"] == original
    assert repo.list_events() == []
    repo.close()


def test_lifecycle_and_idempotency(setup):
    repo, source, service = setup
    service.evaluate_company("CN", "605108")
    source.value = state(risk__overall="HIGH")
    event_id = service.evaluate_company("CN", "605108")["created_event_ids"][0]
    assert repo.transition_lifecycle(event_id, "ACKNOWLEDGED")["status"] == "ACKNOWLEDGED"
    assert repo.transition_lifecycle(event_id, "ACKNOWLEDGED")["status"] == "ACKNOWLEDGED"
    assert repo.transition_lifecycle(event_id, "CLOSED")["status"] == "CLOSED"
    assert repo.transition_lifecycle(event_id, "CLOSED")["status"] == "CLOSED"
    with pytest.raises(ValueError):
        repo.transition_lifecycle(event_id, "ACKNOWLEDGED")


def test_universe_is_active_union_previous_in_scope(setup):
    repo, source, service = setup
    service.evaluate_company("CN", "600460")
    assert service.evaluation_universe() == ["600460.SH", "605108.SH"]


def test_dry_run_writes_nothing(setup):
    repo, _source, service = setup
    result = service.evaluate_universe(dry_run=True)
    assert result["would_create_cursors"] == 1
    assert result["would_create_events"] == 0
    assert repo.cursor_count() == 0


def test_projection_ignores_summary_and_dates():
    before, after = state(), state(research_as_of="2026-09-02")
    before["summary"], after["summary"] = "甲", "乙"
    assert project_event_state(before) == project_event_state(after)


def test_event_api_filters_and_lifecycle(setup, monkeypatch):
    repo, source, service = setup
    service.evaluate_company("CN", "605108")
    source.value = state(risk__overall="HIGH")
    event_id = service.evaluate_company("CN", "605108")["created_event_ids"][0]
    monkeypatch.setattr(value_strategy_event_routes, "get_value_strategy_event_service", lambda: service)
    app = FastAPI()
    register_value_strategy_event_routes(app, lambda: True)
    client = TestClient(app)
    assert client.get("/api/value/strategy-events?market=CN&stock_code=605108").json()["count"] >= 1
    batches = client.get("/api/value/strategy-event-batches?stock_code=605108&limit=5")
    assert batches.headers["content-type"].startswith("application/json")
    assert batches.json()["count"] == 1
    assert client.get(f"/api/value/strategy-events/{event_id}").headers["content-type"].startswith("application/json")
    assert client.post(f"/api/value/strategy-events/{event_id}/acknowledge").json()["status"] == "ACKNOWLEDGED"
    assert client.post(f"/api/value/strategy-events/{event_id}/close").json()["status"] == "CLOSED"
    assert client.post(f"/api/value/strategy-events/{event_id}/acknowledge").status_code == 409
    assert client.get("/api/value/strategy-events/missing").status_code == 404
    assert client.delete(f"/api/value/strategy-events/{event_id}").status_code == 405


def test_schema_has_only_requested_event_tables(tmp_path):
    repo = ValueStrategyEventRepository(tmp_path / "db.sqlite")
    with sqlite3.connect(repo.db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'value_strategy_state_%'")}
    assert names == {"value_strategy_state_cursors", "value_strategy_state_events"}
    repo.close()

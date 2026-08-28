from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import low_value_leader_pool_routes
from src.api.low_value_leader_pool_routes import register_low_value_leader_pool_routes
from src.low_value_leader_pool.service import LowValueLeaderPoolService
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository


def member(code: str = "000001.SZ", *, industry: str = "I001", score: float = 80.0, rank: int = 1) -> dict:
    return {
        "stock_code": code, "stock_name": "测试公司", "level3_code": industry,
        "level3_name": f"行业{industry}", "leader_score": score, "leader_rank": rank,
        "lifecycle_status": "ACTIVE",
    }


def pool(members: list[dict], *, pool_id: str = "pool-current", as_of: str = "2026-08-21") -> dict:
    return {"id": pool_id, "as_of": as_of, "members": members}


def zones(status: str) -> dict:
    return {
        "as_of": "2026-08-21", "current_price": 10.0, "formula_version": "value-price-zones-v1.0.0",
        "valuation": {"status": status, "fair_value_low": 12.0, "fair_value_mid": 15.0, "fair_value_high": 20.0},
        "historical_valuation": {"historical_valuation_status": "CHEAP"},
        "support_zones": [{"low": 9.0, "high": 10.0}],
        "data_quality": {"daily_history": {"status": "READY"}, "price": {"status": "READY", "source": "tdx_quote_cache"}},
    }


class FakeLeaderService:
    def __init__(self, current: dict) -> None:
        self.current = current

    def ensure_current_pool(self) -> dict:
        return self.current


class FakePriceZoneService:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.calls: list[tuple[str, str, str | None]] = []

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        self.calls.append((market, stock_code, as_of))
        return zones(self.values[stock_code])


class FakeEntryResearchService:
    def get_entry_research(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        return {"entry_level": "WATCH"}


def build_service(tmp_path, current: dict, valuations: dict[str, str]):
    repository = LowValueLeaderPoolRepository(tmp_path / "focus-pool.db")
    leaders = FakeLeaderService(current)
    prices = FakePriceZoneService(valuations)
    service = LowValueLeaderPoolService(
        repository=repository, leader_service=leaders, price_zone_service=prices,
        entry_research_service=FakeEntryResearchService(),
    )
    return service, repository, leaders, prices


def test_low_value_l3_leaders_enter_and_are_deduplicated(tmp_path):
    current = pool([member("000001.SZ", industry="I001", score=80), member("000001.SZ", industry="I002", score=90)])
    original = deepcopy(current)
    service, repository, _leaders, prices = build_service(tmp_path, current, {"000001.SZ": "DEEPLY_UNDERVALUED"})

    result = service.refresh_low_value_leader_pool()

    active = repository.active()
    assert result["entered"] == 1
    assert result["active"] == 1
    assert len(active) == 1
    assert active[0]["valuation_status"] == "DEEPLY_UNDERVALUED"
    assert active[0]["enter_reason"] == "DEEPLY_UNDERVALUED"
    assert active[0]["industry_code"] == "I002"
    assert active[0]["entry_level"] == "WATCH"
    assert len(active[0]["metadata"]["leader_memberships"]) == 2
    assert prices.calls == [("CN", "000001.SZ", "2026-08-21")]
    assert current == original  # the service never mutates the L3 leader pool


def test_requested_as_of_must_match_materialized_l3_pool(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, _leaders, _prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    try:
        with pytest.raises(ValueError, match="L3_AS_OF_MISMATCH"):
            service.refresh_low_value_leader_pool(as_of="2026-08-24")
        assert repository.active() == []
    finally:
        repository.close()


def test_requested_as_of_uses_its_own_materialized_pool_when_a_newer_pool_exists(tmp_path):
    older = pool([member("000001.SZ")], pool_id="pool-21", as_of="2026-08-21")
    newer = pool([member("000002.SZ")], pool_id="pool-22", as_of="2026-08-22")

    class Store:
        def pool_for_as_of(self, as_of: str):
            return older if as_of == "2026-08-21" else None

    class Leaders(FakeLeaderService):
        def __init__(self):
            super().__init__(newer)
            self.store = Store()

        def get_pool(self, pool_id: str):
            return older if pool_id == "pool-21" else None

    repository = LowValueLeaderPoolRepository(tmp_path / "focus-pool.db")
    prices = FakePriceZoneService({"000001.SZ": "UNDERVALUED", "000002.SZ": "UNDERVALUED"})
    service = LowValueLeaderPoolService(
        repository=repository, leader_service=Leaders(), price_zone_service=prices,
        entry_research_service=FakeEntryResearchService(),
    )
    try:
        result = service.refresh_low_value_leader_pool(as_of="2026-08-21")
        assert result["source_as_of"] == "2026-08-21"
        assert [item["stock_code"] for item in repository.active()] == ["000001.SZ"]
    finally:
        repository.close()


def test_refresh_is_idempotent_then_fair_value_removes_and_preserves_history(tmp_path):
    current = pool([member()])
    service, repository, _leaders, prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})

    first = service.refresh_low_value_leader_pool()
    second = service.refresh_low_value_leader_pool()
    assert first["entered"] == 1
    assert second["entered"] == 0
    assert second["stayed"] == 1
    assert len(repository.history(stock_code="000001.SZ")) == 1

    prices.values["000001.SZ"] = "FAIR"
    removed = service.refresh_low_value_leader_pool()
    history = repository.history(stock_code="000001.SZ")
    assert removed["removed"] == 1
    assert repository.active() == []
    assert len(history) == 1
    assert history[0]["pool_status"] == "REMOVED"
    assert history[0]["remove_reason"] == "VALUATION_RECOVERED"
    assert history[0]["removed_at"]


def test_new_as_of_archives_prior_active_projection_before_advancing(tmp_path):
    current = pool([member()], pool_id="pool-21", as_of="2026-08-21")
    service, repository, leaders, _prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([member()], pool_id="pool-24", as_of="2026-08-24")
    service.refresh_low_value_leader_pool()

    assert repository.active()[0]["source_as_of"] == "2026-08-24"
    archived = repository._conn.execute(  # immutable audit copy, intentionally not a public API
        """SELECT source_as_of, source_pool_id FROM company_low_value_leader_pool_snapshots
           WHERE stock_code='000001.SZ'"""
    ).fetchall()
    assert [(row["source_as_of"], row["source_pool_id"]) for row in archived] == [("2026-08-21", "pool-21")]


def test_new_as_of_archives_prior_projection_before_removing_member(tmp_path):
    current = pool([member()], pool_id="pool-21", as_of="2026-08-21")
    service, repository, leaders, prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([member()], pool_id="pool-24", as_of="2026-08-24")
    prices.values["000001.SZ"] = "FAIR"
    service.refresh_low_value_leader_pool()

    archived = repository._conn.execute(
        "SELECT source_as_of, pool_status FROM company_low_value_leader_pool_snapshots WHERE stock_code='000001.SZ'"
    ).fetchall()
    assert [(row["source_as_of"], row["pool_status"]) for row in archived] == [("2026-08-21", "ACTIVE")]
    assert repository.history(stock_code="000001.SZ")[0]["source_as_of"] == "2026-08-24"


def test_leaving_l3_pool_removes_active_record(tmp_path):
    current = pool([member()])
    service, repository, leaders, _prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([], pool_id="pool-next", as_of="2026-08-22")
    result = service.refresh_low_value_leader_pool()
    history = repository.history(stock_code="000001.SZ")

    assert result["removed"] == 1
    assert repository.active() == []
    assert history[0]["remove_reason"] == "NO_LONGER_LEADER"


def test_low_value_events_record_enter_exit_and_stay_idempotent(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, leaders, prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})

    entered = service.refresh_low_value_leader_pool()
    stayed = service.refresh_low_value_leader_pool()
    first_events = repository.events()
    assert entered["event_entered"] == 1
    assert stayed["event_entered"] == 0
    assert len(first_events) == 1
    assert first_events[0]["event_type"] == "ENTER_LOW_VALUE"
    assert first_events[0]["before_status"] is None
    assert first_events[0]["after_status"] == "UNDERVALUED"

    leaders.current = pool([member()], as_of="2026-08-22")
    prices.values["000001.SZ"] = "FAIR"
    exited = service.refresh_low_value_leader_pool()
    assert exited["event_exited"] == 1
    exit_event = repository.events(event_date="2026-08-22")[0]
    assert exit_event["event_type"] == "EXIT_LOW_VALUE"
    assert exit_event["before_status"] == "UNDERVALUED"
    assert exit_event["after_status"] == "FAIR"
    assert exit_event["current_price"] == 10.0
    assert exit_event["fair_value_mid"] == 15.0

    leaders.current = pool([member()], as_of="2026-08-23")
    prices.values["000001.SZ"] = "UNDERVALUED"
    reentered = service.refresh_low_value_leader_pool()
    enter_event = repository.events(event_date="2026-08-23")[0]
    assert reentered["event_entered"] == 1
    assert enter_event["before_status"] == "FAIR"
    assert enter_event["after_status"] == "UNDERVALUED"
    assert service.refresh_low_value_leader_pool()["event_entered"] == 0
    assert len(repository.events()) == 3


def test_current_event_summary_uses_current_pool_date_when_there_are_no_changes(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, leaders, prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([member()], pool_id="pool-22", as_of="2026-08-22")
    prices.values["000001.SZ"] = "UNDERVALUED"
    refresh = service.refresh_low_value_leader_pool()
    summary = service.low_value_leader_events()

    assert refresh["event_entered"] == 0 and refresh["event_exited"] == 0
    assert summary == {"event_date": "2026-08-22", "entered": 0, "exited": 0, "items": [], "total": 0}


def test_completed_refresh_marker_accepts_zero_events(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, _leaders, prices = build_service(tmp_path, current, {"000001.SZ": "FAIR"})
    result = service.refresh_low_value_leader_pool()
    marker = repository.refresh_status(source_as_of="2026-08-21", source_pool_id="pool-current")
    assert result["status"] == "COMPLETED"
    assert result["event_entered"] == 0 and result["event_exited"] == 0
    assert marker and marker["status"] == "COMPLETED" and marker["active_count"] == 0
    assert prices.calls == [("CN", "000001.SZ", "2026-08-21")]


def test_stale_same_day_price_keeps_previous_pool_projection(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, leaders, prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([member()], pool_id="pool-22", as_of="2026-08-22")
    original = prices.get_price_zones

    def stale_price(*args, **kwargs):
        result = original(*args, **kwargs)
        result["data_quality"]["price"] = {
            "status": "STALE",
            "message": "未找到与研究日期 2026-08-22 一致的行情，未计算正式估值。",
        }
        return result

    prices.get_price_zones = stale_price  # type: ignore[method-assign]
    refreshed = service.refresh_low_value_leader_pool()

    assert refreshed["status"] == "PARTIAL"
    assert refreshed["removed"] == 0
    assert len(refreshed["errors"]) == 1
    assert repository.active()[0]["source_as_of"] == "2026-08-21"


def test_leaving_l3_pool_generates_exit_event(tmp_path):
    current = pool([member()], as_of="2026-08-21")
    service, repository, leaders, _prices = build_service(tmp_path, current, {"000001.SZ": "DEEPLY_UNDERVALUED"})
    service.refresh_low_value_leader_pool()

    leaders.current = pool([], pool_id="pool-next", as_of="2026-08-22")
    result = service.refresh_low_value_leader_pool()
    event = repository.events(event_date="2026-08-22")[0]
    assert result["event_exited"] == 1
    assert event["event_type"] == "EXIT_LOW_VALUE"
    assert event["after_status"] == "NO_LONGER_LEADER"


def test_low_value_leader_pool_api_is_read_only_until_explicit_refresh(tmp_path, monkeypatch):
    current = pool([member()])
    service, _repository, _leaders, _prices = build_service(tmp_path, current, {"000001.SZ": "UNDERVALUED"})
    monkeypatch.setattr(low_value_leader_pool_routes, "get_low_value_leader_pool_service", lambda: service)
    app = FastAPI()
    register_low_value_leader_pool_routes(app, lambda: True)
    client = TestClient(app)

    assert client.get("/api/value/low-value-leaders").json()["total"] == 0
    refresh = client.post("/api/value/low-value-leaders/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["entered"] == 1
    active = client.get("/api/value/low-value-leaders")
    history = client.get("/api/value/low-value-leaders/history", params={"stock_code": "000001.SZ"})
    events = client.get("/api/value/low-value-leader-events")
    assert active.status_code == 200 and active.json()["total"] == 1
    assert history.status_code == 200 and history.json()["total"] == 1
    assert events.status_code == 200 and events.json()["entered"] == 1
    assert events.json()["items"][0]["event_type"] == "ENTER_LOW_VALUE"

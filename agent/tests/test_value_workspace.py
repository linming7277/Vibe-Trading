from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.strategy_engines.store import StrategyEngineStore
from src.value_workspace.service import ValueWorkspaceService
from src.value_workspace.store import ValueWorkspaceStore


class FakeTdx:
    def sectors(self, limit: int = 500):
        return {
            "as_of": "2026-08-12T16:00:00+08:00",
            "items": [
                {"code": "T001", "name": "机器人", "category": "概念", "breadth_pct": 70, "change_pct": 2.4, "amount": 200},
                {"code": "T002", "name": "银行", "category": "行业", "breadth_pct": 45, "change_pct": -.3, "amount": 100},
            ],
        }

    def sector_detail(self, code: str):
        members = {
            "T001": [
                {"code": "000001.SZ", "name": "甲公司", "market_cap_100m": 900, "net_profit_10k": 50, "eps": 1.2, "pe_ttm": 18, "pb_mrq": 2},
                {"code": "000002.SZ", "name": "乙公司", "market_cap_100m": 300, "net_profit_10k": 25, "eps": .7, "pe_ttm": 25, "pb_mrq": 3},
            ],
            "T002": [{"code": "600000.SH", "name": "丙银行", "market_cap_100m": 800, "net_profit_10k": 80, "eps": 1, "pe_ttm": 6, "pb_mrq": .6}],
        }
        return {"members": members.get(code, []), "as_of": "2026-08-12"}

    def security_overview(self, symbol: str, **_kwargs):
        return {
            "code": symbol, "name": "甲公司", "quote": {"price": 10},
            "fundamental": {"pe_ttm": 18, "pb_mrq": 2}, "cache": {"stale": False},
            "as_of": "2026-08-12T16:00:00+08:00",
        }


class FakeValueLine:
    def sectors(self, as_of: str | None = None):
        return {
            "as_of": as_of or "2026-08-13",
            "items": [
                {"sector_code": "881001.SH", "sector_name": "Track A", "score": 78.0, "coverage": 1.0, "status": "ready", "data_as_of": "2026-08-13", "component_scores": {"macro_fit": 90, "policy_fit": 90, "momentum": 80, "risk_quality": 80, "earnings_momentum": 70, "valuation": 60, "capital_flow_proxy": 75}},
                {"sector_code": "881002.SH", "sector_name": "Track B", "score": 60.0, "coverage": 1.0, "status": "ready", "data_as_of": "2026-08-13", "component_scores": {"macro_fit": 40, "policy_fit": 40, "momentum": 50, "risk_quality": 50, "earnings_momentum": 55, "valuation": 65, "capital_flow_proxy": 50}},
            ],
        }

    def leaders(self, track_id: str, as_of: str):
        return {"items": [{"symbol": f"{track_id[-3:]}001.SZ", "name": "Leader", "rank": 1, "score": 81.0, "coverage": 1.0, "component_scores": {"industry_position": 90, "profitability": 80}}]}

    def macro(self, as_of: str | None = None):
        return {"as_of": as_of, "status": "ready", "regime": "Recovery", "axes": {"growth": 70}}


@pytest.fixture()
def stores(tmp_path: Path):
    value = ValueWorkspaceStore(tmp_path / "research.db")
    engine = StrategyEngineStore(value.db_path)
    try:
        yield value, engine
    finally:
        engine.close()
        value.close()


def test_profile_weights_are_normalized_and_versioned(stores):
    value, _ = stores
    profile = value.save_profile(name="偏盈利", mode="composite", weights={"earnings_climate": 3, "liquidity": 1})
    assert profile["model_weights"] == {"earnings_climate": .75, "liquidity": .25}
    updated = value.save_profile(profile_id=profile["id"], name="偏盈利 v2", mode="single", weights={"earnings_climate": 9})
    assert updated["version"] == 2
    assert updated["model_weights"] == {"earnings_climate": 1.0}


def test_value_v2_standard_profile_is_default_and_v1_profiles_are_legacy(stores):
    value, _ = stores
    default = value.get_profile(None)
    assert default and default["id"] == "profile_value_line_v2"
    assert default["name"] == "价值线 V2 标准方案"
    legacy = value.get_profile("profile_balanced")
    assert legacy and legacy["is_default"] is False and "Legacy" in legacy["name"]


def test_track_leaders_are_restricted_to_selected_tdx_board(stores, monkeypatch):
    value, engine = stores
    profile = value.get_profile("profile_balanced")
    run, _ = engine.create_or_get_run(
        idempotency_key="value-test", strategy_line="value", market="CN", as_of="2026-08-12",
        symbols=[], formula_version="test", profile_id=profile["id"], profile_version=profile["version"],
    )
    monkeypatch.setattr("src.value_workspace.service.get_tdx_service", lambda: FakeTdx())
    service = ValueWorkspaceService(value)
    service.materialize_run(run["id"], profile)
    robot_symbols = {item["symbol"] for item in value.list_leaders(run["id"], "T001")}
    bank_symbols = {item["symbol"] for item in value.list_leaders(run["id"], "T002")}
    assert robot_symbols == {"000001.SZ", "000002.SZ"}
    assert bank_symbols == {"600000.SH"}
    assert robot_symbols.isdisjoint(bank_symbols)


def test_v2_snapshot_bridge_uses_profile_and_keeps_sector_leaders_together(stores, monkeypatch):
    value, engine = stores
    monkeypatch.setattr("src.strategy_engines.value_line.get_value_line_service", lambda: FakeValueLine())
    service = ValueWorkspaceService(value)
    first = service.materialize_v2_snapshot("profile_policy")
    second = service.materialize_v2_snapshot("profile_policy")
    refreshed = service.materialize_v2_snapshot("profile_policy", force_refresh=True)
    current = service.materialize_v2_snapshot("profile_policy")
    assert first["run"]["id"] == second["run"]["id"]
    assert refreshed["run"]["id"] != first["run"]["id"]
    assert current["run"]["id"] == refreshed["run"]["id"]
    assert first["run"]["status"] == "completed"
    assert [item["track_id"] for item in first["tracks"]] == ["881001.SH", "881002.SH"]
    assert value.list_leaders(first["run"]["id"], "881001.SH")[0]["symbol"] == ".SH001.SZ"
    assert engine.get_run(first["run"]["id"])["profile_id"] == "profile_policy"


def test_v2_standard_profile_keeps_authoritative_sector_score_and_coverage(stores, monkeypatch):
    value, _ = stores
    monkeypatch.setattr("src.strategy_engines.value_line.get_value_line_service", lambda: FakeValueLine())
    snapshot = ValueWorkspaceService(value).materialize_v2_snapshot("profile_value_line_v2")
    assert snapshot["sectors"][0]["score"] == 78.0
    assert snapshot["tracks"][0]["base_score"] == 78.0
    assert snapshot["tracks"][0]["coverage"] == 1.0
    assert snapshot["tracks"][0]["component_scores"]["policy_fit"] == 90


def test_v2_custom_profile_does_not_hide_missing_dimension_coverage(stores, monkeypatch):
    value, _ = stores
    fake = FakeValueLine()
    rows = fake.sectors()["items"]
    rows[0]["coverage"] = .85
    rows[0]["component_scores"]["policy_fit"] = None
    monkeypatch.setattr(fake, "sectors", lambda as_of=None: {"as_of": as_of or "2026-08-13", "items": rows})
    monkeypatch.setattr("src.strategy_engines.value_line.get_value_line_service", lambda: fake)
    snapshot = ValueWorkspaceService(value).materialize_v2_snapshot("profile_policy")
    track = next(item for item in snapshot["tracks"] if item["track_id"] == "881001.SH")
    assert track["coverage"] <= .85
    assert any(flag.startswith("policy_cycle:coverage=") for flag in track["quality_flags"])


def test_monitor_event_is_idempotent_and_has_independent_deliveries(stores, monkeypatch):
    value, engine = stores
    profile = value.get_profile("profile_balanced")
    run, _ = engine.create_or_get_run(
        idempotency_key="monitor-test", strategy_line="value", market="CN", as_of="2026-08-12",
        symbols=[], formula_version="test", profile_id=profile["id"], profile_version=profile["version"],
    )
    value.replace_tracks(run["id"], profile["id"], [{
        "track_id": "T001", "track_name": "机器人", "category": "概念", "base_score": 80,
        "coverage": 1, "rank": 1, "component_scores": {}, "source_status": "live", "data_as_of": "2026-08-12",
        "leaders": [{"symbol": "000001.SZ", "name": "甲公司", "leader_type": "综合龙头", "base_score": 80, "coverage": 1, "rank": 1, "component_scores": {}}],
    }])
    batch, _ = value.create_batch(run_id=run["id"], profile_id=profile["id"], track_id="T001", companies=[{"symbol": "000001.SZ", "name": "甲公司"}], template_version="v1", concurrency=3)
    job = batch["jobs"][0]
    value.update_job(job["id"], status="partial", stage="review")
    value.create_monitor(job_id=job["id"], conditions={"entry_low": 9, "entry_high": 11}, channels=["in_app", "feishu", "weixin"])
    monkeypatch.setattr("src.value_workspace.service.get_tdx_service", lambda: FakeTdx())
    service = ValueWorkspaceService(value)
    service.evaluate_monitors()
    service.evaluate_monitors()
    events = value.list_events()
    assert len(events) == 1
    assert {item["channel"] for item in events[0]["deliveries"]} == {"in_app", "feishu", "weixin"}
    assert next(item for item in events[0]["deliveries"] if item["channel"] == "in_app")["status"] == "sent"


def test_failed_research_jobs_can_be_retried_without_reopening_completed_jobs(stores):
    value, engine = stores
    profile = value.get_profile("profile_balanced")
    run, _ = engine.create_or_get_run(
        idempotency_key="retry-test", strategy_line="value", market="CN", as_of="2026-08-12",
        symbols=[], formula_version="test", profile_id=profile["id"], profile_version=profile["version"],
    )
    value.replace_tracks(run["id"], profile["id"], [{
        "track_id": "T001", "track_name": "Track", "category": "Industry", "base_score": 80,
        "coverage": 1, "rank": 1, "component_scores": {}, "source_status": "live", "data_as_of": "2026-08-12",
        "leaders": [
            {"symbol": "000001.SZ", "name": "First", "leader_type": "综合龙头", "base_score": 80, "coverage": 1, "rank": 1, "component_scores": {}},
            {"symbol": "000002.SZ", "name": "Second", "leader_type": "综合龙头", "base_score": 70, "coverage": 1, "rank": 2, "component_scores": {}},
        ],
    }])
    batch, _ = value.create_batch(run_id=run["id"], profile_id=profile["id"], track_id="T001", companies=[
        {"symbol": "000001.SZ", "name": "First"}, {"symbol": "000002.SZ", "name": "Second"},
    ], template_version="v1", concurrency=2)
    first, second = batch["jobs"]
    value.update_job(first["id"], status="partial", stage="review")
    value.update_job(second["id"], status="failed", stage="failed", message="source unavailable")
    value.update_batch(batch["id"], status="partial", completed=1, failed=1)
    retried = ValueWorkspaceService(value).retry_failed_jobs(batch["id"])
    statuses = {job["symbol"]: job["status"] for job in retried["jobs"]}
    assert retried["status"] == "queued"
    assert retried["failed"] == 0
    assert statuses == {"000001.SZ": "partial", "000002.SZ": "queued"}


def _test_incremental_universe(value: ValueWorkspaceStore, engine: StrategyEngineStore) -> tuple[dict, dict]:
    profile = value.get_profile("profile_value_line_v2")
    assert profile
    run, _ = engine.create_or_get_run(
        idempotency_key="incremental-progress-test", strategy_line="value", market="CN", as_of="2026-08-14",
        symbols=[], formula_version="test", profile_id=profile["id"], profile_version=profile["version"],
    )
    universe, _ = value.create_universe(
        idempotency_key="incremental-progress-test", run_id=run["id"], profile_id="profile_value_line_v2",
        candidate_limit=5, leader_limit=5, data_as_of="2026-08-14", formula_version="test", members=[
            {"track_id": "T001", "track_name": "Track", "track_rank": 1, "symbol": "000001.SZ", "name": "First", "leader_rank": 1, "leader_type": "leader", "leader_score": 80, "leader_coverage": 1, "inclusion_reason": "test"},
            {"track_id": "T001", "track_name": "Track", "track_rank": 1, "symbol": "000002.SZ", "name": "Second", "leader_rank": 2, "leader_type": "leader", "leader_score": 70, "leader_coverage": 1, "inclusion_reason": "test"},
        ],
    )
    operation, _ = value.create_incremental_run(
        universe_id=universe["id"], run_kind="bootstrap", trigger_kind="test", as_of="2026-08-14",
        companies=[{"symbol": "000001.SZ", "name": "First", "primary_track_id": "T001"}, {"symbol": "000002.SZ", "name": "Second", "primary_track_id": "T001"}],
    )
    return universe, operation


def test_incremental_progress_is_persisted_while_other_jobs_are_queued(stores):
    value, engine = stores
    _, operation = _test_incremental_universe(value, engine)
    value.update_incremental_job(operation["jobs"][0]["id"], status="partial", stage="review")
    live = value.refresh_incremental_progress(operation["id"])
    assert live["completed"] == 1
    assert live["failed"] == 0
    assert live["coverage"] == .5
    assert [job["status"] for job in live["jobs"]] == ["partial", "queued"]


def test_company_snapshot_timeout_fails_only_that_job_and_finishes_operation(stores, monkeypatch):
    value, engine = stores
    _, operation = _test_incremental_universe(value, engine)

    class _Closable:
        def close(self):
            pass

    class FakeFinance:
        def __init__(self):
            self.client, self.store = _Closable(), _Closable()

        def collect_incremental(self, *_args, **_kwargs):
            return {"status": "ready"}

    service = ValueWorkspaceService(value)
    monkeypatch.setattr("src.value_workspace.service.FinancialHistoryService", FakeFinance)
    monkeypatch.setattr("src.value_workspace.service.COMPANY_SNAPSHOT_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(ValueWorkspaceService, "_collect_company_snapshot", lambda *_args, **_kwargs: time.sleep(.05))
    result = service.run_operation(operation["id"])
    assert result["status"] == "failed"
    assert result["completed"] == 0
    assert result["failed"] == 2
    assert {job["status"] for job in result["jobs"]} == {"failed"}

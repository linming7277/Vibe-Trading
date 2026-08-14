from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.strategy_engines.store import StrategyEngineStore
from src.value_workspace.automation import next_value_run
from src.value_workspace.service import ValueWorkspaceService
from src.value_workspace.store import ValueWorkspaceStore


@pytest.fixture()
def workspace(tmp_path: Path):
    value = ValueWorkspaceStore(tmp_path / "research.db")
    engine = StrategyEngineStore(value.db_path)
    profile = value.get_profile("profile_value_line_v2")
    run, _ = engine.create_or_get_run(
        idempotency_key="continuous-test", strategy_line="value", market="CN", as_of="2026-08-14",
        symbols=[], formula_version="value-leader-v2.0.0", profile_id=profile["id"],
        profile_version=profile["version"],
    )
    tracks = []
    for track_index in range(1, 7):
        leaders = []
        for leader_index in range(1, 7):
            symbol = "000001.SZ" if leader_index == 1 else f"{track_index:03d}{leader_index:03d}.SZ"
            leaders.append({
                "symbol": symbol, "name": f"公司{track_index}-{leader_index}", "leader_type": "综合龙头",
                "base_score": 90 - leader_index, "coverage": 1, "rank": leader_index,
                "component_scores": {"industry_position": 90 - leader_index},
            })
        tracks.append({
            "track_id": f"881{track_index:03d}.SH", "track_name": f"赛道{track_index}", "category": "行业",
            "base_score": 90 - track_index, "coverage": 1, "rank": track_index,
            "component_scores": {}, "source_status": "ready", "data_as_of": "2026-08-14", "leaders": leaders,
        })
    value.replace_tracks(run["id"], profile["id"], tracks)
    try:
        yield value, engine, run
    finally:
        engine.close()
        value.close()


def test_universe_freezes_top_tracks_top_five_and_keeps_all_memberships(workspace):
    value, _, run = workspace
    service = ValueWorkspaceService(value)
    universe, created = service.create_research_universe(run["id"], 5)
    repeated, repeated_created = service.create_research_universe(run["id"], 5)
    assert created is True and repeated_created is False
    assert repeated["id"] == universe["id"]
    assert universe["track_count"] == 5
    assert universe["membership_count"] == 25
    assert universe["company_count"] == 21
    shared = next(item for item in universe["companies"] if item["symbol"] == "000001.SZ")
    assert len(shared["memberships"]) == 5
    assert max(item["leader_rank"] for item in universe["members"]) == 5


def test_snapshot_evidence_and_signal_events_are_idempotent(workspace):
    value, _, run = workspace
    service = ValueWorkspaceService(value)
    universe, _ = service.create_research_universe(run["id"], 5)
    evidence = {
        "symbol": "000001.SZ", "evidence_type": "quote", "source": "tdx", "source_id": "quote:000001.SZ:2026-08-14",
        "data_as_of": "2026-08-14", "content_hash": "quote-hash", "payload": {"price": 10},
    }
    first_evidence, created = value.upsert_evidence(evidence)
    repeated_evidence, repeated_created = value.upsert_evidence(evidence)
    assert created is True and repeated_created is False and first_evidence["id"] == repeated_evidence["id"]
    snapshot_payload = {
        "quote": {"price": 10}, "fundamental": {"pe_ttm": 8, "pb_mrq": 1, "dividend_yield": 4},
        "financial_latest": {"revenue_yoy": -30, "net_profit": 10, "operating_cash_flow": -1},
        "financial_previous": {}, "cache": {"stale": False},
    }
    snapshot, snapshot_created = value.save_snapshot({
        "universe_id": universe["id"], "symbol": "000001.SZ", "data_as_of": "2026-08-14",
        "status": "ready", "completeness": 1, "source_hash": "snapshot-hash", "payload": snapshot_payload,
        "diff": {}, "missing_fields": [], "sources": ["tdx"], "evidence_ids": [first_evidence["id"]],
    })
    repeated_snapshot, repeated_snapshot_created = value.save_snapshot({
        "universe_id": universe["id"], "symbol": "000001.SZ", "data_as_of": "2026-08-14",
        "status": "ready", "completeness": 1, "source_hash": "snapshot-hash", "payload": snapshot_payload,
        "diff": {}, "missing_fields": [], "sources": ["tdx"], "evidence_ids": [first_evidence["id"]],
    })
    assert snapshot_created is True and repeated_snapshot_created is False and snapshot["id"] == repeated_snapshot["id"]
    monitor = service.create_universe_monitor(
        universe_id=universe["id"], symbol="000001.SZ", conditions={"max_pe": 10, "exit_price": 9},
        channels=["in_app", "feishu", "weixin"], position_state="watching",
    )
    service.evaluate_signal_rules(universe_id=universe["id"], as_of="2026-08-14")
    service.evaluate_signal_rules(universe_id=universe["id"], as_of="2026-08-14")
    evaluations = value.list_signal_evaluations(symbol="000001.SZ")
    assert len(evaluations) == 1
    assert evaluations[0]["signal_state"] == "entry_candidate"  # watching ignores risk and exit rules
    analysis = service.universe_analysis(universe["id"])
    company_analysis = next(item for item in analysis["items"] if item["symbol"] == "000001.SZ")
    assert analysis["total"] == 21
    assert analysis["state_counts"]["entry_candidate"] == 1
    assert analysis["state_counts"]["not_archived"] == 20
    assert company_analysis["model_state"] == "not_configured"
    assert company_analysis["metrics"]["pe_ttm"] == 8
    assert company_analysis["risk_facts"] == ["营收同比 -30.0% ≤ -20%", "公司盈利但经营现金流为负"]
    assert service.company_archive("000001.SZ")["analysis"]["current_state"] == "entry_candidate"
    assert len(value.list_events()) == 1
    value.acknowledge_event(value.list_events()[0]["id"], status="closed")
    value.save_snapshot({
        "universe_id": universe["id"], "symbol": "000001.SZ", "data_as_of": "2026-08-15",
        "status": "ready", "completeness": 1, "source_hash": "snapshot-hash-next", "payload": snapshot_payload,
        "diff": {}, "missing_fields": [], "sources": ["tdx"], "evidence_ids": [first_evidence["id"]],
    })
    service.evaluate_signal_rules(universe_id=universe["id"], as_of="2026-08-15")
    assert len(value.list_events()) == 2  # a closed condition may reappear on genuinely new input
    value.update_monitor(monitor["id"], position_state="holding")
    service.evaluate_signal_rules(universe_id=universe["id"], as_of="2026-08-15")
    assert value.latest_signal_evaluation(monitor["id"])["signal_state"] == "exit_candidate"
    assert len(value.list_events()) == 3
    value.update_monitor(monitor["id"], thesis_invalidated=True)
    service.evaluate_signal_rules(universe_id=universe["id"], as_of="2026-08-15")
    assert value.latest_signal_evaluation(monitor["id"])["signal_state"] == "thesis_invalidated"


def test_automation_defaults_off_and_runs_at_next_trading_close(workspace):
    value, _, _ = workspace
    automation = value.get_automation()
    assert automation["enabled"] == 0
    assert automation["run_time"] == "16:45"
    shanghai = ZoneInfo("Asia/Shanghai")
    assert next_value_run(datetime(2026, 8, 14, 16, 44, tzinfo=shanghai)).isoformat().startswith("2026-08-14T16:45")
    assert next_value_run(datetime(2026, 8, 14, 16, 46, tzinfo=shanghai)).isoformat().startswith("2026-08-17T16:45")

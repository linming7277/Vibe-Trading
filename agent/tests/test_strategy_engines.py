from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.paper_trading.store import PaperTradingStore
from src.research_workspace.store import ResearchWorkspaceStore
from src.strategy_engines.common.contracts import CommitteeDecision, DecisionStatus, StrategyLine
from src.strategy_engines.common.normalization import cross_sectional_percentiles, rolling_percentile
from src.strategy_engines.common.scoring import weighted_score
from src.strategy_engines.backtest import ReplayConfig, compare_raw_and_committee
from src.strategy_engines.emotion.emotion_regime import transition
from src.strategy_engines.history import HistoricalFeatureStore, OfficialCsvImportAdapter
from src.strategy_engines.formula_registry import formula_manifest, sync_formula_artifacts
from src.strategy_store.sqlite_store import SqliteStrategyStore
from src.strategy_engines.service import StrategyEngineService
from src.strategy_engines.store import StrategyEngineStore


def _value_inputs(evidence_id: str = "") -> dict:
    return {
        "source_status": "live",
        "macro": {"growth": 60, "liquidity": 70, "inflation": 55, "policy": 65, "risk_appetite": 60},
        "sectors": [{
            "id": "SW-L2-TEST",
            "components": {
                "prosperity": 80, "earnings_revision": 75, "macro_policy_fit": 70,
                "relative_strength": 65, "capital_flow": 60, "valuation": 70, "risk": 80,
            },
        }],
        "leaders": [{
            "symbol": "600000.SH", "price": 10,
            "components": {
                "industry_position_proxy": 80, "profitability_quality": 85,
                "growth_stability": 75, "valuation_margin": 80, "cash_flow": 90,
                "governance_risk": 80,
            },
            "timing": {
                "margin_of_safety": 80, "earnings_trend": 70, "price_trend": 65,
                "atr_pullback": 70, "liquidity": 90, "event_window": 80,
                "portfolio_capacity": 90,
            },
            "evidence_ids": [evidence_id] if evidence_id else [],
        }],
    }


def test_normalization_and_missing_policy_are_deterministic() -> None:
    rows = [{"quality": 1.0}, {"quality": 3.0}, {"quality": None}, {"quality": 2.0}]
    first = cross_sectional_percentiles(rows, {"quality": True})
    second = cross_sectional_percentiles(rows, {"quality": True})
    assert first == second
    assert [row["quality"] for row in first] == [0.0, 100.0, None, 50.0]
    assert rolling_percentile(range(119), 10) is None

    result = weighted_score({"a": 80, "b": None}, {"a": 0.6, "b": 0.4})
    assert result.status == "insufficient_data"
    assert result.score is None


def test_cross_sectional_percentiles_keep_ties_equal_and_constant_neutral() -> None:
    constant = cross_sectional_percentiles(
        [{"value": 0.0}, {"value": 0.0}, {"value": 0.0}, {"value": 0.0}], {"value": True},
    )
    assert [row["value"] for row in constant] == [50.0, 50.0, 50.0, 50.0]

    ascending = cross_sectional_percentiles(
        [{"value": 10.0}, {"value": 10.0}, {"value": 20.0}, {"value": 30.0}], {"value": True},
    )
    assert ascending[0]["value"] == ascending[1]["value"] == pytest.approx(16.6667)
    assert ascending[2]["value"] == pytest.approx(66.6667)
    assert ascending[3]["value"] == 100.0

    descending = cross_sectional_percentiles(
        [{"value": 10.0}, {"value": None}, {"value": 10.0}, {"value": 20.0}], {"value": False},
    )
    assert descending[0]["value"] == descending[2]["value"] == 75.0
    assert descending[1]["value"] is None
    assert descending[3]["value"] == 0.0


def test_empty_strategy_inputs_finish_as_insufficient_data(tmp_path: Path) -> None:
    store = StrategyEngineStore(tmp_path / "research.db")
    service = StrategyEngineService(store)
    try:
        value = service.run(
            strategy_line="value", market="CN", as_of="2026-08-12",
            inputs={"source_status": "unavailable", "macro": {}, "sectors": [], "leaders": []},
        )
        emotion = service.run(
            strategy_line="emotion", market="HK", as_of="2026-08-12",
            inputs={"source_status": "unavailable", "market": {"components": {}}, "sectors": [], "candidates": []},
        )
        assert value["status"] == "insufficient_data"
        assert emotion["status"] == "insufficient_data"
        assert store.dashboard("emotion", "HK")["regime"]["regime"] == "insufficient_data"
    finally:
        store.close()


def test_formula_versions_sync_to_existing_strategy_store_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "strategy_store.db"
    first = sync_formula_artifacts(db_path)
    second = sync_formula_artifacts(db_path)
    assert first == second
    assert len(first) == len(formula_manifest()) == 17
    store = SqliteStrategyStore(db_path)
    try:
        artifact = store.get_artifact("dual_line_value_leader_v1")
        assert artifact is not None
        assert artifact.artifact_version == "value-leader-v1.0.0"
        assert '"minimum_coverage":0.8' in (artifact.signal_definition or "")
    finally:
        store.close()


def test_official_csv_import_requires_and_preserves_pit_timestamps(tmp_path: Path) -> None:
    csv_path = tmp_path / "official.csv"
    csv_path.write_text(
        "symbol,data_as_of,available_at,revenue\n"
        "600000.SH,2025-12-31,2026-03-30T08:00:00+00:00,100\n",
        encoding="utf-8",
    )
    store = HistoricalFeatureStore(tmp_path / "history", tmp_path / "research.db")
    records = OfficialCsvImportAdapter(store).import_file(
        path=csv_path, market="CN", dataset="pit_financials", provider="official-csv",
    )
    assert records[0]["data_as_of"] == "2025-12-31"
    assert store.read_as_of(
        market="CN", dataset="pit_financials", as_of="2025-12-31",
        available_before="2026-03-29T00:00:00+00:00",
    ).empty
    visible = store.read_as_of(
        market="CN", dataset="pit_financials", as_of="2025-12-31",
        available_before="2026-03-31T00:00:00+00:00",
    )
    assert visible.iloc[0]["symbol"] == "600000.SH"


def test_strategy_replay_uses_next_day_and_compares_committee_value_add() -> None:
    signals = [{
        "id": "raw-1", "symbol": "600000.SH", "direction": "buy",
        "valid_from": "2026-08-13", "valid_until": "2026-08-14",
        "entry_low": 9, "entry_high": 11, "stop_price": 8,
        "target_low": 10.5, "position_cap": .10,
    }]
    bars = pd.DataFrame([
        {"date": "2026-08-12", "symbol": "600000.SH", "open": 9, "close": 10},
        {"date": "2026-08-13", "symbol": "600000.SH", "open": 10, "close": 10.2},
        {"date": "2026-08-14", "symbol": "600000.SH", "open": 10.6, "close": 10.8},
    ])
    comparison = compare_raw_and_committee(
        signals, bars, approved_signal_ids=set(),
        config=ReplayConfig(market="CN", slippage_bps=0),
    )
    assert comparison["raw"]["trades"][0]["date"] == "2026-08-13"
    assert comparison["raw"]["trades"][1]["date"] == "2026-08-14"
    assert comparison["committee"]["trades"] == []


def test_emotion_regime_uses_hysteresis() -> None:
    assert transition(previous=None, score=18, ice_confirmed=True)[0] == "ice"
    assert transition(previous="ice", score=31, delta_5d=5)[0] == "repair"
    assert transition(previous="repair", score=55, delta_5d=4)[0] == "fermentation"
    assert transition(previous="fermentation", score=78, climax_confirmations=2)[0] == "climax"
    assert transition(previous="climax", score=65, delta_3d=-13)[0] == "ebb"
    assert transition(previous="ebb", score=38, delta_5d=2)[0] == "ebb"


def test_engine_run_is_idempotent_and_persists_traceable_signal(tmp_path: Path) -> None:
    store = StrategyEngineStore(tmp_path / "research.db")
    service = StrategyEngineService(store)
    try:
        first = service.run(strategy_line="value", market="CN", as_of="2026-08-12", inputs=_value_inputs())
        second = service.run(strategy_line="value", market="CN", as_of="2026-08-12", inputs=_value_inputs())
        assert second["id"] == first["id"]
        dashboard = store.dashboard("value", "CN")
        assert dashboard["regime"]["formula_version"] == "value-macro-v1.0.0"
        assert dashboard["signals"][0]["status"] == "proposed"
        assert dashboard["signals"][0]["coverage"] == 1
        chain = store.get_decision_chain(first["id"])
        assert chain and chain["timing_signal_id"] == dashboard["signals"][0]["id"]
    finally:
        store.close()


def test_dashboard_keeps_each_engine_visible_when_one_universe_is_large(tmp_path: Path) -> None:
    store = StrategyEngineStore(tmp_path / "research.db")
    service = StrategyEngineService(store)
    try:
        inputs = _value_inputs()
        inputs["leaders"] = [
            {**inputs["leaders"][0], "symbol": f"600{index:03d}.SH"}
            for index in range(80)
        ]
        service.run(strategy_line="value", market="CN", as_of="2026-08-12", inputs=inputs)
        dashboard = store.dashboard("value", "CN")
        assert any(item["engine"] == "value_sector" for item in dashboard["scores"])
        assert any(item["engine"] == "value_leader" for item in dashboard["scores"])
        assert store.list_scores("value", "CN", engine="value_sector")
    finally:
        store.close()


def test_committee_cannot_loosen_risk_and_only_approved_signal_enters_paper(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    workspace = ResearchWorkspaceStore(db_path, seed=False)
    research_run = workspace.create_research_run("macro", "CN", status="queued")
    workspace.publish_research_results(research_run["id"], [{
        "market": "CN", "data_as_of": "2026-08-12", "source_status": "live",
        "evidence": [{"source": "fixture", "data_as_of": "2026-08-12", "metadata": {}}],
        "macro": {"headline": "fixture", "stance": "neutral", "summary": "fixture", "themes": [], "risks": []},
    }])
    evidence_id = workspace.list_research_evidence("CN", "macro", "2026-08-12")[0]["id"]
    committee = workspace.create_committee("CN", "600000.SH", "浦发银行", None)
    workspace.close()

    engine = StrategyEngineStore(db_path)
    service = StrategyEngineService(engine)
    service.run(strategy_line="value", market="CN", as_of="2026-08-12", inputs=_value_inputs(evidence_id))
    signal = engine.list_signals(strategy_line="value")[0]

    def decision(**overrides):
        values = {
            "id": "decision-test", "committee_id": committee["id"], "signal_id": signal["id"],
            "strategy_line": StrategyLine.VALUE, "status": DecisionStatus.APPROVE,
            "direction": "buy", "position_cap": .08, "entry_low": 9.6, "entry_high": 10,
            "stop_price": 8.9, "target_low": 11.5, "target_high": 13,
            "holding_period": "60d", "confidence": .8, "summary": "approved fixture",
            "review_triggers": ("thesis break",), "evidence_ids": (evidence_id,),
            "engine_run_ids": (signal["engine_run_id"],),
        }
        values.update(overrides)
        return CommitteeDecision(**values)

    with pytest.raises(ValueError, match="position cap"):
        engine.publish_decision(decision(position_cap=.2))
    with pytest.raises(ValueError, match="loosen"):
        engine.publish_decision(decision(stop_price=8.0))

    published = engine.publish_decision(decision())
    assert published["decision_status"] == "approve"
    approved = engine.get_signal(signal["id"])
    assert approved and approved["status"] == "approved"

    paper = PaperTradingStore(tmp_path / "paper.db")
    try:
        order = paper.submit_approved_signal(
            account_id="paper_value_cn", signal=approved, committee_id=committee["id"],
            decision_id=published["id"], quantity=100, limit_price=10,
            submitted_at="2026-08-13T01:30:00+00:00",
        )
        duplicate = paper.submit_approved_signal(
            account_id="paper_value_cn", signal=approved, committee_id=committee["id"],
            decision_id=published["id"], quantity=100, limit_price=10,
            submitted_at="2026-08-13T01:30:00+00:00",
        )
        assert duplicate["id"] == order["id"]
        fill = paper.record_fill(
            order_id=order["id"], quantity=100, price=10, fee=5,
            execution_key="fixture-fill-1", filled_at="2026-08-13T01:31:00+00:00",
        )
        assert paper.record_fill(
            order_id=order["id"], quantity=100, price=10, fee=5,
            execution_key="fixture-fill-1", filled_at="2026-08-13T01:31:00+00:00",
        )["id"] == fill["id"]
        assert paper.list_positions("paper_value_cn")[0]["quantity"] == 100
        assert paper.nav("paper_value_cn", {"600000.SH": 10})["nav"] == pytest.approx(999_995)
    finally:
        paper.close()
        engine.close()


def test_history_store_enforces_point_in_time_availability(tmp_path: Path) -> None:
    history = HistoricalFeatureStore(tmp_path / "history", tmp_path / "research.db")
    history.write_partition(
        market="CN", dataset="ohlcv", data_as_of="2026-08-11",
        frame=pd.DataFrame({"symbol": ["600000.SH"], "close": [10.0]}),
        provider="fixture", available_at="2026-08-11T16:00:00+08:00",
    )
    history.write_partition(
        market="CN", dataset="ohlcv", data_as_of="2026-08-12",
        frame=pd.DataFrame({"symbol": ["600000.SH"], "close": [11.0]}),
        provider="fixture", available_at="2026-08-12T16:00:00+08:00",
    )
    frame = history.read_as_of(
        market="CN", dataset="ohlcv", as_of="2026-08-12",
        available_before="2026-08-12T09:00:00+08:00",
    )
    assert frame["close"].tolist() == [10.0]


def test_seed_cleanup_only_removes_explicit_sample_rows(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore(tmp_path / "research.db", seed=True)
    try:
        removed = store.remove_seed_data()
        assert removed["market_snapshots"] == 3
        assert store.latest_macro("CN") is None
        assert store._conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 6
    finally:
        store.close()

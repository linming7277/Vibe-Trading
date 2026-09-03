"""Valuation-method bundle recorder: content, idempotency, PIT guard."""

from __future__ import annotations

import pytest

from src.pit_replay import ValuationMethodRecorder
from src.pit_replay.store import PITReplayStore
from src.value_price_zones import FORMULA_VERSION as PRICE_ZONE_FORMULA_VERSION
from src.value_strategy import RELIABILITY_FORMULA_VERSION


def zones_fixture(*, as_of: str = "2026-09-02", extreme: bool = False) -> dict:
    mid = 900.0 if extreme else 12.0
    return {
        "as_of": as_of,
        "price_as_of": as_of,
        "current_price": 10.0,
        "formula_version": PRICE_ZONE_FORMULA_VERSION,
        "valuation": {
            "status": "UNDERVALUED",
            "fair_value_low": 8.0, "fair_value_mid": mid, "fair_value_high": 12.0,
            "methods": [
                {"name": "预测利润 + 同三级行业 PE 可比", "kind": "PE", "status": "READY", "peer_count": 9,
                 "multiple_low": 8.0, "multiple_mid": 10.0, "multiple_high": 14.0,
                 "fair_values": [8.0, 10.0, 14.0]},
                {"name": "同三级行业 PB 可比", "kind": "PB", "status": "READY", "peer_count": 7,
                 "multiple_low": 0.8, "multiple_mid": 1.0, "multiple_high": 1.4,
                 "fair_values": [8.0, 10.0, 14.0]},
            ],
        },
        "historical_valuation": {"coverage": {"coverage_status": "READY"}},
        "data_quality": {
            "peer_comparables": {
                "status": "READY", "peer_count": 9,
                "pe_peer_count": 9, "pb_peer_count": 7,
                "pe_codes": ["000002.SZ", "000003.SZ"], "pb_codes": ["000004.SZ"],
            },
        },
    }


def test_bundle_persists_per_method_provenance(tmp_path):
    store = PITReplayStore(tmp_path / "research.db")
    recorder = ValuationMethodRecorder(store)
    row = recorder.record("CN", "000001.SZ", research_as_of="2026-09-02", zones=zones_fixture(), source_pool_id="pool-1")

    bundle = row["peer_method_bundle"]
    assert bundle["PE"]["status"] == "READY"
    assert bundle["PE"]["peer_count"] == 9
    assert bundle["PE"]["median_or_reference"] == 10.0
    assert bundle["PE"]["peer_codes"] == ["000002.SZ", "000003.SZ"]
    assert len(bundle["PE"]["peer_codes_hash"]) == 64
    assert bundle["PB"]["status"] == "READY"
    assert bundle["PB"]["peer_count"] == 7
    assert bundle["PB"]["peer_codes"] == ["000004.SZ"]

    # Reliability: min(9, 7)=7 -> LIMITED, upgraded one level by two methods -> RELIABLE.
    assert row["reliability_status"] == "RELIABLE"
    assert row["reliability_reasons"]
    assert row["extreme_fair_value_flagged"] is False
    assert row["valuation_formula_version"] == PRICE_ZONE_FORMULA_VERSION
    assert row["reliability_formula_version"] == RELIABILITY_FORMULA_VERSION
    assert row["snapshot_origin"] == "FORWARD_CAPTURED"
    assert row["universe_hash"] and row["input_fingerprint"] and row["source_hash"]
    store.close()


def test_same_day_rerun_is_idempotent(tmp_path):
    store = PITReplayStore(tmp_path / "research.db")
    recorder = ValuationMethodRecorder(store)
    first = recorder.record("CN", "000001.SZ", research_as_of="2026-09-02", zones=zones_fixture())
    second = recorder.record("CN", "000001.SZ", research_as_of="2026-09-02", zones=zones_fixture())
    assert first["id"] == second["id"]
    assert len(store.method_snapshots_for_as_of("2026-09-02")) == 1
    store.close()


def test_recorder_rejects_future_zones_projection(tmp_path):
    store = PITReplayStore(tmp_path / "research.db")
    recorder = ValuationMethodRecorder(store)
    with pytest.raises(ValueError, match="PIT violation"):
        recorder.record("CN", "000001.SZ", research_as_of="2026-09-01", zones=zones_fixture(as_of="2026-09-02"))
    assert store.method_snapshots_for_as_of("2026-09-01") == []
    store.close()


def test_not_ready_method_keeps_observed_peers_without_invented_median(tmp_path):
    store = PITReplayStore(tmp_path / "research.db")
    recorder = ValuationMethodRecorder(store)
    fixture = zones_fixture()
    fixture["data_quality"]["peer_comparables"]["pb_codes"] = []
    fixture["data_quality"]["peer_comparables"]["pb_peer_count"] = 2
    fixture["valuation"]["methods"] = [m for m in fixture["valuation"]["methods"] if m.get("kind") != "PB"]
    row = recorder.record("CN", "000006.SZ", research_as_of="2026-09-02", zones=fixture)
    bundle = row["peer_method_bundle"]
    assert bundle["PB"]["status"] == "NOT_READY"
    assert bundle["PB"]["median_or_reference"] is None
    assert bundle["PB"]["peer_count"] == 2
    store.close()


def test_extreme_fair_value_flag_is_persisted(tmp_path):
    store = PITReplayStore(tmp_path / "research.db")
    recorder = ValuationMethodRecorder(store)
    row = recorder.record("CN", "000007.SZ", research_as_of="2026-09-02", zones=zones_fixture(extreme=True))
    # ratio = 900/10 = 90 >= 5.0 -> flagged; max(count 9,7) > 4 so no downgrade,
    # but the flag itself must be persisted as provenance.
    assert row["extreme_fair_value_flagged"] is True
    store.close()

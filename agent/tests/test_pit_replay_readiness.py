"""PIT replay readiness service: per-date evidence checks, read-only."""

from __future__ import annotations

import uuid

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.pit_replay import PITReplayReadinessService
from src.pit_replay.store import PITReplayStore
from src.tdx_data.store import TdxDataStore


def seed_market_close(store: TdxDataStore, market_date: str, *, status: str = "completed",
                      quotes: str = "ready", item_count: int = 5548) -> None:
    run_id, snapshot_id = f"run_{uuid.uuid4().hex[:8]}", f"snap_{uuid.uuid4().hex[:8]}"
    with store._lock, store._conn:  # noqa: SLF001 - test fixture seeding
        store._conn.execute(
            """INSERT INTO refresh_runs(id,profile,market,market_date,snapshot_id,status,progress,total,
               created_at,started_at,completed_at) VALUES(?,'market_close','CN',?,?,?,100,1,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')""",
            (run_id, market_date, snapshot_id, status),
        )
        store._conn.execute(
            """INSERT INTO dataset_snapshots(snapshot_id,refresh_run_id,dataset,market,market_date,
               available_at,source,item_count,expected_count,status) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, run_id, "quotes", "CN", market_date, "2026-01-01T00:00:00Z", "tdx",
             item_count, item_count, quotes),
        )


def pool_item(code: str, as_of: str) -> dict:
    return {
        "market": "CN", "stock_code": code, "company_name": f"公司{code}",
        "industry_code": "I001", "industry_name": "行业", "leader_rank": 1, "leader_score": 80.0,
        "current_price": 10.0, "fair_value_low": 12.0, "fair_value_mid": 15.0, "fair_value_high": 20.0,
        "valuation_status": "UNDERVALUED", "source_pool_id": "pool-1", "source_as_of": as_of,
        "enter_reason": "UNDERVALUED", "metadata": {},
    }


def build(tmp_path, *, codes: list[str], as_of: str = "2026-09-02"):
    tdx = TdxDataStore(tmp_path / "tdx.db")
    pool = LowValueLeaderPoolRepository(tmp_path / "research.db")
    replay = PITReplayStore(pool.db_path)
    for code in codes:
        pool.create_entry(pool_item(code, as_of))
    pool.record_refresh(
        source_as_of=as_of, source_pool_id="pool-1", status="COMPLETED",
        active_count=len(codes), changes={}, errors=[],
    )
    service = PITReplayReadinessService(pool_repository=pool, tdx_store=tdx, replay_store=replay)
    return service, tdx, pool, replay


def close_all(service, tdx, pool, replay) -> None:
    service.close()
    tdx.close()
    pool.close()
    replay.close()


def test_ready_day_requires_close_snapshot_and_full_bundles(tmp_path):
    service, tdx, pool, replay = build(tmp_path, codes=["000001.SZ", "000002.SZ"])
    seed_market_close(tdx, "2026-09-02")
    for code in ("000001.SZ", "000002.SZ"):
        replay.record_method_snapshot(
            market="CN", stock_code=code, research_as_of="2026-09-02",
            valuation_formula_version="value-price-zones-v1.0.0",
            reliability_formula_version="valuation-reliability-v1.0.0",
            peer_method_bundle={"PE": {"status": "READY", "peer_count": 9}, "PB": {"status": "NOT_READY"}},
            reliability_status="RELIABLE", reliability_reasons=["9 家有效同行样本"],
            extreme_fair_value_flagged=False, universe_hash="u", input_fingerprint="i", source_hash="s",
        )
    result = service.evaluate_readiness("2026-09-02")
    assert result["status"] == "READY"
    assert result["complete_companies"] == 2
    assert result["low_value_count"] == 2
    assert {check["check"] for check in result["checks"]} == {
        "market_close", "low_value_snapshot", "valuation_method_bundle", "reliability_provenance",
    }
    close_all(service, tdx, pool, replay)


def test_failed_market_close_is_not_ready_and_never_faked(tmp_path):
    service, tdx, pool, replay = build(tmp_path, codes=["000001.SZ"])
    seed_market_close(tdx, "2026-09-01", status="failed", quotes="failed", item_count=0)
    result = service.evaluate_readiness("2026-09-01")
    assert result["status"] == "NOT_READY"
    market_check = next(item for item in result["checks"] if item["check"] == "market_close")
    assert market_check["evidence"]["qualification"] == "FAILED"
    close_all(service, tdx, pool, replay)


def test_partial_bundle_coverage_is_partial_not_ready(tmp_path):
    service, tdx, pool, replay = build(tmp_path, codes=["000001.SZ", "000002.SZ"])
    seed_market_close(tdx, "2026-09-02")
    replay.record_method_snapshot(
        market="CN", stock_code="000001.SZ", research_as_of="2026-09-02",
        valuation_formula_version="value-price-zones-v1.0.0",
        reliability_formula_version="valuation-reliability-v1.0.0",
        peer_method_bundle={"PE": {"status": "READY", "peer_count": 9}, "PB": {"status": "NOT_READY"}},
        reliability_status="RELIABLE", reliability_reasons=["9 家有效同行样本"],
        extreme_fair_value_flagged=False, universe_hash="u", input_fingerprint="i", source_hash="s",
    )
    result = service.evaluate_readiness("2026-09-02")
    assert result["status"] == "PARTIAL"
    assert result["bundle_coverage"] == 0.5
    close_all(service, tdx, pool, replay)


def test_list_ready_dates_reports_required_fields(tmp_path):
    service, tdx, pool, replay = build(tmp_path, codes=["000001.SZ"])
    seed_market_close(tdx, "2026-08-31")
    seed_market_close(tdx, "2026-09-02")
    dates = service.list_ready_dates(limit=5)
    assert [item["research_as_of"] for item in dates] == ["2026-09-02", "2026-08-31"]
    for item in dates:
        assert {"research_as_of", "status", "complete_companies", "low_value_count"} <= set(item)
    close_all(service, tdx, pool, replay)


def test_market_close_qualification_view_matches_derivation(tmp_path):
    tdx = TdxDataStore(tmp_path / "tdx.db")
    seed_market_close(tdx, "2026-08-25")
    seed_market_close(tdx, "2026-08-26", status="failed", quotes="failed", item_count=0)
    seed_market_close(tdx, "2026-08-27", quotes="ready", item_count=120)
    by_date = {row["market_date"]: row["qualification"] for row in tdx.market_close_qualifications()}
    assert by_date == {"2026-08-25": "QUALIFIED", "2026-08-26": "FAILED", "2026-08-27": "PARTIAL"}
    assert tdx.market_close_qualification("2026-08-26")["qualification"] == "FAILED"
    tdx.close()

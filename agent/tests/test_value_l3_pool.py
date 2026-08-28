from __future__ import annotations

from pathlib import Path

from src.level3_leaders.store import Level3LeaderStore


def leader(as_of: str, industry: str, symbol: str, rank: int, score: float) -> dict:
    return {
        "as_of": as_of,
        "level1_code": "L1", "level1_name": "一级",
        "level2_code": "L2", "level2_name": "二级",
        "level3_code": industry, "level3_name": "三级" + industry,
        "stock_code": symbol, "stock_name": symbol,
        "leader_rank": rank, "leader_score": score,
        "leader_formula_version": "leader-test-v1",
        "component_scores": {"quality": score},
        "coverage": 1.0, "eligibility_status": "eligible",
        "eligibility_reasons": [], "metric_applicability_notes": [],
        "raw_features": {}, "provenance_key": industry + ":" + symbol,
    }


def build(store: Level3LeaderStore, key: str, as_of: str, rows: list[dict]) -> dict:
    run = store.start_run(
        idempotency_key=key, as_of=as_of, catalog_as_of=as_of,
        formula_version="leader-test-v1",
    )
    store.finish_run(run["id"], rows=rows, statistics={})
    pool, _ = store.materialize_pool(run["id"])
    return pool


def test_pool_is_top2_and_idempotent(tmp_path: Path) -> None:
    store = Level3LeaderStore(tmp_path / "research.db")
    try:
        rows = [
            leader("2026-08-17", "I1", "000001.SZ", 1, 90),
            leader("2026-08-17", "I1", "000002.SZ", 2, 80),
            leader("2026-08-17", "I1", "000003.SZ", 3, 70),
        ]
        pool = build(store, "run-1", "2026-08-17", rows)
        reused, created = store.materialize_pool(pool["source_leader_run_id"])
        assert created is False
        assert reused["id"] == pool["id"]
        assert [item["stock_code"] for item in reused["members"]] == ["000001.SZ", "000002.SZ"]
        assert reused["new_count"] == 2
    finally:
        store.close()


def test_pool_lifecycle_and_reentry_preserve_company_state(tmp_path: Path) -> None:
    store = Level3LeaderStore(tmp_path / "research.db")
    try:
        first = build(store, "run-1", "2026-08-17", [
            leader("2026-08-17", "I1", "000001.SZ", 1, 90),
            leader("2026-08-17", "I1", "000002.SZ", 2, 80),
        ])
        store.update_research_state(
            first["id"], "000001.SZ", status="READY",
            snapshot_id="financial-1", researched_at="2026-08-17T10:00:00+00:00",
        )
        second = build(store, "run-2", "2026-08-18", [
            leader("2026-08-18", "I1", "000002.SZ", 1, 91),
            leader("2026-08-18", "I1", "000003.SZ", 2, 81),
        ])
        lifecycle = {(row["stock_code"], row["lifecycle_status"]) for row in second["members"]}
        assert ("000001.SZ", "OUT_OF_TOP2") in lifecycle
        assert ("000002.SZ", "ACTIVE") in lifecycle
        assert ("000003.SZ", "NEW") in lifecycle
        out_state = next(row for row in second["research_states"] if row["stock_code"] == "000001.SZ")
        assert out_state["research_status"] == "INACTIVE"
        assert out_state["last_financial_snapshot_id"] == "financial-1"

        third = build(store, "run-3", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 92),
            leader("2026-08-19", "I1", "000003.SZ", 2, 82),
        ])
        row = next(item for item in third["members"] if item["stock_code"] == "000001.SZ")
        assert row["lifecycle_status"] == "REENTERED"
        state = next(item for item in third["research_states"] if item["stock_code"] == "000001.SZ")
        assert state["last_financial_snapshot_id"] == "financial-1"
        assert state["research_status"] == "PENDING"
    finally:
        store.close()


def test_company_remaining_in_another_industry_stays_active(tmp_path: Path) -> None:
    store = Level3LeaderStore(tmp_path / "research.db")
    try:
        build(store, "run-1", "2026-08-17", [
            leader("2026-08-17", "I1", "000001.SZ", 1, 90),
            leader("2026-08-17", "I2", "000001.SZ", 1, 89),
        ])
        second = build(store, "run-2", "2026-08-18", [
            leader("2026-08-18", "I1", "000002.SZ", 1, 91),
            leader("2026-08-18", "I2", "000001.SZ", 1, 88),
        ])
        state = next(item for item in second["research_states"] if item["stock_code"] == "000001.SZ")
        assert state["lifecycle_status"] == "ACTIVE"
        assert second["company_count"] == 2
    finally:
        store.close()


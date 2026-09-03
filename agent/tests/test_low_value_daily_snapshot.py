"""Same-day immutable daily pool snapshot materialization (PIT remediation)."""

from __future__ import annotations

import pytest

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository


def item(code: str, as_of: str, *, pool_id: str = "pool-1") -> dict:
    return {
        "market": "CN", "stock_code": code, "company_name": f"公司{code}",
        "industry_code": "I001", "industry_name": "行业", "leader_rank": 1, "leader_score": 80.0,
        "current_price": 10.0, "fair_value_low": 12.0, "fair_value_mid": 15.0, "fair_value_high": 20.0,
        "valuation_status": "UNDERVALUED", "source_pool_id": pool_id, "source_as_of": as_of,
        "enter_reason": "UNDERVALUED", "metadata": {"price_as_of": as_of},
    }


def test_materialize_same_day_snapshots_are_immutable_and_idempotent(tmp_path):
    repo = LowValueLeaderPoolRepository(tmp_path / "research.db")
    repo.create_entry(item("000001.SZ", "2026-09-02"))
    repo.create_entry(item("000002.SZ", "2026-09-02"))
    repo.record_refresh(source_as_of="2026-09-02", source_pool_id="pool-1", status="COMPLETED",
                        active_count=2, changes={}, errors=[])

    first = repo.materialize_daily_snapshot("2026-09-02", "pool-1")
    assert first["archived_rows"] == 2
    assert first["safe_reconstructed_rows"] == 0  # default origin is FORWARD_CAPTURED

    # Same-day rerun never duplicates or rewrites the immutable rows.
    second = repo.materialize_daily_snapshot("2026-09-02", "pool-1")
    assert second["archived_rows"] == 2
    assert repo.daily_snapshot_status("2026-09-02") == {
        "source_as_of": "2026-09-02", "archived_rows": 2, "safe_reconstructed_rows": 0, "active_rows": 2,
    }
    rows = repo.snapshots_for_as_of("2026-09-02")
    assert sorted(row["stock_code"] for row in rows) == ["000001.SZ", "000002.SZ"]
    assert all(row["pool_status"] == "ACTIVE" for row in rows)
    repo.close()


def test_controlled_backfill_is_labelled_safe_reconstructed(tmp_path):
    repo = LowValueLeaderPoolRepository(tmp_path / "research.db")
    repo.create_entry(item("000001.SZ", "2026-09-01"))
    repo.record_refresh(source_as_of="2026-09-01", source_pool_id="pool-1", status="COMPLETED",
                        active_count=1, changes={}, errors=[])
    status = repo.materialize_daily_snapshot("2026-09-01", "pool-1", snapshot_origin="SAFE_RECONSTRUCTED")
    assert status["safe_reconstructed_rows"] == 1

    # A later forward materialization must not relabel or duplicate the row.
    repo.materialize_daily_snapshot("2026-09-01", "pool-1", snapshot_origin="FORWARD_CAPTURED")
    assert repo.daily_snapshot_status("2026-09-01")["archived_rows"] == 1
    assert repo.daily_snapshot_status("2026-09-01")["safe_reconstructed_rows"] == 1
    repo.close()


def test_next_day_lazy_archival_still_preserves_prior_projection(tmp_path):
    repo = LowValueLeaderPoolRepository(tmp_path / "research.db")
    repo.create_entry(item("000001.SZ", "2026-09-01"))
    repo.synchronize_refresh(
        eligible={"000001.SZ": item("000001.SZ", "2026-09-02")},
        current_codes={"000001.SZ"}, evaluated={"000001.SZ": "UNDERVALUED"}, error_codes=set(),
        source_pool_id="pool-2", source_as_of="2026-09-02", remove_reason=lambda status: "VALUATION_RECOVERED",
    )
    # The 09-01 projection was archived by the day-after path, and the rows
    # for both dates coexist immutably.
    assert repo.daily_snapshot_status("2026-09-01")["archived_rows"] == 1
    assert repo.daily_snapshot_status("2026-09-02")["active_rows"] == 1
    repo.close()


def test_unsupported_snapshot_origin_is_rejected(tmp_path):
    repo = LowValueLeaderPoolRepository(tmp_path / "research.db")
    try:
        with pytest.raises(ValueError, match="unsupported snapshot_origin"):
            repo.materialize_daily_snapshot("2026-09-02", "pool-1", snapshot_origin="ESTIMATED")
    finally:
        repo.close()

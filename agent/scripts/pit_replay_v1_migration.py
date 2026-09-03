"""One-shot PIT replay V1 migration and controlled backfill.

Operator script.  Applies the additive ``pit-replay-v1`` migration to the
production research.db (after backup) and performs ONLY the two controlled,
audit-approved backfills:

1. Materialize the missing 2026-09-01 immutable Low Value pool snapshot from
   the existing 09-01 ACTIVE projection (no recomputation), labelled
   ``SAFE_RECONSTRUCTED``.
2. Backfill ``company_theses.valid_from`` (factual, from each row's own
   creation day) and one ``MIGRATION_BACKFILL`` lifecycle event per existing
   thesis version.
3. Rebuild historical valuation series (PIT-safe by construction) for the
   audited ``CALCULATION_NOT_RUN`` companies, as of the latest qualified
   close (2026-09-01).

Strategy cursors/events are counted before and after and must be unchanged.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.company_thesis.store import CompanyThesisRepository
from src.config.paths import get_runtime_root
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.pit_replay.store import PIT_REPLAY_MIGRATION_ID, PITReplayStore

RESEARCH_DB = get_runtime_root() / "research.db"
TDX_DB = get_runtime_root() / "tdx_data.db"
POOL_AS_OF = "2026-09-01"
POOL_ID = "l3pool_138bcb1a381e4f20"
HV_COMPANIES = [
    "300358.SZ", "300457.SZ", "600064.SH", "601018.SH",
    "603368.SH", "603856.SH", "002093.SZ", "002185.SZ", "002696.SZ",
]


def _counts() -> dict[str, int]:
    conn = sqlite3.connect(f"file:{RESEARCH_DB.as_posix()}?mode=ro", uri=True)
    try:
        return {
            "cursors": conn.execute("SELECT COUNT(*) FROM value_strategy_state_cursors").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM value_strategy_state_events").fetchone()[0],
            "theses": conn.execute("SELECT COUNT(*) FROM company_theses").fetchone()[0],
            "pool_active_0901": conn.execute(
                "SELECT COUNT(*) FROM company_low_value_leader_pool WHERE source_as_of='2026-09-01' AND pool_status='ACTIVE'"
            ).fetchone()[0],
        }
    finally:
        conn.close()


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    before = _counts()
    conn_ro = sqlite3.connect(f"file:{RESEARCH_DB.as_posix()}?mode=ro", uri=True)
    schema_before = conn_ro.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    conn_ro.close()

    backups = get_runtime_root() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups / f"research-pit-replay-v1-{stamp}.db"
    shutil.copy2(RESEARCH_DB, backup_path)
    print(f"backup: {backup_path} ({backup_path.stat().st_size:,} bytes)")

    # --- migration: additive columns/tables only -------------------------
    pool_repo = LowValueLeaderPoolRepository(RESEARCH_DB)
    thesis_repo = CompanyThesisRepository(RESEARCH_DB)
    replay_store = PITReplayStore(RESEARCH_DB)

    conn_ro = sqlite3.connect(f"file:{RESEARCH_DB.as_posix()}?mode=ro", uri=True)
    schema_after = conn_ro.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
    conn_ro.close()

    # --- controlled backfill 1: 09-01 pool snapshot ----------------------
    status = pool_repo.materialize_daily_snapshot(
        POOL_AS_OF, POOL_ID, market="CN", snapshot_origin="SAFE_RECONSTRUCTED",
    )
    print("09-01 snapshot:", status)

    # --- controlled backfill 2: thesis valid_from + lifecycle events -----
    thesis_backfill = thesis_repo.backfill_lifecycle_events()
    print("thesis backfill:", thesis_backfill)

    # --- controlled backfill 3: historical valuation rebuild -------------
    hv_results: list[dict[str, object]] = []
    try:
        from src.historical_valuation.service import HistoricalValuationService
        hv = HistoricalValuationService()
        for code in HV_COMPANIES:
            result = hv.refresh_company("CN", code, as_of=POOL_AS_OF)
            hv_results.append({
                "stock_code": code,
                "coverage_status": result.get("coverage_status"),
                "pe_count": result.get("pe_count"), "pb_count": result.get("pb_count"),
                "fetched_prices": result.get("fetched_prices"), "changed_count": result.get("changed_count"),
                "last_error": result.get("last_error"),
            })
            print("HV", code, result.get("coverage_status"), result.get("pe_count"), result.get("pb_count"), result.get("last_error") or "")
        hv.tdx_store.close()
    except Exception as exc:  # noqa: BLE001
        hv_results.append({"error": f"{type(exc).__name__}: {exc}"})
        print("HV refresh unavailable:", type(exc).__name__, exc)

    after = _counts()
    cursor_unchanged = (before["cursors"], before["events"]) == (after["cursors"], after["events"])

    record = {
        "migration_id": PIT_REPLAY_MIGRATION_ID,
        "applied_at": started,
        "research_db": str(RESEARCH_DB),
        "backup_path": str(backup_path),
        "schema_version_before": schema_before,
        "schema_version_after": schema_after,
        "changes": "additive only: company_low_value_leader_pool_snapshots.snapshot_origin; "
                   "company_theses.valid_from/valid_to; company_thesis_lifecycle_events; "
                   "valuation_method_snapshots; pit_replay_migrations; "
                   "v_market_close_qualifications view (tdx_data.db)",
        "controlled_backfills": {
            "pool_snapshot_2026_09_01": {"origin": "SAFE_RECONSTRUCTED", **status},
            "thesis_lifecycle": thesis_backfill,
            "historical_valuation": hv_results,
        },
        "strategy_state_before": before,
        "strategy_state_after": after,
        "strategy_state_unchanged": cursor_unchanged,
    }
    out = Path(__file__).resolve().parents[1] / ".." / "docs" / "pit" / "migration-records" / f"pit-replay-v1-{stamp}.json"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("record:", out)
    print("strategy state unchanged:", cursor_unchanged)

    pool_repo.close()
    thesis_repo.close()
    replay_store.close()


if __name__ == "__main__":
    main()

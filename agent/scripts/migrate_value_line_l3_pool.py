#!/usr/bin/env python
"""Idempotent Value Line L3 pool migration with mandatory SQLite backups."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.paths import get_runtime_root  # noqa: E402
from src.level3_leaders.store import Level3LeaderStore  # noqa: E402
from src.research_workspace.store import ResearchWorkspaceStore  # noqa: E402


LEGACY_TABLES = (
    "value_signal_evaluations", "value_monitor_events", "notification_deliveries",
    "value_entry_monitors", "company_research_jobs", "company_research_batches",
    "value_research_event_deliveries", "value_research_events",
    "company_incremental_jobs", "company_incremental_runs",
    "value_company_research_monitors", "company_valuation_snapshots",
    "company_research_snapshots", "value_research_universe_members",
    "value_research_universes", "value_track_leaders", "value_tracks",
    "value_calculation_profiles", "company_business_profiles", "company_track_memberships",
    "company_track_suggestions", "fine_track_unclassified",
    "fine_track_classification_runs", "fine_grained_tracks",
)
LEGACY_DATASETS = ("value_sector_scores_v2", "value_leader_scores_v2")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if table in _tables(conn) else 0


def _backup_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _inventory(research_db: Path, tdx_db: Path) -> dict[str, object]:
    result: dict[str, object] = {"research_db": str(research_db), "tdx_db": str(tdx_db)}
    with sqlite3.connect(str(research_db)) as conn:
        tables = _tables(conn)
        result["latest_level3_run"] = (
            conn.execute(
                """SELECT id,as_of FROM value_level3_leader_runs
                   WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT 1"""
            ).fetchone() if "value_level3_leader_runs" in tables else None
        )
        result["legacy_tables"] = {name: _count(conn, name) for name in LEGACY_TABLES if name in tables}
        result["level3_leader_runs"] = _count(conn, "value_level3_leader_runs")
        result["level3_leaders"] = _count(conn, "value_level3_leaders")
        result["pool_runs"] = _count(conn, "l3_leader_pool_runs")
        result["pool_members"] = _count(conn, "l3_leader_pool_members")
        result["financial_snapshots"] = _count(conn, "company_financial_analysis_snapshots")
    with sqlite3.connect(str(tdx_db)) as conn:
        tables = _tables(conn)
        result["legacy_tdx_records"] = {
            dataset: int(conn.execute("SELECT COUNT(*) FROM records WHERE dataset=?", (dataset,)).fetchone()[0])
            for dataset in LEGACY_DATASETS
        } if "records" in tables else {}
    return result


def _preserve_company_assets(conn: sqlite3.Connection, pool_id: str | None) -> dict[str, int]:
    tables = _tables(conn)
    copied = {"research_snapshots": 0, "valuation_snapshots": 0}
    current_symbols = {
        str(row[0]) for row in conn.execute(
            """SELECT DISTINCT stock_code FROM l3_leader_pool_members
               WHERE pool_id=? AND lifecycle_status<>'OUT_OF_TOP2'""", (pool_id,),
        )
    } if pool_id else set()
    if "company_research_snapshots" in tables:
        rows = conn.execute("SELECT * FROM company_research_snapshots ORDER BY created_at").fetchall()
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(company_research_snapshots)")]
        for raw in rows:
            row = dict(zip(columns, raw))
            symbol = str(row["symbol"])
            cursor = conn.execute(
                """INSERT OR IGNORE INTO l3_company_research_snapshots(
                   id,source_snapshot_id,pool_id,stock_code,version,data_as_of,status,completeness,
                   source_hash,payload_json,diff_json,missing_fields_json,sources_json,evidence_ids_json,
                   dossier_id,report_id,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"l3snap_{uuid.uuid4().hex[:20]}", row["id"], pool_id if symbol in current_symbols else None,
                 symbol, row["version"], row["data_as_of"], row["status"], row["completeness"],
                 row["source_hash"], row["payload_json"], row["diff_json"], row["missing_fields_json"],
                 row["sources_json"], row["evidence_ids_json"], row.get("dossier_id"), row.get("report_id"),
                 row["created_at"]),
            )
            copied["research_snapshots"] += int(cursor.rowcount > 0)
    if "company_valuation_snapshots" in tables:
        rows = conn.execute("SELECT * FROM company_valuation_snapshots ORDER BY created_at").fetchall()
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(company_valuation_snapshots)")]
        scalar_fields = (
            "current_price", "pe_ttm", "pb_mrq", "dividend_yield", "peer_pe_median",
            "peer_pb_median", "pe_percentile", "pb_percentile", "safety_margin",
            "fair_value_low", "fair_value_high", "watch_price_low", "watch_price_high",
        )
        for raw in rows:
            row = dict(zip(columns, raw))
            symbol = str(row["symbol"])
            valuation = {key: row.get(key) for key in scalar_fields}
            valuation.update({
                "comparable": json.loads(row.get("comparable_json") or "{}"),
                "dcf": json.loads(row.get("dcf_json") or "{}"),
                "missing_fields": json.loads(row.get("missing_fields_json") or "[]"),
                "sources": json.loads(row.get("sources_json") or "[]"),
            })
            cursor = conn.execute(
                """INSERT OR IGNORE INTO l3_company_valuation_snapshots(
                   id,source_snapshot_id,pool_id,stock_code,version,status,review_status,data_as_of,
                   coverage,source_hash,valuation_json,formula_version,confirmed_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"l3val_{uuid.uuid4().hex[:20]}", row["id"], pool_id if symbol in current_symbols else None,
                 symbol, row["version"], row["status"], row["review_status"], row["data_as_of"],
                 row["coverage"], row["source_hash"], json.dumps(valuation, ensure_ascii=False, sort_keys=True),
                 row["formula_version"], row.get("confirmed_at"), row["created_at"]),
            )
            copied["valuation_snapshots"] += int(cursor.rowcount > 0)
    if pool_id and "company_financial_analysis_snapshots" in tables:
        conn.execute(
            """UPDATE l3_company_research_states
               SET research_status=CASE
                       WHEN (SELECT feature_status FROM company_financial_analysis_snapshots f
                             WHERE f.stock_code=l3_company_research_states.stock_code
                             ORDER BY f.as_of DESC,f.created_at DESC LIMIT 1)='READY' THEN 'READY'
                       WHEN (SELECT feature_status FROM company_financial_analysis_snapshots f
                             WHERE f.stock_code=l3_company_research_states.stock_code
                             ORDER BY f.as_of DESC,f.created_at DESC LIMIT 1)='PARTIAL' THEN 'PARTIAL'
                       ELSE research_status END,
                   last_financial_snapshot_id=(SELECT id FROM company_financial_analysis_snapshots f
                       WHERE f.stock_code=l3_company_research_states.stock_code
                       ORDER BY f.as_of DESC,f.created_at DESC LIMIT 1),
                   last_researched_at=(SELECT updated_at FROM company_financial_analysis_snapshots f
                       WHERE f.stock_code=l3_company_research_states.stock_code
                       ORDER BY f.as_of DESC,f.created_at DESC LIMIT 1)
               WHERE pool_id=?""", (pool_id,),
        )
    return copied


def migrate(research_db: Path, tdx_db: Path, backup_root: Path) -> dict[str, object]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = backup_root / f"value-line-l3-{timestamp}"
    _backup_sqlite(research_db, backup_dir / "research.db")
    _backup_sqlite(tdx_db, backup_dir / "tdx_data.db")

    initializer = ResearchWorkspaceStore(research_db, seed=False)
    initializer.close()
    leader_store = Level3LeaderStore(research_db)
    try:
        run = leader_store.latest_run()
        if not run:
            raise RuntimeError("no completed L3 leader run; migration stopped")
        pool, pool_created = leader_store.materialize_pool(run["id"])
    finally:
        leader_store.close()

    conn = sqlite3.connect(str(research_db))
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        preserved = _preserve_company_assets(conn, pool["id"])
        if "score_snapshots" in _tables(conn):
            conn.execute(
                "DELETE FROM score_snapshots WHERE strategy_line='value' AND engine IN ('value_sector','value_leader')"
            )
        for table in LEGACY_TABLES:
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        engine_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(engine_runs)")}
        for column in ("profile_id", "profile_version"):
            if column in engine_columns:
                conn.execute(f'ALTER TABLE engine_runs DROP COLUMN "{column}"')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    tdx = sqlite3.connect(str(tdx_db))
    tdx.execute("PRAGMA busy_timeout=30000")
    try:
        tdx.execute("BEGIN IMMEDIATE")
        tables = _tables(tdx)
        if "records" in tables:
            tdx.executemany("DELETE FROM records WHERE dataset=?", [(name,) for name in LEGACY_DATASETS])
        if "snapshot_records" in tables:
            tdx.executemany("DELETE FROM snapshot_records WHERE dataset=?", [(name,) for name in LEGACY_DATASETS])
        if "dataset_snapshots" in tables:
            tdx.executemany("DELETE FROM dataset_snapshots WHERE dataset=?", [(name,) for name in LEGACY_DATASETS])
        if "module_state" in tables:
            tdx.execute("DELETE FROM module_state WHERE module='scores'")
        tdx.commit()
    except Exception:
        tdx.rollback()
        raise
    finally:
        tdx.close()
    return {
        "backup_dir": str(backup_dir), "pool_id": pool["id"], "pool_created": pool_created,
        "preserved": preserved, "after": _inventory(research_db, tdx_db),
    }


def main() -> int:
    runtime = get_runtime_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-db", type=Path, default=runtime / "research.db")
    parser.add_argument("--tdx-db", type=Path, default=runtime / "tdx_data.db")
    parser.add_argument("--backup-root", type=Path, default=runtime / "backups")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.research_db.exists() or not args.tdx_db.exists():
        parser.error("research.db and tdx_data.db must both exist")
    before = _inventory(args.research_db, args.tdx_db)
    result = {"mode": "dry-run", "before": before}
    if args.apply:
        result = {"mode": "apply", "before": before, **migrate(args.research_db, args.tdx_db, args.backup_root)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

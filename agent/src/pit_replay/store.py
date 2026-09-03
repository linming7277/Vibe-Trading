"""SQLite persistence for PIT replay evidence (valuation method bundles).

This module owns its additive tables inside research.db.  It never modifies
existing tables, never rewrites snapshots, and performs no valuation of its
own: it only persists, at EOD time, the exact method-level inputs the
ValuePriceZone projection already used so a later replay can verify them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore

# Additive migration identifier recorded once per database file.  Bumping this
# constant re-records the marker row; it does not alter any prior table.
PIT_REPLAY_MIGRATION_ID = "pit-replay-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class PITReplayStore:
    """Owns valuation-method snapshot tables used for point-in-time replay."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                -- Immutable, per-company daily record of exactly which
                -- valuation methods were usable and which peers fed them.
                -- Written once per EOD; UNIQUE makes reruns idempotent.
                CREATE TABLE IF NOT EXISTS valuation_method_snapshots (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    research_as_of TEXT NOT NULL,
                    valuation_formula_version TEXT NOT NULL,
                    reliability_formula_version TEXT NOT NULL,
                    peer_method_bundle_json TEXT NOT NULL DEFAULT '{}',
                    reliability_status TEXT NOT NULL,
                    reliability_reasons_json TEXT NOT NULL DEFAULT '[]',
                    extreme_fair_value_flagged INTEGER NOT NULL DEFAULT 0,
                    universe_hash TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    source_pool_id TEXT,
                    snapshot_origin TEXT NOT NULL DEFAULT 'FORWARD_CAPTURED'
                        CHECK(snapshot_origin IN ('FORWARD_CAPTURED','SAFE_RECONSTRUCTED')),
                    created_at TEXT NOT NULL,
                    UNIQUE(market, stock_code, research_as_of, valuation_formula_version, reliability_formula_version)
                );
                CREATE INDEX IF NOT EXISTS idx_valuation_method_snapshots_asof
                    ON valuation_method_snapshots(research_as_of DESC, stock_code);

                -- One row per applied migration; documents that the additive
                -- tables above were introduced without touching prior schema.
                CREATE TABLE IF NOT EXISTS pit_replay_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO pit_replay_migrations(migration_id, applied_at, note) VALUES(?,?,?)",
                (PIT_REPLAY_MIGRATION_ID, _now(), "additive valuation_method_snapshots + lifecycle events"),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- writes

    def record_method_snapshot(
        self,
        *,
        market: str,
        stock_code: str,
        research_as_of: str,
        valuation_formula_version: str,
        reliability_formula_version: str,
        peer_method_bundle: dict[str, Any],
        reliability_status: str,
        reliability_reasons: list[str],
        extreme_fair_value_flagged: bool,
        universe_hash: str,
        input_fingerprint: str,
        source_hash: str,
        source_pool_id: str | None = None,
        snapshot_origin: str = "FORWARD_CAPTURED",
    ) -> dict[str, Any]:
        """Insert one immutable bundle row; same-day reruns are ignored."""
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO valuation_method_snapshots(
                    id,market,stock_code,research_as_of,valuation_formula_version,reliability_formula_version,
                    peer_method_bundle_json,reliability_status,reliability_reasons_json,extreme_fair_value_flagged,
                    universe_hash,input_fingerprint,source_hash,source_pool_id,snapshot_origin,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"vmsnap_{uuid.uuid4().hex[:20]}", market.upper(), stock_code.upper(), str(research_as_of)[:10],
                    valuation_formula_version, reliability_formula_version,
                    json.dumps(peer_method_bundle, ensure_ascii=False, sort_keys=True),
                    reliability_status,
                    json.dumps(reliability_reasons, ensure_ascii=False),
                    1 if extreme_fair_value_flagged else 0,
                    universe_hash, input_fingerprint, source_hash,
                    source_pool_id, snapshot_origin, _now(),
                ),
            )
        return self.method_snapshot(
            market=market, stock_code=stock_code, research_as_of=research_as_of,
            valuation_formula_version=valuation_formula_version, reliability_formula_version=reliability_formula_version,
        ) or {}

    # ----------------------------------------------------------------- reads

    def method_snapshot(
        self,
        *,
        market: str,
        stock_code: str,
        research_as_of: str,
        valuation_formula_version: str,
        reliability_formula_version: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM valuation_method_snapshots
               WHERE market=? AND stock_code=? AND research_as_of=?
                 AND valuation_formula_version=? AND reliability_formula_version=?""",
            (market.upper(), stock_code.upper(), str(research_as_of)[:10],
             valuation_formula_version, reliability_formula_version),
        ).fetchone()
        return self._row(row) if row else None

    def method_snapshots_for_as_of(self, research_as_of: str, market: str = "CN") -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM valuation_method_snapshots
               WHERE market=? AND research_as_of=? ORDER BY stock_code""",
            (market.upper(), str(research_as_of)[:10]),
        ).fetchall()
        return [self._row(row) for row in rows]

    def method_snapshot_as_of_counts(self, market: str = "CN") -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT research_as_of, COUNT(*) AS n FROM valuation_method_snapshots WHERE market=? GROUP BY research_as_of",
            (market.upper(),),
        ).fetchall()
        return {str(row["research_as_of"]): int(row["n"]) for row in rows}

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["peer_method_bundle"] = _loads(item.pop("peer_method_bundle_json"), {})
        item["reliability_reasons"] = _loads(item.pop("reliability_reasons_json"), [])
        item["extreme_fair_value_flagged"] = bool(item.pop("extreme_fair_value_flagged"))
        return item

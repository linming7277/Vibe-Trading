"""``research_manifests`` — the single cross-module freshness registry (plan §8).

Modules whose result tables already carry a comparable content hash
(financial/business snapshots, moat fingerprints, evidence set hashes) are
classified by direct comparison and do not need a manifest row.  This table
is for the date-keyed writers that had no input fingerprint at all: the low
value pool refresh, the risk snapshot, and the daily brief (plan §20.3).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchManifestStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(get_runtime_root()) / "research.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_manifests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    research_type TEXT NOT NULL,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL DEFAULT '',
                    research_as_of TEXT NOT NULL,
                    source_data_as_of TEXT,
                    input_fingerprint TEXT NOT NULL,
                    source_hashes_json TEXT NOT NULL DEFAULT '{}',
                    formula_version TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'READY',
                    freshness_status TEXT NOT NULL DEFAULT 'FRESH',
                    dependencies_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_validated_at TEXT,
                    stale_reason TEXT,
                    UNIQUE(market, stock_code, research_type, input_fingerprint)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_manifests_lookup "
                "ON research_manifests(market, stock_code, research_type, research_as_of DESC)"
            )

    def record(
        self,
        *,
        research_type: str,
        market: str,
        stock_code: str = "",
        research_as_of: str,
        input_fingerprint: str,
        source_hashes: dict[str, Any] | None = None,
        formula_version: str = "",
        model_version: str = "",
        prompt_version: str = "",
        status: str = "READY",
        dependencies: dict[str, Any] | None = None,
    ) -> None:
        import json

        now = _utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO research_manifests (
                    research_type, market, stock_code, research_as_of, source_data_as_of,
                    input_fingerprint, source_hashes_json, formula_version, model_version,
                    prompt_version, status, freshness_status, dependencies_json,
                    created_at, updated_at, last_validated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market, stock_code, research_type, input_fingerprint) DO UPDATE SET
                    research_as_of=excluded.research_as_of,
                    source_data_as_of=excluded.source_data_as_of,
                    source_hashes_json=excluded.source_hashes_json,
                    formula_version=excluded.formula_version,
                    model_version=excluded.model_version,
                    prompt_version=excluded.prompt_version,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    last_validated_at=excluded.last_validated_at
                """,
                (
                    research_type, market, stock_code, str(research_as_of)[:10], None,
                    input_fingerprint,
                    json.dumps(source_hashes or {}, ensure_ascii=False, sort_keys=True, default=str),
                    formula_version, model_version, prompt_version, status, "FRESH",
                    json.dumps(dependencies or {}, ensure_ascii=False, sort_keys=True, default=str),
                    now, now, now,
                ),
            )

    def latest(
        self, *, research_type: str, market: str, stock_code: str = "",
        as_of: str | None = None,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT * FROM research_manifests WHERE research_type=? AND market=? AND stock_code=?"
        )
        params: list[Any] = [research_type, market, stock_code]
        if as_of:
            sql += " AND research_as_of<=?"
            params.append(str(as_of)[:10])
        sql += " ORDER BY research_as_of DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        columns = [c[0] for c in self._conn.execute("SELECT * FROM research_manifests LIMIT 0").description]
        return dict(zip(columns, row))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

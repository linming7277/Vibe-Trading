"""Persistence for single-company Business Research snapshots."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class BusinessResearchStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_business_research_snapshots (
                    id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    data_as_of TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    analysis_status TEXT NOT NULL,
                    analysis_json TEXT,
                    agent_provider TEXT,
                    agent_model TEXT,
                    agent_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(stock_code, source_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_business_research_company_time
                    ON company_business_research_snapshots(stock_code,data_as_of DESC,created_at DESC);
                CREATE TABLE IF NOT EXISTS business_analysis_leases (
                    lease_key TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["snapshot"] = _loads(value.pop("snapshot_json"), {})
        value["analysis"] = _loads(value.pop("analysis_json"), None)
        return value

    def latest(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        clauses = ["stock_code=?"]
        args: list[Any] = [stock_code.upper()]
        if as_of:
            # Persisted business snapshots may carry either a date or an ISO
            # timestamp. A date-only PIT request includes the whole stated
            # calendar day rather than accidentally excluding `T...` values.
            clauses.append("substr(data_as_of, 1, 10)<=?")
            args.append(str(as_of)[:10])
        return self._row(self._conn.execute(
            f"""SELECT * FROM company_business_research_snapshots WHERE {' AND '.join(clauses)}
               ORDER BY data_as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
            args,
        ).fetchone())

    def latest_before_hash(self, stock_code: str, source_hash: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        clauses = ["stock_code=?", "source_hash<>?"]
        args: list[Any] = [stock_code.upper(), source_hash]
        if as_of:
            clauses.append("substr(data_as_of, 1, 10)<=?")
            args.append(str(as_of)[:10])
        return self._row(self._conn.execute(
            f"""SELECT * FROM company_business_research_snapshots
               WHERE {' AND '.join(clauses)}
               ORDER BY data_as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
            args,
        ).fetchone())

    def by_hash(self, stock_code: str, source_hash: str) -> dict[str, Any] | None:
        return self._row(self._conn.execute(
            "SELECT * FROM company_business_research_snapshots WHERE stock_code=? AND source_hash=?",
            (stock_code.upper(), source_hash),
        ).fetchone())

    def list_snapshots(self, stock_code: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first snapshot rows for content-level PIT profile recovery.

        Snapshot ``data_as_of`` carries the profile cache touch date, so a
        snapshot persisted after ``as_of`` may still embed profile content
        whose business dates are valid at ``as_of``; callers therefore need the
        sequence, not just the date-filtered latest.
        """
        return [self._row(row) for row in self._conn.execute(
            "SELECT * FROM company_business_research_snapshots WHERE stock_code=? "
            "ORDER BY data_as_of DESC,created_at DESC,rowid DESC LIMIT ?",
            (stock_code.upper(), limit),
        ).fetchall()]

    def acquire_analysis_lease(self, lease_key: str, stock_code: str, source_hash: str,
                               owner: str, *, ttl_seconds: int = 600) -> bool:
        """Single-flight lease: one model-analysis owner per source fingerprint.

        An expired lease is recoverable by the next caller, so a crashed
        analyst can never lock a company permanently.
        """
        now = datetime.now(timezone.utc)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM business_analysis_leases WHERE lease_key=? AND expires_at<=?",
                (lease_key, now.isoformat()),
            )
            try:
                self._conn.execute(
                    "INSERT INTO business_analysis_leases(lease_key,stock_code,source_hash,owner,acquired_at,expires_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (lease_key, stock_code.upper(), source_hash, owner,
                     now.isoformat(), (now + timedelta(seconds=ttl_seconds)).isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def release_analysis_lease(self, lease_key: str, owner: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM business_analysis_leases WHERE lease_key=? AND owner=?",
                (lease_key, owner),
            )

    def save(self, snapshot: dict[str, Any], *, configured: bool, provider: str, model: str) -> tuple[dict[str, Any], bool]:
        if existing := self.by_hash(snapshot["stock_code"], snapshot["source_hash"]):
            return existing, False
        timestamp = _now()
        snapshot_id = f"business_{uuid.uuid4().hex[:20]}"
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """INSERT INTO company_business_research_snapshots(
                       id,stock_code,company_name,data_as_of,source_hash,snapshot_json,analysis_status,
                       analysis_json,agent_provider,agent_model,agent_error,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot_id, snapshot["stock_code"], snapshot["company_name"], snapshot["data_as_of"],
                        snapshot["source_hash"], json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                        "NOT_RUN" if configured else "CONFIGURATION_REQUIRED", None, provider, model, "",
                        timestamp, timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                # Concurrent callers may race past the by_hash pre-check; the
                # UNIQUE(stock_code, source_hash) guard makes the save
                # idempotent — the surviving row wins and reuses.
                pass
        row = self.by_hash(snapshot["stock_code"], snapshot["source_hash"]) or {}
        return row, row.get("id") == snapshot_id

    def update_analysis(self, snapshot_id: str, *, status: str, provider: str, model: str,
                        analysis: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE company_business_research_snapshots
                   SET analysis_status=?,analysis_json=?,agent_provider=?,agent_model=?,agent_error=?,updated_at=?
                   WHERE id=?""",
                (
                    status, json.dumps(analysis, ensure_ascii=False, sort_keys=True) if analysis is not None else None,
                    provider, model, error, _now(), snapshot_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(snapshot_id)
        return self._row(self._conn.execute(
            "SELECT * FROM company_business_research_snapshots WHERE id=?", (snapshot_id,),
        ).fetchone()) or {}

"""Persistence for single-company Business Research snapshots."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
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

    def save(self, snapshot: dict[str, Any], *, configured: bool, provider: str, model: str) -> tuple[dict[str, Any], bool]:
        if existing := self.by_hash(snapshot["stock_code"], snapshot["source_hash"]):
            return existing, False
        timestamp = _now()
        snapshot_id = f"business_{uuid.uuid4().hex[:20]}"
        with self._lock, self._conn:
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
        return self.by_hash(snapshot["stock_code"], snapshot["source_hash"]) or {}, True

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

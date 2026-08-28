"""Persistent canonical company-action events and their source references."""

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


class CompanyActionEventStore:
    """SQLite event store.

    ``initialize=False`` is used by read-only GET paths: a missing table is
    treated as an empty event layer rather than being created during a query.
    Explicit prepare paths use the default and are the only paths that create
    schema or event records.
    """

    def __init__(self, db_path: Path | None = None, *, initialize: bool = True) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self._missing = not self.db_path.exists()
        if initialize:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._missing = False
        # Read-only API paths must not create either a database or a schema.
        self._conn = sqlite3.connect(":memory:" if self._missing and not initialize else str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        if initialize:
            self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_action_events (
                    id TEXT PRIMARY KEY,
                    canonical_key TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL UNIQUE,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_status TEXT NOT NULL,
                    event_stage TEXT NOT NULL,
                    parent_event_id TEXT,
                    announcement_date TEXT,
                    event_date TEXT,
                    effective_date TEXT,
                    research_visible_from TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    cash_amount REAL,
                    share_count REAL,
                    share_ratio REAL,
                    price REAL,
                    currency TEXT,
                    shares_before REAL,
                    shares_after REAL,
                    purpose TEXT,
                    reason TEXT,
                    reason_source_event_id TEXT,
                    pit_status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    data_quality TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_company_action_event_query
                    ON company_action_events(market,stock_code,event_date DESC,announcement_date DESC);
                CREATE TABLE IF NOT EXISTS company_action_event_source_refs (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    announcement_date TEXT,
                    event_date TEXT,
                    pit_status TEXT NOT NULL,
                    source_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(event_id,source_type,source_id,source_hash),
                    FOREIGN KEY(event_id) REFERENCES company_action_events(id)
                );
                CREATE TABLE IF NOT EXISTS company_action_event_versions (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(event_id,payload_hash),
                    FOREIGN KEY(event_id) REFERENCES company_action_events(id)
                );
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def available(self) -> bool:
        if self._missing:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_action_events'"
        ).fetchone()
        return bool(row)

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json"), {})
        return item

    def _refs(self, event_id: str) -> list[dict[str, Any]]:
        if not self.available():
            return []
        rows = self._conn.execute(
            """SELECT source_type,source_id,source_url,source_hash,announcement_date,event_date,pit_status,source_payload_json
               FROM company_action_event_source_refs WHERE event_id=? ORDER BY created_at,id""", (event_id,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_payload"] = _loads(item.pop("source_payload_json"), {})
            result.append(item)
        return result

    def _upsert_source_ref(self, event_id: str, source: dict[str, Any]) -> None:
        source_hash = str(source.get("source_hash") or "")
        values = (
            f"company_action_source_{uuid.uuid4().hex[:20]}", event_id, str(source["source_type"]), str(source["source_id"]),
            str(source.get("source_url") or ""), source_hash, source.get("announcement_date"), source.get("event_date"),
            str(source.get("pit_status") or "PIT_LIMITED"), json.dumps(source.get("source_payload") or {}, ensure_ascii=False, sort_keys=True), _now(),
        )
        self._conn.execute(
            """INSERT OR IGNORE INTO company_action_event_source_refs(
                id,event_id,source_type,source_id,source_url,source_hash,announcement_date,event_date,pit_status,source_payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", values,
        )

    def _save_version(self, event_id: str, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        import hashlib
        payload_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        prior = self._conn.execute(
            "SELECT MAX(version) FROM company_action_event_versions WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        self._conn.execute(
            """INSERT OR IGNORE INTO company_action_event_versions(id,event_id,version,payload_hash,payload_json,created_at)
               VALUES(?,?,?,?,?,?)""",
            (f"company_action_version_{uuid.uuid4().hex[:20]}", event_id, int(prior or 0) + 1, payload_hash, content, _now()),
        )

    def save_event(self, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create one canonical event or merge a further source reference."""
        if not self.available():
            raise RuntimeError("company_action_event_store_not_initialized")
        canonical = str(item["canonical_key"])
        source = dict(item["source_ref"])
        with self._lock, self._conn:
            prior = self._conn.execute("SELECT * FROM company_action_events WHERE canonical_key=?", (canonical,)).fetchone()
            if prior:
                event = self._event(prior)
                # The canonical economic event is stable, while the local
                # extractor can become more precise on a later explicit
                # preparation (for example an earlier proximity-only share
                # reason is corrected to UNKNOWN).  Keep its id and sources,
                # but version the refined derived fields.
                if str(item.get("event_status")) == "DERIVED_FROM_TDX":
                    refreshed = {
                        "event_status": item.get("event_status"), "event_stage": item.get("event_stage"),
                        "title": item.get("title"), "summary": item.get("summary"),
                        "reason": item.get("reason"), "reason_source_event_id": item.get("reason_source_event_id"),
                        "data_quality": item.get("data_quality"), "extractor_version": item.get("extractor_version"),
                        "payload_json": json.dumps(item.get("payload") or {}, ensure_ascii=False, sort_keys=True),
                    }
                    changed = any(
                        event.get(key) != value
                        for key, value in refreshed.items()
                        if key != "payload_json"
                    ) or event.get("payload") != (item.get("payload") or {})
                    if changed:
                        self._conn.execute(
                            "UPDATE company_action_events SET " + ",".join(f"{key}=?" for key in refreshed) + ",updated_at=? WHERE id=?",
                            (*refreshed.values(), _now(), event["id"]),
                        )
                        self._save_version(str(event["id"]), {**event, **refreshed})
                        row = self._conn.execute("SELECT * FROM company_action_events WHERE id=?", (event["id"],)).fetchone()
                        event = self._event(row) if row else event
                self._upsert_source_ref(str(event["id"]), source)
                return {**event, "source_refs": self._refs(str(event["id"]))}, False
            now, event_id = _now(), f"company_action_{uuid.uuid4().hex[:20]}"
            columns = (
                "canonical_key", "fingerprint", "market", "stock_code", "event_type", "event_status", "event_stage",
                "parent_event_id", "announcement_date", "event_date", "effective_date", "research_visible_from",
                "source_type", "source_id", "source_url", "source_hash", "title", "summary", "cash_amount", "share_count",
                "share_ratio", "price", "currency", "shares_before", "shares_after", "purpose", "reason", "reason_source_event_id",
                "pit_status", "confidence", "data_quality", "extractor_version", "payload_json",
            )
            values = [
                item.get(name) if name != "payload_json" else json.dumps(item.get("payload") or {}, ensure_ascii=False, sort_keys=True)
                for name in columns
            ]
            self._conn.execute(
                f"INSERT INTO company_action_events(id,{','.join(columns)},created_at,updated_at) VALUES(?,{','.join('?' for _ in columns)},?,?)",
                (event_id, *values, now, now),
            )
            self._upsert_source_ref(event_id, source)
            self._save_version(event_id, {key: item.get(key) for key in columns})
            row = self._conn.execute("SELECT * FROM company_action_events WHERE id=?", (event_id,)).fetchone()
        return {**(self._event(row) if row else {}), "source_refs": self._refs(event_id)}, True

    def list_events(
        self, market: str, stock_code: str, *, as_of: str | None = None, event_type: str | None = None,
        start_date: str | None = None, end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.available():
            return []
        clauses, values = ["market=?", "stock_code=?"], [market, stock_code]
        if as_of:
            clauses.append("((pit_status='STRICT' AND COALESCE(research_visible_from,announcement_date)<=?) OR (pit_status='PIT_LIMITED' AND COALESCE(event_date,effective_date)<=?))")
            values.extend([str(as_of)[:10], str(as_of)[:10]])
        if event_type:
            clauses.append("event_type=?")
            values.append(event_type.upper())
        if start_date:
            clauses.append("COALESCE(event_date,effective_date,announcement_date)>=?")
            values.append(str(start_date)[:10])
        if end_date:
            clauses.append("COALESCE(event_date,effective_date,announcement_date)<=?")
            values.append(str(end_date)[:10])
        rows = self._conn.execute(
            f"SELECT * FROM company_action_events WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(event_date,effective_date,announcement_date) DESC,created_at DESC", values,
        ).fetchall()
        return [{**self._event(row), "source_refs": self._refs(str(row["id"]))} for row in rows]

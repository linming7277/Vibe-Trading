"""SQLite storage for low-value leader event notification delivery."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class LowValueLeaderNotificationRepository:
    """Owns delivery state; source research projections stay read-only."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
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
                CREATE TABLE IF NOT EXISTS low_value_event_notification_activation (
                    channel TEXT PRIMARY KEY,
                    historical_cutoff_created_at TEXT,
                    activated_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS low_value_event_notifications (
                    event_date TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('READY','SKIPPED_DISABLED','DRY_RUN','FAILED','SENT')),
                    event_ids_json TEXT NOT NULL DEFAULT '[]',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    sent_at TEXT,
                    message_id TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_date, channel)
                );
                CREATE INDEX IF NOT EXISTS idx_low_value_event_notifications_status
                    ON low_value_event_notifications(channel, status, event_date DESC);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _delivery(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["event_ids"] = _loads(item.pop("event_ids_json"), [])
        return item

    def ensure_activation(self, *, channel: str) -> dict[str, Any]:
        """Capture pre-feature events once, before an EOD creates new events."""
        timestamp = _now()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT * FROM low_value_event_notification_activation WHERE channel=?", (channel,)
            ).fetchone()
            if existing:
                return dict(existing)
            row = self._conn.execute(
                "SELECT MAX(created_at) AS cutoff FROM low_value_leader_events"
            ).fetchone()
            cutoff = str(row["cutoff"]) if row and row["cutoff"] else None
            self._conn.execute(
                """INSERT INTO low_value_event_notification_activation(
                    channel,historical_cutoff_created_at,activated_at,updated_at
                ) VALUES(?,?,?,?)""",
                (channel, cutoff, timestamp, timestamp),
            )
        return self.activation(channel=channel) or {}

    def activation(self, *, channel: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM low_value_event_notification_activation WHERE channel=?", (channel,)
        ).fetchone()
        return dict(row) if row else None

    def events_for_notification(self, *, channel: str, research_as_of: str) -> list[dict[str, Any]]:
        activation = self.activation(channel=channel)
        if not activation:
            raise RuntimeError(f"notification channel {channel} has not been activated")
        cutoff = activation.get("historical_cutoff_created_at")
        clauses = ["event_date=?", "source_as_of=?"]
        args: list[Any] = [research_as_of, research_as_of]
        if cutoff:
            clauses.append("created_at>?")
            args.append(cutoff)
        rows = self._conn.execute(
            f"""SELECT * FROM low_value_leader_events WHERE {' AND '.join(clauses)}
                ORDER BY CASE event_type WHEN 'ENTER_LOW_VALUE' THEN 0 ELSE 1 END, stock_code""",  # noqa: S608
            tuple(args),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _loads(item.pop("metadata_json"), {})
            items.append(item)
        return items

    def delivery(self, *, event_date: str, channel: str) -> dict[str, Any] | None:
        return self._delivery(self._conn.execute(
            "SELECT * FROM low_value_event_notifications WHERE event_date=? AND channel=?",
            (event_date, channel),
        ).fetchone())

    def record_delivery(
        self,
        *,
        event_date: str,
        channel: str,
        status: str,
        event_ids: list[str],
        message_id: str | None = None,
        error: str = "",
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self._lock, self._conn:
            existing = self.delivery(event_date=event_date, channel=channel)
            if existing and existing.get("status") == "SENT":
                return existing
            attempts = int((existing or {}).get("attempts") or 0) + (1 if increment_attempt else 0)
            self._conn.execute(
                """INSERT INTO low_value_event_notifications(
                    event_date,channel,status,event_ids_json,attempts,sent_at,message_id,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_date,channel) DO UPDATE SET
                    status=excluded.status,event_ids_json=excluded.event_ids_json,attempts=excluded.attempts,
                    sent_at=excluded.sent_at,message_id=excluded.message_id,error=excluded.error,updated_at=excluded.updated_at""",
                (
                    event_date, channel, status, json.dumps(event_ids, ensure_ascii=False, sort_keys=True), attempts,
                    timestamp if status == "SENT" else None, message_id, error[:1000],
                    str((existing or {}).get("created_at") or timestamp), timestamp,
                ),
            )
        return self.delivery(event_date=event_date, channel=channel) or {}

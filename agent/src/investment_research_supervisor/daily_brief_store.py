"""Persistence for deterministic Investment Research Supervisor daily briefs."""

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


_READY = "READY"
_DELIVERY_TERMINAL = {"SENT", "DRY_RUN", "SKIPPED_DISABLED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class InvestmentResearchDailyBriefRepository:
    """Owns daily-brief and daily-brief delivery state only."""

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
                CREATE TABLE IF NOT EXISTS investment_research_daily_briefs (
                    id TEXT PRIMARY KEY,
                    research_as_of TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('READY','FAILED')),
                    low_value_active_count INTEGER NOT NULL DEFAULT 0,
                    enter_count INTEGER NOT NULL DEFAULT 0,
                    exit_count INTEGER NOT NULL DEFAULT 0,
                    priority_companies_json TEXT NOT NULL DEFAULT '[]',
                    risk_summary_json TEXT NOT NULL DEFAULT '{}',
                    thesis_changes_json TEXT NOT NULL DEFAULT '[]',
                    financial_changes_json TEXT NOT NULL DEFAULT '[]',
                    data_gaps_json TEXT NOT NULL DEFAULT '[]',
                    brief_payload_json TEXT NOT NULL DEFAULT '{}',
                    formula_version TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_daily_briefs_status_date
                    ON investment_research_daily_briefs(status, research_as_of DESC);

                CREATE TABLE IF NOT EXISTS investment_research_daily_brief_deliveries (
                    id TEXT PRIMARY KEY,
                    research_as_of TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('SENT','FAILED','DRY_RUN','SKIPPED_DISABLED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    message_id TEXT,
                    attachment_message_id TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(research_as_of, channel, target_id)
                );
                """
            )
            delivery_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(investment_research_daily_brief_deliveries)")
            }
            if "attachment_message_id" not in delivery_columns:
                self._conn.execute(
                    "ALTER TABLE investment_research_daily_brief_deliveries ADD COLUMN attachment_message_id TEXT"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _brief_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for key, fallback in (
            ("priority_companies", []),
            ("risk_summary", {}),
            ("thesis_changes", []),
            ("financial_changes", []),
            ("data_gaps", []),
            ("brief_payload", {}),
        ):
            item[key] = _loads(item.pop(f"{key}_json"), fallback)
        return item

    @staticmethod
    def _delivery_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def get_for_as_of(self, research_as_of: str) -> dict[str, Any] | None:
        with self._lock:
            return self._brief_row(self._conn.execute(
                "SELECT * FROM investment_research_daily_briefs WHERE research_as_of=?", (research_as_of,)
            ).fetchone())

    def get_completed(self, research_as_of: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if research_as_of:
                row = self._conn.execute(
                    """SELECT * FROM investment_research_daily_briefs
                       WHERE research_as_of=? AND status=?""",
                    (research_as_of, _READY),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT * FROM investment_research_daily_briefs WHERE status=?
                       ORDER BY research_as_of DESC LIMIT 1""",
                    (_READY,),
                ).fetchone()
        return self._brief_row(row)

    def previous_completed(self, research_as_of: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM investment_research_daily_briefs
                   WHERE research_as_of<? AND status=?
                   ORDER BY research_as_of DESC LIMIT 1""",
                (research_as_of, _READY),
            ).fetchone()
        return self._brief_row(row)

    def save_ready(self, payload: dict[str, Any]) -> dict[str, Any]:
        research_as_of = str(payload["research_as_of"])
        timestamp = _now()
        fields = (
            "low_value_active_count", "enter_count", "exit_count", "priority_companies",
            "risk_summary", "thesis_changes", "financial_changes", "data_gaps", "brief_payload",
        )
        values = [payload.get(field, [] if field.endswith("companies") or field.endswith("changes") or field == "data_gaps" else {}) for field in fields]
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO investment_research_daily_briefs(
                    id,research_as_of,status,low_value_active_count,enter_count,exit_count,
                    priority_companies_json,risk_summary_json,thesis_changes_json,financial_changes_json,
                    data_gaps_json,brief_payload_json,formula_version,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(research_as_of) DO UPDATE SET
                    status=excluded.status,low_value_active_count=excluded.low_value_active_count,
                    enter_count=excluded.enter_count,exit_count=excluded.exit_count,
                    priority_companies_json=excluded.priority_companies_json,
                    risk_summary_json=excluded.risk_summary_json,
                    thesis_changes_json=excluded.thesis_changes_json,
                    financial_changes_json=excluded.financial_changes_json,
                    data_gaps_json=excluded.data_gaps_json,brief_payload_json=excluded.brief_payload_json,
                    formula_version=excluded.formula_version,error='',updated_at=excluded.updated_at""",
                (
                    f"daily_brief_{uuid.uuid4().hex[:20]}", research_as_of, _READY,
                    int(values[0] or 0), int(values[1] or 0), int(values[2] or 0),
                    json.dumps(values[3], ensure_ascii=False, sort_keys=True),
                    json.dumps(values[4], ensure_ascii=False, sort_keys=True),
                    json.dumps(values[5], ensure_ascii=False, sort_keys=True),
                    json.dumps(values[6], ensure_ascii=False, sort_keys=True),
                    json.dumps(values[7], ensure_ascii=False, sort_keys=True),
                    json.dumps(values[8], ensure_ascii=False, sort_keys=True),
                    str(payload.get("formula_version") or "daily-brief-v1"), "", timestamp, timestamp,
                ),
            )
        return self.get_for_as_of(research_as_of) or {}

    def record_failure(self, research_as_of: str, error: str, *, formula_version: str = "daily-brief-v1") -> dict[str, Any]:
        timestamp = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO investment_research_daily_briefs(
                    id,research_as_of,status,formula_version,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(research_as_of) DO UPDATE SET
                    status=excluded.status,error=excluded.error,formula_version=excluded.formula_version,
                    updated_at=excluded.updated_at""",
                (f"daily_brief_{uuid.uuid4().hex[:20]}", research_as_of, "FAILED", formula_version, error, timestamp, timestamp),
            )
        return self.get_for_as_of(research_as_of) or {}

    def delivery(self, *, research_as_of: str, channel: str, target_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._delivery_row(self._conn.execute(
                """SELECT * FROM investment_research_daily_brief_deliveries
                   WHERE research_as_of=? AND channel=? AND target_id=?""",
                (research_as_of, channel, target_id),
            ).fetchone())

    def record_delivery(
        self, *, research_as_of: str, channel: str, target_id: str, status: str,
        message_id: str | None = None, attachment_message_id: str | None = None,
        error: str = "", increment_attempt: bool = False,
    ) -> dict[str, Any]:
        timestamp = _now()
        sent_at = timestamp if status == "SENT" else None
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO investment_research_daily_brief_deliveries(
                    id,research_as_of,channel,target_id,status,attempts,message_id,attachment_message_id,error,created_at,updated_at,sent_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(research_as_of,channel,target_id) DO UPDATE SET
                    status=excluded.status,attempts=investment_research_daily_brief_deliveries.attempts + excluded.attempts,
                    message_id=COALESCE(excluded.message_id, investment_research_daily_brief_deliveries.message_id),
                    attachment_message_id=COALESCE(excluded.attachment_message_id, investment_research_daily_brief_deliveries.attachment_message_id),
                    error=excluded.error,updated_at=excluded.updated_at,sent_at=excluded.sent_at""",
                (
                    f"daily_brief_delivery_{uuid.uuid4().hex[:20]}", research_as_of, channel, target_id,
                    status, int(increment_attempt), message_id, attachment_message_id, error, timestamp, timestamp, sent_at,
                ),
            )
        return self.delivery(research_as_of=research_as_of, channel=channel, target_id=target_id) or {}

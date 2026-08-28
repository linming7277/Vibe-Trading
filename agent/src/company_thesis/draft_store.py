"""Persistence for editable, non-authoritative Company Thesis drafts."""

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class CompanyThesisDraftRepository:
    """Stores drafts separately from durable, versioned Company Thesis records."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_thesis_drafts (
                    draft_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    core_thesis TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('FORMING','STRENGTHENING','UNCHANGED','WEAKENING','FALSIFIED')),
                    confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
                    invalid_conditions_json TEXT NOT NULL DEFAULT '[]',
                    source_data_as_of TEXT,
                    source_hash TEXT NOT NULL,
                    source_snapshots_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    draft_status TEXT NOT NULL CHECK(draft_status IN ('DRAFT','CONFIRMED','REJECTED','SUPERSEDED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    confirmed_at TEXT,
                    confirmed_by TEXT,
                    confirmed_thesis_id TEXT,
                    rejected_at TEXT,
                    rejected_by TEXT,
                    rejection_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(market, stock_code, source_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_company_thesis_drafts_company
                    ON company_thesis_drafts(market, stock_code, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_company_thesis_drafts_status
                    ON company_thesis_drafts(draft_status, updated_at DESC);
                """)
            # V1 expands an older minimal draft into an auditable research
            # packet.  Additive columns keep previously saved drafts readable.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_thesis_drafts)")}
            additions = {
                "research_as_of": "TEXT",
                "thesis_summary": "TEXT",
                "core_drivers_json": "TEXT NOT NULL DEFAULT '[]'",
                "competitive_advantages_json": "TEXT NOT NULL DEFAULT '[]'",
                "key_assumptions_json": "TEXT NOT NULL DEFAULT '[]'",
                "key_metrics_to_monitor_json": "TEXT NOT NULL DEFAULT '[]'",
                "main_risks_json": "TEXT NOT NULL DEFAULT '[]'",
                "data_quality_json": "TEXT NOT NULL DEFAULT '{}'",
                "workflow_status": "TEXT NOT NULL DEFAULT 'DRAFT'",
                "reviewed_by": "TEXT",
                "reviewed_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE company_thesis_drafts ADD COLUMN {name} {definition}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        for key, fallback in (
            ("invalid_conditions", []), ("source_snapshots", []),
            ("source_refs", []), ("core_drivers", []), ("competitive_advantages", []),
            ("key_assumptions", []), ("key_metrics_to_monitor", []), ("main_risks", []),
            ("data_quality", {}), ("metadata", {}),
        ):
            item[key] = _loads(item.pop(f"{key}_json"), fallback)
        return item

    def get(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_thesis_drafts WHERE draft_id=?", (draft_id,),
            ).fetchone())

    def latest(self, market: str, stock_code: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_thesis_drafts WHERE market=? AND stock_code=?
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""", (market, stock_code),
            ).fetchone())

    def by_source_hash(self, market: str, stock_code: str, source_hash: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_thesis_drafts WHERE market=? AND stock_code=? AND source_hash=?""",
                (market, stock_code, source_hash),
            ).fetchone())

    def save(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self.by_source_hash(payload["market"], payload["stock_code"], payload["source_hash"])
        if existing:
            return existing, False
        timestamp, draft_id = _now(), f"thesis_draft_{uuid.uuid4().hex[:20]}"
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_thesis_drafts(
                    draft_id,market,stock_code,company_name,title,core_thesis,status,confidence,
                    invalid_conditions_json,source_data_as_of,source_hash,source_snapshots_json,
                    source_refs_json,draft_status,created_at,updated_at,created_by,metadata_json,
                    research_as_of,thesis_summary,core_drivers_json,competitive_advantages_json,
                    key_assumptions_json,key_metrics_to_monitor_json,main_risks_json,data_quality_json,workflow_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    draft_id, payload["market"], payload["stock_code"], payload["company_name"],
                    payload["title"], payload["core_thesis"], payload["status"], payload["confidence"],
                    json.dumps(payload["invalid_conditions"], ensure_ascii=False, sort_keys=True),
                    payload.get("source_data_as_of"), payload["source_hash"],
                    json.dumps(payload["source_snapshots"], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload["source_refs"], ensure_ascii=False, sort_keys=True),
                    "DRAFT", timestamp, timestamp, "SYSTEM_DRAFT",
                    json.dumps(payload.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    payload.get("research_as_of"), payload.get("thesis_summary") or payload["core_thesis"],
                    json.dumps(payload.get("core_drivers") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("competitive_advantages") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("key_assumptions") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("key_metrics_to_monitor") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("main_risks") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("data_quality") or {}, ensure_ascii=False, sort_keys=True),
                    str(payload.get("workflow_status") or "DRAFT"),
                ),
            )
        return self.get(draft_id) or {}, True

    def confirm(self, draft_id: str, *, thesis_id: str, actor: str = "HUMAN") -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE company_thesis_drafts SET draft_status='CONFIRMED',workflow_status=?,updated_at=?,
                    confirmed_at=?,confirmed_by=?,confirmed_thesis_id=?,reviewed_by=?,reviewed_at=?
                   WHERE draft_id=? AND draft_status='DRAFT'""",
                ("APPROVED" if actor == "HUMAN" else "PROMOTED_TO_PROVISIONAL", _now(), _now(), actor, thesis_id, actor, _now(), draft_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only an active draft can be confirmed")
        return self.get(draft_id) or {}

    def reject(self, draft_id: str, *, reason: str, actor: str = "HUMAN") -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE company_thesis_drafts SET draft_status='REJECTED',workflow_status='REJECTED',updated_at=?,
                    rejected_at=?,rejected_by=?,rejection_reason=?,reviewed_by=?,reviewed_at=?
                   WHERE draft_id=? AND draft_status='DRAFT'""",
                (_now(), _now(), actor, reason, actor, _now(), draft_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only an active draft can be rejected")
        return self.get(draft_id) or {}

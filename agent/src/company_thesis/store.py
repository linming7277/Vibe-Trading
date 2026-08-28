"""SQLite repository for immutable Company Thesis versions."""

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


def _loads(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


class CompanyThesisRepository:
    """Owns thesis reads and append-only version writes in ``research.db``."""

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
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_theses)")}
            if "authority_status" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN authority_status TEXT")
                # Historical authority cannot be inferred safely.  Legacy is
                # intentionally distinct from human confirmation.
                self._conn.execute("UPDATE company_theses SET authority_status='LEGACY_UNVERIFIED' WHERE authority_status IS NULL")
            if "source_draft_id" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN source_draft_id TEXT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["is_current"] = bool(item["is_current"])
        item["invalid_conditions"] = _loads(item.pop("invalid_conditions_json"))
        return item

    def get_current_thesis(self, market: str, stock_code: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_theses WHERE market=? AND stock_code=? AND is_current=1
                   ORDER BY version DESC LIMIT 1""",
                (market, stock_code),
            ).fetchone())

    def list_thesis_versions(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_theses WHERE market=? AND stock_code=?
                   ORDER BY version DESC""", (market, stock_code),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def get_thesis_by_id(self, thesis_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_theses WHERE thesis_id=?", (thesis_id,),
            ).fetchone())

    def create_initial_thesis(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert version 1 only; existing versions must use ``create_new_version``."""
        market, stock_code = payload["market"], payload["stock_code"]
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT 1 FROM company_theses WHERE market=? AND stock_code=? LIMIT 1",
                    (market, stock_code),
                ).fetchone()
                if existing:
                    raise ValueError("current thesis already exists; create new version instead")
                created = self._insert(payload, version=1, is_current=True)
                self._conn.commit()
                return created
            except Exception:
                self._conn.rollback()
                raise

    def create_new_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                created = self._create_new_version_in_transaction(payload)
                self._conn.commit()
                return created
            except Exception:
                self._conn.rollback()
                raise

    def _create_new_version_in_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the next version using the caller's already-open transaction."""
        market, stock_code = payload["market"], payload["stock_code"]
        current = self._conn.execute(
            "SELECT * FROM company_theses WHERE market=? AND stock_code=? AND is_current=1",
            (market, stock_code),
        ).fetchone()
        if not current:
            raise KeyError("current thesis does not exist; create initial thesis first")
        previous = self._row(current) or {}
        next_version = int(previous["version"]) + 1
        evidence_ids = self._validate_history_evidence_ids(payload.get("evidence_ids") or [], previous)
        self._conn.execute(
            "UPDATE company_theses SET is_current=0 WHERE market=? AND stock_code=? AND is_current=1",
            (market, stock_code),
        )
        created = self._insert(payload, version=next_version, is_current=True)
        self._insert_history(previous, created, payload, evidence_ids)
        self._conn.execute(
            "UPDATE company_thesis_reviews SET is_stale=1 WHERE thesis_id=? AND is_stale=0",
            (previous["thesis_id"],),
        )
        return created

    def _validate_history_evidence_ids(self, evidence_ids: list[str], previous: dict[str, Any]) -> list[str]:
        """Capture only evidence explicitly attached to the version being replaced."""
        normalized: list[str] = []
        for raw_id in evidence_ids:
            evidence_id = str(raw_id or "").strip()
            if not evidence_id or evidence_id in normalized:
                continue
            row = self._conn.execute(
                """SELECT thesis_id,market,stock_code FROM company_thesis_evidence WHERE evidence_id=?""",
                (evidence_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"history evidence not found: {evidence_id}")
            if (row["thesis_id"], row["market"], row["stock_code"]) != (
                previous["thesis_id"], previous["market"], previous["stock_code"],
            ):
                raise ValueError("history evidence must belong to the previous thesis version")
            normalized.append(evidence_id)
        return normalized

    def _insert_history(self, previous: dict[str, Any], created: dict[str, Any],
                        payload: dict[str, Any], evidence_ids: list[str]) -> None:
        timestamp = _now()
        actor = payload["updated_by"]
        trigger_type = payload.get("history_trigger_type") or (
            "MANUAL" if actor == "HUMAN" else "SYSTEM" if actor == "SYSTEM" else "AGENT"
        )
        self._conn.execute(
            """INSERT INTO company_thesis_history(
                history_id,market,stock_code,from_thesis_id,to_thesis_id,from_version,to_version,
                old_status,new_status,old_confidence,new_confidence,change_type,change_reason,
                trigger_type,trigger_ref,evidence_ids_json,created_by,created_at,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"thesis_history_{uuid.uuid4().hex[:20]}", previous["market"], previous["stock_code"],
                previous["thesis_id"], created["thesis_id"], previous["version"], created["version"],
                previous["status"], created["status"], previous["confidence"], created["confidence"],
                "VERSION_CREATED", payload["change_reason"], trigger_type, payload.get("trigger_ref"),
                json.dumps(evidence_ids, ensure_ascii=False), actor, timestamp,
                json.dumps(payload.get("history_metadata") or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _insert(self, payload: dict[str, Any], *, version: int, is_current: bool) -> dict[str, Any]:
        thesis_id, timestamp = f"thesis_{uuid.uuid4().hex[:20]}", _now()
        self._conn.execute(
            """INSERT INTO company_theses(
                thesis_id,market,stock_code,title,core_thesis,status,confidence,
                invalid_conditions_json,change_reason,version,is_current,created_at,updated_at,
                created_by,updated_by,source_data_as_of,authority_status,source_draft_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                thesis_id, payload["market"], payload["stock_code"], payload["title"],
                payload["core_thesis"], payload["status"], payload["confidence"],
                json.dumps(payload["invalid_conditions"], ensure_ascii=False, sort_keys=True),
                payload.get("change_reason"), version, int(is_current), timestamp, timestamp,
                payload["created_by"], payload["updated_by"], payload.get("source_data_as_of"),
                payload.get("authority_status", "LEGACY_UNVERIFIED"), payload.get("source_draft_id"),
            ),
        )
        return self.get_thesis_by_id(thesis_id) or {}

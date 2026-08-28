"""SQLite repository for evidence attached to one immutable Thesis version."""

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


def _loads(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class CompanyThesisEvidenceRepository:
    """Append-only evidence storage; deactivation is a reversible state, not deletion."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["is_active"] = bool(item["is_active"])
        item["metadata"] = _loads(item.pop("metadata_json"))
        return item

    def create_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        evidence_id, timestamp = f"thesis_evidence_{uuid.uuid4().hex[:20]}", _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_thesis_evidence(
                    evidence_id,thesis_id,market,stock_code,evidence_type,effect,claim,summary,
                    source_type,source_id,source_ref,source_title,source_date,data_as_of,
                    confidence,is_active,created_at,updated_at,created_by,evidence_fingerprint,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id, payload["thesis_id"], payload["market"], payload["stock_code"],
                    payload["evidence_type"], payload["effect"], payload["claim"], payload["summary"],
                    payload["source_type"], payload.get("source_id"), payload.get("source_ref"),
                    payload.get("source_title"), payload.get("source_date"), payload.get("data_as_of"),
                    payload["confidence"], 1, timestamp, timestamp, payload["created_by"],
                    payload.get("evidence_fingerprint"),
                    json.dumps(payload.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.execute(
                "UPDATE company_thesis_reviews SET is_stale=1 WHERE thesis_id=? AND is_stale=0",
                (payload["thesis_id"],),
            )
        return self.get_evidence_by_id(evidence_id) or {}

    def get_evidence_by_id(self, evidence_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_thesis_evidence WHERE evidence_id=?", (evidence_id,),
            ).fetchone())

    def list_evidence_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_evidence WHERE thesis_id=?
                   ORDER BY created_at DESC,rowid DESC""", (thesis_id,),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def list_active_evidence_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_evidence WHERE thesis_id=? AND is_active=1
                   ORDER BY created_at DESC,rowid DESC""", (thesis_id,),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def find_active_evidence_by_fingerprint(self, thesis_id: str, fingerprint: str) -> list[dict[str, Any]]:
        """Return active rows for one deterministic extractor fingerprint.

        The extractor does a second metadata/actor check, so this lookup remains useful
        even if a future migration changes how automation metadata is represented.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_evidence
                   WHERE thesis_id=? AND evidence_fingerprint=? AND is_active=1
                   ORDER BY created_at DESC,rowid DESC""",
                (thesis_id, fingerprint),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def list_evidence_for_company(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_evidence WHERE market=? AND stock_code=?
                   ORDER BY created_at DESC,rowid DESC""", (market, stock_code),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def deactivate_evidence(self, evidence_id: str, reason: str, *, deactivated_by: str) -> dict[str, Any]:
        timestamp = _now()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT thesis_id FROM company_thesis_evidence WHERE evidence_id=?", (evidence_id,),
            ).fetchone()
            if not existing:
                raise KeyError(evidence_id)
            cursor = self._conn.execute(
                """UPDATE company_thesis_evidence
                   SET is_active=0,deactivation_reason=?,deactivated_at=?,deactivated_by=?,updated_at=?
                   WHERE evidence_id=?""",
                (reason, timestamp, deactivated_by, timestamp, evidence_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(evidence_id)
            self._conn.execute(
                "UPDATE company_thesis_reviews SET is_stale=1 WHERE thesis_id=? AND is_stale=0",
                (existing["thesis_id"],),
            )
        return self.get_evidence_by_id(evidence_id) or {}

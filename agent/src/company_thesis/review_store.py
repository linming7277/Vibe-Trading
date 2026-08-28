"""Persistence for deterministic Company Thesis Review recommendations."""

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


OPEN_REVIEW_STATUSES = ("PENDING", "REVIEWED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class CompanyThesisReviewRepository:
    """Store review snapshots without mutating Thesis, Evidence or History."""

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
        item["is_stale"] = bool(item["is_stale"])
        item["support_evidence_ids"] = _loads(item.pop("support_evidence_ids_json"), [])
        item["challenge_evidence_ids"] = _loads(item.pop("challenge_evidence_ids_json"), [])
        item["neutral_evidence_ids"] = _loads(item.pop("neutral_evidence_ids_json"), [])
        item["metadata"] = _loads(item.pop("metadata_json"), {})
        return item

    def create_review(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create one review, returning an existing open review for the same snapshot."""
        review_id, timestamp = f"thesis_review_{uuid.uuid4().hex[:20]}", _now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    """SELECT * FROM company_thesis_reviews
                       WHERE thesis_id=? AND evidence_set_hash=?
                         AND review_status IN ('PENDING','REVIEWED')
                       ORDER BY created_at DESC LIMIT 1""",
                    (payload["thesis_id"], payload["evidence_set_hash"]),
                ).fetchone()
                if existing:
                    self._conn.commit()
                    return self._row(existing) or {}, False
                self._conn.execute(
                    """INSERT INTO company_thesis_reviews(
                        review_id,market,stock_code,thesis_id,thesis_version,review_status,
                        current_status,recommended_status,current_confidence,recommended_confidence,
                        support_count,challenge_count,neutral_count,support_evidence_ids_json,
                        challenge_evidence_ids_json,neutral_evidence_ids_json,evidence_set_hash,
                        review_reason,review_summary,trigger_type,trigger_ref,is_stale,
                        created_by,created_at,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        review_id, payload["market"], payload["stock_code"], payload["thesis_id"],
                        payload["thesis_version"], "PENDING", payload["current_status"],
                        payload["recommended_status"], payload["current_confidence"],
                        payload["recommended_confidence"], payload["support_count"],
                        payload["challenge_count"], payload["neutral_count"],
                        json.dumps(payload["support_evidence_ids"], ensure_ascii=False),
                        json.dumps(payload["challenge_evidence_ids"], ensure_ascii=False),
                        json.dumps(payload["neutral_evidence_ids"], ensure_ascii=False),
                        payload["evidence_set_hash"], payload["review_reason"],
                        payload["review_summary"], payload.get("trigger_type") or "MANUAL",
                        payload.get("trigger_ref"), 0, payload.get("created_by") or "SYSTEM",
                        timestamp, json.dumps(payload.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_review_by_id(review_id) or {}, True

    def get_review_by_id(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_thesis_reviews WHERE review_id=?", (review_id,),
            ).fetchone())

    def get_latest_review(self, market: str, stock_code: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_thesis_reviews WHERE market=? AND stock_code=?
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (market, stock_code),
            ).fetchone())

    def list_reviews_for_company(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_reviews WHERE market=? AND stock_code=?
                   ORDER BY created_at DESC,rowid DESC""", (market, stock_code),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def list_reviews_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_reviews WHERE thesis_id=?
                   ORDER BY created_at DESC,rowid DESC""", (thesis_id,),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def find_review_by_evidence_hash(self, thesis_id: str, evidence_set_hash: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_thesis_reviews
                   WHERE thesis_id=? AND evidence_set_hash=?
                     AND review_status IN ('PENDING','REVIEWED')
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (thesis_id, evidence_set_hash),
            ).fetchone())

    def mark_reviewed(self, review_id: str, *, reviewed_by: str = "HUMAN") -> dict[str, Any]:
        return self._transition(review_id, "REVIEWED", actor=reviewed_by)

    def mark_dismissed(self, review_id: str, reason: str, *, dismissed_by: str = "HUMAN") -> dict[str, Any]:
        return self._transition(review_id, "DISMISSED", actor=dismissed_by, reason=reason)

    def mark_applied(self, review_id: str, *, applied_by: str) -> dict[str, Any]:
        """Repository primitive reserved for the future Apply step; no V1 API exposes it."""
        return self._transition(review_id, "APPLIED", actor=applied_by)

    def _transition(self, review_id: str, status: str, *, actor: str, reason: str | None = None) -> dict[str, Any]:
        timestamp = _now()
        columns = {
            "REVIEWED": ("reviewed_at", "reviewed_by"),
            "DISMISSED": ("dismissed_at", "dismissed_by"),
            "APPLIED": ("applied_at", "applied_by"),
        }
        time_column, actor_column = columns[status]
        with self._lock, self._conn:
            current = self._conn.execute(
                "SELECT review_status FROM company_thesis_reviews WHERE review_id=?", (review_id,),
            ).fetchone()
            if not current:
                raise KeyError(review_id)
            if status == "REVIEWED" and current["review_status"] == "REVIEWED":
                return self.get_review_by_id(review_id) or {}
            if current["review_status"] not in OPEN_REVIEW_STATUSES:
                raise ValueError(f"review in {current['review_status']} cannot transition to {status}")
            assignments = f"review_status=?,{time_column}=?,{actor_column}=?"
            values: list[Any] = [status, timestamp, actor]
            if status == "DISMISSED":
                assignments += ",dismissal_reason=?"
                values.append(reason)
            values.append(review_id)
            self._conn.execute(
                f"UPDATE company_thesis_reviews SET {assignments} WHERE review_id=?", values,
            )
        return self.get_review_by_id(review_id) or {}

    def mark_stale_for_thesis(self, thesis_id: str, *, current_evidence_hash: str | None = None) -> int:
        with self._lock, self._conn:
            if current_evidence_hash is None:
                cursor = self._conn.execute(
                    "UPDATE company_thesis_reviews SET is_stale=1 WHERE thesis_id=? AND is_stale=0",
                    (thesis_id,),
                )
            else:
                cursor = self._conn.execute(
                    """UPDATE company_thesis_reviews
                       SET is_stale=CASE WHEN evidence_set_hash=? THEN 0 ELSE 1 END
                       WHERE thesis_id=?""",
                    (current_evidence_hash, thesis_id),
                )
        return int(cursor.rowcount)

    def mark_stale_for_company_except(self, market: str, stock_code: str, thesis_id: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE company_thesis_reviews SET is_stale=1
                   WHERE market=? AND stock_code=? AND thesis_id<>? AND is_stale=0""",
                (market, stock_code, thesis_id),
            )
        return int(cursor.rowcount)

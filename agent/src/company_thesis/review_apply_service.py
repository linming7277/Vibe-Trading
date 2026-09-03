"""Human-only, atomic application of a Company Thesis Review recommendation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .review_service import CompanyThesisReviewService
from .review_store import CompanyThesisReviewRepository
from .service import THESIS_CONFIDENCES, THESIS_STATUSES
from .store import CompanyThesisRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewApplyError(ValueError):
    """Stable, machine-readable Apply rejection code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CompanyThesisReviewApplyService:
    """Apply one PENDING/REVIEWED snapshot in a single SQLite transaction."""

    def __init__(self, *, thesis_repository: CompanyThesisRepository | None = None,
                 db_path: Path | None = None) -> None:
        self.thesis_repository = thesis_repository or CompanyThesisRepository(db_path)
        self._owns_thesis_repository = thesis_repository is None

    def close(self) -> None:
        if self._owns_thesis_repository:
            self.thesis_repository.close()

    def apply_review(self, review_id: str, *, apply_reason: str, applied_by: str = "HUMAN",
                     applied_status: str | None = None,
                     applied_confidence: str | None = None) -> dict[str, Any]:
        review_id = str(review_id or "").strip()
        if not review_id:
            raise ReviewApplyError("REVIEW_NOT_FOUND")
        reason = str(apply_reason or "").strip()
        if not reason:
            raise ReviewApplyError("APPLY_REASON_REQUIRED")
        actor = str(applied_by or "").strip().upper()
        if actor != "HUMAN":
            raise ReviewApplyError("APPLIED_BY_MUST_BE_HUMAN")
        status_override = self._status_override(applied_status)
        confidence_override = self._confidence_override(applied_confidence)

        repository = self.thesis_repository
        with repository._lock:  # The transaction-aware Thesis primitive owns this connection.
            repository._conn.execute("BEGIN IMMEDIATE")
            try:
                review_row = repository._conn.execute(
                    "SELECT * FROM company_thesis_reviews WHERE review_id=?", (review_id,),
                ).fetchone()
                review = CompanyThesisReviewRepository._row(review_row)
                if review is None:
                    raise ReviewApplyError("REVIEW_NOT_FOUND")
                self._validate_review_state(review)
                current_row = repository._conn.execute(
                    """SELECT * FROM company_theses WHERE market=? AND stock_code=? AND is_current=1""",
                    (review["market"], review["stock_code"]),
                ).fetchone()
                current = repository._row(current_row)
                if current is None or current["thesis_id"] != review["thesis_id"]:
                    raise ReviewApplyError("THESIS_CHANGED_SINCE_REVIEW")
                active = self._active_evidence_in_transaction(repository, review["thesis_id"])
                if CompanyThesisReviewService.evidence_set_hash(active) != review["evidence_set_hash"]:
                    repository._conn.execute(
                        "UPDATE company_thesis_reviews SET is_stale=1 WHERE review_id=?", (review_id,),
                    )
                    repository._conn.commit()
                    raise ReviewApplyError("EVIDENCE_CHANGED_SINCE_REVIEW")
                next_status = status_override or review["recommended_status"]
                next_confidence = confidence_override or review["recommended_confidence"]
                evidence_ids = self._review_evidence_snapshot(review)
                metadata = dict(review.get("metadata") or {})
                metadata.update({
                    "recommended_status": review["recommended_status"],
                    "applied_status": next_status,
                    "recommended_confidence": review["recommended_confidence"],
                    "applied_confidence": next_confidence,
                    "human_override": (
                        next_status != review["recommended_status"] or
                        next_confidence != review["recommended_confidence"]
                    ),
                    "apply_reason": reason,
                })
                payload = {
                    "market": current["market"], "stock_code": current["stock_code"],
                    "title": current["title"], "core_thesis": current["core_thesis"],
                    "invalid_conditions": current["invalid_conditions"],
                    "supporting_conditions": current.get("supporting_conditions") or [],
                    "key_metrics_to_monitor": current.get("key_metrics_to_monitor") or [],
                    "authority_status": current.get("authority_status"),
                    "source_draft_id": current.get("source_draft_id"),
                    "status": next_status, "confidence": next_confidence,
                    "created_by": actor, "updated_by": actor,
                    "source_data_as_of": current.get("source_data_as_of"),
                    "change_reason": reason, "evidence_ids": evidence_ids,
                    "trigger_ref": review_id, "history_trigger_type": "THESIS_REVIEW",
                    "history_metadata": metadata,
                }
                created = repository._create_new_version_in_transaction(payload)
                timestamp = _now()
                cursor = repository._conn.execute(
                    """UPDATE company_thesis_reviews
                       SET review_status='APPLIED',applied_at=?,applied_by=?,
                           applied_thesis_id=?,applied_thesis_version=?,metadata_json=?
                       WHERE review_id=? AND review_status IN ('PENDING','REVIEWED')""",
                    (
                        timestamp, actor, created["thesis_id"], created["version"],
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True), review_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("review application update failed")
                repository._conn.commit()
                return {
                    "status": "APPLIED", "review_id": review_id,
                    "previous_thesis_id": current["thesis_id"], "new_thesis_id": created["thesis_id"],
                    "new_version": created["version"], "old_status": current["status"],
                    "new_status": created["status"], "old_confidence": current["confidence"],
                    "new_confidence": created["confidence"],
                }
            except ReviewApplyError:
                if repository._conn.in_transaction:
                    repository._conn.rollback()
                raise
            except Exception:
                if repository._conn.in_transaction:
                    repository._conn.rollback()
                raise

    @staticmethod
    def _validate_review_state(review: dict[str, Any]) -> None:
        status = review["review_status"]
        if status == "APPLIED":
            raise ReviewApplyError("REVIEW_ALREADY_APPLIED")
        if status == "DISMISSED":
            raise ReviewApplyError("REVIEW_DISMISSED")
        if review["is_stale"]:
            raise ReviewApplyError("REVIEW_STALE")
        if status not in {"PENDING", "REVIEWED"}:
            raise ReviewApplyError("REVIEW_NOT_APPLICABLE")

    @staticmethod
    def _active_evidence_in_transaction(repository: CompanyThesisRepository, thesis_id: str) -> list[dict[str, Any]]:
        rows = repository._conn.execute(
            """SELECT * FROM company_thesis_evidence WHERE thesis_id=? AND is_active=1
               ORDER BY created_at DESC,rowid DESC""", (thesis_id,),
        ).fetchall()
        return [CompanyThesisReviewApplyService._evidence_row(row) for row in rows]

    @staticmethod
    def _evidence_row(row: Any) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _review_evidence_snapshot(review: dict[str, Any]) -> list[str]:
        combined = (
            list(review.get("support_evidence_ids") or []) +
            list(review.get("challenge_evidence_ids") or []) +
            list(review.get("neutral_evidence_ids") or [])
        )
        return list(dict.fromkeys(str(item) for item in combined if str(item).strip()))

    @staticmethod
    def _status_override(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        if not normalized:
            return None
        if normalized not in THESIS_STATUSES:
            raise ReviewApplyError("INVALID_APPLIED_STATUS")
        return normalized

    @staticmethod
    def _confidence_override(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        if not normalized:
            return None
        if normalized not in THESIS_CONFIDENCES:
            raise ReviewApplyError("INVALID_APPLIED_CONFIDENCE")
        return normalized


_service: CompanyThesisReviewApplyService | None = None


def get_company_thesis_review_apply_service() -> CompanyThesisReviewApplyService:
    global _service
    if _service is None:
        _service = CompanyThesisReviewApplyService()
    return _service

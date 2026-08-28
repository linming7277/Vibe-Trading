"""Deterministic, recommendation-only Company Thesis Review service."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.research_workspace.store import normalize_market, normalize_symbol

from .evidence_store import CompanyThesisEvidenceRepository
from .review_store import CompanyThesisReviewRepository
from .store import CompanyThesisRepository


CONFIDENCE_UP = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "HIGH"}
CONFIDENCE_DOWN = {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}


class CompanyThesisReviewService:
    """Create explainable Review suggestions without applying them."""

    def __init__(self, *, repository: CompanyThesisReviewRepository | None = None,
                 thesis_repository: CompanyThesisRepository | None = None,
                 evidence_repository: CompanyThesisEvidenceRepository | None = None,
                 db_path: Path | None = None) -> None:
        self.repository = repository or CompanyThesisReviewRepository(db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(self.repository.db_path)
        self.evidence_repository = evidence_repository or CompanyThesisEvidenceRepository(self.repository.db_path)
        self._owns_thesis_repository = thesis_repository is None
        self._owns_evidence_repository = evidence_repository is None

    def close(self) -> None:
        self.repository.close()
        if self._owns_thesis_repository:
            self.thesis_repository.close()
        if self._owns_evidence_repository:
            self.evidence_repository.close()

    @staticmethod
    def evidence_set_hash(evidence: list[dict[str, Any]]) -> str:
        """Hash the exact active evidence snapshot relevant to the deterministic rule."""
        canonical = [{
            "evidence_id": item["evidence_id"],
            "effect": item["effect"],
            "confidence": item["confidence"],
            "claim": item["claim"],
            "summary": item["summary"],
            "updated_at": item["updated_at"],
        } for item in sorted(evidence, key=lambda row: row["evidence_id"])]
        body = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def refresh_current_review(self, market: str, stock_code: str, *,
                               trigger_ref: str | None = None) -> dict[str, Any]:
        """Explicitly create/reuse the current Evidence snapshot's PENDING Review.

        This is recommendation-only.  It intentionally does not call any
        Thesis version, History, or Review Apply path.
        """
        normalized_market, normalized_stock_code = self._company(market, stock_code)
        thesis = self.thesis_repository.get_current_thesis(normalized_market, normalized_stock_code)
        if thesis is None:
            return {"status": "THESIS_NOT_CREATED", "review": None, "created": False}
        active = self.evidence_repository.list_active_evidence_for_thesis(thesis["thesis_id"])
        if not active:
            # A Review with no active supporting material must not be presented
            # as current. Keep the audit rows, but do not create an empty one.
            self.repository.mark_stale_for_thesis(thesis["thesis_id"])
            return {"status": "NO_ACTIVE_EVIDENCE", "review": None, "created": False}
        snapshot_hash = self.evidence_set_hash(active)
        self._refresh_stale(thesis, snapshot_hash)
        existing = self.repository.find_review_by_evidence_hash(thesis["thesis_id"], snapshot_hash)
        if existing is not None:
            return {"status": "EXISTING", "review": existing, "created": False}
        recommendation = self._recommend(thesis, active)
        review, created = self.repository.create_review({
            "market": normalized_market,
            "stock_code": normalized_stock_code,
            "thesis_id": thesis["thesis_id"],
            "thesis_version": thesis["version"],
            "current_status": thesis["status"],
            "recommended_status": recommendation["recommended_status"],
            "current_confidence": thesis["confidence"],
            "recommended_confidence": recommendation["recommended_confidence"],
            "support_count": recommendation["support_count"],
            "challenge_count": recommendation["challenge_count"],
            "neutral_count": recommendation["neutral_count"],
            "support_evidence_ids": recommendation["support_evidence_ids"],
            "challenge_evidence_ids": recommendation["challenge_evidence_ids"],
            "neutral_evidence_ids": recommendation["neutral_evidence_ids"],
            "evidence_set_hash": snapshot_hash,
            "review_reason": recommendation["review_reason"],
            "review_summary": recommendation["review_summary"],
            "trigger_type": "MANUAL",
            "trigger_ref": str(trigger_ref).strip() if trigger_ref else None,
            "created_by": "SYSTEM",
            "metadata": {
                "rule_version": "company-thesis-review-v1", "evidence_weighting": "UNWEIGHTED",
                "evidence_source_summary": self._source_summary(active),
            },
        })
        return {"status": "CREATED" if created else "EXISTING", "review": review, "created": created}

    def review_current_thesis(self, market: str, stock_code: str, *,
                              trigger_ref: str | None = None) -> dict[str, Any]:
        """Backward-compatible name for explicit current Review refresh."""
        return self.refresh_current_review(market, stock_code, trigger_ref=trigger_ref)

    def get_latest_review(self, market: str, stock_code: str) -> dict[str, Any]:
        normalized_market, normalized_stock_code = self._company(market, stock_code)
        self._refresh_current_company(normalized_market, normalized_stock_code)
        review = self.repository.get_latest_review(normalized_market, normalized_stock_code)
        return {"status": "NOT_REVIEWED" if review is None else "OK", "review": review}

    def list_reviews(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        normalized_market, normalized_stock_code = self._company(market, stock_code)
        self._refresh_current_company(normalized_market, normalized_stock_code)
        return self.repository.list_reviews_for_company(normalized_market, normalized_stock_code)

    def list_reviews_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]:
        return self.repository.list_reviews_for_thesis(str(thesis_id or "").strip())

    def mark_reviewed(self, review_id: str, *, reviewed_by: str = "HUMAN") -> dict[str, Any]:
        return self.repository.mark_reviewed(
            str(review_id or "").strip(), reviewed_by=self._human_actor(reviewed_by),
        )

    def dismiss_review(self, review_id: str, reason: str, *, dismissed_by: str = "HUMAN") -> dict[str, Any]:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("dismissal reason is required")
        return self.repository.mark_dismissed(
            str(review_id or "").strip(), normalized_reason,
            dismissed_by=self._human_actor(dismissed_by),
        )

    def _refresh_current_company(self, market: str, stock_code: str) -> None:
        thesis = self.thesis_repository.get_current_thesis(market, stock_code)
        if thesis is None:
            return
        active = self.evidence_repository.list_active_evidence_for_thesis(thesis["thesis_id"])
        self._refresh_stale(thesis, self.evidence_set_hash(active))

    def _refresh_stale(self, thesis: dict[str, Any], snapshot_hash: str) -> None:
        self.repository.mark_stale_for_company_except(
            thesis["market"], thesis["stock_code"], thesis["thesis_id"],
        )
        self.repository.mark_stale_for_thesis(
            thesis["thesis_id"], current_evidence_hash=snapshot_hash,
        )

    @staticmethod
    def _source_summary(evidence: list[dict[str, Any]]) -> dict[str, int]:
        """Audit the origin of all active evidence without influencing rules."""
        return dict(sorted(Counter(str(item.get("created_by") or "UNKNOWN") for item in evidence).items()))

    @staticmethod
    def _recommend(thesis: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        grouped = {
            effect: [item for item in evidence if item["effect"] == effect]
            for effect in ("SUPPORT", "CHALLENGE", "NEUTRAL")
        }
        support_count = len(grouped["SUPPORT"])
        challenge_count = len(grouped["CHALLENGE"])
        neutral_count = len(grouped["NEUTRAL"])
        strong_support = support_count >= 3 and challenge_count == 0
        strong_challenge = challenge_count >= 2 and challenge_count > support_count
        balanced = support_count > 0 and challenge_count > 0
        no_signal = support_count == 0 and challenge_count == 0

        current_status = thesis["status"]
        current_confidence = thesis["confidence"]
        if current_status == "FALSIFIED":
            recommended_status = "FALSIFIED"
            recommended_confidence = current_confidence
        elif strong_challenge:
            recommended_status = "WEAKENING"
            recommended_confidence = CONFIDENCE_DOWN[current_confidence]
        elif strong_support:
            recommended_status = "UNCHANGED" if current_status == "FORMING" else "STRENGTHENING"
            recommended_confidence = CONFIDENCE_UP[current_confidence]
        else:
            recommended_status = current_status if no_signal else "UNCHANGED" if balanced else current_status
            recommended_confidence = current_confidence

        if not evidence:
            reason = "NO_ACTIVE_EVIDENCE"
        else:
            reason = (
                f"当前存在 {support_count} 条支持证据、{challenge_count} 条挑战证据、"
                f"{neutral_count} 条中性证据；建议 Thesis 状态由 {current_status} 调整为 "
                f"{recommended_status}，confidence 由 {current_confidence} 调整为 {recommended_confidence}。"
            )
        support_claims = "；".join(item["claim"] for item in grouped["SUPPORT"][:3]) or "无"
        challenge_claims = "；".join(item["claim"] for item in grouped["CHALLENGE"][:3]) or "无"
        summary = (
            f"Thesis v{thesis['version']}《{thesis['title']}》当前为 {current_status}/{current_confidence}。"
            f"Active Evidence：SUPPORT {support_count}、CHALLENGE {challenge_count}、NEUTRAL {neutral_count}。"
            f"建议 {recommended_status}/{recommended_confidence}。主要支持：{support_claims}。"
            f"主要挑战：{challenge_claims}。"
        )
        return {
            "recommended_status": recommended_status,
            "recommended_confidence": recommended_confidence,
            "support_count": support_count,
            "challenge_count": challenge_count,
            "neutral_count": neutral_count,
            "support_evidence_ids": [item["evidence_id"] for item in grouped["SUPPORT"]],
            "challenge_evidence_ids": [item["evidence_id"] for item in grouped["CHALLENGE"]],
            "neutral_evidence_ids": [item["evidence_id"] for item in grouped["NEUTRAL"]],
            "review_reason": reason,
            "review_summary": summary,
        }

    @staticmethod
    def _company(market: str, stock_code: str) -> tuple[str, str]:
        normalized_market = normalize_market(market)
        return normalized_market, normalize_symbol(normalized_market, stock_code)

    @staticmethod
    def _human_actor(actor: str) -> str:
        normalized = str(actor or "").strip().upper()
        if normalized != "HUMAN":
            raise ValueError("Review handling must be performed by HUMAN")
        return normalized


_service: CompanyThesisReviewService | None = None


def get_company_thesis_review_service() -> CompanyThesisReviewService:
    global _service
    if _service is None:
        _service = CompanyThesisReviewService()
    return _service

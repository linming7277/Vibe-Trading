"""Validation, attribution and summary rules for Company Thesis Evidence V1."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from src.research_workspace.store import normalize_market, normalize_symbol

from .evidence_store import CompanyThesisEvidenceRepository
from .store import CompanyThesisRepository


EVIDENCE_TYPES = {"FINANCIAL", "BUSINESS", "INDUSTRY", "MACRO_POLICY", "VALUATION", "RISK", "MANAGEMENT", "OTHER"}
EVIDENCE_EFFECTS = {"SUPPORT", "CHALLENGE", "NEUTRAL"}
EVIDENCE_CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
SOURCE_TYPES = {"TDX", "FINANCIAL_SNAPSHOT", "COMPANY_RESEARCH_SNAPSHOT", "FINANCIAL_ANALYSIS", "COMPANY_DOSSIER", "MANUAL", "EXTERNAL", "SYSTEM"}
EVIDENCE_ACTORS = {"HUMAN", "SYSTEM", "AGENT_FINANCIAL", "AGENT_COMPANY", "AGENT_RISK", "AGENT_VALUATION", "AGENT_MACRO_POLICY", "AGENT_RESEARCH_LEAD"}


class CompanyThesisEvidenceService:
    def __init__(self, *, repository: CompanyThesisEvidenceRepository | None = None,
                 thesis_repository: CompanyThesisRepository | None = None,
                 db_path: Path | None = None) -> None:
        self.repository = repository or CompanyThesisEvidenceRepository(db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(self.repository.db_path)
        self._owns_thesis_repository = thesis_repository is None

    def close(self) -> None:
        self.repository.close()
        if self._owns_thesis_repository:
            self.thesis_repository.close()

    def create_evidence(self, *, thesis_id: str, evidence_type: str, effect: str,
                        claim: str, summary: str, source_type: str, confidence: str,
                        market: str | None = None, stock_code: str | None = None,
                        source_id: str | None = None, source_ref: str | None = None,
                        source_title: str | None = None, source_date: str | None = None,
                        data_as_of: str | None = None, created_by: str = "HUMAN",
                        metadata: dict[str, Any] | None = None,
                        evidence_fingerprint: str | None = None) -> dict[str, Any]:
        thesis = self.thesis_repository.get_thesis_by_id(str(thesis_id or "").strip())
        if thesis is None:
            raise KeyError("thesis not found")
        thesis_market, thesis_stock_code = thesis["market"], thesis["stock_code"]
        if market is not None or stock_code is not None:
            if market is None or stock_code is None:
                raise ValueError("market and stock_code must be provided together")
            candidate_market = normalize_market(market)
            candidate_stock_code = normalize_symbol(candidate_market, stock_code)
            if (candidate_market, candidate_stock_code) != (thesis_market, thesis_stock_code):
                raise ValueError("evidence company must match thesis company")
        normalized_type = self._enum("evidence_type", evidence_type, EVIDENCE_TYPES)
        normalized_effect = self._enum("effect", effect, EVIDENCE_EFFECTS)
        normalized_confidence = self._enum("confidence", confidence, EVIDENCE_CONFIDENCES)
        normalized_source_type = self._enum("source_type", source_type, SOURCE_TYPES)
        actor = self._enum("created_by", created_by, EVIDENCE_ACTORS)
        normalized_claim, normalized_summary = str(claim or "").strip(), str(summary or "").strip()
        if not normalized_claim:
            raise ValueError("claim is required")
        if not normalized_summary:
            raise ValueError("summary is required")
        references = [str(value or "").strip() for value in (source_id, source_ref, source_title)]
        if normalized_source_type != "MANUAL" and not any(references):
            raise ValueError("source_id, source_ref or source_title is required for non-manual evidence")
        if normalized_source_type == "MANUAL" and actor != "HUMAN":
            raise ValueError("MANUAL evidence must be created_by HUMAN")
        if not isinstance(metadata or {}, dict):
            raise ValueError("metadata must be an object")
        fingerprint = str(evidence_fingerprint or "").strip() or None
        if fingerprint is not None and len(fingerprint) > 128:
            raise ValueError("evidence_fingerprint is too long")
        return self.repository.create_evidence({
            "thesis_id": thesis["thesis_id"], "market": thesis_market, "stock_code": thesis_stock_code,
            "evidence_type": normalized_type, "effect": normalized_effect,
            "claim": normalized_claim, "summary": normalized_summary,
            "source_type": normalized_source_type,
            "source_id": references[0] or None, "source_ref": references[1] or None,
            "source_title": references[2] or None,
            "source_date": str(source_date).strip() if source_date else None,
            "data_as_of": str(data_as_of).strip() if data_as_of else None,
            "confidence": normalized_confidence, "created_by": actor, "metadata": metadata or {},
            "evidence_fingerprint": fingerprint,
        })

    def get_evidence_summary(self, thesis_id: str) -> dict[str, Any]:
        evidence = self.repository.list_evidence_for_thesis(thesis_id)
        active = [item for item in evidence if item["is_active"]]
        effects = Counter(item["effect"] for item in active)
        types = Counter(item["evidence_type"] for item in active)
        return {
            "total": len(evidence), "active": len(active),
            "support": effects["SUPPORT"], "challenge": effects["CHALLENGE"],
            "neutral": effects["NEUTRAL"], "by_type": dict(sorted(types.items())),
        }

    def deactivate_evidence(self, evidence_id: str, reason: str, *, deactivated_by: str = "HUMAN") -> dict[str, Any]:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("deactivation reason is required")
        actor = self._enum("deactivated_by", deactivated_by, EVIDENCE_ACTORS)
        return self.repository.deactivate_evidence(str(evidence_id or "").strip(), normalized_reason, deactivated_by=actor)

    def current_thesis_evidence(self, market: str, stock_code: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        normalized_stock_code = normalize_symbol(normalized_market, stock_code)
        thesis = self.thesis_repository.get_current_thesis(normalized_market, normalized_stock_code)
        if thesis is None:
            return {"status": "THESIS_NOT_CREATED", "current_thesis": None, "evidence": [], "summary": None}
        evidence = self.repository.list_evidence_for_thesis(thesis["thesis_id"])
        return {"status": "OK", "current_thesis": thesis, "evidence": evidence,
                "summary": self.get_evidence_summary(thesis["thesis_id"])}

    @staticmethod
    def _enum(label: str, value: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().upper()
        if normalized not in allowed:
            raise ValueError(f"invalid {label}: {value}")
        return normalized


_service: CompanyThesisEvidenceService | None = None


def get_company_thesis_evidence_service() -> CompanyThesisEvidenceService:
    global _service
    if _service is None:
        _service = CompanyThesisEvidenceService()
    return _service

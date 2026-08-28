"""HTTP API for Company Thesis Evidence V1."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.company_thesis.evidence_service import (
    CompanyThesisEvidenceService,
    get_company_thesis_evidence_service,
)
from src.company_thesis.evidence_extractor_service import (
    CompanyThesisEvidenceExtractorService,
    get_company_thesis_evidence_extractor_service,
)
from src.company_thesis.financial_evidence_service import (
    CompanyThesisFinancialEvidenceService,
    get_company_thesis_financial_evidence_service,
)
from src.company_thesis.business_evidence_service import (
    CompanyThesisBusinessEvidenceService,
    get_company_thesis_business_evidence_service,
)

AuthDep = Callable[..., Awaitable[Any] | Any]


class EvidenceCreateRequest(BaseModel):
    evidence_type: str
    effect: str
    claim: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_type: str
    source_id: str | None = None
    source_ref: str | None = None
    source_title: str | None = None
    source_date: str | None = None
    data_as_of: str | None = None
    confidence: str
    created_by: str = "HUMAN"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceDeactivateRequest(BaseModel):
    reason: str = Field(min_length=1)
    deactivated_by: str = "HUMAN"


def _service() -> CompanyThesisEvidenceService:
    return get_company_thesis_evidence_service()


def _extractor() -> CompanyThesisEvidenceExtractorService:
    return get_company_thesis_evidence_extractor_service()


def _financial_extractor() -> CompanyThesisFinancialEvidenceService:
    return get_company_thesis_financial_evidence_service()


def _business_extractor() -> CompanyThesisBusinessEvidenceService:
    return get_company_thesis_business_evidence_service()


def register_company_thesis_evidence_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/thesis/evidence", dependencies=auth)
    async def current_company_thesis_evidence(stock_code: str, market: str = Query(default="CN")):
        try:
            return await asyncio.to_thread(_service().current_thesis_evidence, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/thesis-evidence/extract/{stock_code}", dependencies=auth)
    async def extract_company_thesis_evidence(stock_code: str, market: str = Query(default="CN")):
        """Manually extract deterministic evidence for one company; no Thesis/Review apply."""
        try:
            return await asyncio.to_thread(_extractor().extract_for_company, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/thesis/evidence/from-financial-agent", dependencies=auth)
    async def extract_financial_agent_thesis_evidence(stock_code: str, market: str = Query(default="CN")):
        """Extract verified Financial Agent FACT/INFERENCE Claims for one current Thesis."""
        try:
            return await asyncio.to_thread(
                _financial_extractor().extract_from_latest_financial_analysis, market, stock_code,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/thesis/evidence/from-business-research", dependencies=auth)
    async def extract_business_research_thesis_evidence(stock_code: str, market: str = Query(default="CN")):
        """Extract verified Business Research Claims for one current Thesis."""
        try:
            return await asyncio.to_thread(
                _business_extractor().extract_from_latest_business_research, market, stock_code,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/thesis-evidence/extract-current-pool", dependencies=auth)
    async def extract_current_pool_thesis_evidence():
        """Batch extraction for active/new/reentered current-pool companies only."""
        return await asyncio.to_thread(_extractor().extract_current_pool)

    @app.get("/api/value/theses/{thesis_id}/evidence", dependencies=auth)
    async def thesis_evidence(thesis_id: str):
        try:
            thesis = _service().thesis_repository.get_thesis_by_id(thesis_id)
            if thesis is None:
                raise HTTPException(404, "thesis not found")
            evidence = await asyncio.to_thread(_service().repository.list_evidence_for_thesis, thesis_id)
            summary = await asyncio.to_thread(_service().get_evidence_summary, thesis_id)
            return {"thesis": thesis, "evidence": evidence, "summary": summary}
        except HTTPException:
            raise

    @app.post("/api/value/theses/{thesis_id}/evidence", dependencies=auth, status_code=201)
    async def create_thesis_evidence(thesis_id: str, payload: EvidenceCreateRequest):
        try:
            return await asyncio.to_thread(
                _service().create_evidence,
                thesis_id=thesis_id, evidence_type=payload.evidence_type, effect=payload.effect,
                claim=payload.claim, summary=payload.summary, source_type=payload.source_type,
                source_id=payload.source_id, source_ref=payload.source_ref,
                source_title=payload.source_title, source_date=payload.source_date,
                data_as_of=payload.data_as_of, confidence=payload.confidence,
                created_by=payload.created_by, metadata=payload.metadata,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/api/value/thesis-evidence/{evidence_id}/deactivate", dependencies=auth)
    async def deactivate_thesis_evidence(evidence_id: str, payload: EvidenceDeactivateRequest):
        try:
            return await asyncio.to_thread(
                _service().deactivate_evidence, evidence_id, payload.reason,
                deactivated_by=payload.deactivated_by,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

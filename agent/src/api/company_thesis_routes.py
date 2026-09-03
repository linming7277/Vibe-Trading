"""Minimal HTTP API for the durable Company Thesis V1 object."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.company_thesis.service import CompanyThesisService, get_company_thesis_service
from src.company_thesis.draft_service import CompanyThesisDraftService, get_company_thesis_draft_service

AuthDep = Callable[..., Awaitable[Any] | Any]


class ThesisCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    core_thesis: str = Field(min_length=1)
    status: str
    confidence: str
    invalid_conditions: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str = "HUMAN"
    source_data_as_of: str | None = None


class ThesisVersionRequest(ThesisCreateRequest):
    change_reason: str = Field(min_length=1)
    updated_by: str = "HUMAN"
    evidence_ids: list[str] = Field(default_factory=list)
    trigger_ref: str | None = None
    history_metadata: dict[str, Any] = Field(default_factory=dict)


class ThesisDraftConfirmRequest(ThesisCreateRequest):
    """Human-edited content used to explicitly promote a draft to a Thesis."""
    supporting_conditions: list[dict[str, Any]] | None = None
    key_metrics_to_monitor: list[Any] | None = None


class ThesisDraftRejectRequest(BaseModel):
    reason: str = Field(default="人工未采纳", min_length=1, max_length=500)


def _service() -> CompanyThesisService:
    return get_company_thesis_service()


def _draft_service() -> CompanyThesisDraftService:
    return get_company_thesis_draft_service()


def register_company_thesis_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/thesis", dependencies=auth)
    async def get_current_company_thesis(stock_code: str, market: str = Query(default="CN")):
        try:
            thesis = await asyncio.to_thread(_service().get_current_thesis, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"status": "NOT_CREATED", "thesis": None} if thesis is None else {"status": "OK", "thesis": thesis}

    @app.get("/api/value/companies/{stock_code}/thesis/draft", dependencies=auth)
    async def get_company_thesis_draft(stock_code: str, market: str = Query(default="CN")):
        try:
            draft = await asyncio.to_thread(_draft_service().get_latest, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"status": "NOT_CREATED", "draft": None} if draft is None else {"status": "OK", "draft": draft}

    @app.post("/api/value/companies/{stock_code}/thesis/draft", dependencies=auth)
    async def generate_company_thesis_draft(stock_code: str, market: str = Query(default="CN"), research_as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(_draft_service().generate, market, stock_code, research_as_of=research_as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/thesis/draft/{draft_id}/confirm", dependencies=auth, status_code=201)
    async def confirm_company_thesis_draft(stock_code: str, draft_id: str, payload: ThesisDraftConfirmRequest,
                                           market: str = Query(default="CN")):
        try:
            # The service owns the draft's company key.  This check prevents a
            # valid draft from being confirmed through another company's URL.
            draft = await asyncio.to_thread(_draft_service().get, market, stock_code, draft_id)
            if not draft:
                raise KeyError("thesis draft not found for company")
            return await asyncio.to_thread(
                _draft_service().confirm, draft_id, title=payload.title,
                core_thesis=payload.core_thesis, status=payload.status,
                confidence=payload.confidence, invalid_conditions=payload.invalid_conditions,
                supporting_conditions=payload.supporting_conditions,
                key_metrics_to_monitor=payload.key_metrics_to_monitor,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(409 if "current thesis already exists" in message else 422, message) from exc

    @app.post("/api/value/companies/{stock_code}/thesis/draft/{draft_id}/reject", dependencies=auth)
    async def reject_company_thesis_draft(stock_code: str, draft_id: str, payload: ThesisDraftRejectRequest,
                                          market: str = Query(default="CN")):
        try:
            draft = await asyncio.to_thread(_draft_service().get, market, stock_code, draft_id)
            if not draft:
                raise KeyError("thesis draft not found for company")
            return await asyncio.to_thread(_draft_service().reject, draft_id, reason=payload.reason)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    # V1 aliases use the product term “thesis draft” while retaining legacy
    # routes for the already-shipped company-research panel.
    @app.get("/api/value/companies/{stock_code}/thesis-draft", dependencies=auth)
    async def get_company_thesis_draft_v1(stock_code: str, market: str = Query(default="CN")):
        return await get_company_thesis_draft(stock_code, market)

    @app.post("/api/value/companies/{stock_code}/thesis-draft/generate", dependencies=auth)
    async def generate_company_thesis_draft_v1(stock_code: str, market: str = Query(default="CN"), research_as_of: str | None = Query(default=None)):
        return await generate_company_thesis_draft(stock_code, market, research_as_of)

    @app.post("/api/value/companies/{stock_code}/thesis-draft/promote-provisional", dependencies=auth, status_code=201)
    async def promote_thesis_draft_to_provisional(stock_code: str, market: str = Query(default="CN"), research_as_of: str | None = Query(default=None)):
        return await asyncio.to_thread(_draft_service().promote_to_provisional, market, stock_code, research_as_of=research_as_of)

    @app.post("/api/value/companies/{stock_code}/thesis-draft/approve", dependencies=auth, status_code=201)
    async def approve_company_thesis_draft_v1(stock_code: str, payload: ThesisDraftConfirmRequest,
                                               draft_id: str = Query(min_length=1), market: str = Query(default="CN")):
        return await confirm_company_thesis_draft(stock_code, draft_id, payload, market)

    @app.post("/api/value/companies/{stock_code}/thesis-draft/reject", dependencies=auth)
    async def reject_company_thesis_draft_v1(stock_code: str, payload: ThesisDraftRejectRequest,
                                              draft_id: str = Query(min_length=1), market: str = Query(default="CN")):
        return await reject_company_thesis_draft(stock_code, draft_id, payload, market)

    @app.get("/api/value/companies/{stock_code}/thesis/versions", dependencies=auth)
    async def list_company_thesis_versions(stock_code: str, market: str = Query(default="CN")):
        try:
            items = await asyncio.to_thread(_service().list_thesis_versions, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"items": items, "total": len(items)}

    @app.post("/api/value/companies/{stock_code}/thesis", dependencies=auth, status_code=201)
    async def create_company_thesis(stock_code: str, payload: ThesisCreateRequest,
                                    market: str = Query(default="CN")):
        try:
            thesis = await asyncio.to_thread(
                _service().create_initial_thesis,
                market=market, stock_code=stock_code, title=payload.title,
                core_thesis=payload.core_thesis, status=payload.status,
                confidence=payload.confidence, invalid_conditions=payload.invalid_conditions,
                created_by=payload.created_by, source_data_as_of=payload.source_data_as_of,
            )
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(409 if message.startswith("current thesis already exists") else 422, message) from exc
        return thesis

    @app.post("/api/value/companies/{stock_code}/thesis/version", dependencies=auth, status_code=201)
    async def create_company_thesis_version(stock_code: str, payload: ThesisVersionRequest,
                                            market: str = Query(default="CN")):
        try:
            thesis = await asyncio.to_thread(
                _service().create_new_version,
                market=market, stock_code=stock_code, title=payload.title,
                core_thesis=payload.core_thesis, status=payload.status,
                confidence=payload.confidence, invalid_conditions=payload.invalid_conditions,
                change_reason=payload.change_reason, updated_by=payload.updated_by,
                source_data_as_of=payload.source_data_as_of,
                evidence_ids=payload.evidence_ids, trigger_ref=payload.trigger_ref,
                history_metadata=payload.history_metadata,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return thesis

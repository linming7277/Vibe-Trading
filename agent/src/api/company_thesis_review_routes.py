"""HTTP API for recommendation-only Company Thesis Review V1."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.company_thesis.review_service import (
    CompanyThesisReviewService,
    get_company_thesis_review_service,
)
from src.company_thesis.review_apply_service import (
    CompanyThesisReviewApplyService, ReviewApplyError, get_company_thesis_review_apply_service,
)

AuthDep = Callable[..., Awaitable[Any] | Any]


class ReviewHandledRequest(BaseModel):
    handled_by: str = "HUMAN"


class ReviewDismissRequest(BaseModel):
    reason: str = Field(min_length=1)
    dismissed_by: str = "HUMAN"


class ReviewApplyRequest(BaseModel):
    applied_status: str | None = None
    applied_confidence: str | None = None
    apply_reason: str = Field(min_length=1)
    applied_by: str = "HUMAN"


def _service() -> CompanyThesisReviewService:
    return get_company_thesis_review_service()


def _apply_service() -> CompanyThesisReviewApplyService:
    return get_company_thesis_review_apply_service()


def register_company_thesis_review_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.post("/api/value/companies/{stock_code}/thesis/review", dependencies=auth)
    async def create_company_thesis_review(
        stock_code: str,
        market: str = Query(default="CN"),
        trigger_ref: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                _service().refresh_current_review, market, stock_code, trigger_ref=trigger_ref,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/companies/{stock_code}/thesis/review", dependencies=auth)
    async def get_latest_company_thesis_review(stock_code: str, market: str = Query(default="CN")):
        try:
            return await asyncio.to_thread(_service().get_latest_review, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/companies/{stock_code}/thesis/reviews", dependencies=auth)
    async def list_company_thesis_reviews(stock_code: str, market: str = Query(default="CN")):
        try:
            items = await asyncio.to_thread(_service().list_reviews, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"items": items, "total": len(items)}

    @app.patch("/api/value/thesis-reviews/{review_id}/reviewed", dependencies=auth)
    async def mark_company_thesis_reviewed(
        review_id: str, payload: ReviewHandledRequest | None = None,
    ):
        try:
            return await asyncio.to_thread(
                _service().mark_reviewed, review_id,
                reviewed_by=payload.handled_by if payload else "HUMAN",
            )
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.patch("/api/value/thesis-reviews/{review_id}/dismiss", dependencies=auth)
    async def dismiss_company_thesis_review(review_id: str, payload: ReviewDismissRequest):
        try:
            return await asyncio.to_thread(
                _service().dismiss_review, review_id, payload.reason,
                dismissed_by=payload.dismissed_by,
            )
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/value/thesis-reviews/{review_id}/apply", dependencies=auth)
    async def apply_company_thesis_review(review_id: str, payload: ReviewApplyRequest):
        try:
            return await asyncio.to_thread(
                _apply_service().apply_review, review_id, apply_reason=payload.apply_reason,
                applied_by=payload.applied_by, applied_status=payload.applied_status,
                applied_confidence=payload.applied_confidence,
            )
        except ReviewApplyError as exc:
            status_code = 404 if exc.code == "REVIEW_NOT_FOUND" else 422 if exc.code in {
                "INVALID_APPLIED_STATUS", "INVALID_APPLIED_CONFIDENCE", "APPLY_REASON_REQUIRED",
                "APPLIED_BY_MUST_BE_HUMAN",
            } else 409
            raise HTTPException(status_code, exc.code) from exc

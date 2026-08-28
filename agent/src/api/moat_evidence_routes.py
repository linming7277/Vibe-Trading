"""Read-only moat-evidence audit API plus explicit local extraction trigger."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.moat_evidence.service import get_moat_evidence_extraction_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_moat_evidence_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/moat-evidence", dependencies=auth)
    async def get_moat_evidence(
        stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None),
        dimension: str | None = Query(default=None), evidence_type: str | None = Query(default=None), active: bool | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_moat_evidence_extraction_service().get_evidence,
                market, stock_code, as_of=as_of, dimension=dimension, evidence_type=evidence_type, active=active,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/moat-evidence/extract", dependencies=auth)
    async def extract_moat_evidence(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        """Explicitly structure already-local evidence; never downloads or calls an LLM."""
        try:
            return await asyncio.to_thread(
                get_moat_evidence_extraction_service().extract, market, stock_code, as_of=as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

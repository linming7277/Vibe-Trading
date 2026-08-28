"""Authenticated Business Research APIs for the existing Financial Researcher."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from src.business_research.service import get_business_research_service


AuthDep = Callable[..., Awaitable[Any] | Any]


class BusinessAnalyzeRequest(BaseModel):
    force: bool = False


def register_business_research_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/business-research", dependencies=auth)
    async def get_company_business_research(stock_code: str):
        try:
            return await asyncio.to_thread(get_business_research_service().get, stock_code)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/business-research/analyze", dependencies=auth)
    async def analyze_company_business_research(stock_code: str, payload: BusinessAnalyzeRequest):
        try:
            return await asyncio.to_thread(
                get_business_research_service().analyze, stock_code, force=payload.force,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

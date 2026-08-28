"""Read-only Company Research Overview API."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.company_research.overview_service import get_company_research_overview_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_company_research_overview_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/research-overview", dependencies=auth)
    async def get_company_research_overview(stock_code: str, market: str = Query(default="CN")):
        try:
            return await asyncio.to_thread(
                get_company_research_overview_service().get_overview, market, stock_code,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

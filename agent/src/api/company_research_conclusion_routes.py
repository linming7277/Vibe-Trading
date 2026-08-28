"""Read-only company research conclusion endpoint."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.company_research.conclusion_service import get_company_research_conclusion_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_company_research_conclusion_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/research-conclusion", dependencies=auth)
    async def get_research_conclusion(stock_code: str, market: str = Query(default="CN")):
        try:
            return await asyncio.to_thread(
                get_company_research_conclusion_service().get_conclusion, market, stock_code,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

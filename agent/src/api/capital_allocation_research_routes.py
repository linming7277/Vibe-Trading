"""Read-only capital-allocation research endpoint."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.capital_allocation_research import get_capital_allocation_research_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_capital_allocation_research_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/companies/{stock_code}/capital-allocation-research", dependencies=[Depends(require_auth)])
    async def capital_allocation_research(
        stock_code: str,
        market: str = Query(default="CN"),
        as_of: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_capital_allocation_research_service().get_research, market, stock_code, as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

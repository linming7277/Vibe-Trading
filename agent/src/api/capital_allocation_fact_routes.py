"""Read-only capital-allocation facts endpoint."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.capital_allocation_facts import get_capital_allocation_fact_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_capital_allocation_fact_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/companies/{stock_code}/capital-allocation-facts", dependencies=[Depends(require_auth)])
    async def capital_allocation_facts(
        stock_code: str,
        market: str = Query(default="CN"),
        as_of: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_capital_allocation_fact_service().get_history, market, stock_code, as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

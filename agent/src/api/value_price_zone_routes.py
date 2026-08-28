"""Read-only Value Line valuation and historical price-zone endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.value_price_zones import get_value_price_zone_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_value_price_zone_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/price-zones", dependencies=auth)
    async def get_price_zones(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_value_price_zone_service().get_price_zones, market, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/price-zones/rebuild", dependencies=auth)
    async def rebuild_price_zones(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        """Explicitly recompute the deterministic projection from existing cache.

        No market fetch, LLM invocation or database write is performed.  The
        endpoint exists so a future persisted cache can remain an explicit user
        action without changing the public contract.
        """
        try:
            return await asyncio.to_thread(get_value_price_zone_service().get_price_zones, market, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

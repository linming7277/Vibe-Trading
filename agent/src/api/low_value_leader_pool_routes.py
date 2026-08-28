"""API for the persisted, automatic low-value L3 leader pool."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Query

from src.low_value_leader_pool import get_low_value_leader_pool_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_low_value_leader_pool_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/low-value-leaders", dependencies=auth)
    async def low_value_leaders():
        return await asyncio.to_thread(get_low_value_leader_pool_service().active_low_value_leaders)

    @app.get("/api/value/low-value-leaders/history", dependencies=auth)
    async def low_value_leader_history(stock_code: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
        return await asyncio.to_thread(
            get_low_value_leader_pool_service().low_value_leader_history,
            stock_code=stock_code, limit=limit,
        )

    @app.get("/api/value/low-value-leader-events", dependencies=auth)
    async def low_value_leader_events(limit: int = Query(default=20, ge=1, le=500)):
        """Read recent persisted low-value entrance and exit changes."""
        return await asyncio.to_thread(
            get_low_value_leader_pool_service().low_value_leader_events,
            limit=limit,
        )

    @app.post("/api/value/low-value-leaders/refresh", dependencies=auth)
    async def refresh_low_value_leaders(as_of: str | None = None):
        """Scheduler/operations endpoint; it is intentionally not used by any page."""
        return await asyncio.to_thread(
            get_low_value_leader_pool_service().refresh_low_value_leader_pool,
            as_of=as_of,
        )

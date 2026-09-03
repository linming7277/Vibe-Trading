"""Read-only unified Value Line strategy-state and watchpoint APIs."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.value_strategy import get_value_strategy_state_service
from src.value_watchpoints import get_value_watchpoint_projection_service

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_value_strategy_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/companies/{stock_code}/strategy-state", dependencies=[Depends(require_auth)])
    async def get_value_strategy_state(
        stock_code: str,
        market: str = Query(default="CN"),
        research_as_of: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_value_strategy_state_service().get_strategy_state,
                market,
                stock_code,
                research_as_of=research_as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/companies/{stock_code}/watchpoints", dependencies=[Depends(require_auth)])
    async def get_value_watchpoints(
        stock_code: str,
        market: str = Query(default="CN"),
        research_as_of: str | None = Query(default=None),
        limit: int | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_value_watchpoint_projection_service().get_watchpoints,
                market,
                stock_code,
                research_as_of,
                limit,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

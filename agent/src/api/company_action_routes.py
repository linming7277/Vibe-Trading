"""Company-action event endpoints: read-only queries plus explicit local preparation."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.company_actions import get_company_action_event_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_company_action_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/company-actions", dependencies=auth)
    async def company_actions(
        stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None),
        event_type: str | None = Query(default=None), start_date: str | None = Query(default=None), end_date: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_company_action_event_service().get_events, market, stock_code,
                as_of=as_of, event_type=event_type, start_date=start_date, end_date=end_date,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/company-actions/prepare", dependencies=auth)
    async def prepare_company_actions(stock_code: str, market: str = Query(default="CN")):
        """Explicitly persist actions from existing local TDX cache only."""
        try:
            return await asyncio.to_thread(get_company_action_event_service().prepare_from_cached_details, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

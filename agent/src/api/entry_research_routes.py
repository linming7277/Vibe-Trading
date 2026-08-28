"""Read-only Value Line entry-research endpoint."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.entry_research import get_entry_research_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_entry_research_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/entry-research", dependencies=auth)
    async def get_entry_research(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(
                get_entry_research_service().get_entry_research, market, stock_code, as_of=as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

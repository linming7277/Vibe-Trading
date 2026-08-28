"""Read-only competition-advantage research API."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.moat_research.service import get_moat_research_service

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_moat_research_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/companies/{stock_code}/moat-research", dependencies=[Depends(require_auth)])
    async def get_moat_research(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_moat_research_service().get_research, market, stock_code, as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

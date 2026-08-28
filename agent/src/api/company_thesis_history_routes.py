"""Read-only API for Company Thesis version-change history."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.company_thesis.history_service import (
    CompanyThesisHistoryService,
    get_company_thesis_history_service,
)

AuthDep = Callable[..., Awaitable[Any] | Any]


def _service() -> CompanyThesisHistoryService:
    return get_company_thesis_history_service()


def register_company_thesis_history_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/thesis/history", dependencies=auth)
    async def company_thesis_history(stock_code: str, market: str = Query(default="CN")):
        try:
            items = await asyncio.to_thread(_service().list_history_for_company, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"items": items, "total": len(items)}

    @app.get("/api/value/theses/{thesis_id}/history", dependencies=auth)
    async def thesis_history(thesis_id: str):
        items = await asyncio.to_thread(_service().list_history_for_thesis, thesis_id)
        return {"items": items, "total": len(items)}

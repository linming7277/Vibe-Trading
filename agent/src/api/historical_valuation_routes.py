"""PIT historical valuation endpoints for Value Line."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.historical_valuation import get_historical_valuation_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_historical_valuation_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/valuation-history", dependencies=auth)
    async def valuation_history(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_historical_valuation_service().get_valuation_history, market, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/valuation-history/refresh", dependencies=auth)
    async def refresh_valuation_history(stock_code: str, market: str = Query(default="CN"), as_of: str = Query(...)):
        try:
            return await asyncio.to_thread(get_historical_valuation_service().refresh_company, market, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/valuation-history/current-l3/refresh", dependencies=auth)
    async def refresh_current_l3_valuation_history(limit: int = Query(..., ge=1, le=20), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_historical_valuation_service().refresh_current_l3, limit=limit, as_of=as_of)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/valuation-history/current-l3/backfill", dependencies=auth)
    async def backfill_current_l3_valuation_history(
        run_id: str | None = Query(default=None), as_of: str | None = Query(default=None),
        batch_size: int = Query(default=20, ge=1, le=50), max_batches: int = Query(default=1, ge=1, le=50),
        offset: int = Query(default=0, ge=0), retry_failed: bool = Query(default=False),
    ):
        """Run bounded, durable batches; resume with the returned run ID."""
        try:
            return await asyncio.to_thread(
                get_historical_valuation_service().backfill_current_l3_pool,
                as_of=as_of, batch_size=batch_size, resume_run_id=run_id, offset=offset,
                max_batches=max_batches, retry_failed=retry_failed,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/valuation-history/current-l3/backfill/{run_id}", dependencies=auth)
    async def current_l3_valuation_backfill_status(run_id: str):
        try:
            return await asyncio.to_thread(
                get_historical_valuation_service().tdx_store.historical_valuation_backfill_summary, run_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

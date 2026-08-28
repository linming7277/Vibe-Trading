"""Explicit Value Line daily-bar cache endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.adjusted_daily_bars import get_adjusted_daily_bar_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_adjusted_daily_bar_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/daily-bars/status", dependencies=auth)
    async def get_daily_bar_status(stock_code: str, market: str = Query(default="CN")):
        try:
            return await asyncio.to_thread(get_adjusted_daily_bar_service().status, market, stock_code)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/companies/{stock_code}/daily-bars/compact", dependencies=auth)
    async def get_compact_daily_bars(
        stock_code: str,
        market: str = Query(default="CN"),
        as_of: str | None = Query(default=None),
        limit: int = Query(default=126, ge=120, le=180),
    ):
        """Read a bounded front-adjusted daily series for the leader Quick View."""
        try:
            return await asyncio.to_thread(
                get_adjusted_daily_bar_service().compact_daily_bars,
                market, stock_code, as_of=as_of, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/daily-bars/refresh", dependencies=auth)
    async def refresh_daily_bars(stock_code: str, market: str = Query(default="CN"), as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_adjusted_daily_bar_service().refresh_company, market, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/daily-bars/current-l3/refresh", dependencies=auth)
    async def refresh_current_l3_daily_bars(limit: int = Query(..., ge=1, le=20), as_of: str | None = Query(default=None)):
        """Bounded staged validation only; this endpoint cannot backfill the full pool."""
        try:
            return await asyncio.to_thread(
                get_adjusted_daily_bar_service().refresh_current_l3_daily_bars, limit=limit, as_of=as_of,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/daily-bars/current-l3/backfill", dependencies=auth)
    async def backfill_current_l3_daily_bars(
        run_id: str | None = Query(default=None), as_of: str | None = Query(default=None),
        batch_size: int = Query(default=20, ge=1, le=50), max_batches: int = Query(default=1, ge=1, le=50),
        offset: int = Query(default=0, ge=0), retry_failed: bool = Query(default=False),
    ):
        """Run one or more bounded batches; resume with the returned ``run_id``.

        The public endpoint intentionally defaults to one batch.  The explicit
        command/service can run all remaining batches for an operator-approved
        initial backfill; no scheduled task invokes either path.
        """
        try:
            return await asyncio.to_thread(
                get_adjusted_daily_bar_service().backfill_current_l3_pool,
                as_of=as_of, batch_size=batch_size, resume_run_id=run_id, offset=offset,
                max_batches=max_batches, retry_failed=retry_failed,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/daily-bars/current-l3/backfill/{run_id}", dependencies=auth)
    async def current_l3_daily_bar_backfill_status(run_id: str):
        try:
            return await asyncio.to_thread(
                get_adjusted_daily_bar_service().tdx_store.adjusted_daily_bar_backfill_summary, run_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

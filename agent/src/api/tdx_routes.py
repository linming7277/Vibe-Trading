"""Authenticated HTTP routes for the local TongDaXin data cache."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.tdx_data.service import MODULES, get_tdx_service
from src.tdx_data.financial_history import FinancialHistoryService

AuthDep = Callable[..., Awaitable[Any] | Any]


class TdxUpdateRequest(BaseModel):
    module: str = Field(default="all")


class SecurityRefreshRequest(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)


class KlineRequest(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    period: str = Field(default="1d", pattern="^(1d|1w|1m|5m|15m|30m|60m)$")
    count: int = Field(default=300, ge=1, le=5000)
    dividend_type: str = Field(default="front", pattern="^(none|front|back)$")


class FormulaScanRequest(BaseModel):
    formula_type: int = Field(ge=0, le=3)
    formula_code: str = Field(min_length=1, max_length=64)
    formula_args: str = Field(default="", max_length=256)
    universe: str = Field(default="all", max_length=32)
    period: str = Field(default="1d", pattern="^(1d|1w|1m|5m|15m|30m|60m)$")


def register_tdx_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    if require_auth is None:
        import sys
        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("api_server module must be loaded before TDX routes")
        require_auth = host.require_auth

    @app.get("/tdx/status", dependencies=[Depends(require_auth)])
    async def tdx_status():
        return get_tdx_service().status()

    @app.get("/tdx/financial-history/{symbol}", dependencies=[Depends(require_auth)])
    async def tdx_financial_history(
        symbol: str,
        as_of: str | None = None,
        period_type: str | None = Query(default=None, pattern="^(annual|semiannual|q1|q3|other)$"),
    ):
        service = get_tdx_service()
        return await asyncio.to_thread(
            FinancialHistoryService(service.store, service.client).query,
            symbol, as_of=as_of, period_type=period_type,
        )

    @app.get("/tdx/market/overview", dependencies=[Depends(require_auth)])
    async def tdx_market_overview():
        return await asyncio.to_thread(get_tdx_service().market_overview)

    @app.get("/tdx/market/ranks", dependencies=[Depends(require_auth)])
    async def tdx_market_ranks(
        category: str = "涨幅榜", query: str = "", sector: str = "",
        sort: str = "rank", direction: str = "asc",
        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ):
        return await asyncio.to_thread(
            get_tdx_service().market_ranks, category=category, query=query, sector=sector,
            sort=sort, direction=direction, limit=limit, offset=offset,
        )

    @app.get("/tdx/sectors", dependencies=[Depends(require_auth)])
    async def tdx_sectors(
        category: str = "全部", query: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    ):
        return await asyncio.to_thread(get_tdx_service().sectors, category=category, query=query, limit=limit, offset=offset)

    @app.get("/tdx/sectors/{code}", dependencies=[Depends(require_auth)])
    async def tdx_sector_detail(code: str):
        value = await asyncio.to_thread(get_tdx_service().sector_detail, code)
        if not value:
            raise HTTPException(404, "TDX sector not found")
        return value

    @app.get("/tdx/screener", dependencies=[Depends(require_auth)])
    async def tdx_screener(
        query: str = "", sector: str = "", min_price: float | None = None, max_price: float | None = None,
        min_change: float | None = None, max_change: float | None = None, min_turnover: float | None = None,
        max_pe: float | None = None, max_pb: float | None = None, min_dividend_yield: float | None = None,
        min_market_cap: float | None = None, min_revenue: float | None = None, min_net_profit: float | None = None,
        min_eps: float | None = None, include_st: bool = False, include_quit: bool = False, include_bj: bool = False,
        is_hs300: bool = False, is_margin: bool = False, is_connect: bool = False,
        sort: str = "change_pct", direction: str = "desc", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ):
        return await asyncio.to_thread(get_tdx_service().screener, locals())

    @app.get("/tdx/funds", dependencies=[Depends(require_auth)])
    async def tdx_funds(
        category: str = "ETF", query: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    ):
        return await asyncio.to_thread(get_tdx_service().funds, category=category, query=query, limit=limit, offset=offset)

    @app.get("/tdx/securities/search", dependencies=[Depends(require_auth)])
    async def tdx_security_search(query: str, limit: int = Query(20, ge=1, le=100)):
        return {"items": await asyncio.to_thread(get_tdx_service().search_securities, query, limit)}

    @app.get("/tdx/securities/{symbol}/overview", dependencies=[Depends(require_auth)])
    async def tdx_security_overview(symbol: str):
        value = await asyncio.to_thread(get_tdx_service().security_overview, symbol)
        if not value:
            raise HTTPException(404, "TDX security not found")
        return value

    @app.post("/tdx/formula-scans", dependencies=[Depends(require_auth)])
    async def tdx_formula_scan(payload: FormulaScanRequest):
        try:
            return get_tdx_service().start_formula_scan(payload.model_dump())
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/tdx/formula-scans/{scan_id}", dependencies=[Depends(require_auth)])
    async def tdx_formula_scan_detail(scan_id: str):
        value = get_tdx_service().store.get_formula_scan(scan_id)
        if not value:
            raise HTTPException(404, "formula scan not found")
        return value

    @app.post("/tdx/update", dependencies=[Depends(require_auth)])
    async def tdx_update(payload: TdxUpdateRequest):
        if payload.module != "all" and payload.module not in MODULES:
            raise HTTPException(422, f"unknown TDX module: {payload.module}")
        try:
            return get_tdx_service().start_update(payload.module)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/tdx/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def tdx_job(job_id: str):
        value = get_tdx_service().store.get_job(job_id)
        if not value:
            raise HTTPException(404, "TDX update job not found")
        return value

    @app.get("/tdx/data/{dataset}", dependencies=[Depends(require_auth)])
    async def tdx_dataset(
        dataset: str,
        category: str | None = None,
        query: str = "",
        limit: int = Query(100, ge=1, le=10_000),
        offset: int = Query(0, ge=0),
    ):
        return get_tdx_service().store.list_records(dataset, category=category, query=query, limit=limit, offset=offset)

    @app.get("/tdx/securities/{symbol}", dependencies=[Depends(require_auth)])
    async def tdx_security(symbol: str):
        value = get_tdx_service().security_detail(symbol)
        if not value:
            raise HTTPException(404, "security detail is not cached; refresh it first")
        return value

    @app.post("/tdx/securities/refresh", dependencies=[Depends(require_auth)])
    async def tdx_security_refresh(payload: SecurityRefreshRequest):
        try:
            return await asyncio.to_thread(get_tdx_service().refresh_security, payload.symbol)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/tdx/klines", dependencies=[Depends(require_auth)])
    async def tdx_kline(payload: KlineRequest):
        try:
            return await asyncio.to_thread(
                get_tdx_service().kline,
                payload.symbol,
                period=payload.period,
                count=payload.count,
                dividend_type=payload.dividend_type,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/tdx/formulas/{formula_type}/{formula_code}/refresh", dependencies=[Depends(require_auth)])
    async def tdx_formula_detail(formula_type: int, formula_code: str):
        if formula_type not in {0, 1, 2, 3}:
            raise HTTPException(422, "formula_type must be 0, 1, 2 or 3")
        try:
            return await asyncio.to_thread(get_tdx_service().formula_detail, formula_type, formula_code)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc

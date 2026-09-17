"""Read-only A/B/C focus-selection API for active low-value leaders.

同时承载闭环 MVP 的跟踪清单读写（/api/value/tracking）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from src.focus_selection import get_focus_selection_service


AuthDep = Callable[..., Awaitable[Any] | Any]


class TrackingAddRequest(BaseModel):
    """纳入跟踪请求体（模块级定义：函数内类无法被 FastAPI 注解解析）。"""

    market: str = "CN"
    stock_code: str
    company_name: str = ""
    tier: str = ""
    reasons: list[str] = []
    note: str = ""


def register_focus_selection_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/focus-selection", dependencies=[Depends(require_auth)])
    async def focus_selection(as_of: str | None = Query(default=None)):
        """Return a durable-data projection; never triggers research or writes state."""
        try:
            return await asyncio.to_thread(get_focus_selection_service().get_focus_selection, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/tracking", dependencies=[Depends(require_auth)])
    async def list_tracking():
        """跟踪清单：纳入时参考价 vs 现价、涨跌、跟踪天数与当日档位。"""
        from src.company_tracking import get_company_tracking_service

        return await asyncio.to_thread(get_company_tracking_service().list_items)

    @app.post("/api/value/tracking", status_code=201, dependencies=[Depends(require_auth)])
    async def add_tracking(payload: TrackingAddRequest):
        """纳入跟踪：记录纳入时参考价与当初理由（人工批准持续关注）。"""
        from src.company_tracking import get_company_tracking_service

        try:
            return await asyncio.to_thread(
                get_company_tracking_service().add,
                market=payload.market, stock_code=payload.stock_code,
                company_name=payload.company_name, tier=payload.tier,
                reasons=payload.reasons, note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/value/tracking/{stock_code}", dependencies=[Depends(require_auth)])
    async def remove_tracking(stock_code: str, note: str = Query(default="manual")):
        """移出跟踪：结束一条跟踪记录（历史保留）。"""
        from src.company_tracking import get_company_tracking_service

        result = await asyncio.to_thread(
            get_company_tracking_service().remove, stock_code, note=note)
        if not result.get("removed"):
            raise HTTPException(404, "该股票不在跟踪清单中")
        return result

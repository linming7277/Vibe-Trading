"""Authenticated lifecycle API for Value Strategy state-change events."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.research_workspace.store import normalize_market, normalize_symbol
from src.value_strategy import get_value_strategy_event_service
from src.value_strategy.delivery import ValueStrategyEventDeliveryPolicy, ValueStrategyEventDeliveryStore

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_value_strategy_event_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/strategy-events", dependencies=[Depends(require_auth)])
    async def list_value_strategy_events(
        market: str | None = Query(default=None),
        stock_code: str | None = Query(default=None),
        status: str | None = Query(default=None),
        event_type: str | None = Query(default=None),
        since: str | None = Query(default=None),
        research_as_of: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        normalized_market = normalize_market(market) if market else None
        symbol = normalize_symbol(normalized_market, stock_code) if normalized_market and stock_code else stock_code
        if status and status not in {"OPEN", "ACKNOWLEDGED", "CLOSED"}:
            raise HTTPException(422, "status must be OPEN, ACKNOWLEDGED or CLOSED")
        items = await asyncio.to_thread(
            get_value_strategy_event_service().repository.list_events,
            market=normalized_market, stock_code=symbol, status=status,
            event_type=event_type, since=since, research_as_of=research_as_of, limit=limit,
        )
        return {"items": items, "count": len(items)}

    @app.get("/api/value/strategy-event-batches", dependencies=[Depends(require_auth)])
    async def list_value_strategy_event_batches(stock_code: str | None = None, limit: int = Query(default=10, ge=1, le=100)):
        service = get_value_strategy_event_service()
        code = normalize_symbol("CN", stock_code) if stock_code else None
        events = await asyncio.to_thread(service.repository.list_events, market="CN", stock_code=code, limit=500)
        batches = ValueStrategyEventDeliveryPolicy().aggregate(events)
        delivery_store = ValueStrategyEventDeliveryStore(service.repository.db_path)
        try:
            deliveries = delivery_store.list_for_batches([item["transition_batch_id"] for item in batches])
        finally:
            delivery_store.close()
        for item in batches:
            delivery = deliveries.get(item["transition_batch_id"])
            if delivery:
                item["delivery"] = delivery
        return {"items": batches[:limit], "count": len(batches)}

    @app.get("/api/value/strategy-events/{event_id}", dependencies=[Depends(require_auth)])
    async def get_value_strategy_event(event_id: str):
        item = await asyncio.to_thread(get_value_strategy_event_service().repository.get_event, event_id)
        if item is None:
            raise HTTPException(404, "strategy event not found")
        return item

    async def transition(event_id: str, target: str):
        try:
            item = await asyncio.to_thread(
                get_value_strategy_event_service().repository.transition_lifecycle, event_id, target,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if item is None:
            raise HTTPException(404, "strategy event not found")
        return item

    @app.post("/api/value/strategy-events/{event_id}/acknowledge", dependencies=[Depends(require_auth)])
    async def acknowledge_value_strategy_event(event_id: str):
        return await transition(event_id, "ACKNOWLEDGED")

    @app.post("/api/value/strategy-events/{event_id}/close", dependencies=[Depends(require_auth)])
    async def close_value_strategy_event(event_id: str):
        return await transition(event_id, "CLOSED")

"""Authenticated API for the Value Line L3 industry and leader pool."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.level3_leaders.service import get_level3_leader_service
from src.level3_leaders.constants import VALUE_LINE_LEADER_LIMIT

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_value_l3_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/industries", dependencies=auth)
    async def value_industries():
        return await asyncio.to_thread(get_level3_leader_service().industries)

    @app.get("/api/value/industry-tree", dependencies=auth)
    async def value_industry_tree():
        return await asyncio.to_thread(get_level3_leader_service().industry_tree)

    @app.get("/api/value/level3-leaders", dependencies=auth)
    async def all_level3_top_leaders(
        limit: int = Query(default=VALUE_LINE_LEADER_LIMIT, ge=VALUE_LINE_LEADER_LIMIT, le=VALUE_LINE_LEADER_LIMIT),
        as_of: str | None = None,
    ):
        """Return the persisted Top leaders for every terminal industry in one snapshot."""
        return await asyncio.to_thread(
            get_level3_leader_service().get_all_level3_top_leaders,
            as_of=as_of, limit=limit,
        )

    @app.get("/api/value/industries/{industry_code}/leaders", dependencies=auth)
    async def level3_industry_leaders(
        industry_code: str,
        limit: int = Query(default=VALUE_LINE_LEADER_LIMIT, ge=1, le=100),
        as_of: str | None = None,
    ):
        try:
            return await asyncio.to_thread(
                get_level3_leader_service().get_level3_leaders,
                industry_code, as_of=as_of, limit=limit,
            )
        except KeyError as exc:
            raise HTTPException(404, "industry not found") from exc

    @app.post("/api/value/level3-leaders/build", dependencies=auth)
    async def build_level3_leaders(as_of: str, force: bool = False):
        try:
            return await asyncio.to_thread(
                get_level3_leader_service().build_level3_leaders, as_of, force=force,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/current-leader-pool", dependencies=auth)
    async def current_leader_pool():
        try:
            return await asyncio.to_thread(get_level3_leader_service().ensure_current_pool)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/value/leader-pools", dependencies=auth)
    async def leader_pools(limit: int = Query(default=30, ge=1, le=200)):
        service = get_level3_leader_service()
        return {"items": await asyncio.to_thread(service.store.list_pools, limit)}

    @app.get("/api/value/leader-pools/{pool_id}", dependencies=auth)
    async def leader_pool(pool_id: str):
        service = get_level3_leader_service()
        value = await asyncio.to_thread(service.get_pool, pool_id, include_inactive=True)
        if not value:
            raise HTTPException(404, "leader pool not found")
        return value


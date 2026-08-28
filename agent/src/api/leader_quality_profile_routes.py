"""Read-only API for deterministic Leader Quality Profile V1."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.leader_quality_profile.service import get_leader_quality_profile_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_leader_quality_profile_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/leader-quality", dependencies=auth)
    async def get_leader_quality_profile(
        stock_code: str,
        market: str = Query(default="CN"),
        as_of: str | None = Query(default=None),
    ):
        try:
            return await asyncio.to_thread(
                get_leader_quality_profile_service().get_profile, market, stock_code, as_of,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

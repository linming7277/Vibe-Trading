"""Authenticated API for the persisted Fine Track V1 pipeline."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.fine_tracks.service import get_fine_track_service
from src.level3_leaders.service import get_level3_leader_service

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_fine_track_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/industries", dependencies=auth)
    async def fine_track_industries():
        return await asyncio.to_thread(get_fine_track_service().industries)

    @app.get("/api/value/industry-tree", dependencies=auth)
    async def value_industry_tree():
        return await asyncio.to_thread(get_level3_leader_service().industry_tree)

    @app.get("/api/value/level3-leaders", dependencies=auth)
    async def all_level3_top_leaders(
        limit: int = Query(default=2, ge=1, le=10),
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
        limit: int = Query(default=2, ge=0, le=100),
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

    @app.get("/api/value/industries/{industry_code}/companies", dependencies=auth)
    async def fine_track_industry_companies(industry_code: str):
        try:
            return await asyncio.to_thread(get_fine_track_service().companies, industry_code)
        except KeyError as exc:
            raise HTTPException(404, "industry not found") from exc

    @app.post("/api/value/industries/{industry_code}/classify-tracks", dependencies=auth)
    async def classify_fine_tracks(
        industry_code: str,
        mode: str = Query(default="database", pattern="^(database|agent)$"),
    ):
        try:
            method = (get_fine_track_service().classify_industry_from_database
                      if mode == "database" else get_fine_track_service().classify_industry)
            return await asyncio.to_thread(method, industry_code)
        except KeyError as exc:
            raise HTTPException(404, "industry not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/value/industries/{industry_code}/tracks", dependencies=auth)
    async def fine_tracks(industry_code: str):
        try:
            return await asyncio.to_thread(get_fine_track_service().tracks, industry_code)
        except KeyError as exc:
            raise HTTPException(404, "industry not found") from exc

"""Freshness query API (research-cache plan §22).

GET /api/research/freshness/{stock_code}?as_of=
→ module-level FRESH / STALE / PARTIALLY_STALE / UNKNOWN / INVALID verdicts.

Read-only: classification never triggers a refresh and never treats
"not generated today" as staleness.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Path, Query

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_research_freshness_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/research/freshness/{stock_code}", dependencies=[Depends(require_auth)])
    async def research_freshness(
        stock_code: str = Path(min_length=4, max_length=12),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        from src.research_freshness import get_research_freshness_service

        try:
            return await asyncio.to_thread(
                get_research_freshness_service().classify, "CN", stock_code, as_of
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface a readable API error
            raise HTTPException(500, f"freshness classification failed: {exc}") from exc

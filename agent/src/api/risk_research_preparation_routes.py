"""Read-only status endpoint for the low-value risk data-preparation worker."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Query

from src.risk_research_preparation import get_risk_research_preparation_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_risk_research_preparation_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/risk-research-preparation", dependencies=auth)
    async def risk_research_preparation(research_as_of: str | None = Query(default=None)):
        """Return durable state only.  This endpoint never triggers preparation."""
        return await asyncio.to_thread(
            get_risk_research_preparation_service().list_current_preparation,
            research_as_of=research_as_of,
        )

"""Read-only API for persisted Investment Research Supervisor daily briefs."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException

from src.investment_research_supervisor import get_investment_research_daily_brief_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_research_supervisor_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/research-supervisor/daily-brief", dependencies=[Depends(require_auth)])
    async def get_daily_brief(as_of: str | None = None):
        brief = await asyncio.to_thread(
            get_investment_research_daily_brief_service().get_completed,
            as_of,
        )
        if brief is None:
            raise HTTPException(status_code=404, detail="completed daily research brief not found")
        return brief

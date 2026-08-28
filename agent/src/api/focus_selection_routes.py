"""Read-only A/B/C focus-selection API for active low-value leaders."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from src.focus_selection import get_focus_selection_service


AuthDep = Callable[..., Awaitable[Any] | Any]


def register_focus_selection_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/value/focus-selection", dependencies=[Depends(require_auth)])
    async def focus_selection(as_of: str | None = Query(default=None)):
        """Return a durable-data projection; never triggers research or writes state."""
        try:
            return await asyncio.to_thread(get_focus_selection_service().get_focus_selection, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

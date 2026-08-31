"""Deep Research On-Demand API (task §12): single-company only, no batch.

GET  /api/research/deep-coverage/{stock_code}   → read-only coverage projection
POST /api/research/deep-prepare/{stock_code}    → idempotent gap-filling
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_deep_research_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/research/deep-coverage/{stock_code}", dependencies=[Depends(require_auth)])
    async def deep_coverage(
        stock_code: str = Path(min_length=4, max_length=12),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        from src.deep_research import get_deep_research_coverage_service

        try:
            return await asyncio.to_thread(
                get_deep_research_coverage_service().coverage, "CN", stock_code, as_of=as_of)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"coverage projection failed: {exc}") from exc

    @app.post("/api/research/deep-prepare/{stock_code}", dependencies=[Depends(require_auth)], status_code=200)
    async def deep_prepare(
        stock_code: str = Path(min_length=4, max_length=12),
        payload: dict[str, Any] = Body(default={}),
    ) -> dict[str, Any]:
        from src.deep_research import get_deep_research_preparation_service

        as_of = str(payload.get("as_of") or "")[:10] or None
        try:
            return await asyncio.to_thread(
                get_deep_research_preparation_service().prepare, "CN", stock_code,
                as_of=as_of,
                include_p1=bool(payload.get("include_p1", True)),
                max_documents_per_kind=int(payload.get("max_documents_per_kind", 2)),
            )
        except RuntimeError as exc:
            message = str(exc)
            status = 429 if "DAILY_LIMIT" in message or "BUSY" in message else 422
            raise HTTPException(status, message) from exc

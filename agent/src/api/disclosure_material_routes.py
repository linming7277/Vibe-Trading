"""Official periodic-report material APIs; no analysis is run on read."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.disclosure_materials.service import get_disclosure_material_service


AuthDep = Callable[..., Awaitable[Any] | Any]


class DisclosureSyncRequest(BaseModel):
    as_of: str | None = None
    max_documents_per_kind: int = Field(default=2, ge=1, le=8)


def register_disclosure_material_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/api/value/companies/{stock_code}/disclosure-materials", dependencies=auth)
    async def get_disclosure_materials(stock_code: str, as_of: str | None = None):
        return await asyncio.to_thread(get_disclosure_material_service().get_materials, stock_code, as_of=as_of)

    @app.post("/api/value/companies/{stock_code}/disclosure-materials/sync", dependencies=auth)
    async def sync_disclosure_materials(stock_code: str, payload: DisclosureSyncRequest):
        try:
            return await asyncio.to_thread(
                get_disclosure_material_service().sync_periodic_reports,
                stock_code, as_of=payload.as_of, max_documents_per_kind=payload.max_documents_per_kind,
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"official_disclosure_sync_failed:{type(exc).__name__}") from exc

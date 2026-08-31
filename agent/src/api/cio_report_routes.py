"""CIO report API (research-cache plan §22).

GET  /api/research/cio/{stock_code}?as_of=   → latest persisted report (read-only)
POST /api/research/cio/{stock_code}/refresh  → classify-first rebuild; sections
     are deterministic and cheap, the synthesis LLM only reruns when the
     report fingerprint changed.  Never a blind full-chain rerun.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Path, Query

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_cio_report_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/api/research/cio/{stock_code}", dependencies=[Depends(require_auth)])
    async def get_cio_report(
        stock_code: str = Path(min_length=4, max_length=12),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        from src.cio_report import get_cio_report_service

        report = await asyncio.to_thread(
            get_cio_report_service().get_report, "CN", stock_code, as_of=as_of,
        )
        if report is None:
            raise HTTPException(404, "no persisted CIO report; call refresh first")
        return report

    @app.get("/api/research/cio/{stock_code}/quick-brief", dependencies=[Depends(require_auth)])
    async def get_cio_quick_brief(
        stock_code: str = Path(min_length=4, max_length=12),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        """Deterministic six-block brief projected from the persisted report.

        Read-only: no refresh, no LLM, no specialist calls; a missing report
        returns CIO_REPORT_NOT_FOUND instead of silently building one.
        """
        from src.cio_report import get_cio_report_service

        try:
            return await asyncio.to_thread(
                get_cio_report_service().get_quick_brief, "CN", stock_code, as_of=as_of,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/research/cio/{stock_code}/freshness", dependencies=[Depends(require_auth)])
    async def cio_report_freshness(
        stock_code: str = Path(min_length=4, max_length=12),
    ) -> dict[str, Any]:
        """Live per-section FRESH/STALE against the persisted report (plan §17)."""
        from src.cio_report import get_cio_report_service

        result = await asyncio.to_thread(
            get_cio_report_service().classify_report_sections, "CN", stock_code,
        )
        if result is None:
            raise HTTPException(404, "no persisted CIO report")
        return result

    @app.post("/api/research/cio/ensure-focus-tiers", dependencies=[Depends(require_auth)], status_code=200)
    async def ensure_focus_tier_reports(
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        """Focus A/B/C CIO-report resource policy (plan §11): A always READY,
        B build-when-missing, C on demand only."""
        from src.cio_report import get_cio_report_service

        return await asyncio.to_thread(
            get_cio_report_service().ensure_focus_tier_reports, as_of=as_of,
        )

    @app.post("/api/research/cio/{stock_code}/refresh", dependencies=[Depends(require_auth)], status_code=201)
    async def refresh_cio_report(
        stock_code: str = Path(min_length=4, max_length=12),
        as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        force_synthesis: bool = Query(default=False),
    ) -> dict[str, Any]:
        from src.cio_report import get_cio_report_service

        try:
            return await asyncio.to_thread(
                get_cio_report_service().build_report, "CN", stock_code,
                as_of=as_of, force_synthesis=force_synthesis,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

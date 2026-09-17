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
    @app.get("/api/research/cio/company-search", dependencies=[Depends(require_auth)])
    async def search_cio_companies(q: str = Query(default="", min_length=1)):
        """按名称/代码搜公司：优先返回低估值龙头池内（带研究档位）的公司。"""
        import asyncio as _asyncio

        needle = q.strip()
        if not needle:
            return {"items": []}

        def _search() -> list[dict[str, Any]]:
            tiers: dict[str, str] = {}
            pool_names: dict[str, str] = {}
            try:
                from src.focus_selection import get_focus_selection_service

                selection = get_focus_selection_service().get_focus_selection()
                for tier_key, label in (("A", "重点研究"), ("B", "继续观察"), ("C", "暂缓优先")):
                    for item in selection.get(tier_key) or []:
                        code = str(item.get("stock_code") or "").upper()
                        tiers[code] = label
                        pool_names[code] = str(item.get("company_name") or code)
            except Exception:  # noqa: BLE001 - 档位读取失败退化为纯名录搜索
                tiers, pool_names = {}, {}

            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            for code, name in pool_names.items():
                if needle in code or needle in name:
                    items.append({"stock_code": code, "company_name": name,
                                  "focus_tier": tiers.get(code), "in_pool": True})
                    seen.add(code)

            try:
                from src.tdx_data.store import TdxDataStore

                store = TdxDataStore()
                try:
                    rows = store.list_records("securities", query=needle, limit=12)["items"]
                finally:
                    store.close()
                for row in rows:
                    code = str(row.get("record_key") or "").upper()
                    if not code or code in seen or not code[:1].isdigit():
                        continue
                    items.append({"stock_code": code,
                                  "company_name": str(row.get("name") or code),
                                  "focus_tier": None, "in_pool": False})
                    seen.add(code)
            except Exception:  # noqa: BLE001 - 名录搜索失败不阻断池内结果
                pass
            return items[:10]

        return await _asyncio.to_thread(_search)

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

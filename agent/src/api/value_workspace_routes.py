"""Focused HTTP API for the Value Line L3 leader pool and research state."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from src.financial_analysis.service import get_financial_analysis_service
from src.level3_leaders.service import get_level3_leader_service
from src.level3_leaders.store import Level3LeaderStore

AuthDep = Callable[..., Awaitable[Any] | Any]


class AutomationPatch(BaseModel):
    enabled: bool


class ResearchStatePatch(BaseModel):
    is_priority: bool


def register_value_workspace_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    @app.get("/strategy/value/research-states", dependencies=auth)
    async def value_research_states():
        try:
            pool = await asyncio.to_thread(get_level3_leader_service().ensure_current_pool)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "pool_id": pool["id"], "as_of": pool["as_of"],
            "items": [
                item for item in pool.get("research_states", [])
                if item.get("lifecycle_status") != "OUT_OF_TOP2"
            ],
        }

    @app.patch("/strategy/value/research-states/{stock_code}", dependencies=auth)
    async def patch_value_research_state(stock_code: str, payload: ResearchStatePatch):
        service = get_level3_leader_service()
        pool = await asyncio.to_thread(service.ensure_current_pool)
        store = Level3LeaderStore()
        try:
            cursor = store._conn.execute(
                """UPDATE l3_company_research_states SET is_priority=?,updated_at=datetime('now')
                   WHERE pool_id=? AND stock_code=?""",
                (int(payload.is_priority), pool["id"], stock_code.upper()),
            )
            store._conn.commit()
            if cursor.rowcount != 1:
                raise HTTPException(404, "company is not in the current leader pool")
            row = store._conn.execute(
                "SELECT * FROM l3_company_research_states WHERE pool_id=? AND stock_code=?",
                (pool["id"], stock_code.upper()),
            ).fetchone()
            return dict(row)
        finally:
            store.close()

    @app.post("/strategy/value/research-states/{stock_code}/refresh", dependencies=auth)
    async def refresh_value_company_research(stock_code: str):
        service = get_level3_leader_service()
        pool = await asyncio.to_thread(service.ensure_current_pool)
        state = next(
            (item for item in pool.get("research_states", []) if item["stock_code"] == stock_code.upper()),
            None,
        )
        if not state or state.get("lifecycle_status") == "OUT_OF_TOP2":
            raise HTTPException(404, "company is not in the current leader pool")
        snapshot = await asyncio.to_thread(
            get_financial_analysis_service().prepare, stock_code.upper(), as_of=str(pool["as_of"]),
        )
        store = Level3LeaderStore()
        try:
            store.update_research_state(
                pool["id"], stock_code.upper(),
                status="READY" if snapshot.get("feature_status") in {"READY", "PARTIAL"} else "PARTIAL",
                snapshot_id=snapshot.get("id"), researched_at=snapshot.get("updated_at"),
            )
        finally:
            store.close()
        return snapshot

    @app.get("/strategy/value/automation", dependencies=auth)
    async def value_automation():
        store = Level3LeaderStore()
        try:
            return store.get_automation()
        finally:
            store.close()

    @app.patch("/strategy/value/automation", dependencies=auth)
    async def patch_value_automation(payload: AutomationPatch):
        store = Level3LeaderStore()
        try:
            value = store.update_automation(enabled=payload.enabled)
        finally:
            store.close()
        from src.value_workspace.automation import get_value_research_scheduler
        get_value_research_scheduler().wake()
        return value

    @app.post("/strategy/value/automation/run-now", dependencies=auth)
    async def run_value_automation_now():
        """Manually resume the normal Value Line pipeline from one qualified close.

        This deliberately consumes the latest published close instead of
        synthesising a natural-day snapshot, so a manual click cannot label
        intraday or incomplete data as a completed Value Line result.
        """
        from src.tdx_data.service import get_tdx_service
        from src.value_workspace.automation import get_value_research_scheduler

        # A historical repair can legitimately materialize a newer Value Line
        # version before the ordinary TDX `market_close` snapshot catches up.
        # Never let a click on this operational recovery button overwrite that
        # newer result with an older qualified-close version.
        ready, _reason, snapshot = get_tdx_service().latest_qualified_close_snapshot()
        target_as_of = str((snapshot or {}).get("market_date") or "")[:10]
        store = Level3LeaderStore()
        try:
            latest_run = store.latest_run()
        finally:
            store.close()
        if ready and target_as_of and latest_run and str(latest_run.get("as_of") or "") > target_as_of:
            from src.investment_research_supervisor import get_daily_brief_bitable_publisher

            # A historical repair may have already created the newer report.
            # The normal scheduler is intentionally not allowed to roll that
            # result back to an older qualified close, but the managed Feishu
            # table must still be brought to the same research date.
            bitable = await asyncio.to_thread(
                get_daily_brief_bitable_publisher().publish,
                research_as_of=str(latest_run["as_of"]),
            )
            if bitable.get("status") != "READY":
                return {
                    "status": "PARTIAL",
                    "as_of": latest_run["as_of"],
                    "pool_id": latest_run["id"],
                    "stages": {"FEISHU_BITABLE": "FAILED"},
                    "error": str(bitable.get("error") or "Feishu Bitable synchronization failed"),
                }
            return {
                "status": "UP_TO_DATE",
                "as_of": latest_run["as_of"],
                "pool_id": latest_run["id"],
                "stages": {"FEISHU_BITABLE": "READY"},
                "reason": "当前价值线结果已比最近合格收盘快照更新，保留较新的结果。",
            }

        result = await asyncio.to_thread(
            get_value_research_scheduler().recover_latest_completed,
        )
        if result.get("status") == "DISABLED":
            raise HTTPException(409, "value research automation is disabled")
        return result

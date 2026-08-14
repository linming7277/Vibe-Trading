"""HTTP API for the A-share value research workbench."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.strategy_engines.store import StrategyEngineStore
from src.value_workspace.service import RESEARCH_TEMPLATE_VERSION, ValueWorkspaceService
from src.value_workspace.store import ValueWorkspaceStore

AuthDep = Callable[..., Awaitable[Any] | Any]


class ProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["single", "composite"]
    model_weights: dict[str, float]


class ResearchBatchPayload(BaseModel):
    run_id: str
    track_id: str
    symbols: list[str] = Field(min_length=1, max_length=20)
    concurrency: int = Field(default=3, ge=1, le=5)
    template_version: str = Field(default=RESEARCH_TEMPLATE_VERSION, min_length=1, max_length=100)


class MonitorPayload(BaseModel):
    research_job_id: str
    conditions: dict[str, Any] = Field(default_factory=dict)
    channels: list[Literal["in_app", "feishu", "weixin"]] = Field(default_factory=lambda: ["in_app"])


class MonitorPatch(BaseModel):
    status: Literal["active", "paused", "closed"] | None = None
    conditions: dict[str, Any] | None = None
    channels: list[Literal["in_app", "feishu", "weixin"]] | None = None


def _run_batch(batch_id: str) -> None:
    service = ValueWorkspaceService()
    try:
        service.run_batch(batch_id)
    finally:
        service.close()


def register_value_workspace_routes(app: FastAPI, require_auth: AuthDep) -> None:
    @app.get("/strategy/value/profiles", dependencies=[Depends(require_auth)])
    async def list_profiles():
        store = ValueWorkspaceStore()
        try:
            return {"items": store.list_profiles()}
        finally:
            store.close()

    @app.post("/strategy/value/profiles", dependencies=[Depends(require_auth)])
    async def create_profile(payload: ProfilePayload):
        store = ValueWorkspaceStore()
        try:
            return store.save_profile(name=payload.name, mode=payload.mode, weights=payload.model_weights)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            store.close()

    @app.get("/strategy/value/profiles/{profile_id}", dependencies=[Depends(require_auth)])
    async def get_profile(profile_id: str):
        store = ValueWorkspaceStore()
        try:
            value = store.get_profile(profile_id)
            if not value:
                raise HTTPException(404, "calculation profile not found")
            return value
        finally:
            store.close()

    @app.patch("/strategy/value/profiles/{profile_id}", dependencies=[Depends(require_auth)])
    async def patch_profile(profile_id: str, payload: ProfilePayload):
        store = ValueWorkspaceStore()
        try:
            return store.save_profile(profile_id=profile_id, name=payload.name, mode=payload.mode, weights=payload.model_weights)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            store.close()

    @app.delete("/strategy/value/profiles/{profile_id}", dependencies=[Depends(require_auth)])
    async def delete_profile(profile_id: str):
        store = ValueWorkspaceStore()
        try:
            store.delete_profile(profile_id)
            return {"status": "deleted"}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            store.close()

    @app.get("/strategy/value/workbench", dependencies=[Depends(require_auth)])
    async def workbench(profile_id: str | None = Query(default=None)):
        service = ValueWorkspaceService()
        try:
            profile = service.store.get_profile(profile_id)
            if not profile:
                raise HTTPException(404, "calculation profile not found")
            snapshot = service.materialize_v2_snapshot(profile["id"])
            batches = service.store.list_batches(10)
            monitors = service.store.list_monitors()
            return {
                "profile": profile, "latest_run": snapshot["run"], "macro": snapshot["macro"],
                "tracks": snapshot["tracks"], "sector_scores": snapshot.get("sectors", []),
                "research_batches": batches,
                "monitor_summary": {"active": sum(item["status"] == "active" for item in monitors), "events": len(service.store.list_events(200))},
            }
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            service.close()

    @app.get("/strategy/value/tracks", dependencies=[Depends(require_auth)])
    async def tracks(run_id: str):
        store = ValueWorkspaceStore()
        try:
            return {"run_id": run_id, "items": store.list_tracks(run_id)}
        finally:
            store.close()

    @app.get("/strategy/value/tracks/{track_id}/leaders", dependencies=[Depends(require_auth)])
    async def leaders(track_id: str, run_id: str):
        service = ValueWorkspaceService()
        try:
            return {"run_id": run_id, "track_id": track_id, "items": service.ensure_track_leaders(run_id, track_id)}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            service.close()

    @app.post("/strategy/value/research-batches", dependencies=[Depends(require_auth)])
    async def create_batch(payload: ResearchBatchPayload, background_tasks: BackgroundTasks):
        store = ValueWorkspaceStore()
        engine = StrategyEngineStore(store.db_path)
        try:
            run = engine.get_run(payload.run_id)
            if not run or run.get("strategy_line") != "value" or run.get("market") != "CN":
                raise HTTPException(404, "A-share value strategy run not found")
            leaders = {item["symbol"]: item for item in store.list_leaders(payload.run_id, payload.track_id)}
            symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in payload.symbols))
            if any(symbol not in leaders for symbol in symbols):
                raise HTTPException(422, "research symbols must belong to the selected track leader snapshot")
            companies = [{"symbol": symbol, "name": leaders[symbol]["name"]} for symbol in symbols]
            batch, created = store.create_batch(
                run_id=payload.run_id, profile_id=str(run.get("profile_id") or "profile_balanced"), track_id=payload.track_id,
                companies=companies, template_version=payload.template_version, concurrency=payload.concurrency,
            )
            if created:
                background_tasks.add_task(_run_batch, batch["id"])
            return {**batch, "created": created}
        finally:
            engine.close()
            store.close()

    @app.get("/strategy/value/research-batches", dependencies=[Depends(require_auth)])
    async def list_batches():
        store = ValueWorkspaceStore()
        try:
            return {"items": store.list_batches()}
        finally:
            store.close()

    @app.get("/strategy/value/research-batches/{batch_id}", dependencies=[Depends(require_auth)])
    async def get_batch(batch_id: str):
        store = ValueWorkspaceStore()
        try:
            value = store.get_batch(batch_id)
            if not value:
                raise HTTPException(404, "research batch not found")
            return value
        finally:
            store.close()

    @app.post("/strategy/value/research-batches/{batch_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_batch(batch_id: str):
        store = ValueWorkspaceStore()
        try:
            if not store.get_batch(batch_id):
                raise HTTPException(404, "research batch not found")
            store.update_batch(batch_id, cancel_requested=1)
            return store.get_batch(batch_id)
        finally:
            store.close()

    @app.post("/strategy/value/research-batches/{batch_id}/retry", dependencies=[Depends(require_auth)])
    async def retry_batch(batch_id: str, background_tasks: BackgroundTasks):
        service = ValueWorkspaceService()
        try:
            batch = service.retry_failed_jobs(batch_id)
            background_tasks.add_task(_run_batch, batch_id)
            return batch
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            service.close()

    @app.get("/strategy/value/monitors", dependencies=[Depends(require_auth)])
    async def list_monitors():
        store = ValueWorkspaceStore()
        try:
            return {"items": store.list_monitors()}
        finally:
            store.close()

    @app.post("/strategy/value/monitors", dependencies=[Depends(require_auth)])
    async def create_monitor(payload: MonitorPayload):
        store = ValueWorkspaceStore()
        try:
            return store.create_monitor(job_id=payload.research_job_id, conditions=payload.conditions, channels=list(dict.fromkeys(payload.channels)))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            store.close()

    @app.patch("/strategy/value/monitors/{monitor_id}", dependencies=[Depends(require_auth)])
    async def patch_monitor(monitor_id: str, payload: MonitorPatch):
        store = ValueWorkspaceStore()
        try:
            return store.update_monitor(monitor_id, **payload.model_dump(exclude_none=True))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            store.close()

    @app.post("/strategy/value/monitors/evaluate", dependencies=[Depends(require_auth)])
    async def evaluate_monitors():
        service = ValueWorkspaceService()
        try:
            events = service.evaluate_monitors()
            import sys
            host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
            try:
                runtime = host._get_channel_runtime() if host else None
            except Exception:
                runtime = None
            await service.deliver_notifications(events, getattr(runtime, "manager", None))
            return {"items": service.store.list_events(len(events) or 1) if events else []}
        finally:
            service.close()

    @app.get("/strategy/value/monitor-events", dependencies=[Depends(require_auth)])
    async def monitor_events(limit: int = Query(default=200, ge=1, le=1000)):
        store = ValueWorkspaceStore()
        try:
            return {"items": store.list_events(limit)}
        finally:
            store.close()

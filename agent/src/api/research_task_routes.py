"""Authenticated APIs for Research Task + Multi-Agent V1."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.research_tasks.service import ResearchTaskService

AuthDep = Callable[..., Awaitable[Any] | Any]
_service: ResearchTaskService | None = None


def get_research_task_service() -> ResearchTaskService:
    global _service
    if _service is None:
        _service = ResearchTaskService()
    return _service


class AgentConfigUpdate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class AgentModelSettingUpdate(BaseModel):
    model_id: str = Field(min_length=1, max_length=80)
    enabled: bool = True


class AgentConnectionSettingUpdate(BaseModel):
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    clear_api_key: bool = False
    enabled: bool = True


class ResearchTaskCreate(BaseModel):
    source: Literal["SYSTEM", "BOSS", "AGENT_ESCALATION"] = "BOSS"
    scope_type: Literal["INDUSTRY", "COMPANY"]
    scope_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    requested_by: str = Field(default="boss", min_length=1, max_length=100)
    trigger_context: dict[str, Any] = Field(default_factory=dict)


def register_research_task_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    if require_auth is None:
        import sys
        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("api_server module must be loaded before research task routes")
        require_auth = host.require_auth

    auth = [Depends(require_auth)]

    @app.get("/api/agents/config", dependencies=auth)
    async def agent_configs():
        return {"items": get_research_task_service().get_configs()}

    @app.put("/api/agents/config/{role}", dependencies=auth)
    async def update_agent_config(role: str, payload: AgentConfigUpdate):
        try:
            return get_research_task_service().update_config(role, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/agents/providers", dependencies=auth)
    async def agent_providers():
        return {"items": get_research_task_service().get_providers()}

    @app.get("/api/agents/model-settings", dependencies=auth)
    async def agent_model_settings():
        return {"items": get_research_task_service().get_model_settings()}

    @app.put("/api/agents/model-settings/{role}", dependencies=auth)
    async def update_agent_model_setting(role: str, payload: AgentModelSettingUpdate):
        try:
            return get_research_task_service().update_model_setting(role, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/agents/connections", dependencies=auth)
    async def agent_connection_settings():
        return {"items": get_research_task_service().get_connection_settings()}

    @app.put("/api/agents/connections/{role}", dependencies=auth)
    async def update_agent_connection_setting(role: str, payload: AgentConnectionSettingUpdate):
        try:
            return get_research_task_service().update_connection_setting(role, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/research/tasks", dependencies=auth, status_code=201)
    async def create_research_task(payload: ResearchTaskCreate):
        try:
            return get_research_task_service().create_task(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/research/tasks/{task_id}", dependencies=auth)
    async def get_research_task(task_id: str):
        try:
            return get_research_task_service().get_task(task_id)
        except KeyError as exc:
            raise HTTPException(404, "research task not found") from exc

    @app.post("/api/research/tasks/{task_id}/run", dependencies=auth)
    async def run_research_task(task_id: str):
        try:
            return await asyncio.to_thread(get_research_task_service().run_task, task_id)
        except KeyError as exc:
            raise HTTPException(404, "research task not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/research/tasks/{task_id}/participants", dependencies=auth)
    async def research_task_participants(task_id: str):
        try:
            return {"items": get_research_task_service().get_participants(task_id)}
        except KeyError as exc:
            raise HTTPException(404, "research task not found") from exc

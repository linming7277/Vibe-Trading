"""IM channel HTTP routes.

Mounted by ``agent/api_server.py`` via ``register_channels_routes(app, ...)``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.config.personal import SUPPORTED_CHANNELS


# ---------------------------------------------------------------------------
# Pydantic models (defined locally -- NO shared modules, per maintainer rule)
# ---------------------------------------------------------------------------

class ChannelPairingCommandRequest(BaseModel):
    """Pairing command payload for IM channel sender pairing."""

    channel: str
    command: str


class FeishuChannelConfigUpdate(BaseModel):
    auto_start: bool = True
    enabled: bool = True
    app_id: str = Field(default="", max_length=120)
    app_secret: str | None = Field(default=None, max_length=500)
    clear_app_secret: bool = False
    domain: Literal["feishu", "lark"] = "feishu"
    group_policy: Literal["mention", "open"] = "mention"
    reply_to_message: bool = True
    streaming: bool = True
    topic_isolation: bool = True
    default_agent: Literal[
        "general",
        "financial_analyst",
        "investment_research_supervisor",
        "risk_researcher",
        "valuation_researcher",
        "macro_policy_researcher",
    ] = "financial_analyst"


# ---------------------------------------------------------------------------
# Lifecycle helpers (module-level, access host state via sys.modules)
# ---------------------------------------------------------------------------


async def _start_channel_runtime():
    """Start the IM channel runtime."""
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    runtime = host._get_channel_runtime()
    await runtime.start(start_manager=True)
    return runtime


async def _stop_channel_runtime() -> None:
    """Stop the IM channel runtime if it was initialized."""
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
    if host._channel_runtime is None:
        return
    await host._channel_runtime.stop()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

AuthDep = Callable[..., Awaitable[Any] | Any]


def register_channels_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the channel routes onto ``app``.

    Resolves ``require_auth`` from the host ``api_server`` module via
    ``sys.modules`` when not passed explicitly.
    """
    # Resolve host dependencies via sys.modules fallback
    import sys as _sys

    host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")

    if host is None:
        raise RuntimeError(
            "register_channels_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )

    if require_auth is None:
        require_auth = host.require_auth

    # Late-access closure for monkeypatch compatibility
    def _get_channel_runtime():
        """Late-access _get_channel_runtime for test monkeypatch compat."""
        h = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        return h._get_channel_runtime()

    # --- Routes ---

    @app.get("/channels/status", dependencies=[Depends(require_auth)])
    async def channels_status():
        """Return IM channel runtime and adapter status."""
        runtime = _get_channel_runtime()
        return runtime.status()

    @app.get("/channels/feishu/config", dependencies=[Depends(require_auth)])
    async def get_feishu_config():
        """Return Feishu configuration without exposing its App Secret."""
        from src.channels.settings import get_feishu_settings

        return get_feishu_settings()

    @app.put("/channels/feishu/config", dependencies=[Depends(require_auth)])
    async def update_feishu_config(payload: FeishuChannelConfigUpdate):
        """Validate, persist, and hot-reload the personal Feishu bot."""
        from src.api.state import _reload_channel_runtime
        from src.channels.settings import (
            get_feishu_settings,
            save_feishu_settings,
            verify_feishu_credentials,
        )

        current = get_feishu_settings(include_secret=True)
        app_id = payload.app_id.strip()
        app_secret = "" if payload.clear_app_secret else (
            payload.app_secret.strip() if payload.app_secret is not None else str(current.get("app_secret") or "")
        )
        if payload.enabled and (not app_id or not app_secret):
            raise HTTPException(status_code=422, detail="启用飞书前必须配置 App ID 和 App Secret")

        bot: dict[str, str] | None = None
        if app_id and app_secret:
            try:
                bot = await asyncio.to_thread(verify_feishu_credentials, app_id, app_secret)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = save_feishu_settings({
            "auto_start": payload.auto_start,
            "enabled": payload.enabled,
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": payload.domain,
            "group_policy": payload.group_policy,
            "reply_to_message": payload.reply_to_message,
            "streaming": payload.streaming,
            "topic_isolation": payload.topic_isolation,
            "default_agent": payload.default_agent,
        })
        runtime = await _reload_channel_runtime(start=payload.enabled)
        runtime_status = runtime.status() if runtime is not None else _get_channel_runtime().status()
        return {"config": saved, "bot": bot, "runtime": runtime_status}

    @app.get("/channels/feishu-supervisor/config", dependencies=[Depends(require_auth)])
    async def get_feishu_supervisor_config():
        """Return the dedicated investment-supervisor bot configuration."""
        from src.channels.settings import get_feishu_channel_settings

        return get_feishu_channel_settings("feishu_supervisor")

    @app.put("/channels/feishu-supervisor/config", dependencies=[Depends(require_auth)])
    async def update_feishu_supervisor_config(payload: FeishuChannelConfigUpdate):
        """Persist and start the separate Feishu investment-supervisor bot."""
        from src.api.state import _reload_channel_runtime
        from src.channels.settings import (
            get_feishu_channel_settings,
            save_feishu_channel_settings,
            verify_feishu_credentials,
        )

        current = get_feishu_channel_settings("feishu_supervisor", include_secret=True)
        app_id = payload.app_id.strip()
        app_secret = "" if payload.clear_app_secret else (
            payload.app_secret.strip() if payload.app_secret is not None else str(current.get("app_secret") or "")
        )
        if payload.enabled and (not app_id or not app_secret):
            raise HTTPException(status_code=422, detail="启用投研主管飞书前必须配置 App ID 和 App Secret")

        bot: dict[str, str] | None = None
        if app_id and app_secret:
            try:
                bot = await asyncio.to_thread(verify_feishu_credentials, app_id, app_secret)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        saved = save_feishu_channel_settings("feishu_supervisor", {
            "auto_start": payload.auto_start,
            "enabled": payload.enabled,
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": payload.domain,
            "group_policy": payload.group_policy,
            "reply_to_message": payload.reply_to_message,
            "streaming": payload.streaming,
            "topic_isolation": payload.topic_isolation,
            # This bot is intentionally fixed to the supervisor route.  The
            # financial bot keeps its own default agent and is never replaced.
            "default_agent": "investment_research_supervisor",
            "daily_research_brief_notification": current.get("daily_research_brief_notification") or {},
        })
        runtime = await _reload_channel_runtime(start=payload.enabled)
        runtime_status = runtime.status() if runtime is not None else _get_channel_runtime().status()
        return {"config": saved, "bot": bot, "runtime": runtime_status}

    async def _update_specialist_feishu_config(
        channel_name: str,
        agent_name: str,
        role_label: str,
        payload: FeishuChannelConfigUpdate,
    ) -> dict[str, Any]:
        """Persist one dedicated specialist bot without touching sibling bots."""
        from src.api.state import _reload_channel_runtime
        from src.channels.settings import (
            get_feishu_channel_settings,
            save_feishu_channel_settings,
            verify_feishu_credentials,
        )

        current = get_feishu_channel_settings(channel_name, include_secret=True)
        app_id = payload.app_id.strip()
        app_secret = "" if payload.clear_app_secret else (
            payload.app_secret.strip()
            if payload.app_secret is not None
            else str(current.get("app_secret") or "")
        )
        if payload.enabled and (not app_id or not app_secret):
            raise HTTPException(status_code=422, detail=f"启用{role_label}飞书前必须配置 App ID 和 App Secret")
        bot: dict[str, str] | None = None
        if app_id and app_secret:
            try:
                bot = await asyncio.to_thread(verify_feishu_credentials, app_id, app_secret)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = save_feishu_channel_settings(channel_name, {
            "auto_start": payload.auto_start,
            "enabled": payload.enabled,
            "app_id": app_id,
            "app_secret": app_secret,
            "domain": payload.domain,
            "group_policy": payload.group_policy,
            "reply_to_message": payload.reply_to_message,
            "streaming": payload.streaming,
            "topic_isolation": payload.topic_isolation,
            "default_agent": agent_name,
        })
        runtime = await _reload_channel_runtime(start=payload.enabled)
        runtime_status = runtime.status() if runtime is not None else _get_channel_runtime().status()
        return {"config": saved, "bot": bot, "runtime": runtime_status}

    @app.get("/channels/feishu-risk/config", dependencies=[Depends(require_auth)])
    async def get_feishu_risk_config():
        from src.channels.settings import get_feishu_channel_settings

        return get_feishu_channel_settings("feishu_risk")

    @app.put("/channels/feishu-risk/config", dependencies=[Depends(require_auth)])
    async def update_feishu_risk_config(payload: FeishuChannelConfigUpdate):
        return await _update_specialist_feishu_config(
            "feishu_risk", "risk_researcher", "风险研究员", payload,
        )

    @app.get("/channels/feishu-valuation/config", dependencies=[Depends(require_auth)])
    async def get_feishu_valuation_config():
        from src.channels.settings import get_feishu_channel_settings

        return get_feishu_channel_settings("feishu_valuation")

    @app.put("/channels/feishu-valuation/config", dependencies=[Depends(require_auth)])
    async def update_feishu_valuation_config(payload: FeishuChannelConfigUpdate):
        return await _update_specialist_feishu_config(
            "feishu_valuation", "valuation_researcher", "估值研究员", payload,
        )

    @app.get("/channels/feishu-macro-policy/config", dependencies=[Depends(require_auth)])
    async def get_feishu_macro_policy_config():
        from src.channels.settings import get_feishu_channel_settings

        return get_feishu_channel_settings("feishu_macro_policy")

    @app.put("/channels/feishu-macro-policy/config", dependencies=[Depends(require_auth)])
    async def update_feishu_macro_policy_config(payload: FeishuChannelConfigUpdate):
        return await _update_specialist_feishu_config(
            "feishu_macro_policy", "macro_policy_researcher", "宏观政策研究员", payload,
        )

    @app.post("/channels/start", dependencies=[Depends(require_auth)])
    async def channels_start():
        """Start configured IM channel adapters."""
        runtime = await _start_channel_runtime()
        return {"status": "started", **runtime.status()}

    @app.post("/channels/stop", dependencies=[Depends(require_auth)])
    async def channels_stop():
        """Stop configured IM channel adapters."""
        runtime = _get_channel_runtime()
        await runtime.stop()
        return {"status": "stopped", **runtime.status()}

    @app.post("/channels/pairing/command", dependencies=[Depends(require_auth)])
    async def channels_pairing_command(payload: ChannelPairingCommandRequest):
        """Run a pairing command against the shared pairing store."""
        from src.channels.pairing import handle_pairing_command

        if payload.channel not in SUPPORTED_CHANNELS:
            raise HTTPException(status_code=422, detail="Only Feishu and Weixin channels are supported")

        return {
            "channel": payload.channel,
            "reply": handle_pairing_command(payload.channel, payload.command),
        }

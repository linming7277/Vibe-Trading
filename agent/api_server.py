#!/usr/bin/env python3
"""恒值投资 API 服务。

Thin assembler: creates the FastAPI app, mounts middleware, registers route
modules, and re-exports symbols for test compatibility.  All shared
infrastructure lives in ``src.api.{security,models,helpers,state}``.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, HTTPException, Request, status  # noqa: F401
from fastapi.responses import FileResponse  # noqa: F401
from fastapi.middleware.cors import CORSMiddleware
from rich.console import Console

from cli._version import __version__ as APP_VERSION
from src.ui_services import build_run_analysis, load_run_context  # noqa: F401

# UTF-8 on Windows
import sys as _sys
for _s in ("stdout", "stderr"):
    _r = getattr(getattr(_sys, _s, None), "reconfigure", None)
    if callable(_r):
        _r(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Extracted infrastructure — re-exported for route-module and test access
# ---------------------------------------------------------------------------

from src.api.security import (  # noqa: F401, E402
    _API_KEY,
    _CORS_ORIGINS,
    _DEFAULT_CORS_ORIGINS,
    _DEFAULT_LOOPBACK_HOSTS,
    _EXTRA_LOOPBACK_HOSTS,
    _SAFE_BROWSER_METHODS,
    _apply_security_headers,
    _auth_credential_from_header_or_query,
    _configured_api_key,
    _consume_sse_ticket,
    _default_gateway_ips,
    _env_shell_tools_enabled,
    _host_without_port,
    _is_allowed_loopback_host,
    _is_local_client,
    _is_loopback_bind_host,
    _is_loopback_origin,
    _mint_sse_ticket,
    _origin_matches_request_host,
    _parse_cors_origins,
    _parse_extra_cors_origins,
    _parse_extra_loopback_hosts,
    _redact_query_secrets,
    _reject_cross_site_browser_request,
    _reject_untrusted_loopback_host,
    _require_shutdown_authorization,
    _security,
    _shell_tools_enabled_for_request,
    _trusted_docker_loopback_ip,
    _validate_api_auth,
    install_access_log_redaction_filter,
    require_auth,
    require_event_stream_auth,
    require_local_or_auth,
    require_settings_write_auth,
)

from src.api.models import (  # noqa: F401, E402
    Artifact,
    BacktestMetrics,
    RAGSelection,
    RunInfo,
    RunResponse,
)

from src.api.helpers import (  # noqa: F401, E402
    AGENT_DIR,
    ENV_EXAMPLE_PATH,
    ENV_PATH,
    LEGACY_ENV_PATH,
    RUNS_DIR,
    SESSIONS_DIR,
    UPLOADS_DIR,
    _coerce_float,
    _coerce_int,
    _ensure_agent_env_file,
    _format_env_value,
    _FRONTEND_DIST,
    _is_configured_secret,
    _is_spa_html_route,
    _project_relative_path,
    _read_env_values,
    _SAFE_PATH_PARAM_RE,
    _spa_html_deep_link_fallback,
    _strip_env_value,
    _validate_path_param,
    _write_env_values,
)

from src.api.state import (  # noqa: F401, E402
    _channel_bus,
    _channel_manager,
    _channel_runtime,
    _get_channel_runtime,
    _get_session_service,
    _session_service,
)

console = Console()
logger = logging.getLogger(__name__)

from src.api.channels_routes import (  # noqa: E402
    _start_channel_runtime,
    _stop_channel_runtime,
)
from src.api.value_price_zone_routes import register_value_price_zone_routes  # noqa: E402
from src.api.low_value_leader_pool_routes import register_low_value_leader_pool_routes  # noqa: E402
from src.api.adjusted_daily_bar_routes import register_adjusted_daily_bar_routes  # noqa: E402
from src.api.historical_valuation_routes import register_historical_valuation_routes  # noqa: E402
from src.api.capital_allocation_fact_routes import register_capital_allocation_fact_routes  # noqa: E402
from src.api.capital_allocation_research_routes import register_capital_allocation_research_routes  # noqa: E402
from src.api.company_action_routes import register_company_action_routes  # noqa: E402
from src.api.entry_research_routes import register_entry_research_routes  # noqa: E402
from src.api.exit_research_routes import register_exit_research_routes  # noqa: E402
from src.api.risk_research_routes import register_risk_research_routes  # noqa: E402
from src.api.risk_research_preparation_routes import register_risk_research_preparation_routes  # noqa: E402
from src.api.focus_selection_routes import register_focus_selection_routes  # noqa: E402
from src.api.research_freshness_routes import register_research_freshness_routes  # noqa: E402
from src.api.cio_report_routes import register_cio_report_routes  # noqa: E402
from src.api.scheduled_routes import (  # noqa: E402
    _start_scheduled_research_executor,
    _stop_scheduled_research_executor,
)


def _start_preflight_background() -> threading.Thread:
    """Run preflight checks in a daemon thread so network probes (OKX / LLM
    / yfinance, each up to ~10s) never delay the server from opening its port.

    Uvicorn blocks the listen socket until the startup lifespan hook returns;
    the OKX / LLM checks were previously synchronously awaiting connect
    timeouts, which pushed port-open past the launcher's readiness window.
    The preflight table is informational here (its return value is unused),
    so running it off the hot path keeps startup fast without losing output.
    Mirrors ``cli.main._start_preflight_async``.
    """
    def _worker() -> None:
        try:
            from src.preflight import run_preflight

            run_preflight(console)
        except Exception:  # noqa: BLE001 — best-effort; never crash the server
            logging.getLogger(__name__).warning("Preflight checks failed", exc_info=True)

    thread = threading.Thread(target=_worker, daemon=True, name="vibe-preflight")
    thread.start()
    return thread


async def _run_startup_preflight() -> None:
    """Run preflight checks on server startup."""
    from src.config import migrate as _migrate

    try:
        _migrate.migrate_legacy_state()  # one-time pre-#904 state move; must never block startup
    except Exception:  # pragma: no cover — best-effort
        logging.getLogger(__name__).warning("Legacy state migration failed", exc_info=True)
    # Preflight is best-effort diagnostics and must not gate the listen socket.
    _start_preflight_background()
    _start_scheduled_research_executor()
    from src.value_workspace.automation import start_value_research_scheduler
    from src.tdx_data.automation import start_data_refresh_scheduler

    # Data collection owns freshness; strategy schedulers only consume a
    # quality-gated completed snapshot.
    start_data_refresh_scheduler()
    start_value_research_scheduler()
    from src.config.accessor import get_env_config

    auto_start_channels = get_env_config().agent_tuning.vibe_trading_channels_auto_start
    if not auto_start_channels:
        try:
            from src.channels.config import load_channels_config

            auto_start_channels = bool(load_channels_config().get("auto_start", False))
        except Exception:
            logging.getLogger(__name__).warning(
                "Unable to read persisted channel auto-start setting", exc_info=True
            )
    if auto_start_channels:
        await _start_channel_runtime()


async def _stop_scheduled_research_on_shutdown() -> None:
    """Stop the scheduled research executor on server shutdown."""
    try:
        await _stop_channel_runtime()
    finally:
        try:
            await _stop_scheduled_research_executor()
        finally:
            from src.value_workspace.automation import stop_value_research_scheduler
            from src.tdx_data.automation import stop_data_refresh_scheduler

            stop_value_research_scheduler()
            stop_data_refresh_scheduler()


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Run API startup and guaranteed reverse-order shutdown."""
    try:
        await _run_startup_preflight()
        yield
    finally:
        await _stop_scheduled_research_on_shutdown()


app = FastAPI(
    title="恒值投资 API",
    description="价值研究、公司分析、估值与投资组合工具",
    version=APP_VERSION,
    docs_url=None,  # docs/redoc/openapi re-registered behind require_auth
    redoc_url=None,  # in register_system_routes -- see the rationale there
    openapi_url=None,
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(_reject_untrusted_loopback_host)
app.middleware("http")(_spa_html_deep_link_fallback)
app.middleware("http")(_apply_security_headers)


# Route registration + re-exports

# --- Runs ---
from src.api.runs_routes import register_runs_routes  # noqa: E402
register_runs_routes(app)

from src.api.runs_routes import (  # noqa: F401, E402
    _load_json_file,
    _load_csv_to_dict,
    _build_response_from_run_dir,
)

# --- Sessions ---
from src.api.sessions_routes import register_sessions_routes  # noqa: E402
register_sessions_routes(app)

from src.api.sessions_routes import (  # noqa: F401, E402
    _goal_store,
    _live_action_frame_from_tool_result,
    _mandate_proposal_frame_from_tool_result,
)

# --- System ---
from src.api.system_routes import register_system_routes  # noqa: E402
register_system_routes(app)

from src.api.system_routes import _terminate_current_process  # noqa: F401, E402

# --- Settings ---
from src.api.settings_routes import register_settings_routes  # noqa: E402
register_settings_routes(app)

from src.api.settings_routes import (  # noqa: F401, E402
    _baostock_supported,
    _baostock_installed,
    _load_llm_providers,
)

# --- Uploads ---
from src.api.uploads_routes import register_uploads_routes  # noqa: E402
register_uploads_routes(app)

from src.api.uploads_routes import (  # noqa: F401, E402
    MAX_UPLOAD_SIZE,
    _BLOCKED_UPLOAD_EXT,
    _BLOCKED_UPLOAD_NAMES,
    _SHADOW_ID_RE,
    _UPLOAD_CHUNK_SIZE,
)

# --- Channels ---
from src.api.channels_routes import register_channels_routes  # noqa: E402
register_channels_routes(app)
from src.api.qveris_routes import qveris_router  # noqa: E402  # QVERIS-INTEGRATION
app.include_router(qveris_router)  # QVERIS-INTEGRATION

from src.api.channels_routes import (  # noqa: F401, E402
    ChannelPairingCommandRequest,
)

# --- Swarm ---
from src.api.swarm_routes import register_swarm_routes  # noqa: E402
register_swarm_routes(app)

from src.api.swarm_routes import _get_swarm_runtime  # noqa: F401, E402

# --- Alpha Zoo ---
from src.api.alpha_routes import register_alpha_routes  # noqa: E402
register_alpha_routes(app)

# --- Auth helpers (SSE tickets) ---
from src.api.auth_routes import register_auth_routes  # noqa: E402
register_auth_routes(app)

# --- OpenBB Workspace agent bridge (GET /agents.json, POST /v1/query) ---
# No-op unless the optional `openbb` extra is installed; self-reports either way.
from src.openbb_bridge import try_register_openbb_routes  # noqa: E402  # OPENBB-WORKSPACE-INTEGRATION
try_register_openbb_routes(app)


# ============================================================================
# Scheduled Research Routes - defined in src/api/scheduled_routes.py
# ============================================================================
# Job CRUD plus the playbook-template catalogue, all auth-gated. Handlers only
# record and expose jobs; execution is guarded by VIBE_TRADING_ENABLE_SCHEDULER.

from src.api.scheduled_routes import register_scheduled_routes  # noqa: E402
register_scheduled_routes(app)

# --- Structured research workspace (dashboard, committee, portfolio) ---
from src.api.research_routes import register_research_routes  # noqa: E402
register_research_routes(app)

from src.api.research_task_routes import register_research_task_routes  # noqa: E402
register_research_task_routes(app, require_auth)

from src.api.research_supervisor_routes import register_research_supervisor_routes  # noqa: E402
register_research_supervisor_routes(app, require_auth)

from src.api.value_l3_routes import register_value_l3_routes  # noqa: E402
register_value_l3_routes(app, require_auth)

from src.api.company_thesis_routes import register_company_thesis_routes  # noqa: E402
register_company_thesis_routes(app, require_auth)

from src.api.company_thesis_evidence_routes import register_company_thesis_evidence_routes  # noqa: E402
register_company_thesis_evidence_routes(app, require_auth)

from src.api.company_thesis_history_routes import register_company_thesis_history_routes  # noqa: E402
register_company_thesis_history_routes(app, require_auth)

from src.api.company_thesis_review_routes import register_company_thesis_review_routes  # noqa: E402
register_company_thesis_review_routes(app, require_auth)

from src.api.financial_analysis_routes import register_financial_analysis_routes  # noqa: E402
register_financial_analysis_routes(app, require_auth)
from src.api.business_research_routes import register_business_research_routes  # noqa: E402
register_business_research_routes(app, require_auth)
from src.api.disclosure_material_routes import register_disclosure_material_routes  # noqa: E402
register_disclosure_material_routes(app, require_auth)
from src.api.company_research_overview_routes import register_company_research_overview_routes  # noqa: E402
register_company_research_overview_routes(app, require_auth)
from src.api.company_research_conclusion_routes import register_company_research_conclusion_routes  # noqa: E402
register_company_research_conclusion_routes(app, require_auth)
from src.api.leader_quality_profile_routes import register_leader_quality_profile_routes  # noqa: E402
register_leader_quality_profile_routes(app, require_auth)
from src.api.moat_evidence_routes import register_moat_evidence_routes  # noqa: E402
register_moat_evidence_routes(app, require_auth)
from src.api.moat_research_routes import register_moat_research_routes  # noqa: E402
register_moat_research_routes(app, require_auth)
register_value_price_zone_routes(app, require_auth)
register_low_value_leader_pool_routes(app, require_auth)
register_adjusted_daily_bar_routes(app, require_auth)
register_historical_valuation_routes(app, require_auth)
register_capital_allocation_fact_routes(app, require_auth)
register_capital_allocation_research_routes(app, require_auth)
register_company_action_routes(app, require_auth)
register_entry_research_routes(app, require_auth)
register_exit_research_routes(app, require_auth)
register_risk_research_routes(app, require_auth)
register_risk_research_preparation_routes(app, require_auth)
register_focus_selection_routes(app, require_auth)
register_research_freshness_routes(app, require_auth)
register_cio_report_routes(app, require_auth)

from src.api.strategy_routes import register_strategy_routes  # noqa: E402
register_strategy_routes(app)
from src.api.value_workspace_routes import register_value_workspace_routes  # noqa: E402
register_value_workspace_routes(app, require_auth)

from src.api.tdx_routes import register_tdx_routes  # noqa: E402
register_tdx_routes(app)

from src.api.scheduled_routes import (  # noqa: E402, F401
    CreateRunFromPlaybookRequest,
    CreateScheduledRunRequest,
    PlaybookResponse,
    ScheduledRunResponse,
    _dispatch_scheduled_research_job,
    _get_scheduled_research_executor,
    _get_scheduled_research_store,
    _scheduled_research_scheduler_enabled,
)


# ============================================================================
# Main Entry Point
# ============================================================================

def serve_main(argv: list[str] | None = None) -> int:
    """Start the API server from CLI-style arguments."""
    import argparse
    import subprocess
    import uvicorn
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SPAStaticFiles(StaticFiles):
        """Serve SPA routes without disguising missing hashed assets as HTML."""

        @staticmethod
        def _with_cache_policy(response, requested_path: str):
            normalized = requested_path.replace("\\", "/")
            if normalized in {"", ".", "index.html"} or normalized.endswith("/index.html"):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            elif normalized.startswith("assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

        async def get_response(self, path: str, scope: Dict[str, Any]):
            try:
                response = await super().get_response(path, scope)
                return self._with_cache_policy(response, path)
            except StarletteHTTPException as exc:
                if exc.status_code != status.HTTP_404_NOT_FOUND:
                    raise
                # A missing chunk, stylesheet, font, source map, or other file
                # must remain a real 404. Returning index.html with status 200
                # makes dynamic import failures opaque and defeats recovery.
                requested = Path(path)
                if path.replace("\\", "/").startswith("assets/") or requested.suffix:
                    raise
                response = await super().get_response("index.html", scope)
                return self._with_cache_policy(response, "index.html")

    parser = argparse.ArgumentParser(description="恒值投资服务")
    # The Windows launcher and Vite development proxy both use this internal
    # application port.  Keep the direct `serve` default aligned so a manual
    # formal launch cannot silently create a second service on :8000.
    parser.add_argument("--port", type=int, default=8899, help="Listen port (default 8899)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--dev", action="store_true", help="Dev mode: spawn Vite on :5173")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if not _is_loopback_bind_host(args.host) and not _configured_api_key():
        print(
            f"[warn] Binding to {args.host} without API_AUTH_KEY set. "
            f"Remote requests are rejected by the loopback peer-IP check, "
            f"but consider using --host 127.0.0.1 for local-only access."
        )

    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    frontend_root = Path(__file__).resolve().parent.parent / "frontend"

    vite_proc = None
    if args.dev and frontend_root.exists():
        print("[dev] Starting Vite dev server on :5173 ...")
        vite_proc = subprocess.Popen(
            ["npx", "vite", "--host", "0.0.0.0"],
            cwd=str(frontend_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[dev] Vite PID={vite_proc.pid}")
        print("[dev] Frontend: Vite on port 5173 (LAN address is shown by the Windows launcher)")
        print(f"[dev] API: internal proxy port {args.port}")
    elif frontend_dist.exists():
        if not any(getattr(route, "path", None) == "/" for route in app.routes):
            app.mount("/", SPAStaticFiles(directory=str(frontend_dist), html=True), name="frontend")
        print(f"[prod] Frontend served from {frontend_dist}")
    else:
        print(f"[warn] No frontend build found at {frontend_dist}")
        print("[warn] Run: cd frontend && npm run build")

    print("=" * 50)
    print("  恒值投资服务")
    print(f"  API internal port: {args.port} (use the frontend LAN address shown by the launcher)")
    print("=" * 50)

    # Redact api_key=/ticket= values from Uvicorn's access log (it logs the full
    # request line including the query string). Installed before run() so the
    # filter is attached when Uvicorn configures its loggers.
    install_access_log_redaction_filter()

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if vite_proc:
            vite_proc.terminate()
            print("[dev] Vite stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_main())

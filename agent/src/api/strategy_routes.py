"""HTTP API for deterministic value/emotion engines and internal paper trading."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.paper_trading.store import PaperTradingStore
from src.research_workspace.store import ResearchWorkspaceStore, normalize_market, normalize_symbol
from src.strategy_engines.common.contracts import CommitteeDecision, DecisionStatus, SignalStatus, StrategyLine
from src.strategy_engines.service import StrategyEngineService
from src.strategy_engines.store import StrategyEngineStore
from src.strategy_engines.formula_registry import formula_manifest, sync_formula_artifacts
from src.strategy_engines.history import HistoricalFeatureStore, MultiSourceHistoryAdapter
from src.value_workspace.service import ValueWorkspaceService
from src.value_workspace.store import ValueWorkspaceStore
from src.strategy_engines.value_line import get_value_line_service

AuthDep = Callable[..., Awaitable[Any] | Any]
_engine_store: StrategyEngineStore | None = None
_paper_store: PaperTradingStore | None = None


def get_engine_store() -> StrategyEngineStore:
    global _engine_store
    if _engine_store is None:
        _engine_store = StrategyEngineStore()
    return _engine_store


def get_paper_store() -> PaperTradingStore:
    global _paper_store
    if _paper_store is None:
        _paper_store = PaperTradingStore()
    return _paper_store


class StrategyRunRequest(BaseModel):
    strategy_line: Literal["value", "emotion"]
    market: Literal["CN", "HK"]
    as_of: str = Field(default_factory=lambda: date.today().isoformat())
    symbols: list[str] = Field(default_factory=list)
    force_refresh: bool = False
    inputs: dict[str, Any] | None = None
    profile_id: str | None = None


class ValueRefreshRequest(BaseModel):
    modules: list[Literal["financial_history", "market_history", "macro", "policy", "scores", "all"]] = Field(default_factory=lambda: ["all"])
    as_of: str = Field(default_factory=lambda: date.today().isoformat())


class CommitteeDecisionRequest(BaseModel):
    signal_id: str
    strategy_line: Literal["value", "emotion"]
    status: Literal["approve", "reject", "wait"]
    direction: Literal["buy", "sell", "wait"]
    position_cap: float = Field(ge=0, le=1)
    entry_low: float | None = Field(default=None, gt=0)
    entry_high: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_low: float | None = Field(default=None, gt=0)
    target_high: float | None = Field(default=None, gt=0)
    holding_period: str
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=5000)
    review_triggers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    engine_run_ids: list[str] = Field(default_factory=list)


class PaperAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    strategy_line: Literal["value", "emotion"]
    horizon: Literal["long", "short", "swing"]
    market: Literal["CN", "HK"]
    currency: Literal["CNY", "HKD"]
    initial_cash: float = Field(ge=0)


class PaperOrderRequest(BaseModel):
    signal_id: str
    committee_id: str
    decision_id: str
    quantity: float = Field(gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    board_lot: int | None = Field(default=None, gt=0)


def _background_run(run: dict[str, Any], inputs: dict[str, Any] | None, profile: dict[str, Any] | None = None) -> None:
    service = StrategyEngineService(get_engine_store())
    result = service.execute_prepared(run, inputs=inputs)
    if result.get("strategy_line") == "value" and result.get("market") == "CN" and profile:
        workspace = ValueWorkspaceService()
        try:
            workspace.materialize_run(result["id"], profile)
        finally:
            workspace.close()


def register_strategy_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    import sys

    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError("api_server module must be loaded before strategy routes")
    require_auth = require_auth or host.require_auth

    @app.post("/strategy-runs", dependencies=[Depends(require_auth)])
    async def create_strategy_run(payload: StrategyRunRequest, background_tasks: BackgroundTasks):
        try:
            symbols = [normalize_symbol(payload.market, symbol) for symbol in payload.symbols]
            profile = None
            if payload.strategy_line == "value":
                profile_store = ValueWorkspaceStore()
                try:
                    profile = profile_store.get_profile(payload.profile_id)
                finally:
                    profile_store.close()
                if not profile:
                    raise ValueError("calculation profile not found")
            service = StrategyEngineService(get_engine_store())
            run, created = service.prepare(
                strategy_line=payload.strategy_line, market=payload.market, as_of=payload.as_of,
                symbols=symbols, force_refresh=payload.force_refresh,
                profile_id=profile["id"] if profile else None,
                profile_version=profile["version"] if profile else None,
            )
            if created:
                background_tasks.add_task(_background_run, run, payload.inputs, profile)
            return {**run, "created": created}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/strategy-runs/{run_id}", dependencies=[Depends(require_auth)])
    async def strategy_run(run_id: str):
        value = get_engine_store().get_run(run_id)
        if not value:
            raise HTTPException(404, "strategy run not found")
        return value

    @app.get("/strategy/formulas", dependencies=[Depends(require_auth)])
    async def strategy_formulas():
        return {"items": formula_manifest(), "strategy_store_ids": sync_formula_artifacts()}

    @app.get("/strategy/data/status", dependencies=[Depends(require_auth)])
    async def strategy_data_status():
        history = HistoricalFeatureStore()
        adapter = MultiSourceHistoryAdapter(history)
        catalog = history.catalog()
        return {
            "providers": [adapter.provider_status("CN"), adapter.provider_status("HK")],
            "partitions": len(catalog),
            "catalog": catalog[:200],
        }

    @app.get("/strategy/value/dashboard", dependencies=[Depends(require_auth)])
    async def value_dashboard(market: str = Query("CN")):
        return get_engine_store().dashboard("value", normalize_market(market))

    @app.get("/strategy/value/data/status", dependencies=[Depends(require_auth)])
    async def value_data_status():
        return get_value_line_service().status()

    @app.post("/strategy/value/refresh", dependencies=[Depends(require_auth)])
    async def value_refresh(payload: ValueRefreshRequest):
        try:
            return get_value_line_service().start_refresh(list(payload.modules), payload.as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/strategy/value/refresh/{job_id}", dependencies=[Depends(require_auth)])
    async def value_refresh_job(job_id: str):
        value = get_value_line_service().data_store.get_job(job_id)
        if not value:
            raise HTTPException(404, "value refresh job not found")
        return value

    @app.get("/strategy/value/macro", dependencies=[Depends(require_auth)])
    async def value_macro(as_of: str | None = None):
        value = get_value_line_service().macro(as_of)
        if not value:
            return {"status": "unavailable", "as_of": as_of, "axes": {}, "missing_fields": list(("growth", "inflation", "liquidity", "credit", "financial_conditions"))}
        return value

    @app.get("/strategy/value/policies", dependencies=[Depends(require_auth)])
    async def value_policies(status: str | None = None, limit: int = Query(100, ge=1, le=500)):
        return {"items": get_value_line_service().policies(status, limit)}

    @app.get("/strategy/value/sectors", dependencies=[Depends(require_auth)])
    async def value_sectors(
        market: str = Query("CN"), as_of: str | None = None,
        status: str | None = None, query: str = "",
    ):
        normalized = normalize_market(market)
        if normalized == "CN":
            cached = get_value_line_service().sectors(as_of, status=status, query=query)
            return {"market": normalized, **cached, "run": None}
        store = get_engine_store()
        dashboard = store.dashboard("value", normalized)
        return {"market": normalized, "items": store.list_scores("value", normalized, engine="value_sector", limit=500), "run": dashboard["latest_run"]}

    @app.get("/strategy/value/leaders", dependencies=[Depends(require_auth)])
    async def value_leaders(
        market: str = Query("CN"), sector_code: str | None = None, as_of: str | None = None,
    ):
        normalized = normalize_market(market)
        if normalized == "CN":
            return {"market": normalized, **get_value_line_service().leaders(sector_code, as_of), "run": None}
        store = get_engine_store()
        dashboard = store.dashboard("value", normalized)
        return {"market": normalized, "items": store.list_scores("value", normalized, engine="value_leader", limit=500), "run": dashboard["latest_run"]}

    @app.get("/strategy/value/signals", dependencies=[Depends(require_auth)])
    async def value_signals(market: str = Query("CN")):
        return get_engine_store().list_signals(strategy_line="value", market=normalize_market(market))

    @app.get("/strategy/value/companies/{market}/{symbol}", dependencies=[Depends(require_auth)])
    async def value_company(market: str, symbol: str):
        normalized_market = normalize_market(market)
        normalized_symbol = normalize_symbol(normalized_market, symbol)
        workspace = ResearchWorkspaceStore(seed=False)
        try:
            dossier = workspace.get_dossier(normalized_market, normalized_symbol)
        finally:
            workspace.close()
        scores = get_engine_store().list_scores("value", normalized_market, subject_id=normalized_symbol, limit=50)
        if not dossier and not scores:
            raise HTTPException(404, "value company research not found")
        return {"dossier": dossier, "scores": scores}

    @app.get("/strategy/emotion/dashboard", dependencies=[Depends(require_auth)])
    async def emotion_dashboard(market: str = Query("CN")):
        return get_engine_store().dashboard("emotion", normalize_market(market))

    @app.get("/strategy/emotion/sectors", dependencies=[Depends(require_auth)])
    async def emotion_sectors(market: str = Query("CN")):
        normalized = normalize_market(market)
        store = get_engine_store()
        dashboard = store.dashboard("emotion", normalized)
        return {"market": normalized, "items": store.list_scores("emotion", normalized, engine="emotion_sector_heat", limit=500), "run": dashboard["latest_run"]}

    @app.get("/strategy/emotion/candidates", dependencies=[Depends(require_auth)])
    async def emotion_candidates(market: str = Query("CN"), horizon: Literal["short", "swing"] = Query("short")):
        normalized = normalize_market(market)
        store = get_engine_store()
        dashboard = store.dashboard("emotion", normalized)
        return {"market": normalized, "horizon": horizon, "items": store.list_scores("emotion", normalized, engine=f"emotion_{horizon}", limit=500), "run": dashboard["latest_run"]}

    @app.get("/strategy/emotion/signals", dependencies=[Depends(require_auth)])
    async def emotion_signals(market: str = Query("CN"), horizon: Literal["short", "swing"] | None = None):
        return get_engine_store().list_signals(strategy_line="emotion", market=normalize_market(market), horizon=horizon)

    @app.get("/decision-chains/{run_id}", dependencies=[Depends(require_auth)])
    async def decision_chain(run_id: str):
        value = get_engine_store().get_decision_chain(run_id)
        if not value:
            raise HTTPException(404, "decision chain not found")
        return value

    @app.get("/signals/{signal_id}", dependencies=[Depends(require_auth)])
    async def strategy_signal(signal_id: str):
        value = get_engine_store().get_signal(signal_id)
        if not value:
            raise HTTPException(404, "strategy signal not found")
        return value

    @app.post("/committees/{committee_id}/decision", dependencies=[Depends(require_auth)])
    async def publish_committee_decision(committee_id: str, payload: CommitteeDecisionRequest):
        try:
            decision = CommitteeDecision(
                id=f"decision_{uuid.uuid4().hex[:16]}", committee_id=committee_id,
                signal_id=payload.signal_id, strategy_line=StrategyLine(payload.strategy_line),
                status=DecisionStatus(payload.status), direction=payload.direction,
                position_cap=payload.position_cap, entry_low=payload.entry_low, entry_high=payload.entry_high,
                stop_price=payload.stop_price, target_low=payload.target_low, target_high=payload.target_high,
                holding_period=payload.holding_period, confidence=payload.confidence, summary=payload.summary,
                review_triggers=tuple(payload.review_triggers), evidence_ids=tuple(payload.evidence_ids),
                engine_run_ids=tuple(payload.engine_run_ids), created_at=datetime.now(timezone.utc).isoformat(),
            )
            return get_engine_store().publish_decision(decision)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/paper/accounts", dependencies=[Depends(require_auth)])
    async def paper_accounts():
        return get_paper_store().list_accounts()

    @app.post("/paper/accounts", dependencies=[Depends(require_auth)])
    async def create_paper_account(payload: PaperAccountRequest):
        if payload.strategy_line == "value" and payload.horizon != "long":
            raise HTTPException(422, "value accounts must use long horizon")
        return get_paper_store().create_account(**payload.model_dump())

    @app.get("/paper/accounts/{account_id}", dependencies=[Depends(require_auth)])
    async def paper_account(account_id: str):
        value = get_paper_store().get_account(account_id)
        if not value:
            raise HTTPException(404, "paper account not found")
        return value

    @app.post("/paper/accounts/{account_id}/orders", dependencies=[Depends(require_auth)])
    async def submit_paper_order(account_id: str, payload: PaperOrderRequest):
        signal = get_engine_store().get_signal(payload.signal_id)
        if not signal:
            raise HTTPException(404, "strategy signal not found")
        decision = get_engine_store().get_decision(payload.decision_id)
        if not decision:
            raise HTTPException(404, "committee decision not found")
        if decision["committee_id"] != payload.committee_id or decision["signal_id"] != payload.signal_id:
            raise HTTPException(422, "committee decision does not match signal")
        if decision["decision_status"] != "approve":
            raise HTTPException(422, "only approved committee decisions can submit paper orders")
        try:
            order = get_paper_store().submit_approved_signal(account_id=account_id, signal=signal, **payload.model_dump(exclude={"signal_id"}))
            get_engine_store().transition_signal(payload.signal_id, SignalStatus.PAPER_SUBMITTED)
            return order
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/paper/accounts/{account_id}/orders", dependencies=[Depends(require_auth)])
    async def paper_orders(account_id: str):
        if not get_paper_store().get_account(account_id):
            raise HTTPException(404, "paper account not found")
        return get_paper_store().list_orders(account_id)

    @app.get("/paper/accounts/{account_id}/positions", dependencies=[Depends(require_auth)])
    async def paper_positions(account_id: str):
        if not get_paper_store().get_account(account_id):
            raise HTTPException(404, "paper account not found")
        return get_paper_store().list_positions(account_id)

    @app.get("/paper/accounts/{account_id}/nav", dependencies=[Depends(require_auth)])
    async def paper_nav(account_id: str):
        try:
            return get_paper_store().nav(account_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

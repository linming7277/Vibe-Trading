"""HTTP routes for the local research, committee and portfolio workspace."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src.research_workspace.store import ResearchWorkspaceStore, normalize_market, normalize_symbol

AuthDep = Callable[..., Awaitable[Any] | Any]
_store: ResearchWorkspaceStore | None = None


def get_research_store() -> ResearchWorkspaceStore:
    global _store
    if _store is None:
        _store = ResearchWorkspaceStore()
    return _store


class RefreshRequest(BaseModel):
    module: str = Field(default="all", pattern="^(all|macro|sectors|screener)$")
    market: str | None = None


class CompanyResearchRequest(BaseModel):
    create_report: bool = True


class CommitteeRequest(BaseModel):
    market: str
    symbol: str
    company_name: str = ""
    strategy_line: str = Field(default="value", pattern="^(value|emotion)$")
    horizon: str = Field(default="long", pattern="^(long|short|swing)$")
    signal_id: str = ""


class PortfolioRequest(BaseModel):
    name: str
    base_currency: str = "CNY"
    benchmark: str = ""
    initial_cash: float = 0
    cash: dict[str, float] | None = None


def register_research_routes(app: FastAPI, require_auth: AuthDep | None = None, require_event_stream_auth: AuthDep | None = None) -> None:
    import sys
    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        raise RuntimeError("api_server module must be loaded before research routes")
    require_auth = require_auth or host.require_auth
    require_event_stream_auth = require_event_stream_auth or host.require_event_stream_auth

    @app.get("/dashboard/today", dependencies=[Depends(require_auth)])
    async def dashboard_today():
        return get_research_store().latest_dashboard()

    @app.post("/dashboard/refresh", dependencies=[Depends(require_auth)])
    async def dashboard_refresh(payload: RefreshRequest):
        market = normalize_market(payload.market) if payload.market else None
        markets = [market] if market else ["CN", "HK", "US"]
        run: dict[str, Any] | None = None
        try:
            service = host._get_session_service()
            if not service:
                raise RuntimeError("session runtime unavailable")
            session = service.create_session(
                title=f"workspace-refresh:{payload.module}:{market or 'ALL'}",
                config={"workspace_refresh": payload.module, "markets": markets},
            )
            run = get_research_store().create_research_run(
                payload.module,
                market,
                message="刷新已投递到 Agent；完成前继续展示上一份有效快照。",
                status="queued",
                linked_run_id=session.session_id,
            )
            prompt = (
                f"刷新恒值投资工作台的 {payload.module} 模块，市场为 {', '.join(markets)}。"
                "行业必须按申万、恒生、GICS 原生口径分别计算；所有结论附来源和 data_as_of。"
                "若数据不完整，明确标记 unavailable/stale，不得虚构数值。"
                f"完成后必须调用 publish_workspace_research，run_id={run['id']}；"
                "一次提交全部目标市场，工具返回成功后再向用户总结。"
            )
            await service.send_message(session.session_id, prompt)
            return run
        except Exception as exc:
            if run:
                return get_research_store().update_research_run(
                    run["id"],
                    "failed",
                    f"刷新未能启动；已保留上一份有效快照。原因：{exc}",
                )
            return get_research_store().create_research_run(
                payload.module,
                market,
                message="刷新未能启动；已保留上一份有效快照并标记本次失败。",
                status="failed",
            )

    @app.get("/research-runs/{run_id}", dependencies=[Depends(require_auth)])
    async def research_run(run_id: str):
        value = get_research_store().get_research_run(run_id)
        if not value:
            raise HTTPException(404, "research run not found")
        return value

    @app.get("/macro/briefs/latest", dependencies=[Depends(require_auth)])
    async def macro_latest(market: str = Query("CN")):
        store = get_research_store()
        value = store.latest_macro(market)
        if not value:
            raise HTTPException(404, "macro brief not found")
        value["evidence"] = store.list_research_evidence(value["market"], "macro", value["as_of"])
        return value

    @app.get("/sectors/rankings", dependencies=[Depends(require_auth)])
    async def sector_rankings(market: str = Query("CN"), limit: int = Query(50, ge=1, le=200)):
        normalized = normalize_market(market)
        store = get_research_store()
        items = store.list_sectors(normalized, limit=limit)
        evidence = store.list_research_evidence(normalized, "sectors", items[0]["as_of"]) if items else []
        return {"market": normalized, "items": items, "evidence": evidence}

    @app.get("/securities/screener", dependencies=[Depends(require_auth)])
    async def security_screener(market: str = Query("CN"), query: str = "", limit: int = Query(50, ge=1, le=200)):
        normalized = normalize_market(market)
        store = get_research_store()
        items = store.list_candidates(normalized, limit=limit, query=query)
        evidence = store.list_research_evidence(normalized, "candidates", items[0]["as_of"]) if items else []
        return {"market": normalized, "items": items, "evidence": evidence}

    @app.get("/securities/{market}/{symbol}/dossier", dependencies=[Depends(require_auth)])
    async def security_dossier(market: str, symbol: str):
        value = get_research_store().get_dossier(market, symbol)
        if not value:
            raise HTTPException(404, "company dossier not found")
        return value

    @app.post("/securities/{market}/{symbol}/research", dependencies=[Depends(require_auth)])
    async def research_company(market: str, symbol: str, payload: CompanyResearchRequest):
        store = get_research_store()
        normalized_market = normalize_market(market)
        normalized_symbol = normalize_symbol(market, symbol)
        dossier = store.get_dossier(normalized_market, normalized_symbol)
        if normalized_market == "CN":
            try:
                from src.tdx_data.service import get_tdx_service
                overview = get_tdx_service().security_overview(normalized_symbol)
                if overview:
                    dossier = store.upsert_tdx_dossier(overview)
            except Exception:
                pass
        elif normalized_market in {"HK", "US"}:
            try:
                from src.research_workspace.global_equity import GlobalEquityResearchService

                dossier = await asyncio.to_thread(
                    GlobalEquityResearchService(store).refresh,
                    normalized_market,
                    normalized_symbol,
                )
            except Exception as exc:
                if not dossier or dossier.get("source_status") == "sample":
                    raise HTTPException(503, f"real company data refresh failed: {exc}") from exc
        if not dossier:
            raise HTTPException(404, "company dossier not found")
        run = store.create_research_run(
            "company", normalized_market, normalized_symbol,
            message="已基于最新结构化事实底稿生成深度研究交付物。",
        )
        report = store.create_company_report(normalized_market, normalized_symbol) if payload.create_report else None
        return {"run": run, "dossier": dossier, "report": report}

    @app.get("/reports", dependencies=[Depends(require_auth)])
    async def reports(report_type: str | None = None, limit: int = Query(100, ge=1, le=500)):
        return get_research_store().list_reports(limit=limit, report_type=report_type)

    @app.get("/reports/{report_id}", dependencies=[Depends(require_auth)])
    async def report_detail(report_id: str):
        value = get_research_store().get_report(report_id)
        if not value:
            raise HTTPException(404, "report not found")
        return value

    @app.post("/committees", dependencies=[Depends(require_auth)])
    async def create_committee(payload: CommitteeRequest):
        market = normalize_market(payload.market)
        symbol = normalize_symbol(market, payload.symbol)
        dossier = get_research_store().get_dossier(market, symbol)
        name = payload.company_name or (dossier or {}).get("name") or symbol
        if payload.strategy_line == "value" and payload.horizon != "long":
            raise HTTPException(422, "value committees require long horizon")
        if payload.strategy_line == "value" and market == "CN":
            from src.value_workspace.store import ValueWorkspaceStore

            value_store = ValueWorkspaceStore()
            try:
                if not value_store.reviewed_job_for_symbol(symbol):
                    raise HTTPException(422, "value committee requires a completed or partial automatic research job selected for review")
            finally:
                value_store.close()
        signal = None
        if payload.signal_id:
            from src.strategy_engines.store import StrategyEngineStore
            engine_store = StrategyEngineStore()
            try:
                signal = engine_store.get_signal(payload.signal_id)
            finally:
                engine_store.close()
            if not signal:
                raise HTTPException(404, "strategy signal not found")
            if signal["market"] != market or signal["symbol"] != symbol:
                raise HTTPException(422, "committee target does not match signal")
            if signal["strategy_line"] != payload.strategy_line or signal["horizon"] != payload.horizon:
                raise HTTPException(422, "committee mandate does not match signal")
            if signal["status"] != "proposed":
                raise HTTPException(422, "only proposed signals can enter committee review")
        committee = get_research_store().create_committee(market, symbol, name, None)
        try:
            from src.api.swarm_routes import _get_swarm_runtime
            preset = "value_investing_committee" if payload.strategy_line == "value" else "short_term_trading_committee"
            context = {
                "target": f"{symbol} {name}", "company": f"{symbol} {name}", "market": market,
                "strategy_line": payload.strategy_line, "horizon": payload.horizon,
                "signal_id": payload.signal_id, "committee_id": committee["id"],
                "engine_run_id": (signal or {}).get("engine_run_id", ""),
            }
            swarm = _get_swarm_runtime().start_run(preset, context, include_shell_tools=False)
            swarm_id = swarm.id
            return get_research_store().attach_committee_run(committee["id"], swarm_id)
        except Exception as exc:
            swarm_id = None
            committee["warning"] = f"投委会运行未启动：{exc}"
            return committee

    def _sync_committee(committee_id: str) -> dict[str, Any] | None:
        store = get_research_store()
        value = store.get_committee(committee_id)
        if not value or not value.get("swarm_run_id"):
            return value
        try:
            from src.api.swarm_routes import _get_swarm_runtime
            runtime = _get_swarm_runtime()
            run = runtime._store.load_run(value["swarm_run_id"])
            if run:
                run = runtime._store.reconcile_run(run, write=True)
                from src.swarm.serialization import serialize_task
                for task in run.tasks:
                    serialized = serialize_task(task)
                    store.upsert_committee_participant_output(
                        committee_id,
                        serialized["id"],
                        serialized["agent_id"],
                        serialized["status"],
                        serialized.get("summary") or serialized.get("error") or "",
                        data_as_of=str(serialized.get("completed_at") or "")[:10] or None,
                    )
                if run.status.value != value["status"]:
                    return store.update_committee_status(committee_id, run.status.value, run.final_report)
                return store.get_committee(committee_id)
        except Exception:
            pass
        return value

    @app.get("/committees", dependencies=[Depends(require_auth)])
    async def committees():
        return [_sync_committee(item["id"]) or item for item in get_research_store().list_committees()]

    @app.get("/committees/{committee_id}", dependencies=[Depends(require_auth)])
    async def committee_detail(committee_id: str):
        value = _sync_committee(committee_id)
        if not value:
            raise HTTPException(404, "committee not found")
        if value.get("swarm_run_id"):
            try:
                from src.api.swarm_routes import _get_swarm_runtime
                run = _get_swarm_runtime()._store.load_run(value["swarm_run_id"])
                if run:
                    from src.swarm.serialization import serialize_task
                    value["tasks"] = [serialize_task(task) for task in run.tasks]
                    value["final_report"] = run.final_report
            except Exception:
                pass
        return value

    @app.get("/committees/{committee_id}/events", dependencies=[Depends(require_event_stream_auth)])
    async def committee_events(committee_id: str, request: Request):
        committee = get_research_store().get_committee(committee_id)
        if not committee:
            raise HTTPException(404, "committee not found")
        swarm_id = committee.get("swarm_run_id")
        async def stream():
            index = 0
            while True:
                if await request.is_disconnected():
                    break
                value = _sync_committee(committee_id) or {}
                if swarm_id:
                    try:
                        from src.api.swarm_routes import _get_swarm_runtime
                        events = _get_swarm_runtime()._store.read_events(swarm_id, after_index=index)
                        for event in events:
                            index += 1
                            yield f"id: {index}\nevent: {event.type}\ndata: {json.dumps(event.model_dump(), ensure_ascii=False)}\n\n"
                    except Exception:
                        pass
                if value.get("status") in {"completed", "failed", "cancelled", "draft"}:
                    yield f"event: done\ndata: {json.dumps({'status': value.get('status')})}\n\n"
                    break
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/committees/{committee_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_committee(committee_id: str):
        value = get_research_store().get_committee(committee_id)
        if not value:
            raise HTTPException(404, "committee not found")
        if value.get("swarm_run_id"):
            try:
                from src.api.swarm_routes import _get_swarm_runtime
                _get_swarm_runtime().cancel_run(value["swarm_run_id"])
            except Exception:
                pass
        return get_research_store().update_committee_status(committee_id, "cancelled")

    @app.get("/trade-plans", dependencies=[Depends(require_auth)])
    async def trade_plans(status: str | None = None):
        return get_research_store().list_trade_plans(status)

    @app.post("/trade-plans", dependencies=[Depends(require_auth)])
    async def create_trade_plan(payload: dict):
        try:
            return get_research_store().create_trade_plan(payload)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.patch("/trade-plans/{plan_id}", dependencies=[Depends(require_auth)])
    async def update_trade_plan(plan_id: str, payload: dict):
        try:
            value = get_research_store().update_trade_plan(plan_id, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not value:
            raise HTTPException(404, "trade plan not found")
        return value

    @app.get("/portfolios", dependencies=[Depends(require_auth)])
    async def portfolios():
        return get_research_store().list_portfolios()

    @app.post("/portfolios", dependencies=[Depends(require_auth)])
    async def create_portfolio(payload: PortfolioRequest):
        try:
            return get_research_store().create_portfolio(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/portfolios/{portfolio_id}", dependencies=[Depends(require_auth)])
    async def portfolio_detail(portfolio_id: str):
        value = get_research_store().get_portfolio(portfolio_id)
        if not value:
            raise HTTPException(404, "portfolio not found")
        return value

    @app.patch("/portfolios/{portfolio_id}", dependencies=[Depends(require_auth)])
    async def update_portfolio(portfolio_id: str, payload: dict):
        value = get_research_store().update_portfolio(portfolio_id, payload)
        if not value:
            raise HTTPException(404, "portfolio not found")
        return value

    @app.delete("/portfolios/{portfolio_id}", dependencies=[Depends(require_auth)])
    async def delete_portfolio(portfolio_id: str):
        if not get_research_store().delete_portfolio(portfolio_id):
            raise HTTPException(404, "portfolio not found")
        return Response(status_code=204)

    @app.post("/portfolios/{portfolio_id}/transactions", dependencies=[Depends(require_auth)])
    async def add_transaction(portfolio_id: str, payload: dict):
        try:
            return get_research_store().add_transaction(portfolio_id, payload)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/portfolios/{portfolio_id}/import", dependencies=[Depends(require_auth)])
    async def import_portfolio(portfolio_id: str, file: UploadFile = File(...)):
        if not get_research_store().get_portfolio(portfolio_id):
            raise HTTPException(404, "portfolio not found")
        raw = await file.read()
        try:
            return get_research_store().import_transactions(portfolio_id, raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/portfolios/{portfolio_id}/positions", dependencies=[Depends(require_auth)])
    async def portfolio_positions(portfolio_id: str):
        if not get_research_store().get_portfolio(portfolio_id):
            raise HTTPException(404, "portfolio not found")
        return get_research_store().portfolio_positions(portfolio_id)

    @app.get("/portfolios/{portfolio_id}/analytics", dependencies=[Depends(require_auth)])
    async def portfolio_analytics(portfolio_id: str):
        try:
            return get_research_store().portfolio_analytics(portfolio_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

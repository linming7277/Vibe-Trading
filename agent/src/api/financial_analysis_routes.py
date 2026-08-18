"""Authenticated Financial Analyst V1 APIs."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from src.financial_analysis.service import get_financial_analysis_service

AuthDep = Callable[..., Awaitable[Any] | Any]


class FinancialAnalyzeRequest(BaseModel):
    as_of: str | None = None
    refresh: bool = True


class FinancialChatRequest(BaseModel):
    question: str
    as_of: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)


def register_financial_analysis_routes(app: FastAPI, require_auth: AuthDep) -> None:
    auth = [Depends(require_auth)]

    def stream_chat(run: Callable[[Callable[[str, str, dict[str, Any]], None]], dict[str, Any]]) -> StreamingResponse:
        """Run a financial chat in a worker and stream its real progress events."""
        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def publish(stage: str, message: str, details: dict[str, Any]) -> None:
            events.put({"event": "progress", "data": {"stage": stage, "message": message, "details": details}})

        def worker() -> None:
            try:
                result = run(publish)
                events.put({"event": "result", "data": result})
            except ValueError as exc:
                events.put({"event": "error", "data": {"status": 422, "message": str(exc)}})
            except RuntimeError as exc:
                events.put({"event": "error", "data": {"status": 503, "message": str(exc)}})
            except Exception as exc:  # noqa: BLE001 - keep stream alive with a safe error envelope
                events.put({"event": "error", "data": {"status": 500, "message": f"{type(exc).__name__}: {exc}"}})
            finally:
                events.put({"event": "done", "data": {}})

        threading.Thread(target=worker, name="financial-agent-chat", daemon=True).start()

        async def generate():
            while True:
                event = await asyncio.to_thread(events.get)
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                if event["event"] == "done":
                    break

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/value/financial-agent/chat", dependencies=auth)
    async def chat_financial_agent(payload: FinancialChatRequest):
        try:
            return await asyncio.to_thread(
                get_financial_analysis_service().chat_workspace,
                question=payload.question, history=payload.history, candidates=payload.candidates,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/value/financial-agent/chat/stream", dependencies=auth)
    async def stream_financial_agent_chat(payload: FinancialChatRequest):
        return stream_chat(lambda progress: get_financial_analysis_service().chat_workspace(
            question=payload.question,
            history=payload.history,
            candidates=payload.candidates,
            progress=progress,
        ))

    @app.get("/api/value/companies/{stock_code}/financial", dependencies=auth)
    async def get_company_financial_analysis(stock_code: str, as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_financial_analysis_service().get, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/value/companies/{stock_code}/financial/dossier", dependencies=auth)
    async def get_company_financial_dossier(stock_code: str, as_of: str | None = Query(default=None)):
        try:
            return await asyncio.to_thread(get_financial_analysis_service().dossier, stock_code, as_of=as_of)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/financial/analyze", dependencies=auth)
    async def analyze_company_financials(stock_code: str, payload: FinancialAnalyzeRequest):
        try:
            return await asyncio.to_thread(
                get_financial_analysis_service().analyze, stock_code,
                as_of=payload.as_of, refresh=payload.refresh,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/financial/chat", dependencies=auth)
    async def chat_company_financials(stock_code: str, payload: FinancialChatRequest):
        try:
            return await asyncio.to_thread(
                get_financial_analysis_service().chat, stock_code,
                question=payload.question, as_of=payload.as_of, history=payload.history,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/value/companies/{stock_code}/financial/chat/stream", dependencies=auth)
    async def stream_company_financial_chat(stock_code: str, payload: FinancialChatRequest):
        return stream_chat(lambda progress: get_financial_analysis_service().chat(
            stock_code,
            question=payload.question,
            as_of=payload.as_of,
            history=payload.history,
            progress=progress,
        ))

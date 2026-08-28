from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import dataclass


def _module():
    return importlib.import_module("mcp_server")


def _tool_fn(tool):
    return getattr(tool, "fn", None) or getattr(tool, "__wrapped__", tool)


def test_supervisor_mcp_tools_are_registered() -> None:
    mod = _module()
    names = {tool.name for tool in asyncio.run(mod.mcp.list_tools())}
    assert "ask_investment_research_supervisor" in names
    assert "get_investment_research_daily_brief" in names
    assert "ask_financial_analyst" in names
    assert "ask_macro_policy_researcher" in names
    assert "ask_valuation_researcher" in names
    assert "ask_risk_researcher" in names


def test_ask_supervisor_returns_serialized_read_only_brief(monkeypatch) -> None:
    mod = _module()

    @dataclass(frozen=True)
    class Brief:
        intent: str
        research_as_of: str
        answer: str
        capabilities: tuple[str, ...]
        status: str = "READY"

    class Supervisor:
        def handle_question(self, *, question: str, as_of: str | None = None):
            assert question == "分析600460的财务"
            assert as_of == "2026-08-25"
            return Brief("FINANCIAL", as_of, "财务结论", ("FINANCIAL",))

    import src.investment_research_supervisor as supervisor_module

    monkeypatch.setattr(
        supervisor_module,
        "get_investment_research_supervisor_service",
        lambda: Supervisor(),
    )
    payload = json.loads(
        _tool_fn(mod.ask_investment_research_supervisor)(
            "分析600460的财务", "2026-08-25",
        )
    )
    assert payload["status"] == "ok"
    assert payload["brief"]["intent"] == "FINANCIAL"
    assert payload["brief"]["answer"] == "财务结论"


def test_daily_brief_tool_does_not_generate_on_cache_miss(monkeypatch) -> None:
    mod = _module()

    class DailyBriefService:
        def get_completed(self, research_as_of: str | None = None):
            assert research_as_of is None
            return None

    import src.investment_research_supervisor as supervisor_module

    monkeypatch.setattr(
        supervisor_module,
        "get_investment_research_daily_brief_service",
        lambda: DailyBriefService(),
    )
    payload = json.loads(_tool_fn(mod.get_investment_research_daily_brief)(""))
    assert payload["status"] == "error"
    assert payload["error_type"] == "not_found"


def test_financial_analyst_uses_existing_read_only_chat(monkeypatch) -> None:
    mod = _module()

    class FinancialAnalyst:
        def chat_current_leader_pool(self, *, question: str, history: list[dict[str, str]]):
            assert question == "分析600460的财务"
            assert history == []
            return {"answer": "财务结论", "scope": "company"}

    import src.financial_analysis.service as financial_module

    monkeypatch.setattr(
        financial_module,
        "get_financial_analysis_service",
        lambda: FinancialAnalyst(),
    )
    payload = json.loads(_tool_fn(mod.ask_financial_analyst)("分析600460的财务"))
    assert payload["status"] == "ok"
    assert payload["result"]["answer"] == "财务结论"


def test_specialist_tools_serialize_existing_specialist_brief(monkeypatch) -> None:
    mod = _module()

    @dataclass(frozen=True)
    class Brief:
        agent: str
        title: str
        answer: str

    class Specialist:
        def handle_question(self, *, agent: str, question: str, history: list[dict[str, str]]):
            assert question == "当前宏观环境怎么样"
            assert history == []
            return Brief(agent, "宏观政策研究员", "宏观结论")

    import src.research_specialist_chat as specialist_module

    monkeypatch.setattr(
        specialist_module,
        "get_research_specialist_chat_service",
        lambda: Specialist(),
    )
    payload = json.loads(_tool_fn(mod.ask_macro_policy_researcher)("当前宏观环境怎么样"))
    assert payload["status"] == "ok"
    assert payload["brief"]["agent"] == "macro_policy_researcher"
    assert payload["brief"]["answer"] == "宏观结论"

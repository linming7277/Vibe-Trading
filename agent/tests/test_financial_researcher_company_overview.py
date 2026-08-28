from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.channels.bus.events import InboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.runtime import classify_company_research_intent
from src.company_research.chat_formatter import format_company_overview_for_chat


def _overview(*, business: str = "PARTIAL", financial: str = "READY", stale: bool = False) -> dict[str, Any]:
    return {
        "company": {"market": "CN", "stock_code": "002371.SZ", "stock_name": "北方华创"},
        "business_summary": {
            "status": business,
            "description": "公司主要业务包括：半导体装备。",
            "changes": ["目前缺少前后两期可比较的经营资料，暂时无法判断经营方向是否发生明显变化。"],
        },
        "financial_summary": {
            "status": financial,
            "items": [
                {"text": "最近一期收入同比增长25.8%，反映销售规模正在变化。"},
                {"text": "最近一期 ROE 同比提高2.0%；ROE 是公司使用股东资金赚钱效率的一个指标。"},
            ] if financial != "UNKNOWN" else [],
        },
        "supporting_evidence": [{"claim": "收入保持增长。"}],
        "challenging_evidence": [{"claim": "现金情况需要继续核验。"}],
        "thesis": {
            "status_label": "正在形成", "core_thesis": "半导体装备需求的增长能否持续。",
        },
        "review": {"is_stale": stale},
        "watch_items": [{"text": "继续观察：经营现金流。"}],
        "data_status": {"financial": financial, "business": business, "thesis": "CREATED", "review": "STALE" if stale else "CURRENT"},
    }


class FakeOverviewFinancialAgent:
    def __init__(self) -> None:
        self.chat_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[tuple[str, str]] = []

    def _resolve_cached_security(self, question: str, entity: str = "") -> dict[str, str] | None:
        self.resolve_calls.append((question, entity))
        if "002371" in question or entity == "北方华创":
            return {"code": "002371.SZ", "name": "北方华创"}
        if "000338" in question or entity == "潍柴动力":
            return {"code": "000338.SZ", "name": "潍柴动力"}
        if "688536" in question or entity == "思瑞浦":
            return {"code": "688536.SH", "name": "思瑞浦"}
        return None

    def _resolve_history_security(self, history: list[dict[str, str]]) -> None:
        del history
        return None

    def chat_current_leader_pool(self, **kwargs: Any) -> dict[str, Any]:
        self.chat_calls.append(kwargs)
        raise AssertionError("明确的快速总览问题不应调用 FinancialAnalysisService.chat")


class FakeDetailedFinancialAgent(FakeOverviewFinancialAgent):
    def chat_current_leader_pool(self, **kwargs: Any) -> dict[str, Any]:
        self.chat_calls.append(kwargs)
        return {
            "answer": (
                "### 公司做什么\n\n思瑞浦主要从事信号链类模拟芯片。\n\n"
                "### 财务质量怎么样\n\n"
                "|年度|营收|\n|---|---|\n|2025|21.42亿元|\n\n已读取完整财务资料。"
            ),
            "scope": "company",
            "stock_code": "688536.SH",
            "stock_name": "思瑞浦",
            "data_dates": {"financial_report_date": "2026-03-31"},
        }


class FakeOverviewService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_overview(self, market: str, stock_code: str) -> dict[str, Any]:
        self.calls.append((market, stock_code))
        result = _overview(stale=True)
        result["company"] = {"market": market, "stock_code": stock_code, "stock_name": "北方华创" if stock_code == "002371.SZ" else "潍柴动力"}
        return result


async def _consume_stream(bus: MessageBus) -> tuple[str, list[Any]]:
    messages: list[Any] = []
    while True:
        message = await asyncio.wait_for(bus.consume_outbound(), timeout=2)
        messages.append(message)
        if message.metadata.get("_stream_end"):
            return "".join(item.content for item in messages), messages


def test_company_overview_intent_keeps_financial_detail_on_existing_path() -> None:
    assert classify_company_research_intent("快速总览北方华创") == "COMPANY_OVERVIEW"
    assert classify_company_research_intent("看下已保存的研究总览") == "COMPANY_OVERVIEW"
    assert classify_company_research_intent("总结一下北方华创") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("潍柴动力现在怎么样？") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("帮我分析下688536") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("分析一下思瑞浦") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("分析这家公司") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("北方华创的现金流怎么样？") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("北方华创的 ROE 和未来利润预测如何？") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("你能分析哪些方面？") == "FINANCIAL_DETAIL"
    assert classify_company_research_intent("依据是什么？") == "COMPANY_OVERVIEW"


def test_formatter_is_plain_handles_missing_data_stale_review_and_no_trading_language() -> None:
    answer = format_company_overview_for_chat(_overview(stale=True))

    assert "半导体装备" in answer
    assert "ROE 是公司使用股东资金赚钱效率" in answer
    assert "上一次逻辑复核已过期" in answer
    assert "研究依据：财务数据 2 条、支持或挑战当前逻辑的证据 2 条。" in answer
    assert not any(word in answer for word in ("买入", "卖出", "目标价", "仓位", "止损"))
    assert len(answer) <= 600
    assert "。；" not in answer

    missing = format_company_overview_for_chat(_overview(business="UNKNOWN", financial="UNKNOWN"))
    assert "经营研究目前还没有生成" in missing
    assert "还没有生成财务研究快照" in missing

    cited = _overview()
    cited["financial_summary"]["items"][0]["citations"] = [{"source_key": "FEATURE_REVENUE_CHANGE_2026Q1", "data_as_of": "2026-03-31"}]
    cited["supporting_evidence"][0]["citations"] = [{"source_title": "已保存财务 Evidence", "source_date": "2026-03-31"}]
    citation_answer = format_company_overview_for_chat(cited, include_citations=True)
    assert "具体依据：财务：FEATURE_REVENUE_CHANGE_2026Q1（2026-03-31）" in citation_answer
    assert "研究证据：已保存财务 Evidence（2026-03-31）" in citation_answer


def test_feishu_company_overview_reuses_read_only_service_without_financial_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import src.channels.runtime as runtime_module
        import src.company_research as company_research
        import src.financial_analysis.service as financial_service
        from src.channels.runtime import ChannelRuntime

        financial = FakeOverviewFinancialAgent()
        overview = FakeOverviewService()
        monkeypatch.setattr(financial_service, "get_financial_analysis_service", lambda: financial)
        monkeypatch.setattr(company_research, "get_company_research_overview_service", lambda: overview)
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus, session_service=object(), manager=None,
            session_map_path=tmp_path / "sessions.json", default_agents={"feishu": "financial_analyst"},
        )
        await runtime.start(start_manager=False)
        try:
            await bus.publish_inbound(InboundMessage(
                channel="feishu", sender_id="owner", chat_id="chat-1", content="快速总览北方华创",
                metadata={"message_id": "overview-1"},
            ))
            content, messages = await _consume_stream(bus)
        finally:
            await runtime.stop()

        assert financial.chat_calls == []
        assert overview.calls == [("CN", "002371.SZ")]
        assert "正在读取公司研究总览" in content
        assert "财报研究员 · 北方华创" in content
        assert "财务资料完整 · 经营部分资料 · 未调用模型重做研究" in content
        assert "上一次逻辑复核已过期" in content
        assert messages[-1].metadata["scope"] == "company_overview"
        assert messages[-1].metadata["stock_code"] == "002371.SZ"

    asyncio.run(scenario())


def test_feishu_company_overview_requires_exact_company_before_reading_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import src.company_research as company_research
        import src.financial_analysis.service as financial_service
        from src.channels.runtime import ChannelRuntime

        financial = FakeOverviewFinancialAgent()
        overview = FakeOverviewService()
        monkeypatch.setattr(financial_service, "get_financial_analysis_service", lambda: financial)
        monkeypatch.setattr(company_research, "get_company_research_overview_service", lambda: overview)
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus, session_service=object(), manager=None,
            session_map_path=tmp_path / "sessions.json", default_agents={"feishu": "financial_analyst"},
        )
        await runtime.start(start_manager=False)
        try:
            await bus.publish_inbound(InboundMessage(
                channel="feishu", sender_id="owner", chat_id="chat-1", content="快速总览这家公司",
                metadata={"message_id": "overview-unknown"},
            ))
            content, _ = await _consume_stream(bus)
        finally:
            await runtime.stop()

        assert overview.calls == []
        assert financial.chat_calls == []
        assert "请补充 A 股公司名称或六位股票代码" in content

    asyncio.run(scenario())


def test_explicit_company_overview_prepares_missing_business_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import src.business_research as business_research
        import src.company_research as company_research
        import src.financial_analysis.service as financial_service
        from src.channels.runtime import ChannelRuntime

        state = {"prepared": False}

        class MissingThenReadyOverview:
            def get_overview(self, market: str, stock_code: str) -> dict[str, Any]:
                result = _overview(business="PARTIAL" if state["prepared"] else "UNKNOWN")
                result["company"] = {"market": market, "stock_code": stock_code, "stock_name": "思瑞浦"}
                if not state["prepared"]:
                    result["business_summary"] = {
                        "status": "UNKNOWN", "description": "", "changes": [],
                    }
                    result["data_status"]["business"] = "UNKNOWN"
                else:
                    result["business_summary"]["description"] = "公司主要业务包括：信号链模拟芯片。"
                    result["data_status"]["business"] = "PARTIAL"
                return result

        class BusinessService:
            def get(self, stock_code: str) -> dict[str, Any]:
                assert stock_code == "688536.SH"
                state["prepared"] = True
                return {"data_as_of": "2026-08-21", "data_quality": {"status": "PARTIAL"}}

        monkeypatch.setattr(financial_service, "get_financial_analysis_service", lambda: FakeOverviewFinancialAgent())
        monkeypatch.setattr(company_research, "get_company_research_overview_service", lambda: MissingThenReadyOverview())
        monkeypatch.setattr(business_research, "get_business_research_service", lambda: BusinessService())
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus, session_service=object(), manager=None,
            session_map_path=tmp_path / "sessions.json", default_agents={"feishu": "financial_analyst"},
        )
        await runtime.start(start_manager=False)
        try:
            await bus.publish_inbound(InboundMessage(
                channel="feishu", sender_id="owner", chat_id="chat-1", content="快速总览688536",
                metadata={"message_id": "overview-688536"},
            ))
            content, messages = await _consume_stream(bus)
        finally:
            await runtime.stop()

        assert state["prepared"] is True
        assert "正在从通达信缓存补充主营业务资料" in content
        assert "公司主要业务包括：信号链模拟芯片" in content
        assert messages[-1].metadata["scope"] == "company_overview"

    asyncio.run(scenario())


def test_generic_company_analysis_uses_full_research_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        import src.company_research as company_research
        import src.financial_analysis.service as financial_service
        from src.channels.runtime import ChannelRuntime

        financial = FakeDetailedFinancialAgent()
        overview = FakeOverviewService()
        monkeypatch.setattr(financial_service, "get_financial_analysis_service", lambda: financial)
        monkeypatch.setattr(company_research, "get_company_research_overview_service", lambda: overview)
        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus, session_service=object(), manager=None,
            session_map_path=tmp_path / "sessions.json", default_agents={"feishu": "financial_analyst"},
        )
        await runtime.start(start_manager=False)
        try:
            await bus.publish_inbound(InboundMessage(
                channel="feishu", sender_id="owner", chat_id="chat-1", content="帮我分析下688536",
                metadata={"message_id": "analysis-688536"},
            ))
            content, messages = await _consume_stream(bus)
        finally:
            await runtime.stop()

        assert len(financial.chat_calls) == 1
        assert financial.chat_calls[0]["question"] == "帮我分析下688536"
        assert overview.calls == []
        assert "### 公司做什么" in content
        assert "信号链类模拟芯片" in content
        assert "公司研究总览" not in content
        final_answer = next(item for item in messages if item.metadata.get("financial_final_answer"))
        assert final_answer.metadata["has_markdown_table"] is True
        assert not final_answer.metadata.get("_stream_delta")
        assert not final_answer.metadata.get("_stream_end")
        assert messages[-1].metadata["scope"] == "company"

    asyncio.run(scenario())

"""Contracts for the supervisor's in-process researcher dispatch."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.feishu import FeishuChannel
from src.channels.runtime import ChannelRuntime
from src.investment_research_supervisor import (
    RESEARCHER_CHANNELS,
    RESEARCHER_TITLES,
    DispatchOutcome,
    DispatchTask,
    plan_dispatch,
    run_dispatch_tasks,
    summarize_dispatch,
)


def _researchers(plan: Any) -> list[str]:
    return [task.researcher for task in plan.tasks]


class TestPlanDispatch:
    def test_comprehensive_question_fans_out_to_three_researchers(self) -> None:
        plan = plan_dispatch("全面分析中远海控")
        assert _researchers(plan) == [
            "financial_analyst", "valuation_researcher", "risk_researcher",
        ]
        assert plan.needs_dispatch

    def test_bare_analysis_verb_with_stock_code_also_dispatches(self) -> None:
        # "分析一下600460" reads as a deep-dive request; without the
        # verb+code rule it fell to COMPANY_OVERVIEW and the supervisor
        # answered alone with no researcher fan-out.
        assert plan_dispatch("分析一下600460").needs_dispatch
        assert _researchers(plan_dispatch("研究一下600519")) == [
            "financial_analyst", "valuation_researcher", "risk_researcher",
        ]

    def test_code_without_analysis_verb_stays_direct(self) -> None:
        assert not plan_dispatch("600460财务怎么样").needs_dispatch
        assert not plan_dispatch("600460估值怎么样").needs_dispatch

    def test_comprehensive_plan_carries_role_specific_assignments(self) -> None:
        plan = plan_dispatch("深入分析一下600460")
        by_researcher = {task.researcher: task for task in plan.tasks}
        # The raw question stays intact for company resolution; each card line
        # and each delegated question use the researcher's duty-aligned phrasing.
        assert all(task.question == "深入分析一下600460" for task in plan.tasks)
        assignments = {task.assignment for task in plan.tasks}
        assert len(assignments) == 3  # every researcher is phrased differently
        assert "营收与归母净利路径" in by_researcher["financial_analyst"].assignment
        assert "证伪阈值" in by_researcher["risk_researcher"].assignment
        assert "合理价值区间" in by_researcher["valuation_researcher"].assignment
        for task in plan.tasks:
            assert task.assignment.startswith("围绕「深入分析一下600460」")
            assert task.full_question == task.assignment

    def test_macro_plan_carries_macro_assignment(self) -> None:
        plan = plan_dispatch("当前宏观流动性和通胀环境怎么样")
        task = plan.tasks[0]
        assert task.researcher == "macro_policy_researcher"
        assert "传导路径" in task.assignment
        assert "当前宏观流动性和通胀环境怎么样" in task.assignment

    def test_comprehensive_with_macro_keywords_adds_macro_researcher(self) -> None:
        plan = plan_dispatch("结合当前宏观流动性环境全面分析中远海控")
        assert _researchers(plan)[-1] == "macro_policy_researcher"
        assert len(plan.tasks) == 4

    def test_single_domain_intents_stay_local(self) -> None:
        # The supervisor's snapshot registry answers these deterministically;
        # users wanting the model-backed explanation can @-mention the
        # researcher bot directly instead.
        assert not plan_dispatch("贵州茅台估值怎么样").needs_dispatch
        assert not plan_dispatch("潍柴动力有什么风险").needs_dispatch
        assert not plan_dispatch("北方华创最新财报怎么样").needs_dispatch

    def test_supervisor_owned_intents_stay_local(self) -> None:
        assert not plan_dispatch("你是谁").needs_dispatch
        assert not plan_dispatch("今天低估龙头池有什么变化").needs_dispatch
        assert not plan_dispatch("中远海控为什么进入低估池").needs_dispatch
        assert not plan_dispatch("中远海控主要做什么业务").needs_dispatch

    def test_macro_question_without_company_delegates_to_macro_researcher(self) -> None:
        plan = plan_dispatch("当前宏观流动性和通胀环境怎么样")
        assert _researchers(plan) == ["macro_policy_researcher"]

    def test_company_overview_question_stays_local(self) -> None:
        # The composite template already merges researchers deterministically.
        assert not plan_dispatch("总结一下中远海控的研究").needs_dispatch


class TestRunDispatchTasks:
    def test_runs_researchers_in_parallel_and_collects_outcomes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.investment_research_supervisor.dispatch as dispatch

        def slow_ok(question: str) -> DispatchOutcome:
            time.sleep(0.15)
            return DispatchOutcome(DispatchTask("risk_researcher", question), "风险结论", "READY")

        def failing(question: str) -> DispatchOutcome:
            raise RuntimeError("no snapshot")

        monkeypatch.setitem(dispatch._RUNNERS, "risk_researcher", slow_ok)
        monkeypatch.setitem(dispatch._RUNNERS, "valuation_researcher", failing)

        started = time.monotonic()
        outcomes = asyncio.run(run_dispatch_tasks([
            DispatchTask("risk_researcher", "q1"),
            DispatchTask("valuation_researcher", "q2"),
        ]))
        elapsed = time.monotonic() - started

        assert elapsed < 0.3  # parallel, not 2x0.15 sequential
        assert [o.status for o in outcomes] == ["READY", "ERROR"]
        assert outcomes[1].error.startswith("RuntimeError:")

    def test_timeout_produces_timeout_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.investment_research_supervisor.dispatch as dispatch

        def hang(question: str) -> DispatchOutcome:
            time.sleep(5)
            raise AssertionError("must not finish")

        monkeypatch.setitem(dispatch._RUNNERS, "risk_researcher", hang)
        monkeypatch.setattr(dispatch, "DISPATCH_TASK_TIMEOUT_S", 0.1)

        outcomes = asyncio.run(run_dispatch_tasks([DispatchTask("risk_researcher", "q")]))
        assert outcomes[0].status == "TIMEOUT"
        assert not outcomes[0].ok


class TestSummarizeDispatch:
    def test_all_failed_returns_actionable_notice(self) -> None:
        outcomes = [
            DispatchOutcome(DispatchTask("risk_researcher", "q"), "", "ERROR", error="boom"),
        ]
        summary = summarize_dispatch("q", outcomes)
        assert "均未返回有效结果" in summary

    def test_degrades_to_rule_summary_when_model_unavailable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.investment_research_supervisor.dispatch as dispatch

        def unavailable(question: str, outcomes: list[DispatchOutcome]) -> str:
            raise RuntimeError("research_lead 模型未启用")

        monkeypatch.setattr(dispatch, "_summarize_with_model", unavailable)
        outcomes = [
            DispatchOutcome(DispatchTask("risk_researcher", "q"), "第一项风险结论。", "READY"),
            DispatchOutcome(DispatchTask("valuation_researcher", "q"), "", "TIMEOUT"),
        ]
        summary = summarize_dispatch("q", outcomes)
        assert "风险研究员" in summary
        assert "第一项风险结论。" in summary
        assert "TIMEOUT" in summary

    def test_rule_summary_truncates_long_answers(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.investment_research_supervisor.dispatch as dispatch

        monkeypatch.setattr(
            dispatch, "_summarize_with_model",
            lambda question, outcomes: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        long_answer = "长" * 2000
        outcomes = [DispatchOutcome(DispatchTask("risk_researcher", "q"), long_answer, "READY")]
        summary = summarize_dispatch("q", outcomes)
        assert len(summary) < 1000

    def test_summary_instruction_demands_bare_json(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Endpoints may ignore response_format; the JSON contract must be stated."""
        import src.investment_research_supervisor.dispatch as dispatch
        import src.research_tasks.service as rts
        import src.research_tasks.store as rts_store

        class FakeStore:
            def get_runtime_config(self, role: str) -> dict[str, Any]:
                return {"enabled": True, "model": "test-model", "provider": "openai"}

        captured: dict[str, Any] = {}

        class FakeRuntime:
            def invoke(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"summary": "综合结论。"}

        monkeypatch.setattr(rts_store, "ResearchTaskStore", FakeStore)
        monkeypatch.setattr(rts, "ProviderModelRuntime", FakeRuntime)
        outcomes = [DispatchOutcome(DispatchTask("risk_researcher", "q"), "风险结论。", "READY")]
        summary = dispatch._summarize_with_model("q", outcomes)
        assert summary == "综合结论。"
        assert "输出必须是且仅是一个 JSON 对象" in captured["instruction"]
        assert '"summary"' in captured["instruction"]


class TestFeishuMentions:
    def test_valid_mentions_normalizes_and_filters(self) -> None:
        msg = OutboundMessage(
            channel="feishu", chat_id="oc_1", content="hello",
            mentions=[
                {"open_id": "ou_risk", "name": "风险研究员"},
                {"user_id": "ou_user", "user_name": "张三"},
                {"name": "no id"},
                "garbage",
            ],
        )
        assert FeishuChannel._valid_mentions(msg) == [
            ("ou_risk", "风险研究员"), ("ou_user", "张三"),
        ]

    def test_outbound_message_mentions_default_to_empty(self) -> None:
        msg = OutboundMessage(channel="feishu", chat_id="oc_1", content="x")
        assert msg.mentions == []

    def test_prepend_post_mentions_inserts_at_paragraph(self) -> None:
        post = FeishuChannel._markdown_to_post("第一行\n第二行")
        result = json.loads(FeishuChannel._prepend_post_mentions(post, [("ou_risk", "风险研究员")]))
        first = result["zh_cn"]["content"][0]
        assert first == [{"tag": "at", "user_id": "ou_risk", "user_name": "风险研究员"}]
        assert result["zh_cn"]["content"][1][0]["text"] == "第一行"

    def test_prepend_card_mentions_prefixes_first_markdown(self) -> None:
        elements = [
            {"tag": "markdown", "content": "### 标题"},
            {"tag": "markdown", "content": "正文"},
        ]
        result = FeishuChannel._prepend_card_mentions(elements, [("ou_val", "估值研究员")])
        assert result[0]["content"].startswith('<at id="ou_val"></at> ### 标题')
        assert result[1]["content"] == "正文"  # only the first element is touched

    def test_prepend_card_mentions_creates_markdown_when_missing(self) -> None:
        elements: list[dict[str, Any]] = [{"tag": "hr"}]
        result = FeishuChannel._prepend_card_mentions(elements, [("ou_risk", "")])
        assert result[0] == {"tag": "markdown", "content": '<at id="ou_risk"></at>'}


class _FakeFeishuChannel:
    """Channel stand-in exposing the bot open_id the runtime reads."""

    def __init__(self, open_id: str) -> None:
        self._bot_open_id = open_id


class _FakeManager:
    def __init__(self, channels: dict[str, _FakeFeishuChannel]) -> None:
        self.channels = channels


async def _drive(
    runtime: ChannelRuntime, bus: MessageBus, msg: InboundMessage, question: str,
) -> list[OutboundMessage]:
    """Run one supervisor handler turn and collect every outbound message."""
    task = asyncio.create_task(runtime._handle_investment_research_supervisor(msg, question))
    out: list[OutboundMessage] = []
    while not task.done():
        try:
            out.append(await asyncio.wait_for(bus.consume_outbound(), timeout=0.2))
        except asyncio.TimeoutError:
            continue
    await task
    while True:
        try:
            out.append(bus.outbound.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


_NO_COMPANY = object()


class _FakeSkeletonService:
    """Supervisor-service stand-in for the deterministic composite skeleton."""

    def __init__(self, company: Any = None) -> None:
        # default → resolves 士兰微; company=_NO_COMPANY → resolves nothing.
        self._company_value = None if company is _NO_COMPANY else (
            company or {"code": "600460.SH", "name": "士兰微"}
        )

    def _company(self, question: str, history: Any) -> dict[str, str] | None:
        return self._company_value

    def _resolve_as_of(self, question: str, as_of: Any) -> tuple[str, None]:
        return ("2026-08-25", None)

    def compose_company_research_summary(
        self, stock_code: str, stock_name: str, research_as_of: str, *,
        intent: str = "COMPREHENSIVE", include_researchers: bool = True,
    ) -> Any:
        assert include_researchers is False
        from types import SimpleNamespace
        return SimpleNamespace(answer=(
            "**结论**：估值判定 合理 · 总体风险 中\n\n"
            "**关键数字**\n| 指标 | 数值 |\n\n"
            "**五年关键指标**\n| 年度 | 营收(亿) |\n\n"
            "**情景推演**\n- 峰值净利对照：历史最高归母净利 15.18 亿"
        ))


class TestSupervisorDispatchOrchestration:
    """End-to-end: the supervisor handler fans out and replies per bot."""

    @staticmethod
    def _inbound(question: str) -> InboundMessage:
        return InboundMessage(
            channel="feishu_supervisor",
            sender_id="ou_asker",
            chat_id="oc_group",
            content=question,
            metadata={"message_id": "om_origin", "chat_type": "group"},
        )

    def test_dispatch_flow_replies_through_researcher_bots_and_mentions_asker(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.investment_research_supervisor as supervisor_pkg

        async def fake_run(tasks: Any) -> list[DispatchOutcome]:
            return [
                DispatchOutcome(
                    DispatchTask("financial_analyst", "q"), "财报结论。", "READY",
                    stock_code="601919", stock_name="中远海控", research_as_of="2026-08-27",
                ),
                DispatchOutcome(DispatchTask("valuation_researcher", "q"), "估值结论。", "READY"),
                DispatchOutcome(DispatchTask("risk_researcher", "q"), "风险结论。", "READY"),
            ]

        monkeypatch.setattr(supervisor_pkg, "run_dispatch_tasks", fake_run)
        monkeypatch.setattr(
            supervisor_pkg, "summarize_dispatch", lambda question, outcomes: "主管综合结论。",
        )
        monkeypatch.setattr(
            supervisor_pkg, "get_investment_research_supervisor_service",
            lambda: _FakeSkeletonService(),
        )

        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus,
            session_service=object(),
            manager=_FakeManager({
                "feishu_supervisor": _FakeFeishuChannel("ou_supervisor"),
                "feishu": _FakeFeishuChannel("ou_financial"),
                "feishu_risk": _FakeFeishuChannel("ou_risk"),
                "feishu_valuation": _FakeFeishuChannel("ou_valuation"),
            }),  # type: ignore[arg-type]
        )
        msg = self._inbound("全面分析中远海控")
        out = asyncio.run(_drive(runtime, bus, msg, "全面分析中远海控"))

        # Dispatch card: from the supervisor bot, @-mentioning every researcher bot.
        dispatch_msg = next(m for m in out if "任务分派" in m.content)
        assert dispatch_msg.channel == "feishu_supervisor"
        assert {item["open_id"] for item in dispatch_msg.mentions} == {
            "ou_financial", "ou_valuation", "ou_risk",
        }
        assert "@风险研究员" in dispatch_msg.content

        # Each researcher answer arrives through its own bot identity.
        financial = next(m for m in out if "财报结论。" in m.content)
        assert financial.channel == "feishu"
        assert "受投研主管委派" in financial.content
        assert "中远海控" in financial.content
        assert next(m for m in out if "估值结论。" in m.content).channel == "feishu_valuation"
        assert next(m for m in out if "风险结论。" in m.content).channel == "feishu_risk"

        # Final summary: supervisor bot with the LLM synthesis on top of the
        # deterministic composite skeleton, @-mentioning the asker.
        final = next(m for m in out if "深度研究综合结论" in m.content and m.channel == "feishu_supervisor")
        assert "**研究员综合**" in final.content
        assert "主管综合结论。" in final.content
        assert "**五年关键指标**" in final.content          # composite skeleton
        assert "峰值净利对照" in final.content
        assert "**分派明细**" in final.content
        assert "ou_asker" in {i["open_id"] for i in final.mentions}
        assert "✅ 财报研究员：已完成" in final.content

        # The stream is closed, history recorded, and the busy lock released.
        assert any(m.metadata.get("_stream_end") for m in out)
        assert runtime._supervisor_histories[msg.session_key][-1]["role"] == "assistant"
        assert msg.session_key not in runtime._supervisor_busy_sessions

    def test_degraded_and_timeout_outcomes_are_flagged_visibly(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A fallback answer must tell the user why it is shallow."""
        import src.investment_research_supervisor as supervisor_pkg

        async def fake_run(tasks: Any) -> list[DispatchOutcome]:
            return [
                DispatchOutcome(
                    DispatchTask("financial_analyst", "q"),
                    "当前发现 1 项需要复核的风险。",
                    "PARTIAL",
                    data_gaps=("MODEL_EXPLANATION_UNAVAILABLE",),
                ),
                DispatchOutcome(DispatchTask("valuation_researcher", "q"), "估值结论。", "READY"),
                DispatchOutcome(
                    DispatchTask("risk_researcher", "q"), "", "TIMEOUT",
                    error="研究员未在 150 秒时限内返回",
                ),
            ]

        monkeypatch.setattr(supervisor_pkg, "run_dispatch_tasks", fake_run)
        monkeypatch.setattr(
            supervisor_pkg, "summarize_dispatch", lambda question, outcomes: "主管综合结论。",
        )
        monkeypatch.setattr(
            supervisor_pkg, "get_investment_research_supervisor_service",
            lambda: _FakeSkeletonService(),
        )

        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus,
            session_service=object(),
            manager=_FakeManager({
                "feishu_supervisor": _FakeFeishuChannel("ou_supervisor"),
                "feishu": _FakeFeishuChannel("ou_financial"),
                "feishu_risk": _FakeFeishuChannel("ou_risk"),
                "feishu_valuation": _FakeFeishuChannel("ou_valuation"),
            }),  # type: ignore[arg-type]
        )
        msg = self._inbound("全面分析士兰微")
        out = asyncio.run(_drive(runtime, bus, msg, "全面分析士兰微"))

        financial = next(m for m in out if m.channel == "feishu" and m.content.startswith("### 财报研究员"))
        assert "本地规则数据摘要" in financial.content
        assert "研究员模型" in financial.content

        risk = next(m for m in out if m.channel == "feishu_risk")
        assert "未能完成" in risk.content
        assert "150 秒" in risk.content

        final = next(m for m in out if "深度研究综合结论" in m.content and m.channel == "feishu_supervisor")
        assert "⚠️ 财报研究员：已降级为规则摘要（角色模型未启用或调用失败）" in final.content
        assert "⏱️ 风险研究员：超时未返回" in final.content
        assert "✅ 估值研究员：已完成" in final.content

    def test_missing_researcher_channel_falls_back_to_supervisor_bot(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import src.investment_research_supervisor as supervisor_pkg

        async def fake_run(tasks: Any) -> list[DispatchOutcome]:
            return [DispatchOutcome(DispatchTask("macro_policy_researcher", "q"), "宏观结论。", "READY")]

        monkeypatch.setattr(supervisor_pkg, "run_dispatch_tasks", fake_run)
        monkeypatch.setattr(
            supervisor_pkg, "summarize_dispatch", lambda question, outcomes: "汇总。",
        )
        # Macro question resolves to no company → empty skeleton, summary-only card.
        monkeypatch.setattr(
            supervisor_pkg, "get_investment_research_supervisor_service",
            lambda: _FakeSkeletonService(company=_NO_COMPANY),
        )

        bus = MessageBus()
        runtime = ChannelRuntime(
            bus=bus, session_service=object(),
            manager=_FakeManager({"feishu_supervisor": _FakeFeishuChannel("ou_supervisor")}),
        )
        msg = self._inbound("当前宏观流动性怎么样")
        out = asyncio.run(_drive(runtime, bus, msg, "当前宏观流动性怎么样"))

        # The macro bot is not running, so its answer is presented by the supervisor.
        researcher_answer = next(m for m in out if "宏观结论。" in m.content)
        assert researcher_answer.channel == "feishu_supervisor"
        # Its dispatch card has no @ mention because the open_id is unknown.
        dispatch_msg = next(m for m in out if "任务分派" in m.content)
        assert dispatch_msg.mentions == []
        final = next(m for m in out if "深度研究综合结论" in m.content)
        assert "汇总。" in final.content
        assert "**五年关键指标**" not in final.content  # no company → no skeleton

    def test_non_dispatch_question_keeps_existing_direct_answer(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Self-intro and pool questions never spawn researcher tasks."""
        import src.investment_research_supervisor as supervisor_pkg

        calls: list[str] = []

        async def must_not_run(tasks: Any) -> list[DispatchOutcome]:  # pragma: no cover
            calls.append("dispatch")
            return []

        monkeypatch.setattr(supervisor_pkg, "run_dispatch_tasks", must_not_run)

        class FakeBrief:
            intent = "SELF_INTRO"
            answer = "我是投研主管。"
            stock_code = None
            stock_name = None
            research_as_of = None
            capabilities = ()

        class FakeService:
            def handle_question(self, *, question: str, history: Any) -> FakeBrief:
                calls.append("direct")
                return FakeBrief()

        monkeypatch.setattr(
            supervisor_pkg, "get_investment_research_supervisor_service", FakeService,
        )

        bus = MessageBus()
        runtime = ChannelRuntime(bus=bus, session_service=object(), manager=None)
        msg = self._inbound("你是谁")
        out = asyncio.run(_drive(runtime, bus, msg, "你是谁"))

        assert calls == ["direct"]
        assert any("我是投研主管。" in m.content for m in out)
        assert not any("任务分派" in m.content for m in out)


def test_researcher_registry_covers_every_dispatchable_agent() -> None:
    assert set(RESEARCHER_TITLES) == set(RESEARCHER_CHANNELS)
    assert RESEARCHER_CHANNELS["risk_researcher"] == "feishu_risk"
    assert RESEARCHER_CHANNELS["financial_analyst"] == "feishu"

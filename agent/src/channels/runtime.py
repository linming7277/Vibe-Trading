"""IM channel runtime that connects MessageBus traffic to SessionService."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.manager import ChannelManager
from src.channels.pairing import PAIRING_COMMAND_META_KEY, handle_pairing_command
from src.config.paths import get_data_dir
from src.session.models import Message, Session
from src.session.service import SessionBusyError

logger = logging.getLogger(__name__)

_FINANCIAL_AGENT_PREFIX = re.compile(
    r"^\s*(?:/财报(?:研究员)?|/financial(?:-analyst)?|财报(?:研究员)?|问财报研究员|@财报研究员)\s*[:：]?\s*",
    re.IGNORECASE,
)
_GENERAL_AGENT_PREFIX = re.compile(r"^\s*(?:/通用|/general)\s*[:：]?\s*", re.IGNORECASE)
_COMPANY_OVERVIEW_QUESTION = re.compile(
    r"快速总览|研究总览|已保存(?:的)?(?:研究|公司)?总览|当前逻辑|支持逻辑",
)
_FINANCIAL_DETAIL_QUESTION = re.compile(
    r"现金流|利润|净利润|营收|收入|ROE|毛利|净利|负债|财报|预测|"
    r"资产负债|资本开支|财务怎么样|财务表现",
    re.IGNORECASE,
)
_OVERVIEW_CITATION_QUESTION = re.compile(r"依据是什么|证据是什么|来源是什么|引用是什么|查看依据|看依据")
_OVERVIEW_ENTITY_NOISE = re.compile(
    r"快速总览|研究总览|已保存的研究总览|已保存的公司总览|总结一下|总结|整体情况|整体|现在怎么样|怎么样|公司情况|当前逻辑|支持逻辑|"
    r"主要做什么|经营情况|有什么问题需要注意|有什么问题|需要注意|重点观察|重点看|"
    r"接下来重点看什么|接下来.*看|这家公司|公司|请问|一下|的",
)


def classify_company_research_intent(question: str) -> str:
    """Classify only the small Feishu V1 split without using an LLM."""
    text = question.strip()
    if _OVERVIEW_CITATION_QUESTION.search(text):
        return "COMPANY_OVERVIEW"
    if _FINANCIAL_DETAIL_QUESTION.search(text):
        return "FINANCIAL_DETAIL"
    if _COMPANY_OVERVIEW_QUESTION.search(text):
        return "COMPANY_OVERVIEW"
    return "FINANCIAL_DETAIL"


def _overview_entity_candidates(question: str) -> list[str]:
    """Extract possible names for exact validation against the TDX cache."""
    stripped = _OVERVIEW_ENTITY_NOISE.sub(" ", question)
    candidates = [item.strip(" ：:，。？?！!、") for item in re.findall(r"[\u4e00-\u9fffA-Za-z]{2,20}", stripped)]
    # Validation is performed by FinancialAnalysisService against TDX; these
    # candidates are only search keys and never become an assumed company.
    return list(dict.fromkeys(item for item in candidates if item))


@dataclass
class ChannelRuntimeConfig:
    """Runtime controls for IM channel processing."""

    reply_timeout_s: float = 600.0
    poll_interval_s: float = 0.25


class ChannelRuntime:
    """Route inbound channel messages into Vibe-Trading sessions."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        session_service: Any,
        manager: ChannelManager | None,
        session_map_path: Path | None = None,
        reply_timeout_s: float = 600.0,
        poll_interval_s: float = 0.25,
        operators: Iterable[str] | None = None,
        channel_operators: Mapping[str, Iterable[str]] | None = None,
        default_agents: Mapping[str, str] | None = None,
    ) -> None:
        self.bus = bus
        self.session_service = session_service
        self.manager = manager
        self.config = ChannelRuntimeConfig(
            reply_timeout_s=reply_timeout_s,
            poll_interval_s=poll_interval_s,
        )
        # Channel-independent (global) operators may run /pairing on any channel
        # with cross-channel authority. Per-channel operators may run /pairing
        # only on their own channel. Both empty by default → IM /pairing is
        # fail-closed and pairing is managed via the authenticated CLI/REST plane.
        self._operators: set[str] = {str(o) for o in (operators or ())}
        self._channel_operators: dict[str, set[str]] = {
            str(ch): {str(o) for o in ops}
            for ch, ops in (channel_operators or {}).items()
        }
        self._default_agents = {
            str(channel): str(agent)
            for channel, agent in (default_agents or {}).items()
        }
        self.session_map_path = session_map_path or (get_data_dir() / "channels" / "sessions.json")
        self._session_map: dict[str, str] = {}
        self._consumer_task: asyncio.Task[None] | None = None
        self._manager_task: asyncio.Task[Any] | None = None
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._financial_histories: dict[str, list[dict[str, str]]] = {}
        self._supervisor_histories: dict[str, list[dict[str, str]]] = {}
        self._specialist_histories: dict[str, list[dict[str, str]]] = {}
        self._financial_busy_sessions: set[str] = set()
        self._supervisor_busy_sessions: set[str] = set()
        self._specialist_busy_sessions: set[str] = set()
        self._running = False

    async def start(self, *, start_manager: bool = True) -> None:
        """Start channel processing and, optionally, platform adapters."""
        if self._running:
            return
        self._session_map = self._load_session_map()
        self._running = True
        if start_manager and self.manager is not None:
            self._manager_task = asyncio.create_task(self.manager.start_all())
            await asyncio.sleep(0)
        self._consumer_task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        """Stop channel processing and platform adapters."""
        self._running = False
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._consumer_task
            self._consumer_task = None
        for task in list(self._handler_tasks):
            task.cancel()
        for task in list(self._handler_tasks):
            with suppress(asyncio.CancelledError):
                await task
        self._handler_tasks.clear()
        if self.manager is not None:
            await self.manager.stop_all()
        if self._manager_task is not None:
            with suppress(asyncio.CancelledError):
                await self._manager_task
            self._manager_task = None

    def status(self) -> dict[str, Any]:
        """Return runtime and channel status."""
        return {
            "running": self._running,
            "inbound_queue": self.bus.inbound_size,
            "outbound_queue": self.bus.outbound_size,
            "session_count": len(self._session_map),
            "channels": self.manager.get_status() if self.manager is not None else {},
        }

    async def _consume_loop(self) -> None:
        while True:
            msg = await self.bus.consume_inbound()
            task = asyncio.create_task(self._handle_inbound(msg))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)

    async def _handle_inbound(self, msg: InboundMessage) -> None:
        try:
            if self._is_pairing_command(msg.content):
                is_operator, is_global = self._resolve_operator(msg.channel, msg.sender_id)
                if not is_operator:
                    logger.warning(
                        "Rejected /pairing from non-operator %s on %s",
                        msg.sender_id,
                        msg.channel,
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=(
                                "Not authorized: pairing management is restricted to "
                                "configured operators."
                            ),
                            metadata={
                                PAIRING_COMMAND_META_KEY: True,
                                "unauthorized": True,
                                "message_id": msg.metadata.get("message_id"),
                            },
                        )
                    )
                    return
                reply = handle_pairing_command(
                    msg.channel,
                    self._pairing_subcommand_text(msg.content),
                    requesting_channel=msg.channel,
                    is_global_operator=is_global,
                )
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=reply,
                        metadata={
                            PAIRING_COMMAND_META_KEY: True,
                            "message_id": msg.metadata.get("message_id"),
                        },
                    )
                )
                return

            if self._is_new_session_command(msg.content):
                old_id = self.reset_session(msg.session_key)
                self._financial_histories.pop(msg.session_key, None)
                self._supervisor_histories.pop(msg.session_key, None)
                self._specialist_histories.pop(msg.session_key, None)
                if old_id:
                    reply = "✅ Session reset. Your next message will start a new conversation."
                else:
                    reply = "ℹ️ No active session to reset."
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=reply,
                        metadata={
                            "_channel_runtime": True,
                            "session_reset": True,
                            "message_id": msg.metadata.get("message_id"),
                        },
                    )
                )
                return

            specialist_agent = self._specialist_agent(msg)
            if specialist_agent is not None:
                await self._handle_research_specialist(msg, specialist_agent)
                return

            supervisor_question = self._supervisor_agent_question(msg)
            if supervisor_question is not None:
                await self._handle_investment_research_supervisor(msg, supervisor_question)
                return

            financial_question = self._financial_agent_question(msg)
            if financial_question is not None:
                await self._handle_financial_agent(
                    msg,
                    financial_question,
                    force_financial_detail=bool(_FINANCIAL_AGENT_PREFIX.match(msg.content or "")),
                )
                return

            general_question = self._general_agent_question(msg)
            if general_question is not None:
                msg.content = general_question

            session_id = self._session_for(msg)
            result = await self.session_service.send_message(
                session_id,
                msg.content,
                include_shell_tools=False,
            )
            attempt_id = result.get("attempt_id") if isinstance(result, dict) else None
            progress_task: asyncio.Task[None] | None = None
            stream_id = f"agent:{attempt_id or msg.metadata.get('message_id') or msg.session_key}"
            if msg.channel == "feishu":
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="### Agent 执行过程\n\n- ⏳ 已接收任务，正在制定执行步骤",
                    metadata=self._stream_metadata(msg, stream_id, progress=True),
                ))
                progress_task = asyncio.create_task(
                    self._forward_agent_progress(msg, session_id, attempt_id, stream_id)
                )
            reply = await self._wait_for_reply(session_id, attempt_id)
            if progress_task is not None:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(progress_task), timeout=2)
                if not progress_task.done():
                    progress_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await progress_task
            if msg.channel == "feishu":
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"\n\n---\n\n### 执行结果\n\n{reply.content}",
                    metadata={
                        **self._stream_metadata(msg, stream_id),
                        "attempt_id": attempt_id,
                        "session_id": session_id,
                    },
                ))
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="",
                    metadata={
                        **self._stream_metadata(msg, stream_id, end=True),
                        "attempt_id": attempt_id,
                        "session_id": session_id,
                    },
                ))
            else:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=reply.content,
                        metadata={
                            "_channel_runtime": True,
                            "attempt_id": attempt_id,
                            "session_id": session_id,
                            # QQ (and other platforms) need the originating message id
                            # to reply as a passive message; without it, replies are
                            # treated as active messages and rejected for
                            # non-privileged bots.
                            "message_id": msg.metadata.get("message_id"),
                        },
                    )
                )

        except asyncio.CancelledError:
            raise
        except SessionBusyError:
            # A chat maps to one persistent session, so a second message sent
            # while the first is still running is ordinary user behaviour, not
            # a fault. Say so plainly instead of surfacing an exception name.
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=(
                        "Still working on your previous message — send this again "
                        "once I reply, or use the reset command to start over."
                    ),
                    metadata={
                        "_channel_runtime": True,
                        "busy": True,
                        "message_id": msg.metadata.get("message_id"),
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - channel errors must surface to users
            logger.exception("Channel runtime failed for %s:%s", msg.channel, msg.chat_id)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Channel runtime error: {type(exc).__name__}: {exc}",
                    metadata={
                        "_channel_runtime": True,
                        "error": True,
                        "message_id": msg.metadata.get("message_id"),
                    },
                )
            )

    @staticmethod
    def _is_feishu_message(msg: InboundMessage) -> bool:
        """Return whether an inbound message came from a Feishu bot instance."""
        return msg.channel == "feishu" or msg.channel.startswith("feishu_")

    def _financial_agent_question(self, msg: InboundMessage) -> str | None:
        """Return a Financial Analyst prompt for explicit or dedicated routing."""
        if not self._is_feishu_message(msg):
            return None
        matched = _FINANCIAL_AGENT_PREFIX.match(msg.content or "")
        if matched:
            question = (msg.content or "")[matched.end():].strip()
            return question or "请说明你想研究的公司名称或股票代码，以及具体财务问题。"
        if self._default_agents.get(msg.channel) != "financial_analyst":
            return None
        if _GENERAL_AGENT_PREFIX.match(msg.content or ""):
            return None
        question = (msg.content or "").strip()
        return question or "请说明你想研究的公司名称或股票代码，以及具体财务问题。"

    def _general_agent_question(self, msg: InboundMessage) -> str | None:
        """Strip the escape command used by a dedicated Financial Analyst bot."""
        if self._default_agents.get(msg.channel) != "financial_analyst":
            return None
        matched = _GENERAL_AGENT_PREFIX.match(msg.content or "")
        if not matched:
            return None
        return (msg.content or "")[matched.end():].strip() or "你好"

    def _supervisor_agent_question(self, msg: InboundMessage) -> str | None:
        if (not self._is_feishu_message(msg)
                or self._default_agents.get(msg.channel) != "investment_research_supervisor"):
            return None
        if _GENERAL_AGENT_PREFIX.match(msg.content or ""):
            return None
        return (msg.content or "").strip() or "请说明你想研究的公司名称或股票代码，以及具体问题。"

    def _specialist_agent(self, msg: InboundMessage) -> str | None:
        """Return the dedicated specialist route configured for a Feishu bot."""
        if not self._is_feishu_message(msg) or _GENERAL_AGENT_PREFIX.match(msg.content or ""):
            return None
        agent = self._default_agents.get(msg.channel)
        if agent in {"risk_researcher", "valuation_researcher", "macro_policy_researcher"}:
            return agent
        return None

    async def _handle_research_specialist(self, msg: InboundMessage, agent: str) -> None:
        from src.research_specialist_chat import ROLE_SPECS

        title = ROLE_SPECS[agent]["title"]
        session_key = msg.session_key
        if session_key in self._specialist_busy_sessions:
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"{title}正在处理上一条问题，请等待回复后再继续提问。",
                metadata={
                    "_channel_runtime": True,
                    "research_specialist": agent,
                    "busy": True,
                    "message_id": msg.metadata.get("message_id"),
                },
            ))
            return
        self._specialist_busy_sessions.add(session_key)
        stream_id = f"{agent}:{msg.metadata.get('message_id') or session_key}"
        metadata = {
            "_channel_runtime": True,
            "research_specialist": agent,
            "_stream_id": stream_id,
            "message_id": msg.metadata.get("message_id"),
            "chat_type": msg.metadata.get("chat_type"),
        }
        try:
            from src.research_specialist_chat import get_research_specialist_chat_service

            question = (msg.content or "").strip() or "你有什么功能？"
            history = self._specialist_histories.get(session_key, [])[-8:]
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"### {title}\n\n- ⏳ 正在读取本地研究数据并整理回答",
                metadata={**metadata, "_progress": True, "_stream_delta": True},
            ))
            brief = await asyncio.to_thread(
                get_research_specialist_chat_service().handle_question,
                agent=agent,
                question=question,
                history=history,
            )
            self._specialist_histories[session_key] = [
                *history,
                {
                    "role": "user", "content": question,
                    "stock_code": brief.stock_code or "", "stock_name": brief.stock_name or "",
                },
                {
                    "role": "assistant", "content": brief.answer,
                    "stock_code": brief.stock_code or "", "stock_name": brief.stock_name or "",
                },
            ][-12:]
            subject = brief.stock_name or brief.stock_code
            heading = f"### {title}" + (f" · {subject}" if subject else "")
            footer_parts = []
            if brief.research_as_of:
                footer_parts.append(f"研究基准日 {brief.research_as_of}")
            if brief.model_name:
                footer_parts.append(f"角色模型 {brief.model_name}")
            footer = f"\n\n— {' · '.join(footer_parts)}" if footer_parts else ""
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"{heading}\n\n{brief.answer}{footer}",
                metadata={
                    **metadata,
                    "specialist_final_answer": True,
                    "stock_code": brief.stock_code,
                    "research_as_of": brief.research_as_of,
                    "status": brief.status,
                },
            ))
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={**metadata, "_stream_end": True},
            ))
        except Exception as exc:  # noqa: BLE001 - IM users need an actionable response
            logger.exception("Research specialist %s failed for %s", agent, msg.chat_id)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"{title}暂时无法完成回答：{type(exc).__name__}: {exc}",
                metadata={**metadata, "_stream_delta": True, "error": True},
            ))
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={**metadata, "_stream_end": True, "error": True},
            ))
        finally:
            self._specialist_busy_sessions.discard(session_key)

    async def _handle_investment_research_supervisor(self, msg: InboundMessage, question: str) -> None:
        session_key = msg.session_key
        if session_key in self._supervisor_busy_sessions:
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="投研主管正在处理上一条问题，请等待回复后再继续提问。",
                metadata={"_channel_runtime": True, "investment_research_supervisor": True, "busy": True,
                          "message_id": msg.metadata.get("message_id")},
            ))
            return
        self._supervisor_busy_sessions.add(session_key)
        stream_id = f"supervisor:{msg.metadata.get('message_id') or session_key}"
        metadata = {
            "_channel_runtime": True,
            "investment_research_supervisor": True,
            "_stream_id": stream_id,
            "message_id": msg.metadata.get("message_id"),
            "chat_type": msg.metadata.get("chat_type"),
        }
        try:
            from src.investment_research_supervisor import (
                get_investment_research_supervisor_service,
                plan_dispatch,
            )

            history = self._supervisor_histories.get(session_key, [])[-8:]
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="### 投研主管\n\n- ⏳ 已接收问题，正在识别问题并准备回答",
                metadata={**metadata, "_progress": True, "_stream_delta": True},
            ))
            plan = plan_dispatch(question, history)
            if plan.needs_dispatch:
                await self._run_supervisor_dispatch(msg, question, plan, metadata, history)
                return
            brief = await asyncio.to_thread(
                get_investment_research_supervisor_service().handle_question,
                question=question,
                history=history,
            )
            self._supervisor_histories[session_key] = [
                *history,
                {"role": "user", "content": question, "stock_code": brief.stock_code or "", "stock_name": brief.stock_name or ""},
                {"role": "assistant", "content": brief.answer},
            ][-12:]
            if brief.intent == "SELF_INTRO":
                final_content = f"### 投研主管\n\n{brief.answer}"
            else:
                company = brief.stock_name or brief.stock_code or "公司研究"
                capabilities = "、".join(brief.capabilities) or "资料定位"
                as_of = brief.research_as_of or "暂无"
                final_content = (
                    f"### 投研主管 · {company}\n\n{brief.answer}\n\n"
                    f"— 研究基准日 {as_of} · 依据 {capabilities}"
                )
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=final_content,
                metadata={**metadata, "supervisor_final_answer": True, "intent": brief.intent,
                          "stock_code": brief.stock_code, "research_as_of": brief.research_as_of},
            ))
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="",
                metadata={**metadata, "_stream_end": True},
            ))
        except Exception as exc:  # noqa: BLE001 - IM users need an actionable response
            logger.exception("Investment Research Supervisor request failed for %s", msg.chat_id)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=f"投研主管暂时无法完成研究：{type(exc).__name__}: {exc}",
                metadata={**metadata, "_stream_delta": True, "error": True},
            ))
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="",
                metadata={**metadata, "_stream_end": True, "error": True},
            ))
        finally:
            self._supervisor_busy_sessions.discard(session_key)

    def _feishu_bot_open_ids(self) -> dict[str, str]:
        """Map each running Feishu channel name to its bot open_id.

        Feishu bots learn their own open_id at startup; because every bot
        lives in this process the supervisor can read its peers' ids to
        @-mention them in dispatch cards.
        """
        if self.manager is None:
            return {}
        result: dict[str, str] = {}
        for name, channel in self.manager.channels.items():
            if name == "feishu" or name.startswith("feishu_"):
                open_id = getattr(channel, "_bot_open_id", None)
                if open_id:
                    result[name] = str(open_id)
        return result

    async def _run_supervisor_dispatch(
        self,
        msg: InboundMessage,
        question: str,
        plan: Any,
        metadata: dict[str, Any],
        history: list[dict[str, str]],
    ) -> None:
        """Delegate to researchers in-process and present answers per bot."""
        from src.investment_research_supervisor import (
            RESEARCHER_CHANNELS,
            RESEARCHER_TITLES,
            run_dispatch_tasks,
            summarize_dispatch,
        )

        session_key = msg.session_key
        is_group = str(msg.metadata.get("chat_type") or "") == "group"
        bot_open_ids = self._feishu_bot_open_ids()

        # Dispatch card: an ordinary supervisor message so @-mentions render
        # through the standard card path rather than the streaming buffer.
        mention_items = []
        dispatch_lines = []
        for task in plan.tasks:
            title = RESEARCHER_TITLES.get(task.researcher, task.researcher)
            channel_name = RESEARCHER_CHANNELS.get(task.researcher, "")
            open_id = bot_open_ids.get(channel_name, "")
            if open_id:
                mention_items.append({"open_id": open_id, "name": title})
            dispatch_lines.append(f"- @{title}：{task.assignment or task.question}")
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="### 投研主管 · 任务分派\n\n" + "\n".join(dispatch_lines),
            metadata={**metadata, "supervisor_dispatch": True, "dispatch_reason": plan.reason},
            mentions=mention_items,
        ))
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"\n- ⏳ 已分派 {len(plan.tasks)} 位研究员，正在并行研究",
            metadata={**metadata, "_progress": True, "_stream_delta": True},
        ))

        outcomes = await run_dispatch_tasks(plan.tasks)

        for outcome in outcomes:
            title = RESEARCHER_TITLES.get(outcome.task.researcher, outcome.task.researcher)
            if outcome.ok and outcome.answer:
                subject = outcome.stock_name or outcome.stock_code
                heading = f"### {title}" + (f" · {subject}" if subject else "")
                footer_bits = ["受投研主管委派"]
                if outcome.research_as_of:
                    footer_bits.append(f"研究基准日 {outcome.research_as_of}")
                body = outcome.answer
                if outcome.degraded:
                    body += (
                        "\n\n> ⚠️ 以上为本地规则数据摘要：该研究员角色模型未启用或调用失败。"
                        "请在「设置 → 研究员模型」启用对应角色后重新提问，可获得深度分析。"
                    )
                content = f"{heading}\n\n{body}\n\n— {' · '.join(footer_bits)}"
            else:
                reason = outcome.error or "未返回内容"
                content = f"### {title}\n\n受投研主管委派的研究未能完成：{reason}"
            target_channel = msg.channel
            if is_group:
                channel_name = RESEARCHER_CHANNELS.get(outcome.task.researcher, "")
                if self.manager is not None and channel_name in self.manager.channels:
                    target_channel = channel_name
            await self.bus.publish_outbound(OutboundMessage(
                channel=target_channel, chat_id=msg.chat_id, content=content,
                metadata={
                    "_channel_runtime": True,
                    "supervisor_dispatch": True,
                    "researcher": outcome.task.researcher,
                    "researcher_status": outcome.status,
                    "message_id": msg.metadata.get("message_id"),
                    "chat_type": msg.metadata.get("chat_type"),
                },
            ))

        summary = await asyncio.to_thread(summarize_dispatch, question, outcomes)
        composite_text = await asyncio.to_thread(self._supervisor_composite_skeleton, question, history)
        detail_lines = []
        for outcome in outcomes:
            title = RESEARCHER_TITLES.get(outcome.task.researcher, outcome.task.researcher)
            if outcome.ok and outcome.degraded:
                detail_lines.append(f"- ⚠️ {title}：已降级为规则摘要（角色模型未启用或调用失败）")
            elif outcome.ok:
                detail_lines.append(f"- ✅ {title}：已完成")
            elif outcome.status == "TIMEOUT":
                detail_lines.append(f"- ⏱️ {title}：超时未返回")
            else:
                detail_lines.append(f"- ⚠️ {title}：{outcome.error or '未能返回'}")
        final_parts = [f"### 投研主管 · 深度研究综合结论\n\n**研究员综合**\n\n{summary}"]
        if composite_text:
            final_parts.append(composite_text)
        final_parts.append(
            "**分派明细**\n" + "\n".join(detail_lines)
            + f"\n\n— 协同 {len(outcomes)} 位研究员 · {plan.reason}"
        )
        final_content = "\n\n".join(final_parts)
        summary_mentions = []
        if str(msg.sender_id).startswith("ou_"):
            summary_mentions.append({"open_id": msg.sender_id, "name": ""})
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=final_content,
            metadata={**metadata, "supervisor_final_answer": True, "supervisor_dispatch": True,
                      "dispatch_reason": plan.reason},
            mentions=summary_mentions,
        ))
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="",
            metadata={**metadata, "_stream_end": True},
        ))
        answered = "；".join(
            f"{RESEARCHER_TITLES.get(o.task.researcher, o.task.researcher)}:{o.status}"
            for o in outcomes
        )
        self._supervisor_histories[session_key] = [
            *history,
            {"role": "user", "content": question},
            {"role": "assistant", "content": f"{summary}\n（分派：{answered}）"},
        ][-12:]

    def _supervisor_composite_skeleton(self, question: str, history: list[dict[str, str]]) -> str:
        """Deterministic composite skeleton appended to the dispatch final card.

        The researchers' own messages already carry their narrative answers, so
        the skeleton reuses the composite template without researcher sections
        (key numbers, five-year path, cycle position, scenarios, watch points,
        data boundary).  Purely an enrichment: any failure degrades to "".
        """
        try:
            from src.investment_research_supervisor import get_investment_research_supervisor_service

            service = get_investment_research_supervisor_service()
            company = service._company(question, history)
            stock_code = str((company or {}).get("code") or (company or {}).get("stock_code") or "").upper()
            if not stock_code:
                return ""
            stock_name = str((company or {}).get("name") or (company or {}).get("stock_name") or stock_code)
            research_as_of, _error = service._resolve_as_of(question, None)
            if not research_as_of:
                return ""
            brief = service.compose_company_research_summary(
                stock_code, stock_name, research_as_of,
                intent="COMPREHENSIVE", include_researchers=False,
            )
            return brief.answer
        except Exception:  # noqa: BLE001 - the skeleton must never block the summary
            logger.warning("Supervisor composite skeleton failed; summary-only card", exc_info=True)
            return ""

    async def _handle_financial_agent(
        self, msg: InboundMessage, question: str, *, force_financial_detail: bool = False,
    ) -> None:
        """Answer an explicit Feishu Financial Analyst request outside AgentLoop."""
        session_key = msg.session_key
        if session_key in self._financial_busy_sessions:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="财报研究员正在处理上一条问题，请等待回复后再继续提问。",
                    metadata={"_channel_runtime": True, "financial_agent": True, "busy": True,
                              "message_id": msg.metadata.get("message_id")},
                )
            )
            return
        self._financial_busy_sessions.add(session_key)
        stream_id = f"financial:{msg.metadata.get('message_id') or session_key}"
        base_stream_meta = {
            "_channel_runtime": True,
            "financial_agent": True,
            "_stream_id": stream_id,
            "message_id": msg.metadata.get("message_id"),
            "chat_type": msg.metadata.get("chat_type"),
        }
        try:
            # Import lazily: channel startup must remain available when the
            # financial-analysis package or its optional providers are absent.
            from src.financial_analysis.service import get_financial_analysis_service

            history = self._financial_histories.get(session_key, [])[-8:]
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="### 财报研究过程\n\n- ⏳ 已接收问题，正在解析问题意图",
                metadata={**base_stream_meta, "_progress": True, "_stream_delta": True},
            ))
            loop = asyncio.get_running_loop()
            buffered_model_answer = ""

            def publish_progress(stage: str, message: str, details: dict[str, Any]) -> None:
                nonlocal buffered_model_answer
                if stage == "model_output_delta":
                    delta = str(details.get("text_delta") or "")
                    if not delta:
                        return
                    # Keep the completed report out of the streaming progress
                    # card.  The regular Feishu sender can then convert Markdown
                    # tables into native card tables and split them safely at
                    # the one-table-per-card platform limit.
                    buffered_model_answer += delta
                    return
                else:
                    content = f"\n- ✅ {message}"
                future = asyncio.run_coroutine_threadsafe(
                    self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=content,
                        metadata={
                            **base_stream_meta,
                            "_progress": True,
                            "_stream_delta": True,
                            "progress_stage": stage,
                        },
                    )),
                    loop,
                )
                future.result(timeout=3)

            async def publish_overview_progress(stage: str, message: str, details: dict[str, Any]) -> None:
                """Publish read-only overview stages from the active event loop.

                ``publish_progress`` above is intentionally synchronous because
                financial chat invokes it from a worker thread.  The overview
                path itself is async, so it must not block the event loop while
                waiting for a coroutine scheduled onto that same loop.
                """
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"\n- ✅ {message}",
                    metadata={
                        **base_stream_meta,
                        "_progress": True,
                        "_stream_delta": True,
                        "progress_stage": stage,
                        **details,
                    },
                ))

            intent = "FINANCIAL_DETAIL" if force_financial_detail else classify_company_research_intent(question)
            if intent == "COMPANY_OVERVIEW":
                _financial = get_financial_analysis_service()
                await publish_overview_progress(
                    "company_overview_intent",
                    "已识别为公司整体研究问题，正在定位公司",
                    {"intent": intent},
                )
                security = _financial._resolve_cached_security(question)
                if security is None:
                    for candidate in _overview_entity_candidates(question):
                        security = _financial._resolve_cached_security(question, candidate)
                        if security is not None:
                            break
                if security is None:
                    security = _financial._resolve_history_security(history)
                if security is None:
                    result = {
                        "answer": "未能识别出具体公司。请补充 A 股公司名称或六位股票代码后，我再读取已保存的公司研究总览。",
                        "scope": "company_not_loaded",
                        "deterministic": True,
                        "routing": {"intent": intent, "source": "rules"},
                    }
                else:
                    stock_code = str(security.get("code") or "").upper()
                    stock_name = str(security.get("name") or stock_code)
                    await publish_overview_progress(
                        "security_matched",
                        f"已定位公司：{stock_name}（{stock_code}）",
                        {"stock_code": stock_code, "stock_name": stock_name},
                    )
                    await publish_overview_progress(
                        "company_overview_loaded",
                        "正在读取公司研究总览（仅使用已保存资料，不会重新分析）",
                        {"stock_code": stock_code},
                    )
                    from src.company_research import (
                        format_company_overview_for_chat,
                        get_company_research_overview_service,
                    )

                    overview = await asyncio.to_thread(
                        get_company_research_overview_service().get_overview,
                        "CN", stock_code,
                    )
                    business = dict(overview.get("business_summary") or {})
                    if business.get("status") == "UNKNOWN":
                        await publish_overview_progress(
                            "business_snapshot_loading",
                            "经营快照尚未建立，正在从通达信缓存补充主营业务资料",
                            {"stock_code": stock_code},
                        )
                        try:
                            from src.business_research import get_business_research_service

                            prepared_business = await asyncio.to_thread(
                                get_business_research_service().get,
                                stock_code,
                            )
                        except Exception as exc:  # noqa: BLE001 - overview remains partially useful
                            logger.warning(
                                "Unable to prepare business snapshot for %s: %s",
                                stock_code, exc,
                            )
                            await publish_overview_progress(
                                "business_snapshot_unavailable",
                                "主营业务资料暂时不可用，将继续展示已有财务与研究信息",
                                {"stock_code": stock_code, "error_type": type(exc).__name__},
                            )
                        else:
                            await publish_overview_progress(
                                "business_snapshot_loaded",
                                "已从通达信缓存补充主营业务与产品资料",
                                {
                                    "stock_code": stock_code,
                                    "business_status": (prepared_business.get("data_quality") or {}).get("status"),
                                    "data_as_of": prepared_business.get("data_as_of"),
                                },
                            )
                            overview = await asyncio.to_thread(
                                get_company_research_overview_service().get_overview,
                                "CN", stock_code,
                            )
                    await publish_overview_progress(
                        "company_overview_formatting",
                        "正在整理财务与经营重点",
                        {"stock_code": stock_code},
                    )
                    result = {
                        "answer": format_company_overview_for_chat(
                            overview,
                            include_citations=bool(_OVERVIEW_CITATION_QUESTION.search(question)),
                        ),
                        "scope": "company_overview",
                        "stock_code": stock_code,
                        "stock_name": str((overview.get("company") or {}).get("stock_name") or stock_name),
                        "overview": overview,
                        "deterministic": True,
                        "routing": {"intent": intent, "source": "rules"},
                    }
            else:
                result = await asyncio.to_thread(
                    get_financial_analysis_service().chat_current_leader_pool,
                    question=question, history=history, progress=publish_progress,
                )
            answer = str(
                result.get("answer") or buffered_model_answer
                or "财报研究员没有返回可展示的内容。"
            )
            scope = str(result.get("scope") or "workspace")
            if scope in {"company", "company_overview"}:
                title = f"财报研究员 · {result.get('stock_name') or result.get('stock_code') or '公司研究'}"
                if scope == "company_overview":
                    status = dict((result.get("overview") or {}).get("data_status") or {})
                    status_labels = {
                        "READY": "资料完整",
                        "PARTIAL": "部分资料",
                        "STALE": "待更新",
                        "UNKNOWN": "暂无资料",
                        "UNAVAILABLE": "暂不可用",
                    }
                    financial_status = str(status.get("financial") or "UNKNOWN").upper()
                    business_status = str(status.get("business") or "UNKNOWN").upper()
                    data_note = (
                        "公司研究总览："
                        f"财务{status_labels.get(financial_status, '状态未知')} · "
                        f"经营{status_labels.get(business_status, '状态未知')} · "
                        "未调用模型重做研究"
                    )
                else:
                    dates = dict(result.get("data_dates") or {})
                    parts = []
                    for label, key in (
                        ("行情", "quote_as_of"),
                        ("估值", "valuation_as_of"),
                        ("财报期", "financial_report_date"),
                        ("财报公告", "financial_announcement_date"),
                        ("龙头排名", "leader_as_of"),
                    ):
                        if value := dates.get(key):
                            parts.append(f"{label} {str(value).replace('T', ' ')[:16]}")
                    data_note = " · ".join(parts) or f"分析基准日 {result.get('as_of') or '—'}"
            elif scope == "capability":
                title = "财报研究员 · 能力范围"
                data_note = f"能力清单版本 {result.get('capability_version') or '—'}"
            elif scope == "company_not_loaded":
                title = "财报研究员 · 证券查询"
                data_note = "未定位证券，未读取公司财务数据"
            elif scope in {"data_boundary", "context_required"}:
                title = "财报研究员 · 数据边界"
                data_note = "未接入的数据不会参与结论"
            elif scope == "general_method":
                title = "财报研究员 · 财报方法"
                data_note = "本次未加载公司或龙头池数据"
            else:
                title = "财报研究员 · 龙头池研究"
                data_note = f"龙头池数据截至 {result.get('leader_snapshot_as_of') or '—'}"
            user_history_entry = {"role": "user", "content": question}
            if scope in {"company", "company_overview"} and result.get("stock_code"):
                user_history_entry["stock_code"] = str(result["stock_code"])
                user_history_entry["stock_name"] = str(result.get("stock_name") or "")
            self._financial_histories[session_key] = [
                *history,
                user_history_entry,
                {"role": "assistant", "content": answer},
            ][-12:]
            # ``answer`` is authoritative; ``buffered_model_answer`` only
            # proves that the model callback produced the same final body.
            # Send it as a regular message so Feishu's static-card renderer can
            # turn Markdown tables into native table elements.
            final_content = f"### {title}\n\n{answer}\n\n— {data_note}"
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=final_content,
                    metadata={**base_stream_meta, "financial_final_answer": True, "scope": scope,
                              "stock_code": result.get("stock_code"),
                              "has_markdown_table": "|---" in answer.replace(" ", "")},
                )
            )
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={**base_stream_meta, "_stream_end": True, "scope": scope,
                          "stock_code": result.get("stock_code")},
            ))
        except Exception as exc:  # noqa: BLE001 - IM users need an actionable response
            logger.exception("Financial Analyst channel request failed for %s", msg.chat_id)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"\n- ❌ 财报研究中断：{type(exc).__name__}: {exc}",
                    metadata={**base_stream_meta, "_stream_delta": True, "error": True},
                )
            )
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={**base_stream_meta, "_stream_end": True, "error": True},
            ))
        finally:
            self._financial_busy_sessions.discard(session_key)

    @staticmethod
    def _stream_metadata(
        msg: InboundMessage,
        stream_id: str,
        *,
        progress: bool = False,
        end: bool = False,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "_channel_runtime": True,
            "_stream_id": stream_id,
            "message_id": msg.metadata.get("message_id"),
            "chat_type": msg.metadata.get("chat_type"),
        }
        if end:
            metadata["_stream_end"] = True
        else:
            metadata["_stream_delta"] = True
        if progress:
            metadata["_progress"] = True
        return metadata

    async def _forward_agent_progress(
        self,
        msg: InboundMessage,
        session_id: str,
        attempt_id: str | None,
        stream_id: str,
    ) -> None:
        """Render safe AgentLoop activity summaries into the Feishu stream."""
        event_bus = getattr(self.session_service, "event_bus", None)
        if event_bus is None or not hasattr(event_bus, "subscribe"):
            return
        reasoning_iters: set[int] = set()
        async for event in event_bus.subscribe(session_id, replay_all=True):
            data = dict(getattr(event, "data", {}) or {})
            if attempt_id and data.get("attempt_id") != attempt_id:
                continue
            event_type = str(getattr(event, "event_type", ""))
            line: str | None = None
            if event_type == "attempt.started":
                line = "通用 Agent 已开始执行"
            elif event_type == "reasoning_delta":
                iteration = int(data.get("iter") or 0)
                if iteration not in reasoning_iters:
                    reasoning_iters.add(iteration)
                    line = f"正在分析任务并决定下一步（第 {max(iteration, 1)} 轮）"
            elif event_type == "tool_call":
                tool = str(data.get("tool") or "未知工具")
                argument_keys = ", ".join(sorted((data.get("arguments") or {}).keys()))
                line = f"调用工具：{tool}" + (f"（参数：{argument_keys}）" if argument_keys else "")
            elif event_type == "tool_progress":
                line = str(data.get("message") or data.get("stage") or "工具正在执行")
            elif event_type == "tool_result":
                tool = str(data.get("tool") or "工具")
                status = "完成" if data.get("status") == "ok" else "失败"
                elapsed = int(data.get("elapsed_ms") or 0)
                line = f"{tool} 执行{status}" + (f"（{elapsed / 1000:.1f} 秒）" if elapsed else "")
            elif event_type in {"attempt.completed", "attempt.failed", "attempt.cancelled"}:
                break
            if line:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"\n- ✅ {line}",
                    metadata=self._stream_metadata(msg, stream_id, progress=True),
                ))

    def _session_for(self, msg: InboundMessage) -> str:
        key = msg.session_key
        existing = self._session_map.get(key)
        if existing:
            return existing
        session = self.session_service.create_session(
            title=f"{msg.channel}:{msg.chat_id}",
            config={"channel": msg.channel, "channel_chat_id": msg.chat_id},
        )
        session_id = _session_id(session)
        self._session_map[key] = session_id
        self._save_session_map()
        return session_id

    async def _wait_for_reply(self, session_id: str, attempt_id: str | None) -> Message:
        deadline = time.monotonic() + self.config.reply_timeout_s
        last_assistant: Message | None = None
        while time.monotonic() < deadline:
            messages = self.session_service.get_messages(session_id, limit=200)
            for message in reversed(messages):
                if message.role != "assistant":
                    continue
                if attempt_id and message.linked_attempt_id != attempt_id:
                    if last_assistant is None:
                        last_assistant = message
                    continue
                return message
            await asyncio.sleep(self.config.poll_interval_s)
        if last_assistant is not None:
            return last_assistant
        raise TimeoutError("timed out waiting for assistant reply")

    def _load_session_map(self) -> dict[str, str]:
        try:
            data = json.loads(self.session_map_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            logger.warning("Ignoring invalid channel session map at %s", self.session_map_path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if value}

    def _save_session_map(self) -> None:
        self.session_map_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.session_map_path.with_suffix(self.session_map_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._session_map, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.session_map_path)

    def reset_session(self, session_key: str) -> str | None:
        """Remove a session mapping so the next message creates a fresh session.

        Args:
            session_key: The channel:chat_id key to reset.

        Returns:
            The removed session_id, or None if no mapping existed.
        """
        removed = self._session_map.pop(session_key, None)
        if removed is not None:
            self._save_session_map()
        return removed

    def _resolve_operator(self, channel: str, sender_id: str | None) -> tuple[bool, bool]:
        """Resolve pairing authorization for a sender.

        Args:
            channel: The channel the command arrived on.
            sender_id: The inbound message sender id.

        Returns:
            ``(is_operator, is_global_operator)``. ``is_operator`` is ``True``
            for global operators or per-channel operators of ``channel``;
            ``is_global_operator`` is ``True`` only for channel-independent
            operators, who may act cross-channel with full request details.
        """
        sid = str(sender_id)
        is_global = sid in self._operators
        is_channel = sid in self._channel_operators.get(channel, set())
        return (is_global or is_channel, is_global)

    @staticmethod
    def operators_from_config(
        config: Mapping[str, Any] | None,
    ) -> tuple[set[str], dict[str, set[str]]]:
        """Extract global and per-channel operators from a channels config dict.

        Args:
            config: The channels config mapping (as produced by
                ``ChannelsConfig.model_dump``). Top-level ``operators`` are
                global; a per-channel section's own ``operators`` list is
                channel-scoped.

        Returns:
            ``(global_operators, channel_operators)``.
        """
        if not config:
            return set(), {}
        global_ops = {str(o) for o in (config.get("operators") or ())}
        channel_ops: dict[str, set[str]] = {}
        for key, value in config.items():
            if isinstance(value, Mapping) and value.get("operators"):
                channel_ops[str(key)] = {str(o) for o in value["operators"]}
        return global_ops, channel_ops

    @staticmethod
    def default_agents_from_config(config: Mapping[str, Any] | None) -> dict[str, str]:
        """Extract optional dedicated-agent routing from channel sections."""
        if not config:
            return {}
        defaults: dict[str, str] = {}
        for key, value in config.items():
            if not isinstance(value, Mapping):
                continue
            agent = value.get("default_agent") or value.get("defaultAgent")
            if agent in {
                "general", "financial_analyst", "investment_research_supervisor",
                "risk_researcher", "valuation_researcher", "macro_policy_researcher",
            }:
                defaults[str(key)] = str(agent)
        return defaults

    @staticmethod
    def _is_pairing_command(content: str) -> bool:
        stripped = content.strip().lower()
        return stripped == "/pairing" or stripped.startswith("/pairing ")

    @staticmethod
    def _pairing_subcommand_text(content: str) -> str:
        parts = content.strip().split(None, 1)
        return parts[1] if len(parts) > 1 else "list"

    @staticmethod
    def _is_new_session_command(content: str) -> bool:
        """Check if the message is a session reset command (/new, /reset, /newsession)."""
        return content.strip().lower() in ("/new", "/reset", "/newsession")


def _session_id(session: Session | dict[str, Any] | Any) -> str:
    if isinstance(session, Session):
        return session.session_id
    if isinstance(session, dict):
        return str(session["session_id"])
    return str(getattr(session, "session_id"))

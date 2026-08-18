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
        self._financial_busy_sessions: set[str] = set()
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

            financial_question = self._financial_agent_question(msg)
            if financial_question is not None:
                await self._handle_financial_agent(msg, financial_question)
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

    def _financial_agent_question(self, msg: InboundMessage) -> str | None:
        """Return a Financial Analyst prompt for explicit or dedicated routing."""
        if msg.channel != "feishu":
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

    async def _handle_financial_agent(self, msg: InboundMessage, question: str) -> None:
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

            def publish_progress(stage: str, message: str, details: dict[str, Any]) -> None:
                del details
                future = asyncio.run_coroutine_threadsafe(
                    self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"\n- ✅ {message}",
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

            result = await asyncio.to_thread(
                get_financial_analysis_service().chat_current_leader_pool,
                question=question, history=history, progress=publish_progress,
            )
            answer = str(result.get("answer") or "财报研究员没有返回可展示的内容。")
            scope = str(result.get("scope") or "workspace")
            if scope == "company":
                title = f"财报研究员 · {result.get('stock_name') or result.get('stock_code') or '公司研究'}"
                data_note = f"数据截至 {result.get('as_of') or result.get('leader_snapshot_as_of') or '—'}"
            elif scope == "capability":
                title = "财报研究员 · 能力范围"
                data_note = f"能力清单版本 {result.get('capability_version') or '—'}"
            elif scope in {"data_boundary", "company_not_loaded", "context_required"}:
                title = "财报研究员 · 数据边界"
                data_note = "未接入的数据不会参与结论"
            elif scope == "general_method":
                title = "财报研究员 · 财报方法"
                data_note = "本次未加载公司或龙头池数据"
            else:
                title = "财报研究员 · 龙头池研究"
                data_note = f"龙头池数据截至 {result.get('leader_snapshot_as_of') or '—'}"
            self._financial_histories[session_key] = [*history, {"role": "user", "content": question}, {"role": "assistant", "content": answer}][-12:]
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"\n\n---\n\n### {title}\n\n{answer}\n\n— {data_note}",
                    metadata={**base_stream_meta, "_stream_delta": True, "scope": scope,
                              "stock_code": result.get("stock_code")},
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
            if agent in {"general", "financial_analyst"}:
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

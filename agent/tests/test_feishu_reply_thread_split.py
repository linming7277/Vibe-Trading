"""Group replies with split cards must stay inside one thread/topic.

A long composite answer renders as several interactive cards (Feishu allows
one table per card).  If only the first card replies to the user's message,
the remaining cards land in the main group while the first lives in the
topic — the "answer split across two places" bug.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.channels.bus.events import OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.feishu import FeishuChannel, FeishuConfig


def _channel(reply_to_message: bool) -> FeishuChannel:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="cli_x", app_secret="s", reply_to_message=reply_to_message),
        MessageBus(),
    )
    channel._client = object()  # send() only checks truthiness before dispatch
    return channel


def _recording_channel(reply_to_message: bool) -> tuple[FeishuChannel, dict[str, list[Any]]]:
    channel = _channel(reply_to_message)
    calls: dict[str, list[Any]] = {"reply": [], "send": []}
    channel._reply_message_sync = lambda *a, **k: calls["reply"].append(a) or "om_new"  # type: ignore[method-assign]
    channel._send_message_sync = lambda *a, **k: calls["send"].append(a) or "om_new"  # type: ignore[method-assign]
    return channel, calls


_LONG_TABLES = (
    "### 投研主管 · 深度研究综合结论\n\n结论段落。\n\n"
    "| 指标 | 数值 |\n| --- | --- |\n| 营收 | 71.90 |\n\n过渡段落。\n\n"
    "| 年度 | 营收(亿) |\n| --- | --- |\n| 2025 | 93.62 |\n\n"
    "| 情景 | 隐含PE |\n| --- | --- |\n| BASE | 28.1 |\n"
)


def _group_message(content: str) -> OutboundMessage:
    return OutboundMessage(
        channel="feishu",
        chat_id="oc_group",
        content=content,
        metadata={"message_id": "om_origin", "chat_type": "group"},
    )


def test_group_split_cards_all_reply_into_the_same_thread() -> None:
    channel, calls = _recording_channel(reply_to_message=True)
    groups = channel._split_elements_by_table_limit(channel._build_card_elements(_LONG_TABLES))
    assert len(groups) >= 3  # the composite card really splits

    asyncio.run(channel.send(_group_message(_LONG_TABLES)))

    # Every split card replies to the user's message (staying in the topic)…
    assert len(calls["reply"]) == len(groups)
    assert {args[0] for args in calls["reply"]} == {"om_origin"}
    # …and none spills into the main group via a plain create call.
    assert calls["send"] == []


def test_p2p_split_cards_quote_only_once() -> None:
    channel, calls = _recording_channel(reply_to_message=True)
    msg = _group_message(_LONG_TABLES)
    msg.metadata["chat_type"] = "p2p"

    asyncio.run(channel.send(msg))

    assert len(calls["reply"]) == 1
    assert len(calls["send"]) >= 1


def test_group_without_reply_config_sends_all_cards_directly() -> None:
    channel, calls = _recording_channel(reply_to_message=False)
    asyncio.run(channel.send(_group_message(_LONG_TABLES)))
    assert calls["reply"] == []
    assert len(calls["send"]) >= 3


def test_reply_failure_falls_back_to_direct_send_per_chunk() -> None:
    channel = _channel(reply_to_message=True)
    sent: list[Any] = []
    channel._reply_message_sync = lambda *a, **k: None  # type: ignore[method-assign]  # reply rejected
    channel._send_message_sync = lambda *a, **k: sent.append(a) or "om_new"  # type: ignore[method-assign]

    asyncio.run(channel.send(_group_message(_LONG_TABLES)))

    groups = channel._split_elements_by_table_limit(channel._build_card_elements(_LONG_TABLES))
    assert len(sent) == len(groups)
    assert all(tuple(args[:2]) == ("chat_id", "oc_group") for args in sent)

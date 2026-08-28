"""Dedicated Feishu entry point for the investment research supervisor.

The existing ``feishu`` channel remains the financial-research bot.  This
small subclass gives the supervisor its own WebSocket connection, pairing
store namespace, sessions and outbound routing key.
"""

from __future__ import annotations

from src.channels.feishu import FeishuChannel as _FeishuChannel


class FeishuSupervisorChannel(_FeishuChannel):
    """A separate Feishu bot instance routed to the supervisor service."""

    name = "feishu_supervisor"
    display_name = "Feishu Investment Research Supervisor"
    pairing_role_name = "投研主管"

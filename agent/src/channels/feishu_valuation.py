"""Dedicated Feishu entry point for the valuation researcher."""

from __future__ import annotations

from src.channels.feishu import FeishuChannel as _FeishuChannel


class FeishuValuationChannel(_FeishuChannel):
    """A separate Feishu bot instance routed to valuation research."""

    name = "feishu_valuation"
    display_name = "Feishu Valuation Researcher"
    pairing_role_name = "估值研究员"

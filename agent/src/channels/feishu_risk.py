"""Dedicated Feishu entry point for the risk researcher."""

from __future__ import annotations

from src.channels.feishu import FeishuChannel as _FeishuChannel


class FeishuRiskChannel(_FeishuChannel):
    """A separate Feishu bot instance routed to risk research."""

    name = "feishu_risk"
    display_name = "Feishu Risk Researcher"
    pairing_role_name = "风险研究员"

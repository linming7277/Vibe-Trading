"""Dedicated Feishu entry point for the macro-policy researcher."""

from __future__ import annotations

from src.channels.feishu import FeishuChannel as _FeishuChannel


class FeishuMacroPolicyChannel(_FeishuChannel):
    """A separate Feishu bot instance routed to macro-policy research."""

    name = "feishu_macro_policy"
    display_name = "Feishu Macro Policy Researcher"
    pairing_role_name = "宏观政策研究员"

"""Personal-edition feature policy.

This checkout is intentionally scoped to a single user's research workflow.
Keep the policy in one place so removed platform features cannot reappear via
automatic module discovery or a newly added UI/API registration.
"""

from __future__ import annotations


# ``feishu`` remains the existing financial-research bot.  Additional
# first-party Feishu roles are separate channel instances, not aliases that
# replace the financial bot's default agent.
SUPPORTED_CHANNELS = frozenset({
    "feishu",
    "feishu_supervisor",
    "feishu_risk",
    "feishu_valuation",
    "feishu_macro_policy",
    "weixin",
})

# India (NSE/BSE) and Korea (KRX/KOSPI/KOSDAQ) are intentionally outside this
# personal research workspace.  Keep both the market names and their dedicated
# sources here so every public surface can reject them consistently.
DISABLED_MARKETS = frozenset({"india_equity", "kr_equity"})
DISABLED_DATA_SOURCES = frozenset({"india_broker", "pykrx"})
DISABLED_FACTOR_UNIVERSES = frozenset({"equity_in", "equity_kr"})
DISABLED_SYMBOL_SUFFIXES = (".NS", ".BO", ".KS", ".KQ")


def is_disabled_market_symbol(code: str) -> bool:
    """Return whether a symbol belongs to an excluded personal-edition market."""
    return str(code).strip().upper().endswith(DISABLED_SYMBOL_SUFFIXES)


def visible_factor_universes(universes: object) -> list[str]:
    """Filter excluded markets from Alpha Zoo metadata exposed to users."""
    if not isinstance(universes, (list, tuple, set, frozenset)):
        return []
    return [str(item) for item in universes if str(item) not in DISABLED_FACTOR_UNIVERSES]

# Broker connectors and live execution are intentionally not part of the
# personal edition. Market-data loaders with similar names remain available
# for research and backtesting.
DISABLED_TRADING_TOOL_NAMES = frozenset(
    {
        "propose_mandate_profiles",
        "trading_connections",
        "trading_select_connection",
        "trading_check",
        "trading_account",
        "trading_positions",
        "trading_orders",
        "trading_quote",
        "trading_history",
        "trading_place_order",
        "trading_cancel_order",
        "etoro_search_instruments",
        "etoro_close_position",
        "etoro_cancel_close_order",
        "etoro_edit_position_stops",
        "etoro_copy_precheck",
        "etoro_copy_start",
        "etoro_copy_poll",
        "etoro_copy_close",
    }
)

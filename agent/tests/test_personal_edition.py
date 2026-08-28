"""Personal-edition surface regression tests."""

from __future__ import annotations

from src.channels.registry import discover_channel_names
import pytest

from src.config.personal import (
    DISABLED_DATA_SOURCES,
    DISABLED_FACTOR_UNIVERSES,
    DISABLED_TRADING_TOOL_NAMES,
    visible_factor_universes,
)
from src.tools import build_registry


def test_only_feishu_and_weixin_channels_are_exposed() -> None:
    assert discover_channel_names() == [
        "feishu", "feishu_macro_policy", "feishu_risk", "feishu_supervisor",
        "feishu_valuation", "weixin",
    ]


def test_agent_registry_exposes_no_broker_or_live_trading_tools() -> None:
    names = set(build_registry().tool_names)
    assert names.isdisjoint(DISABLED_TRADING_TOOL_NAMES)


def test_api_exposes_no_live_trading_routes() -> None:
    import api_server

    paths = {path for route in api_server.app.routes if (path := getattr(route, "path", None))}
    assert "/live/status" not in paths
    assert "/live/runner/start" not in paths
    assert "/mandate/commit" not in paths


def test_mcp_exposes_no_broker_connector_functions() -> None:
    import mcp_server

    assert not hasattr(mcp_server, "trading_connections")
    assert not hasattr(mcp_server, "trading_place_order")


def test_india_and_korea_data_sources_are_not_registered() -> None:
    from backtest.loaders.registry import FALLBACK_CHAINS, VALID_SOURCES

    assert VALID_SOURCES.isdisjoint(DISABLED_DATA_SOURCES)
    assert "india_equity" not in FALLBACK_CHAINS
    assert "kr_equity" not in FALLBACK_CHAINS


@pytest.mark.parametrize("symbol", ["RELIANCE.NS", "500325.BO", "005930.KS", "247540.KQ"])
def test_india_and_korea_symbols_are_rejected(symbol: str) -> None:
    from backtest.runner import BacktestConfigSchema
    from src.market_data import fetch_market_data

    with pytest.raises(ValueError, match="India and Korea"):
        fetch_market_data(
            codes=[symbol],
            start_date="2025-01-01",
            end_date="2025-01-31",
        )
    with pytest.raises(ValueError, match="India and Korea"):
        BacktestConfigSchema(
            codes=[symbol],
            start_date="2025-01-01",
            end_date="2025-01-31",
        )


def test_personal_market_tool_and_factor_metadata_hide_removed_markets() -> None:
    from src.tools.market_data_tool import MarketDataTool

    source_enum = MarketDataTool.parameters["properties"]["source"]["enum"]
    assert set(source_enum).isdisjoint(DISABLED_DATA_SOURCES)
    assert visible_factor_universes(
        ["equity_cn", "equity_in", "equity_kr", "equity_us"]
    ) == ["equity_cn", "equity_us"]
    assert DISABLED_FACTOR_UNIVERSES == {"equity_in", "equity_kr"}

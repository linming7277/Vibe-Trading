from __future__ import annotations

from pathlib import Path

import pytest

from src.research_workspace.store import (
    ResearchWorkspaceStore,
    normalize_symbol,
)


@pytest.fixture()
def store(tmp_path: Path):
    value = ResearchWorkspaceStore(tmp_path / "research.db", seed=True)
    try:
        yield value
    finally:
        value.close()


def test_sqlite_bootstrap_is_idempotent_and_market_taxonomies_stay_native(tmp_path: Path) -> None:
    path = tmp_path / "research.db"
    first = ResearchWorkspaceStore(path)
    assert all(not row["sectors"] for row in first.latest_dashboard()["markets"])
    first.close()
    second = ResearchWorkspaceStore(path, seed=True)
    try:
        dashboard = second.latest_dashboard()
        assert [row["market"] for row in dashboard["markets"]] == ["CN", "HK", "US"]
        assert {row["market"]: row["sectors"][0]["taxonomy"] for row in dashboard["markets"]} == {
            "CN": "申万",
            "HK": "恒生",
            "US": "GICS",
        }
        assert second._conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "9"
        tables = {
            row[0]
            for row in second._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"engine_runs", "strategy_signals", "decision_chain_runs", "structured_committee_decisions"} <= tables
        assert [row[0] for row in second._conn.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    finally:
        second.close()


@pytest.mark.parametrize(
    ("market", "raw", "expected"),
    [
        ("CN", "600519", "600519.SH"),
        ("CN", "000001", "000001.SZ"),
        ("CN", "920729.BJ", "920729.BJ"),
        ("HK", "700", "00700.HK"),
        ("US", "aapl", "AAPL.US"),
    ],
)
def test_symbol_normalization(market: str, raw: str, expected: str) -> None:
    assert normalize_symbol(market, raw) == expected


def test_sector_and_candidate_scores_follow_weight_contract(store: ResearchWorkspaceStore) -> None:
    sector = store.list_sectors("CN")[0]
    expected = (
        sector["momentum"] * 0.25
        + sector["earnings"] * 0.20
        + sector["fund_flow"] * 0.15
        + sector["breadth"] * 0.15
        + sector["valuation"] * 0.15
        + sector["risk"] * 0.10
    )
    assert sector["base_score"] == pytest.approx(expected, abs=0.01)
    assert -5 <= sector["agent_adjustment"] <= 5
    assert sector["final_score"] == pytest.approx(sector["base_score"] + sector["agent_adjustment"])

    candidate = store.list_candidates("US")[0]
    expected_candidate = (
        candidate["industry_position"] * 0.20
        + candidate["growth"] * 0.20
        + candidate["quality"] * 0.20
        + candidate["valuation"] * 0.15
        + candidate["momentum"] * 0.15
        + candidate["liquidity"] * 0.10
    )
    assert candidate["base_score"] == pytest.approx(expected_candidate, abs=0.01)


def test_portfolio_positions_cost_basis_and_oversell_guard(store: ResearchWorkspaceStore) -> None:
    portfolio = store.create_portfolio({"name": "核心组合", "base_currency": "CNY", "initial_cash": 100_000})
    buy = {"market": "CN", "symbol": "600519", "name": "贵州茅台", "side": "buy", "trade_date": "2026-01-02", "quantity": 10, "price": 1500, "fee": 5, "currency": "CNY"}
    store.add_transaction(portfolio["id"], buy)
    store.add_transaction(portfolio["id"], {**buy, "side": "sell", "trade_date": "2026-01-03", "quantity": 4, "price": 1600, "fee": 4})
    position = store.portfolio_positions(portfolio["id"])[0]
    assert position["quantity"] == 6
    assert position["average_cost"] == pytest.approx(1500.5)
    assert position["realized_pnl"] == pytest.approx(394.0)
    assert store.get_portfolio(portfolio["id"])["cash"]["CNY"] == pytest.approx(91_391.0)
    with pytest.raises(ValueError, match="exceeds"):
        store.add_transaction(portfolio["id"], {**buy, "side": "sell", "quantity": 7})


def test_multicurrency_portfolio_never_silently_aggregates(store: ResearchWorkspaceStore) -> None:
    portfolio = store.create_portfolio({"name": "跨市场", "base_currency": "CNY", "initial_cash": 10_000})
    for market, symbol, name, currency in (("CN", "600519", "贵州茅台", "CNY"), ("US", "AAPL", "Apple", "USD")):
        store.add_transaction(portfolio["id"], {"market": market, "symbol": symbol, "name": name, "side": "buy", "trade_date": "2026-01-02", "quantity": 1, "price": 100, "fee": 0, "currency": currency})
    analytics = store.portfolio_analytics(portfolio["id"])
    assert analytics["aggregate_available"] is False
    assert analytics["base_currency_total"] is None
    assert set(analytics["subtotals"]) == {"CNY", "USD"}
    assert "汇率" in analytics["aggregate_warning"]

    store.record_exchange_rate("USD", "CNY", 7.1, analytics["as_of"], source="test", evidence="fixture rate")
    converted = store.portfolio_analytics(portfolio["id"], as_of=analytics["as_of"])
    assert converted["aggregate_available"] is True
    assert converted["base_currency_total"] == pytest.approx(analytics["subtotals"]["CNY"] + analytics["subtotals"]["USD"] * 7.1)
    assert converted["fx_evidence"]


def test_csv_import_reports_bad_rows_without_losing_good_rows(store: ResearchWorkspaceStore) -> None:
    portfolio = store.create_portfolio({"name": "导入组合", "base_currency": "HKD"})
    csv_text = "market,symbol,name,side,trade_date,quantity,price,fee,currency\nHK,700,腾讯控股,buy,2026-01-01,10,400,2,HKD\nHK,700,腾讯控股,sell,2026-01-02,20,410,2,HKD\n"
    result = store.import_transactions(portfolio["id"], csv_text)
    assert result["imported"] == 1
    assert result["errors"][0]["line"] == 3


def test_completed_committee_requires_structured_decision_and_only_creates_report(store: ResearchWorkspaceStore) -> None:
    committee = store.create_committee("US", "AAPL", "Apple", "swarm-1")
    completed = store.update_committee_status(committee["id"], "completed", "PM recommends waiting for a better entry.")
    assert completed and completed["decision"] is None
    assert store.list_trade_plans() == []
    assert any(report["source_id"] == committee["id"] for report in store.list_reports())


def test_tdx_dossier_creates_research_base_for_any_cached_a_share(store: ResearchWorkspaceStore) -> None:
    dossier = store.upsert_tdx_dossier({
        "code": "601318.SH", "name": "中国平安",
        "as_of": "2026-08-12T10:30:00+08:00",
        "quote": {"price": 50.2, "change_pct": 1.3},
        "fundamental": {"net_profit_10k": 1000, "pe_ttm": 8.5, "dividend_yield": 4.2},
        "sectors": [{"sector_code": "T0002", "sector_name": "保险"}],
        "professional_finance_available": False, "cache": {"stale": False},
    })
    assert dossier["symbol"] == "601318.SH"
    assert dossier["name"] == "中国平安"
    assert dossier["sector_name"] == "保险"
    assert dossier["source_status"] == "live"
    assert dossier["metrics"]["price"] == 50.2
    report = store.create_company_report("CN", "601318.SH")
    assert report["title"] == "中国平安深度研究底稿"
    assert "不构成自动交易指令" in report["content_md"]


def test_agent_macro_refresh_publishes_structured_result_and_evidence(store: ResearchWorkspaceStore) -> None:
    run = store.create_research_run("macro", "CN", status="queued", linked_run_id="session-1")
    result = store.publish_research_results(run["id"], [{
        "market": "CN",
        "data_as_of": "2099-08-12",
        "source_status": "live",
        "evidence": [{
            "source": "中国人民银行",
            "url": "https://example.test/pbc",
            "data_as_of": "2099-08-12",
            "metadata": {"document": "货币政策执行报告"},
        }],
        "macro": {
            "headline": "流动性保持合理充裕",
            "stance": "neutral",
            "summary": "以可核验公开资料为准。",
            "themes": ["流动性"],
            "risks": ["数据修订"],
        },
    }])

    assert result["run"]["status"] == "completed"
    assert result["published"] == {"CN": ["macro"]}
    assert store.latest_macro("CN")["headline"] == "流动性保持合理充裕"
    evidence = store.list_research_evidence("CN", "macro", "2099-08-12")
    assert evidence[0]["source"] == "中国人民银行"
    assert evidence[0]["metadata"]["document"] == "货币政策执行报告"


def test_unavailable_refresh_preserves_last_usable_snapshot(store: ResearchWorkspaceStore) -> None:
    previous = store.latest_macro("US")
    run = store.create_research_run("macro", "US", status="queued")

    result = store.publish_research_results(run["id"], [{
        "market": "US",
        "data_as_of": "2026-08-12",
        "source_status": "unavailable",
        "evidence": [],
    }])

    assert result["published"] == {"US": ["unavailable"]}
    assert store.latest_macro("US")["id"] == previous["id"]


def test_publish_tool_rejects_partial_multi_market_result(store: ResearchWorkspaceStore) -> None:
    from src.tools.workspace_research_tool import PublishWorkspaceResearchTool

    run = store.create_research_run("macro", status="queued")
    payload = PublishWorkspaceResearchTool(store).execute(
        run_id=run["id"],
        results=[{
            "market": "CN",
            "data_as_of": "2026-08-12",
            "source_status": "unavailable",
            "evidence": [],
        }],
    )

    assert '"status": "error"' in payload
    assert "CN, HK, US" in payload
    assert store.get_research_run(run["id"])["status"] == "queued"


@pytest.mark.parametrize(
    ("market", "symbol", "name", "sector"),
    [
        ("US", "AAPL.US", "Apple Inc.", "Technology"),
        ("HK", "00700.HK", "Tencent Holdings Limited", "Communication Services"),
    ],
)
def test_global_equity_refresh_replaces_sample_dossier_with_real_profile(
    store: ResearchWorkspaceStore,
    market: str,
    symbol: str,
    name: str,
    sector: str,
) -> None:
    from src.research_workspace.global_equity import GlobalEquityResearchService

    def fetcher(_symbol: str, modules: list[str]):
        assert {"price", "assetProfile", "financialData"} <= set(modules)
        return {
            "price": {
                "longName": name,
                "regularMarketPrice": {"raw": 200.0},
                "regularMarketTime": {"raw": 1786500000},
                "marketCap": {"raw": 3_000_000_000},
                "currency": "USD" if market == "US" else "HKD",
                "exchangeName": "NASDAQ" if market == "US" else "HKSE",
            },
            "assetProfile": {
                "sector": sector,
                "industry": "Software",
                "longBusinessSummary": "A factual business profile.",
            },
            "summaryDetail": {"trailingPE": {"raw": 25.0}},
            "defaultKeyStatistics": {"forwardPE": {"raw": 22.0}, "priceToBook": {"raw": 8.0}},
            "financialData": {"revenueGrowth": {"raw": 0.12}, "returnOnEquity": {"raw": 0.35}},
            "earningsTrend": {"trend": [{"period": "0q"}]},
        }

    dossier = GlobalEquityResearchService(store, fetcher=fetcher).refresh(market, symbol)

    assert dossier["source_status"] == "live"
    assert dossier["name"] == name
    assert dossier["metrics"]["price"] == 200.0
    assert dossier["metrics"]["source"] == "Yahoo Finance"
    assert "Yahoo Finance" in dossier["overview"]

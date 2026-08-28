"""HK/US company dossier refresh with TDX market data as the primary source."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from backtest.loaders.yahoo_client import get_quote_summary, map_symbol

from .store import ResearchWorkspaceStore, normalize_market, normalize_symbol

_MODULES = [
    "price",
    "assetProfile",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "earningsTrend",
]

_SECTOR_ZH = {
    "Basic Materials": "原材料",
    "Communication Services": "通信服务",
    "Consumer Cyclical": "可选消费",
    "Consumer Defensive": "必需消费",
    "Energy": "能源",
    "Financial Services": "金融",
    "Healthcare": "医疗保健",
    "Industrials": "工业",
    "Real Estate": "房地产",
    "Technology": "信息技术",
    "Utilities": "公用事业",
}

MARKET_LABELS = {"HK": "港股", "US": "美股"}


def _raw(value: Any) -> Any:
    return value.get("raw") if isinstance(value, dict) else value


def _metric(source: dict[str, Any], key: str) -> Any:
    value = _raw(source.get(key))
    return value if value not in (None, "") else None


class GlobalEquityResearchService:
    """Fetch and persist one evidence-dated HK/US company profile."""

    def __init__(
        self,
        store: ResearchWorkspaceStore,
        fetcher: Callable[[str, list[str]], dict[str, Any]] | None = None,
        tdx_fetcher: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.store = store
        self.fetcher = fetcher or get_quote_summary
        self.tdx_fetcher = tdx_fetcher
        # An explicitly injected Yahoo fetcher is used by deterministic tests
        # and remains a deliberate fallback path. Production defaults to TDX.
        self.use_tdx = tdx_fetcher is not None or fetcher is None

    def refresh(self, market: str, symbol: str) -> dict[str, Any]:
        market = normalize_market(market)
        if market not in {"HK", "US"}:
            raise ValueError("company refresh supports HK and US only")
        symbol = normalize_symbol(market, symbol)
        if self.use_tdx:
            tdx = self._tdx_summary(market, symbol)
            if tdx:
                return self._persist_tdx(market, symbol, tdx)
        return self._refresh_yahoo(market, symbol)

    def _tdx_summary(self, market: str, symbol: str) -> dict[str, Any] | None:
        if self.tdx_fetcher is not None:
            return self.tdx_fetcher(market, symbol)
        from src.tdx_data.service import get_tdx_service

        return get_tdx_service().global_security_overview(market, symbol)

    def _persist_tdx(self, market: str, symbol: str, summary: dict[str, Any]) -> dict[str, Any]:
        quote = summary.get("quote") if isinstance(summary.get("quote"), dict) else {}
        finance = summary.get("finance") if isinstance(summary.get("finance"), dict) else {}
        name = str(summary.get("name") or quote.get("name") or symbol)
        data_as_of = str(summary.get("data_as_of") or datetime.now(timezone.utc).date().isoformat())[:10]
        def ten_thousand_to_unit(value: Any) -> float | None:
            try:
                return float(value) * 10_000
            except (TypeError, ValueError):
                return None
        def hundred_million_to_unit(value: Any) -> float | None:
            try:
                return float(value) * 100_000_000
            except (TypeError, ValueError):
                return None

        metrics = {
            "price": quote.get("price"), "last_close": quote.get("last_close"),
            "change_pct": quote.get("change_pct"),
            "market_cap": hundred_million_to_unit(finance.get("market_cap_100m")),
            "trailing_pe": finance.get("pe_ttm"), "forward_pe": finance.get("pe_dynamic"),
            "price_to_book": finance.get("pb_mrq"), "dividend_yield": finance.get("dividend_yield"),
            "total_revenue": ten_thousand_to_unit(finance.get("revenue_10k")),
            "net_profit": ten_thousand_to_unit(finance.get("net_profit_10k")),
            "operating_profit": ten_thousand_to_unit(finance.get("operating_profit_10k")),
            "total_assets": ten_thousand_to_unit(finance.get("total_assets_10k")),
            "net_assets": ten_thousand_to_unit(finance.get("net_assets_10k")),
            "eps": finance.get("eps"), "bps": finance.get("bps"),
            "main_business": finance.get("main_business"), "report_date": finance.get("report_date"),
            "source": "通达信客户端", "source_url": "",
            "snapshot_id": summary.get("snapshot_id"),
        }
        available = {key: value for key, value in metrics.items() if value not in (None, "")}
        overview = (
            f"{name}（{symbol}）的行情、基础财务和估值来自通达信{MARKET_LABELS[market]}市场快照。"
            f"截至 {data_as_of}，价格为 {metrics['price'] if metrics['price'] is not None else '暂缺'}，"
            f"PE(TTM) 为 {metrics['trailing_pe'] if metrics['trailing_pe'] is not None else '暂缺'}。"
            "行业分类与公司叙事需以后续交易所披露源补齐。"
        )
        bull_thesis = (
            "正向验证重点：利润、现金流、资本回报和估值安全边际。"
            f"当前净利润={metrics['net_profit'] if metrics['net_profit'] is not None else '暂缺'}，"
            f"PB={metrics['price_to_book'] if metrics['price_to_book'] is not None else '暂缺'}。"
        )
        bear_thesis = (
            "反向验证重点：盈利下滑、估值抬升、财务杠杆和公司披露的风险变化。"
            f"当前动态PE={metrics['forward_pe'] if metrics['forward_pe'] is not None else '暂缺'}，"
            f"总资产={metrics['total_assets'] if metrics['total_assets'] is not None else '暂缺'}。"
        )
        return self.store.upsert_company_dossier(
            market=market, symbol=symbol, name=name, exchange="", sector_code="TDX:UNCLASSIFIED",
            sector_name="通达信未分类", overview=overview, bull_thesis=bull_thesis, bear_thesis=bear_thesis,
            metrics=available, catalysts=["下一份财报与业绩指引", "通达信行情与估值字段更新"],
            risks=["通达信行业分类与公告披露尚未接入", "基础财务字段需与交易所披露交叉核验"],
            data_as_of=data_as_of, source_status="live",
        )

    def _refresh_yahoo(self, market: str, symbol: str) -> dict[str, Any]:
        summary = self.fetcher(symbol, list(_MODULES))
        if not summary:
            raise RuntimeError(f"Yahoo did not return a company profile for {symbol}")

        price = summary.get("price") if isinstance(summary.get("price"), dict) else {}
        profile = summary.get("assetProfile") if isinstance(summary.get("assetProfile"), dict) else {}
        detail = summary.get("summaryDetail") if isinstance(summary.get("summaryDetail"), dict) else {}
        stats = summary.get("defaultKeyStatistics") if isinstance(summary.get("defaultKeyStatistics"), dict) else {}
        financial = summary.get("financialData") if isinstance(summary.get("financialData"), dict) else {}
        trend = summary.get("earningsTrend") if isinstance(summary.get("earningsTrend"), dict) else {}

        name = str(price.get("longName") or price.get("shortName") or symbol)
        sector_en = str(profile.get("sector") or "Unclassified")
        industry = str(profile.get("industry") or sector_en)
        sector_name = _SECTOR_ZH.get(sector_en, sector_en)
        timestamp = _metric(price, "regularMarketTime")
        if timestamp:
            data_as_of = datetime.fromtimestamp(float(timestamp), timezone.utc).date().isoformat()
        else:
            data_as_of = datetime.now(timezone.utc).date().isoformat()
        source_url = f"https://finance.yahoo.com/quote/{map_symbol(symbol)}"
        metrics = {
            "price": _metric(price, "regularMarketPrice"),
            "currency": price.get("currency"),
            "market_cap": _metric(price, "marketCap"),
            "trailing_pe": _metric(detail, "trailingPE"),
            "forward_pe": _metric(stats, "forwardPE"),
            "price_to_book": _metric(stats, "priceToBook"),
            "trailing_eps": _metric(stats, "trailingEps"),
            "forward_eps": _metric(stats, "forwardEps"),
            "dividend_yield": _metric(detail, "dividendYield"),
            "total_revenue": _metric(financial, "totalRevenue"),
            "revenue_growth": _metric(financial, "revenueGrowth"),
            "gross_margin": _metric(financial, "grossMargins"),
            "operating_margin": _metric(financial, "operatingMargins"),
            "return_on_equity": _metric(financial, "returnOnEquity"),
            "total_cash": _metric(financial, "totalCash"),
            "total_debt": _metric(financial, "totalDebt"),
            "analyst_target_mean": _metric(financial, "targetMeanPrice"),
            "analyst_opinions": _metric(financial, "numberOfAnalystOpinions"),
            "source": "Yahoo Finance",
            "source_url": source_url,
        }
        available = {key: value for key, value in metrics.items() if value is not None}
        summary_text = str(profile.get("longBusinessSummary") or "").strip()
        overview = (
            f"{name}（{symbol}）属于{sector_name} / {industry}。截至 {data_as_of}，"
            f"价格为 {metrics['price'] if metrics['price'] is not None else '暂缺'}，"
            f"总市值为 {metrics['market_cap'] if metrics['market_cap'] is not None else '暂缺'}。"
            f"{summary_text[:500]} 数据来自 Yahoo Finance，只作为研究事实底稿。"
        )
        bull_thesis = (
            "正向验证重点：收入增长、利润率、资本回报和分析师盈利预期是否持续改善。"
            f"当前收入增速={metrics['revenue_growth'] if metrics['revenue_growth'] is not None else '暂缺'}，"
            f"ROE={metrics['return_on_equity'] if metrics['return_on_equity'] is not None else '暂缺'}。"
        )
        bear_thesis = (
            "反向验证重点：估值收缩、增长低于预期、负债上升和行业竞争加剧。"
            f"当前远期PE={metrics['forward_pe'] if metrics['forward_pe'] is not None else '暂缺'}，"
            f"总负债={metrics['total_debt'] if metrics['total_debt'] is not None else '暂缺'}。"
        )
        trend_rows = trend.get("trend") if isinstance(trend.get("trend"), list) else []
        catalysts = ["下一份财报与业绩指引", "盈利预测上调或下调", "行业需求与监管变化"]
        if trend_rows:
            catalysts.append(f"Yahoo 盈利趋势覆盖 {len(trend_rows)} 个预测周期")
        risks = ["Yahoo 数据可能延迟或缺项", "行业分类来自 Yahoo，需与恒生/GICS 官方口径交叉核验", "分析师目标价不构成投资建议"]
        return self.store.upsert_company_dossier(
            market=market,
            symbol=symbol,
            name=name,
            exchange=str(price.get("exchangeName") or price.get("exchange") or ""),
            sector_code=f"YH:{sector_en.upper().replace(' ', '_')}",
            sector_name=sector_name,
            overview=overview,
            bull_thesis=bull_thesis,
            bear_thesis=bear_thesis,
            metrics=available,
            catalysts=catalysts,
            risks=risks,
            data_as_of=data_as_of,
            source_status="live",
        )

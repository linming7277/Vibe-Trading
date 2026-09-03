"""Read-only coordination and presentation for existing investment research services."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable
from urllib.parse import quote, urljoin, urlparse


CAPABILITY_REGISTRY = {
    "COMPANY_OVERVIEW": "CompanyResearchOverviewService",
    "FINANCIAL": "FinancialAnalysisService",
    "BUSINESS": "BusinessResearchService",
    "VALUATION": "ValuePriceZoneService, HistoricalValuationService, EntryResearchService, ExitResearchService",
    "RISK": "RiskResearchService",
    "LOW_VALUE": "LowValueLeaderPool, low_value_leader_events, LowValueRiskSnapshotRepository",
}

# Versioned template for the comprehensive multi-researcher composite answer.
# External orchestrators (Feishu agents, MCP callers) should render this
# persisted format instead of stitching three separate researcher replies.
# v2 adds the five-year path table, profit-cycle positioning, balance-sheet
# structure, scenario cross-checks, and forward watch points.
COMPOSITE_TEMPLATE_VERSION = "supervisor-composite-v2"

_INTENT_PATTERNS = (
    ("SELF_INTRO", re.compile(r"你是谁|什么模型|哪个模型|有什么功能|能做什么|如何使用|使用说明|介绍(?:一下)?你自己", re.I)),
    ("LOW_VALUE_REASON", re.compile(r"(?:为什么|为何).{0,12}(?:进入|纳入|成为).{0,12}低估|低估池.{0,12}(?:为什么|原因)", re.I)),
    # Bare "分析/研究 + 股票代码" phrasing ("分析一下600460") reads as a
    # deep-dive request too: without this it falls to COMPANY_OVERVIEW and
    # the supervisor answers alone, which users experience as the dispatch
    # flow silently not happening.
    ("COMPREHENSIVE", re.compile(
        r"(?:全面|综合|深入|完整)(?:分析|研究|评估|看|了解|总结)"
        r"|(?:三个|多个|全部)研究员|委派研究员"
        r"|(?:分析|研究|评估|看看|了解)[^，。！？]{0,4}\d{6}", re.I)),
    ("LOW_VALUE", re.compile(r"低估龙头池|低估池|低估事件|进入低估|退出低估", re.I)),
    ("RISK", re.compile(r"风险|风险点|价值陷阱", re.I)),
    ("BUSINESS", re.compile(r"主要做什么|主营|业务|经营|产品|商业模式", re.I)),
    ("WATCHPOINT", re.compile(r"接下来|重点看什么|验证什么|最需要验证|盯什么|盯哪些|下一份财报|关注哪些指标|核心验证点", re.I)),
    ("FINANCIAL", re.compile(r"财务|财报|营收|收入|利润|现金流|毛利|净利|ROE|负债", re.I)),
    ("VALUATION", re.compile(r"估值|合理价值|价格区间|高估|低估|PE|PB|市盈率|市净率", re.I)),
    ("COMPANY_OVERVIEW", re.compile(r"总结一下|公司总览|研究总览|整体情况|公司情况", re.I)),
)
_DATE_PATTERN = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TRADING_LANGUAGE = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓")

_VALUATION_LABELS = {
    "UNDERVALUED": "进入低估区域",
    "DEEPLY_UNDERVALUED": "进入深度低估区域",
    "FAIR": "恢复合理估值区间",
    "OVERVALUED": "离开低估区域，估值偏高",
    "DEEPLY_OVERVALUED": "离开低估区域，估值偏高",
    "NO_LONGER_LEADER": "已移出低估龙头研究范围：当前不再属于三级行业Top1/Top2",
}
_EXIT_REASON_LABELS = {
    "VALUATION_RECOVERED": "估值恢复至非低估区间",
    "NO_LONGER_LEADER": "已移出低估龙头研究范围：当前不再属于三级行业Top1/Top2",
}

# Compact display labels for the composite answer.  Raw enum codes never
# reach the owner-facing text.
_COMPOSITE_VALUATION_LABELS = {
    "UNDERVALUED": "低估", "DEEPLY_UNDERVALUED": "深度低估", "FAIR": "合理",
    "OVERVALUED": "偏高", "DEEPLY_OVERVALUED": "明显偏高", "INSUFFICIENT_DATA": "资料不足",
}
_COMPOSITE_RISK_LABELS = {"HIGH": "高", "MEDIUM": "中", "LOW": "低", "UNKNOWN": "资料不足"}
_COMPOSITE_SEVERITY_LABELS = {"HIGH": "重点复核", "MEDIUM": "需要复核", "LOW": "观察"}
_COMPOSITE_METRIC_LABELS = {
    "revenue": "营收", "net_profit": "净利润", "operating_cash_flow": "经营现金流",
    "roe": "ROE", "debt_ratio": "资产负债率",
}
# latest_changes mixes currency metrics with ratio metrics; ratios must never
# be divided into 亿元.
_COMPOSITE_PERCENT_METRICS = {"roe", "debt_ratio", "gross_margin", "net_margin"}
_COMPOSITE_RISK_TYPE_LABELS = {
    "FINANCIAL_PROFIT_CASH_DIVERGENCE": "利润与现金流背离", "FINANCIAL_RECEIVABLE": "应收账款",
    "FINANCIAL_INVENTORY": "存货", "FINANCIAL_LIQUIDITY": "短期流动性",
    "FINANCIAL_CASH_COVERAGE": "现金覆盖", "FINANCIAL_INTEREST_DEBT": "有息负债",
    "FINANCIAL_CAPEX_PRESSURE": "资本开支压力", "FINANCIAL_CASH_FLOW": "经营现金流",
    "FINANCIAL_DEBT_RATIO": "资产负债率抬升", "FINANCIAL_PROFIT_DECLINE": "利润下滑",
    "FINANCIAL_REVENUE_DECLINE": "收入下滑", "FINANCIAL_FORECAST_DOWNGRADE": "盈利预测下修",
    "FINANCIAL_MARGIN_DECLINE": "利润率下滑", "FINANCIAL_ROE_DECLINE": "ROE 下滑",
    "VALUE_TRAP": "低估陷阱", "THESIS_STATUS": "核心逻辑状态",
    "DISCLOSURE_COVERAGE": "公告材料覆盖不足", "BUSINESS_PROFILE_MISSING": "主营业务资料缺失",
}
_COMPOSITE_HISTORY_FIELD_LABELS = {
    "accounts_receivable": "应收账款", "inventory": "存货", "cash_and_equivalents": "货币资金",
    "current_assets": "流动资产", "current_liabilities": "流动负债",
    "non_current_liabilities": "非流动负债", "interest_bearing_debt_ratio": "有息负债率",
    "debt_ratio": "资产负债率", "capex": "资本开支", "operating_cash_flow": "经营现金流",
    "gross_margin": "毛利率", "roe": "ROE", "revenue": "营业收入", "net_profit": "净利润",
}


@dataclass(frozen=True)
class ResearchBrief:
    intent: str
    research_as_of: str | None
    answer: str
    capabilities: tuple[str, ...]
    stock_code: str | None = None
    stock_name: str | None = None
    status: str = "READY"
    data_gaps: tuple[str, ...] = ()
    sources: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationPayload:
    title: str
    elements: list[dict[str, Any]]
    research_as_of: str
    briefs: tuple[ResearchBrief, ...]

    def card(self) -> dict[str, Any]:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": self.title}},
            "elements": self.elements,
        }


def _number(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "暂无数据"


def _risk_label(snapshot: dict[str, Any] | None) -> str:
    if not snapshot or snapshot.get("error") or str(snapshot.get("overall_risk") or "") == "UNKNOWN":
        return "资料不足"
    if int(snapshot.get("high_risk_count") or 0) > 0:
        return "有明显风险需要重点核验"
    if int(snapshot.get("medium_risk_count") or 0) > 0 or int(snapshot.get("material_risk_count") or 0) > 0:
        return "有风险需要复核"
    return "暂无明显风险"


def _company_url(stock_code: str, web_base_url: str) -> str | None:
    parsed = urlparse(web_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = (
        f"/company/CN/{quote(stock_code, safe='')}?from=%2Fvalue%2Ffocus"
        "&from_label=%E4%BD%8E%E4%BC%B0%E9%BE%99%E5%A4%B4%E6%B1%A0&tab=overview"
    )
    return urljoin(web_base_url.rstrip("/") + "/", path.lstrip("/"))


class InvestmentResearchSupervisorService:
    """Coordinate existing research outputs without changing their conclusions."""

    def __init__(
        self,
        *,
        financial_service: Any | None = None,
        business_service: Any | None = None,
        overview_service: Any | None = None,
        price_zone_service: Any | None = None,
        historical_valuation_service: Any | None = None,
        entry_service: Any | None = None,
        exit_service: Any | None = None,
        risk_service: Any | None = None,
        financial_history_service: Any | None = None,
        low_value_event_reader: Callable[[str], list[dict[str, Any]]] | None = None,
        tdx_service: Any | None = None,
        security_resolver: Callable[[str, str], dict[str, Any] | None] | None = None,
        model_setting_reader: Callable[[], dict[str, Any]] | None = None,
        event_only: bool = False,
    ) -> None:
        self._event_only = event_only
        if event_only:
            return
        if financial_service is None:
            from src.financial_analysis import get_financial_analysis_service
            financial_service = get_financial_analysis_service()
        if business_service is None:
            from src.business_research import get_business_research_service
            business_service = get_business_research_service()
        if overview_service is None:
            from src.company_research import get_company_research_overview_service
            overview_service = get_company_research_overview_service()
        if price_zone_service is None:
            from src.value_price_zones import get_value_price_zone_service
            price_zone_service = get_value_price_zone_service()
        if historical_valuation_service is None:
            from src.historical_valuation import get_historical_valuation_service
            historical_valuation_service = get_historical_valuation_service()
        if entry_service is None:
            from src.entry_research import get_entry_research_service
            entry_service = get_entry_research_service()
        if exit_service is None:
            from src.exit_research import get_exit_research_service
            exit_service = get_exit_research_service()
        if risk_service is None:
            from src.risk_research import get_risk_research_service
            risk_service = get_risk_research_service()
        if financial_history_service is None:
            from src.tdx_data.financial_history import FinancialHistoryService
            financial_history_service = FinancialHistoryService(store=getattr(tdx_service, "store", None))
        if tdx_service is None:
            from src.tdx_data import get_tdx_service
            tdx_service = get_tdx_service()
        if low_value_event_reader is None:
            from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
            repository = LowValueLeaderPoolRepository()

            def low_value_event_reader(as_of: str) -> list[dict[str, Any]]:  # noqa: F811
                return repository.events(event_date=as_of)
        self.financial_service = financial_service
        self.business_service = business_service
        self.overview_service = overview_service
        self.price_zone_service = price_zone_service
        self.historical_valuation_service = historical_valuation_service
        self.entry_service = entry_service
        self.exit_service = exit_service
        self.risk_service = risk_service
        self.financial_history_service = financial_history_service
        self.low_value_event_reader = low_value_event_reader
        self.tdx_service = tdx_service
        self.security_resolver = security_resolver or self._resolve_security
        self.model_setting_reader = model_setting_reader or self._read_research_lead_model_setting

    @staticmethod
    def classify_intent(question: str) -> str:
        text = question.strip()
        for intent, pattern in _INTENT_PATTERNS:
            if pattern.search(text):
                return intent
        return "COMPANY_OVERVIEW"

    @staticmethod
    def _resolve_security(question: str, entity: str = "") -> dict[str, Any] | None:
        from src.financial_analysis.service import FinancialAnalysisService
        return FinancialAnalysisService._resolve_cached_security(question, entity)

    @staticmethod
    def _read_research_lead_model_setting() -> dict[str, Any]:
        """Read the safe, credential-free model setting for the research lead."""
        try:
            from src.research_tasks.service import ResearchTaskService

            return next(
                item
                for item in ResearchTaskService().get_model_settings()
                if item.get("role") == "research_lead"
            )
        except (StopIteration, OSError, RuntimeError, ValueError):
            return {}

    def _self_intro(self) -> ResearchBrief:
        setting = dict(self.model_setting_reader() or {})
        model_name = str(setting.get("model_name") or "").strip()
        if model_name:
            model_text = model_name
        else:
            model_text = "尚未配置或当前不可用"
        answer = (
            "我是投研主管，负责统一读取并汇总系统里已有的研究成果，"
            "帮助你从公司整体、财务、经营、估值、风险和低估龙头池等角度快速了解研究结论。\n\n"
            f"当前投研主管研究角色配置的模型是：{model_text}。"
            "飞书里的日常查询采用规则分流并读取本地已保存资料，不会因为普通问答自动重跑研究模型，"
            "也不会自动修改公司核心逻辑或产生交易指令。\n\n"
            "你可以直接问公司名称或六位股票代码，例如："
            "“总结一下北方华创”“贵州茅台估值怎么样”“潍柴动力有什么风险”"
            "或“今天低估龙头池有什么变化”。"
        )
        return ResearchBrief(
            "SELF_INTRO",
            None,
            answer,
            tuple(CAPABILITY_REGISTRY),
            status="READY",
            sources={"model_setting": {"model_name": model_name, "ready": bool(setting.get("ready"))}},
        )

    def _resolve_as_of(self, question: str, as_of: str | None) -> tuple[str | None, str | None]:
        requested = (as_of or "").strip()
        if not requested:
            match = _DATE_PATTERN.search(question)
            requested = match.group(1) if match else ""
        if requested:
            try:
                return date.fromisoformat(requested).isoformat(), None
            except ValueError:
                return None, "指定的研究日期无效。"
        ready, reason, snapshot = self.tdx_service.latest_qualified_close_snapshot()
        market_date = str((snapshot or {}).get("market_date") or "")[:10]
        if not ready or not market_date:
            return None, reason or "最新合格收盘快照尚未就绪。"
        return market_date, None

    @staticmethod
    def _history_security(history: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
        for item in reversed(list(history or [])):
            code = str(item.get("stock_code") or "").strip()
            name = str(item.get("stock_name") or "").strip()
            if code:
                return {"code": code, "name": name or code}
        return None

    def _company(self, question: str, history: Iterable[dict[str, Any]] | None) -> dict[str, Any] | None:
        security = self.security_resolver(question, "")
        return security or self._history_security(history)

    @staticmethod
    def _summary_from_financial(snapshot: dict[str, Any]) -> str:
        feature = dict(snapshot.get("feature") or {})
        analysis = dict(snapshot.get("analysis") or {})
        text = str(analysis.get("executive_summary") or "").strip()
        if text:
            return text
        changes = list(feature.get("latest_changes") or [])
        facts = []
        for item in changes[:3]:
            metric = str(item.get("metric") or "").strip()
            value = item.get("change_percent")
            if metric and value is not None:
                facts.append(f"{metric}同比变化 {_number(value)}%")
        return "；".join(facts) if facts else "财务资料不足，暂无法形成可靠的财务说明。"

    @staticmethod
    def _summary_from_business(snapshot: dict[str, Any]) -> str:
        main_business = str(snapshot.get("main_business") or "").strip()
        if not main_business or main_business == "UNKNOWN":
            return "经营资料不足，暂无法可靠说明公司主要业务。"
        products = [str(item) for item in list(snapshot.get("products") or []) if str(item).strip()]
        suffix = f"主要产品包括：{'、'.join(products[:5])}。" if products else ""
        return f"公司主营业务为：{main_business}。{suffix}"

    def _read_financial(self, stock_code: str, as_of: str) -> dict[str, Any]:
        reader = getattr(self.financial_service, "get_saved_resolved_analysis", None)
        if callable(reader):
            return dict(reader(stock_code, as_of=as_of) or {})
        return dict(self.financial_service.get_resolved_analysis(stock_code, as_of=as_of) or {})

    def _read_business(self, stock_code: str, as_of: str) -> dict[str, Any]:
        reader = getattr(self.business_service, "get_saved_research", None)
        if callable(reader):
            try:
                return dict(reader(stock_code, as_of=as_of) or {})
            except TypeError:
                return dict(reader(stock_code) or {})
        return dict(self.business_service.get(stock_code, as_of=as_of) or {})

    # ------------------------------------------------------------------
    # Comprehensive composite answer (supervisor-composite-v2)
    # ------------------------------------------------------------------

    @staticmethod
    def _yi(value: Any) -> str:
        try:
            return f"{float(value) / 1e8:.2f} 亿"
        except (TypeError, ValueError):
            return "暂无数据"

    @staticmethod
    def _pct(value: Any) -> str:
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return "暂无数据"

    @staticmethod
    def _trend_items(feature: dict[str, Any], key: str) -> list[dict[str, Any]]:
        """Point series live inside grouped feature blocks (growth/profitability/...).

        Plain metrics are stored as bare point lists; metrics with sector
        applicability (e.g. gross_margin) are wrapped in ``{"items": [...]}``.
        Both shapes must resolve.
        """
        for group in ("growth", "profitability", "cash_flow", "balance_sheet", "capital_expenditure"):
            series = dict(feature.get(group) or {}).get(key)
            if series is None:
                continue
            raw = series if isinstance(series, list) else list(series.get("items") or [])
            items = [
                dict(item) for item in raw
                if isinstance(item, dict) and item.get("value") is not None
            ]
            if items:
                return items
        return []

    @classmethod
    def _annual_items(cls, feature: dict[str, Any], key: str) -> list[dict[str, Any]]:
        return [item for item in cls._trend_items(feature, key) if item.get("period_type") == "annual"]

    @classmethod
    def _annual_yoy(cls, feature: dict[str, Any], key: str) -> float | None:
        items = cls._annual_items(feature, key)
        if len(items) < 2:
            return None
        try:
            previous, current = float(items[-2]["value"]), float(items[-1]["value"])
        except (TypeError, ValueError):
            return None
        if previous == 0:
            return None
        return (current / previous - 1) * 100

    @staticmethod
    def _history_field_gaps(rows: list[dict[str, Any]]) -> list[str]:
        """Report balance-sheet fields whose pipeline exists but whose vendor
        data is missing for this company (distinct from un-integrated fields)."""
        if not rows:
            return []
        labels = []
        for field_key, label in _COMPOSITE_HISTORY_FIELD_LABELS.items():
            if field_key in {"debt_ratio", "operating_cash_flow", "gross_margin", "roe", "revenue", "net_profit"}:
                continue
            if all(row.get(field_key) is None for row in rows):
                labels.append(label)
        return labels

    @staticmethod
    def _cell(value: Any, kind: str = "num") -> str:
        """Render one table cell, using an em dash for missing values."""
        if value is None:
            return "—"
        if kind == "yi":
            return InvestmentResearchSupervisorService._yi(value)
        if kind == "plain_yi":
            try:
                return f"{float(value) / 1e8:.2f}"
            except (TypeError, ValueError):
                return "—"
        if kind == "pct":
            return InvestmentResearchSupervisorService._pct(value)
        return str(value)

    @classmethod
    def _annual_history_rows(
        cls, financial: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (annual rows oldest→newest, all display rows) from the snapshot.

        The snapshot's ``history`` holds the feature engine's annualized
        periods (four single TDX quarters summed per year; balance-sheet fields
        are Q4 point-in-time), so flow values here are already full-year.
        """
        rows = [dict(row) for row in list(financial.get("history") or []) if isinstance(row, dict)]
        annual = [
            row for row in rows
            if str(row.get("report_date") or "").endswith("-12-31") and row.get("revenue") is not None
        ]
        annual.sort(key=lambda row: str(row.get("report_date") or ""))
        return annual, rows

    @classmethod
    def _five_year_table(cls, annual: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> list[str]:
        """Render the multi-year income/cashflow path table (annual + latest interim)."""
        rows = list(annual[-5:])
        if not rows:
            return []
        latest_annual_year = str(rows[-1].get("report_date"))[:4]
        interim = next(
            (
                row for row in sorted(all_rows, key=lambda r: str(r.get("report_date") or ""))
                if str(row.get("report_date") or "")[:4] > latest_annual_year
                and str(row.get("report_date") or "").endswith(("-03-31", "-06-30", "-09-30"))
            ),
            None,
        )
        if interim is not None:
            rows.append(interim)

        def ocf_ratio(row: dict[str, Any]) -> str:
            ocf, profit = row.get("operating_cash_flow"), row.get("net_profit")
            if ocf is None or profit is None or float(profit) <= 0:
                return "—"
            return f"{float(ocf) / float(profit):.2f}x"

        lines = [
            "**五年关键指标**",
            "| 年度 | 营收(亿) | 归母净利(亿) | 毛利率 | ROE | OCF/归母 | 资产负债率 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            date = str(row.get("report_date") or "")
            label = date[:4] if date.endswith("-12-31") else date[:7]
            lines.append(
                f"| {label} | {cls._cell(row.get('revenue'), 'plain_yi')} | {cls._cell(row.get('net_profit'), 'plain_yi')} "
                f"| {cls._cell(row.get('gross_margin'), 'pct')} | {cls._cell(row.get('roe'), 'pct')} "
                f"| {ocf_ratio(row)} | {cls._cell(row.get('debt_ratio'), 'pct')} |"
            )
        return lines

    @classmethod
    def _cycle_position(cls, annual: list[dict[str, Any]]) -> str:
        """Classify the profit-cycle phase from the annual margin/profit path."""
        margins = [
            (str(row.get("report_date"))[:4], float(row["gross_margin"]))
            for row in annual if row.get("gross_margin") is not None
        ]
        if len(margins) < 4:
            return ""
        peak_year, peak_gm = max(margins, key=lambda item: item[1])
        latest_year, latest_gm = margins[-1]
        drawdown = latest_gm - peak_gm
        recovering = latest_gm - margins[-2][1] > 0.3
        if drawdown <= -3:
            phase = "深度回调后进入修复初期" if recovering else "仍处于盈利下行周期"
        elif drawdown >= -1:
            phase = "接近本轮盈利周期高位"
        else:
            phase = "处于中等偏低位置"
        lines = [
            f"**盈利周期定位**：{phase}。"
            f"毛利率 {peak_gm:.1f}%（{peak_year} 年峰值）→ {latest_gm:.1f}%（{latest_year} 年），"
            f"回落 {abs(drawdown):.1f} 个百分点。"
        ]
        profits = [
            (str(row.get("report_date"))[:4], float(row["net_profit"]))
            for row in annual if row.get("net_profit") is not None
        ]
        if len(profits) >= 3:
            peak_p = max(profits, key=lambda item: item[1])
            trough_p = min(profits, key=lambda item: item[1])
            last_p = profits[-1]
            path = f"归母净利 {peak_p[1] / 1e8:.2f} 亿（{peak_p[0]} 年峰值）"
            if trough_p[1] < 0:
                path += f" → {trough_p[1] / 1e8:.2f} 亿（{trough_p[0]} 年亏损）"
            elif trough_p[0] != peak_p[0]:
                path += f" → 低谷 {trough_p[1] / 1e8:.2f} 亿（{trough_p[0]} 年）"
            path += f" → {last_p[1] / 1e8:.2f} 亿（{last_p[0]} 年）。"
            lines.append(f"利润路径：{path}")
        return "\n".join(lines)

    @staticmethod
    def _query_raw_history(service: Any, stock_code: str, as_of: str) -> list[dict[str, Any]]:
        """Read raw single-period PIT rows; the vendor's first-hand coverage."""
        try:
            package = dict(service.query(stock_code, as_of=as_of) or {})
        except Exception:  # noqa: BLE001 - the composite must degrade, not fail
            return []
        return [dict(row) for row in list(package.get("items") or package.get("history") or []) if isinstance(row, dict)]

    @classmethod
    def _balance_sheet_section(cls, rows: list[dict[str, Any]]) -> list[str]:
        """Render key balance-sheet items from the newest row that has any."""
        fields = (
            ("cash_and_equivalents", "货币资金"), ("inventory", "存货"),
            ("accounts_receivable", "应收账款"), ("current_assets", "流动资产"),
            ("current_liabilities", "流动负债"),
        )
        for row in sorted(rows, key=lambda r: str(r.get("report_date") or ""), reverse=True):
            if all(row.get(key) is None for key, _ in fields):
                continue
            lines = ["**资产负债结构**", "| 科目 | 金额(亿) |", "| --- | --- |"]
            for key, label in fields:
                lines.append(f"| {label} | {cls._cell(row.get(key), 'plain_yi')} |")
            ca, cl = row.get("current_assets"), row.get("current_liabilities")
            if ca is not None and cl is not None and float(cl) > 0:
                lines.append("")
                lines.append(f"流动比率 {float(ca) / float(cl):.2f}（报告期 {str(row.get('report_date'))[:10]}）")
            return lines
        return []

    @classmethod
    def _scenario_section(
        cls,
        financial: dict[str, Any],
        market_cap_yi: float | None,
        peak_profit: tuple[str, float] | None,
        current_pe: float | None,
    ) -> list[str]:
        """Objective scenario cross-checks; never a trading instruction."""
        lines = ["**情景推演**"]
        has_content = False
        if market_cap_yi and market_cap_yi > 0 and peak_profit and peak_profit[1] > 0:
            implied = market_cap_yi / (peak_profit[1] / 1e8)
            lines.append(
                f"- 峰值净利对照：历史最高归母净利 {peak_profit[1] / 1e8:.2f} 亿（{peak_profit[0]} 年），"
                f"现市值 {market_cap_yi:.0f} 亿对应 PE {implied:.0f} 倍"
                + (f"（当前 PE-TTM {current_pe:.0f}）" if current_pe else "")
            )
            has_content = True
        forecast = dict(financial.get("forecast") or {})
        scenarios = dict(forecast.get("scenarios") or {})
        profit_rows = {
            key: (scenario.get("forecast") or [{}])[-1]
            for key, scenario in scenarios.items()
        }
        valued = {
            key: tail for key, tail in profit_rows.items()
            if tail.get("net_profit") is not None and market_cap_yi and market_cap_yi > 0
        }
        if valued:
            lines.append("| 情景 | 归母净利(亿) | 现市值隐含PE |")
            lines.append("| --- | --- | --- |")
            for key in ("BEAR", "BASE", "BULL"):
                tail = valued.get(key)
                if not tail:
                    continue
                profit_yi = float(tail["net_profit"]) / 1e8
                lines.append(
                    f"| {scenarios[key].get('label') or key}（{tail.get('year')}） "
                    f"| {profit_yi:.2f} | {market_cap_yi / profit_yi:.0f} |"
                )
            has_content = True
        elif scenarios:
            revenues = {
                key: tail.get("revenue") for key, tail in profit_rows.items() if tail.get("revenue") is not None
            }
            if revenues:
                text = " · ".join(
                    f"{scenarios[key].get('label') or key} {float(revenues[key]) / 1e8:.0f} 亿"
                    for key in ("BEAR", "BASE", "BULL") if key in revenues
                )
                last_year = next((tail.get("year") for tail in profit_rows.values() if tail.get("year")), "")
                lines.append(f"- 情景营收路径（{last_year}）：{text}")
                has_content = True
            status = str(forecast.get("status") or "")
            if any(tail.get("net_profit") is None for tail in profit_rows.values()):
                lines.append(
                    f"- 净利情景受限：历史盈利含亏损或负净利率期，系统情景引擎未生成净利预测（{status or 'LIMITED'}），"
                    "仅提供营收路径；峰值净利对照见上。"
                )
                has_content = True
        if not has_content:
            return []
        return lines

    def _watchpoint_brief(self, stock_code: str, stock_name: str, research_as_of: str) -> ResearchBrief:
        from src.value_watchpoints import get_value_watchpoint_projection_service

        projection = get_value_watchpoint_projection_service().get_watchpoints(
            "CN", stock_code, research_as_of,
        )
        tops = list(projection.get("top_watchpoints") or [])
        if not tops:
            gaps = [str(item.get("description") or "").strip() for item in list(projection.get("data_gaps") or [])]
            gaps = [item for item in gaps if item]
            answer = "当前没有足够结构化验证条件。"
            if gaps:
                answer += "资料缺口：" + "；".join(gaps[:3]) + "。"
            return ResearchBrief(
                "WATCHPOINT", research_as_of, answer, (),
                stock_code, stock_name, "UNKNOWN",
                sources={"watchpoints": projection},
                data_gaps=tuple(gaps[:6]),
            )
        lines = [f"{stock_name}接下来重点验证："]
        for index, item in enumerate(tops, 1):
            next_label = str(item.get("next_review_label") or item.get("next_review_anchor") or "人工复核")
            source = str(item.get("source_module_label") or item.get("source_module") or "")
            lines.append(
                f"{index}. {item.get('title')}。当前：{item.get('current_state')}。"
                f"有利：{item.get('positive_condition')}。不利：{item.get('negative_condition')}。"
                f"下次：{next_label}。来源：{source}。"
            )
        return ResearchBrief(
            "WATCHPOINT", research_as_of, "\n".join(lines), (),
            stock_code, stock_name, "READY",
            sources={"watchpoints": projection},
        )

    @classmethod
    def _forward_watchpoints(
        cls, financial: dict[str, Any], annual: list[dict[str, Any]],
        *, stock_code: str = "", research_as_of: str = "",
    ) -> list[str]:
        """Forward watch points: projection first, else saved metrics, else history."""
        if stock_code:
            try:
                from src.value_watchpoints import get_value_watchpoint_projection_service

                projection = get_value_watchpoint_projection_service().get_watchpoints(
                    "CN", stock_code, research_as_of or None,
                )
                tops = list(projection.get("top_watchpoints") or [])
                if tops:
                    return [
                        "**前瞻验证点**（来自研究验证点投影，不是交易信号）",
                        *[f"- {item.get('title')}：{item.get('current_state')}" for item in tops],
                    ]
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
        metrics = [str(item) for item in list((financial.get("analysis") or {}).get("key_metrics_to_monitor") or []) if str(item).strip()]
        if metrics:
            return ["**前瞻验证点**（来自已保存的财务分析结论）", *[f"- {item}" for item in metrics[:6]]]
        derived: list[str] = []
        margins = [float(row["gross_margin"]) for row in annual if row.get("gross_margin") is not None]
        if len(margins) >= 2 and margins[-1] > margins[-2]:
            derived.append(f"毛利率能否延续回升（最新年度 {margins[-1]:.1f}%）")
        profits = [float(row["net_profit"]) for row in annual if row.get("net_profit") is not None]
        if len(profits) >= 3 and profits[-1] > profits[-2] > 0:
            derived.append("净利润修复的持续性（连续两年回升后能否延续）")
        ratios = [
            float(row["operating_cash_flow"]) / float(row["net_profit"])
            for row in annual
            if row.get("operating_cash_flow") is not None
            and row.get("net_profit") is not None and float(row["net_profit"]) > 0
        ]
        if ratios and ratios[-1] >= 1.5:
            derived.append(f"经营现金流对净利润的高覆盖能否维持（最新 {ratios[-1]:.1f} 倍）")
        debts = [row.get("debt_ratio") for row in annual if row.get("debt_ratio") is not None]
        if len(debts) >= 2 and float(debts[-1]) - float(debts[-2]) > 3:
            derived.append(f"资产负债率变化（最新年度 {float(debts[-1]):.1f}%，较上年抬升）")
        if not derived:
            return []
        return ["**前瞻验证点**（由历史数据派生的观察项，非模型结论）", *[f"- {item}" for item in derived]]

    def compose_company_research_summary(
        self, stock_code: str, stock_name: str, research_as_of: str, *, intent: str = "COMPREHENSIVE",
        include_researchers: bool = True,
    ) -> ResearchBrief:
        """Render one deterministic multi-researcher composite from saved research.

        The template is versioned by ``COMPOSITE_TEMPLATE_VERSION`` so external
        orchestrators can stop stitching three separate researcher replies and
        reuse this persisted format instead.  ``intent`` keeps the classified
        question intent (e.g. ``COMPANY_OVERVIEW``) for routing introspection.
        ``include_researchers=False`` drops the three researcher sections while
        keeping the deterministic skeleton (key numbers, five-year path, cycle,
        scenarios, watch points) — used by the dispatch flow where each
        researcher bot already answers in its own message.
        """
        financial = self._read_financial(stock_code, research_as_of)
        zones = dict(self.price_zone_service.get_price_zones("CN", stock_code, as_of=research_as_of) or {})
        entry = dict(self.entry_service.get_entry_research("CN", stock_code, as_of=research_as_of) or {})
        exit_result = dict(self.exit_service.get_exit_research("CN", stock_code, as_of=research_as_of) or {})
        risk = dict(self.risk_service.get_risk_research("CN", stock_code, as_of=research_as_of) or {})
        business = self._read_business(stock_code, research_as_of)
        annual_rows, all_history_rows = self._annual_history_rows(financial)
        raw_history_rows = self._query_raw_history(
            self.financial_history_service, stock_code, research_as_of,
        )
        # Vendor coverage (gap tiers) is judged on the first-hand PIT rows;
        # older snapshots may simply not carry the balance fields forward.
        history_gaps = self._history_field_gaps(raw_history_rows or all_history_rows)

        feature = dict(financial.get("feature") or {})
        identity = dict(financial.get("identity") or {})
        data_dates = dict(identity.get("data_dates") or {})
        valuation = dict(zones.get("valuation") or {})
        valuation_label = _COMPOSITE_VALUATION_LABELS.get(str(valuation.get("status")), "资料不足")
        risk_label = _COMPOSITE_RISK_LABELS.get(str(risk.get("overall_risk")), "资料不足")
        leader = bool(risk.get("is_current_l3_leader"))

        lines: list[str] = []
        lines.append(
            f"**结论**：估值判定 {valuation_label}"
            f"（合理区间 {_number(valuation.get('fair_value_low'))}–{_number(valuation.get('fair_value_high'))} 元）"
            f" · 总体风险 {risk_label}"
            f" · {'当前L3龙头池成员' if leader else '非当前L3龙头池成员'}"
            f" · 价格与估值关注条件「{entry.get('entry_level_label') or '资料不足'}」"
        )
        main_business = str(business.get("main_business") or "").strip()
        if main_business and main_business != "UNKNOWN":
            lines.append(f"主营：{main_business}")

        # --- 关键数字 table -------------------------------------------------
        market_valuation = dict(identity.get("market_valuation") or {})
        table_rows: list[tuple[str, str, str]] = []
        if zones.get("current_price") is not None:
            table_rows.append((
                "现价", f"{_number(zones.get('current_price'))} 元",
                str(zones.get("price_as_of") or "")[:10],
            ))
        if valuation.get("fair_value_low") is not None:
            table_rows.append((
                "合理价值区间",
                f"{_number(valuation.get('fair_value_low'))}–{_number(valuation.get('fair_value_high'))} 元",
                f"中值 {_number(valuation.get('fair_value_mid'))}，判定 {valuation_label}，研究估算非交易指令",
            ))
        if market_valuation.get("pe") is not None or market_valuation.get("pb") is not None:
            table_rows.append((
                "PE / PB",
                f"{_number(market_valuation.get('pe'))} / {_number(market_valuation.get('pb'))}",
                f"估值快照 {str(market_valuation.get('as_of') or data_dates.get('valuation_as_of') or '')[:10]}，不代表历史分位",
            ))
        annual_revenue = self._annual_items(feature, "revenue")
        if annual_revenue:
            year = str(annual_revenue[-1]["report_date"])[:4]
            yoy = self._annual_yoy(feature, "revenue")
            table_rows.append((
                f"{year} 年营收", self._yi(annual_revenue[-1]["value"]),
                f"同比 {f'{yoy:+.1f}%' if yoy is not None else '暂无数据'}",
            ))
        annual_profit = self._annual_items(feature, "net_profit")
        if annual_profit:
            year = str(annual_profit[-1]["report_date"])[:4]
            yoy = self._annual_yoy(feature, "net_profit")
            table_rows.append((
                f"{year} 年净利润", self._yi(annual_profit[-1]["value"]),
                f"同比 {f'{yoy:+.1f}%' if yoy is not None else '暂无数据'}",
            ))
        annual_margin = self._annual_items(feature, "gross_margin")
        annual_roe = self._annual_items(feature, "roe")
        if annual_margin or annual_roe:
            year = str((annual_margin or annual_roe)[-1]["report_date"])[:4]
            table_rows.append((
                f"{year} 毛利率 / ROE",
                f"{self._pct(annual_margin[-1]['value'] if annual_margin else None)} / "
                f"{self._pct(annual_roe[-1]['value'] if annual_roe else None)}",
                "年度值",
            ))
        debt_items = self._trend_items(feature, "debt_ratio")
        if debt_items:
            table_rows.append((
                "资产负债率", self._pct(debt_items[-1]["value"]),
                f"报告期 {str(debt_items[-1]['report_date'])[:10]}",
            ))
        if table_rows:
            lines.append("")
            lines.append("**关键数字**")
            lines.append("| 指标 | 数值 | 说明 |")
            lines.append("| --- | --- | --- |")
            lines.extend(f"| {name} | {value} | {note} |" for name, value, note in table_rows)

        # --- 五年路径与盈利周期 --------------------------------------------
        five_year = self._five_year_table(annual_rows, all_history_rows)
        if five_year:
            lines.append("")
            lines.extend(five_year)
        cycle_text = self._cycle_position(annual_rows)
        if cycle_text:
            lines.append("")
            lines.append(cycle_text)

        # --- 财报研究员 -----------------------------------------------------
        if include_researchers:
            lines.append("")
            report_date = str(data_dates.get("financial_report_date") or "")[:10]
            announcement = str(data_dates.get("financial_announcement_date") or "")[:10]
            header = f"**财报研究员**（财报期 {report_date or '暂无'}，公告 {announcement or '暂无'}）"
            lines.append(header)
            changes = [dict(item) for item in list(feature.get("latest_changes") or []) if isinstance(item, dict)]
            if changes:
                parts = []
                for item in changes:
                    metric = _COMPOSITE_METRIC_LABELS.get(str(item.get("metric")), str(item.get("metric") or ""))
                    change = item.get("change_percent")
                    if change is None:
                        continue
                    value_text = (
                        self._pct(item.get("current"))
                        if str(item.get("metric")) in _COMPOSITE_PERCENT_METRICS
                        else self._yi(item.get("current"))
                    )
                    parts.append(f"{metric} {value_text}（同比 {float(change):+.1f}%）")
                if parts:
                    lines.append(f"- 最新财报期变化：{'；'.join(parts[:5])}")
            cagr = dict(dict(feature.get("growth") or {}).get("revenue_cagr_5y")
                        or dict(feature.get("growth") or {}).get("revenue_cagr_3y") or {})
            if cagr.get("value") is not None:
                lines.append(f"- 营收近 {cagr.get('years')} 年复合增速约 {float(cagr['value']):.1f}%")
            analysis_summary = str((financial.get("analysis") or {}).get("executive_summary") or "").strip()
            if analysis_summary:
                lines.append(f"- 已保存财务研究结论：{analysis_summary}")
            elif str(financial.get("analysis_status") or "") != "COMPLETED":
                # Surface the narrative layer's status so cache-first callers
                # know a deeper financial explanation would need the
                # Financial Analyst specialist (research-cache plan §5.3).
                lines.append("- 财务叙述层：尚未生成（本节数字均来自确定性计算，如需深度解读可请财报研究员补充）")
            if len(lines) - len(table_rows) < 4 and not changes:
                lines.append("- 财务资料不足，暂无法给出可靠财务要点。")

        # --- 资产负债结构 ---------------------------------------------------
        # Balance fields are point-in-time, so raw single-period rows are a
        # valid source when the snapshot's annualized rows lack them.
        balance_rows = all_history_rows if any(
            any(row.get(key) is not None for key in (
                "cash_and_equivalents", "inventory", "accounts_receivable",
                "current_assets", "current_liabilities",
            ))
            for row in all_history_rows
        ) else raw_history_rows
        balance_lines = self._balance_sheet_section(balance_rows)
        if balance_lines:
            lines.append("")
            lines.extend(balance_lines)

        # --- 估值研究员 -----------------------------------------------------
        if include_researchers:
            lines.append("")
            lines.append(f"**估值研究员**（基准日 {research_as_of}）")
            plain = str(zones.get("plain_summary") or "").strip()
            if plain:
                lines.append(f"- {plain}")
            for method in list(valuation.get("methods") or []):
                method = dict(method)
                if str(method.get("status")) != "READY":
                    continue
                name = str(method.get("name") or "")
                peer_count = method.get("peer_count")
                multiples = (
                    f"{_number(method.get('multiple_low'))}/{_number(method.get('multiple_mid'))}/{_number(method.get('multiple_high'))} 倍"
                    if method.get("multiple_low") is not None else ""
                )
                peer_text = f"（{peer_count} 家可比：P25/P50/P75 = {multiples}）" if peer_count and multiples else ""
                lines.append(f"- 方法：{name}{peer_text}")
            if entry.get("entry_level_label"):
                lines.append(f"- 价格与估值关注条件「{entry['entry_level_label']}」：{entry.get('plain_explanation') or ''}")
            if exit_result.get("exit_level_label"):
                lines.append(f"- 研究复核压力「{exit_result['exit_level_label']}」：{exit_result.get('plain_explanation') or ''}")

        # --- 情景推演（客观对照，非交易指令）---------------------------------
        try:
            market_cap_yi = float(market_valuation.get("market_cap"))
        except (TypeError, ValueError):
            market_cap_yi = None
        try:
            current_pe = float(market_valuation.get("pe"))
        except (TypeError, ValueError):
            current_pe = None
        profits_history = [
            (str(row.get("report_date"))[:4], float(row["net_profit"]))
            for row in annual_rows if row.get("net_profit") is not None
        ]
        peak_profit = max(profits_history, key=lambda item: item[1]) if profits_history else None
        scenario_lines = self._scenario_section(financial, market_cap_yi, peak_profit, current_pe)
        if scenario_lines:
            lines.append("")
            lines.extend(scenario_lines)

        # --- 风险研究员 -----------------------------------------------------
        if include_researchers:
            lines.append("")
            lines.append(f"**风险研究员**（基准日 {research_as_of}）")
            lines.append(f"- 总体风险 {risk_label}：{risk.get('summary') or '风险资料不足。'}")
            for item in list(risk.get("risks") or [])[:3]:
                item = dict(item)
                severity = _COMPOSITE_SEVERITY_LABELS.get(str(item.get("severity")), str(item.get("severity") or ""))
                label = _COMPOSITE_RISK_TYPE_LABELS.get(str(item.get("risk_type")), str(item.get("risk_type") or ""))
                text = str(item.get("text") or "").strip()
                lines.append(f"- 【{severity}】{label}：{text}" if text else f"- 【{severity}】{label}")
            trap = str(risk.get("value_trap_risk") or "")
            if trap and trap not in {"NOT_APPLICABLE", "NONE"}:
                lines.append(f"- 低估陷阱风险：{trap}")

        # --- 前瞻验证点 -----------------------------------------------------
        watch_lines = self._forward_watchpoints(
            financial, annual_rows, stock_code=stock_code, research_as_of=research_as_of,
        )
        if watch_lines:
            lines.append("")
            lines.extend(watch_lines)

        # --- 数据边界（分级）------------------------------------------------
        boundary: list[str] = []
        if history_gaps:
            boundary.append(
                f"- 该公司数据缺失：{'、'.join(history_gaps)}（字段管道系统已接入，该公司数据源暂未提供数值，"
                "相关偿债与营运质量规则只能标记资料不足）"
            )
        daily_status = str(((zones.get("data_quality") or {}).get("daily_history") or {}).get("status") or "")
        historical_status = str(((zones.get("data_quality") or {}).get("historical_valuation") or {}).get("status") or "")
        if daily_status in {"MISSING", "INSUFFICIENT"} or historical_status == "INSUFFICIENT":
            scope_text = "在" if leader else "不在"
            boundary.append(
                f"- 范围未物化：前复权日线与支撑/阻力区、历史估值分位（系统仅对三级行业龙头池与低估龙头池物化；"
                f"该公司当前{scope_text}该范围）"
            )
        risk_quality = dict(risk.get("data_quality") or {})
        missing_list = [str(item) for item in list(risk_quality.get("missing") or [])]
        if str(risk_quality.get("official_disclosure_sources") or "").upper() in {"NOT_COLLECTED", "MISSING", "UNKNOWN", ""}:
            boundary.append("- 系统未接入：官方公告材料采集（产品结构、客户集中度、分产品毛利等无法据此核验；不表示公司未披露）")
        business_status = str(business.get("analysis_status") or "")
        if business_status in {"", "UNKNOWN", "NOT_STARTED"} or not business:
            boundary.append("- 未建立研究：公司经营研究")
        if "THESIS" in missing_list or str(risk_quality.get("thesis") or "") in {"MISSING", "UNKNOWN"}:
            boundary.append("- 未建立研究：公司核心逻辑（Thesis）")
        lines.append("")
        lines.append("**数据边界**")
        lines.extend(boundary or ["- 本研究引用的数据均已就绪，无额外边界说明。"])

        lines.append("")
        lines.append(
            f"— 研究基准日 {research_as_of} · 综合 {COMPOSITE_TEMPLATE_VERSION}"
            " · 依据 FINANCIAL、VALUATION、RISK、BUSINESS"
        )
        answer = "\n".join(lines)
        if _TRADING_LANGUAGE.search(answer):
            raise ValueError("composite summary contains prohibited trading language")
        status = "READY" if financial and zones and risk else "PARTIAL"
        return ResearchBrief(
            intent, research_as_of, answer,
            ("FINANCIAL", "VALUATION", "RISK", "BUSINESS"), stock_code, stock_name,
            status=status, sources={
                "financial": financial, "price_zones": zones, "entry": entry, "exit": exit_result,
                "risk": risk, "business": business, "history_field_gaps": history_gaps,
                "template_version": COMPOSITE_TEMPLATE_VERSION,
            },
        )

    def handle_question(
        self,
        *,
        question: str,
        history: Iterable[dict[str, Any]] | None = None,
        as_of: str | None = None,
    ) -> ResearchBrief:
        intent = self.classify_intent(question)
        if intent == "SELF_INTRO":
            return self._self_intro()
        research_as_of, error = self._resolve_as_of(question, as_of)
        if error or not research_as_of:
            return ResearchBrief(intent, research_as_of, error or "研究日期不可用。", (), status="UNAVAILABLE", data_gaps=("MARKET_CLOSE",))
        company = self._company(question, history)
        if not company:
            return ResearchBrief(
                intent, research_as_of,
                "未能识别出具体公司。请补充 A 股公司名称或六位股票代码。",
                (), status="UNKNOWN", data_gaps=("SECURITY",),
            )
        stock_code = str(company.get("code") or company.get("stock_code") or "").upper()
        stock_name = str(company.get("name") or company.get("stock_name") or stock_code)
        if not stock_code:
            return ResearchBrief(intent, research_as_of, "未能识别出具体公司。请补充 A 股公司名称或六位股票代码。", (), status="UNKNOWN", data_gaps=("SECURITY",))
        if intent == "WATCHPOINT":
            return self._watchpoint_brief(stock_code, stock_name, research_as_of)
        if intent == "FINANCIAL":
            result = self._read_financial(stock_code, research_as_of)
            answer = self._summary_from_financial(result) if result else "尚未保存该公司的财务研究，暂无法可靠说明。"
            return ResearchBrief(intent, research_as_of, f"财务依据：{answer}", ("FINANCIAL",), stock_code, stock_name,
                                "READY" if result else "UNKNOWN", sources={"financial": result})
        if intent == "BUSINESS":
            result = self._read_business(stock_code, research_as_of)
            answer = self._summary_from_business(result) if result else "尚未保存该公司的经营研究，暂无法可靠说明。"
            return ResearchBrief(intent, research_as_of, f"经营依据：{answer}", ("BUSINESS",), stock_code, stock_name,
                                "READY" if result else "UNKNOWN", sources={"business": result})
        if intent == "VALUATION":
            zones = dict(self.price_zone_service.get_price_zones("CN", stock_code, as_of=research_as_of) or {})
            history_result = dict(self.historical_valuation_service.get_valuation_history("CN", stock_code, as_of=research_as_of) or {})
            entry = dict(self.entry_service.get_entry_research("CN", stock_code, as_of=research_as_of) or {})
            exit_result = dict(self.exit_service.get_exit_research("CN", stock_code, as_of=research_as_of) or {})
            answer = str(zones.get("plain_summary") or "估值资料不足，暂无法判断。")
            return ResearchBrief(intent, research_as_of, f"估值依据：{answer}", ("VALUATION",), stock_code, stock_name,
                                sources={"price_zones": zones, "historical_valuation": history_result, "entry": entry, "exit": exit_result})
        if intent == "RISK":
            risk = dict(self.risk_service.get_risk_research("CN", stock_code, as_of=research_as_of) or {})
            answer = str(risk.get("summary") or "风险资料不足，暂无法判断。")
            return ResearchBrief(intent, research_as_of, f"风险依据：{answer}", ("RISK",), stock_code, stock_name,
                                str(risk.get("status") or "UNKNOWN"), sources={"risk": risk})
        if intent == "LOW_VALUE_REASON":
            events = [item for item in self.low_value_event_reader(research_as_of) if str(item.get("stock_code") or "").upper() == stock_code]
            zones = dict(self.price_zone_service.get_price_zones("CN", stock_code, as_of=research_as_of) or {})
            risk = dict(self.risk_service.get_risk_research("CN", stock_code, as_of=research_as_of) or {})
            if not events:
                event_text = "该研究日期未读取到该公司的低估事件。"
            else:
                event_text = "；".join(
                    f"{_VALUATION_LABELS.get(str(item.get('after_status') or ''), '资料不足')}" for item in events
                )
            valuation = str(zones.get("plain_summary") or "估值资料不足，暂无法判断。")
            risk_text = str(risk.get("summary") or "风险资料不足，暂无法判断。")
            answer = f"低估事件依据：{event_text}\n估值依据：{valuation}\n风险依据：{risk_text}"
            return ResearchBrief(intent, research_as_of, answer, ("LOW_VALUE", "VALUATION", "RISK"), stock_code, stock_name,
                                sources={"events": events, "price_zones": zones, "risk": risk})
        if intent == "LOW_VALUE":
            events = [item for item in self.low_value_event_reader(research_as_of) if str(item.get("stock_code") or "").upper() == stock_code]
            answer = "该研究日期未读取到该公司的低估事件。" if not events else "；".join(
                _VALUATION_LABELS.get(str(item.get("after_status") or ""), "资料不足") for item in events
            )
            return ResearchBrief(intent, research_as_of, f"低估龙头池依据：{answer}", ("LOW_VALUE",), stock_code, stock_name,
                                "READY" if events else "UNKNOWN", sources={"events": events})
        if intent in {"COMPREHENSIVE", "COMPANY_OVERVIEW"}:
            return self.compose_company_research_summary(
                stock_code, stock_name, research_as_of, intent=intent,
            )
        overview = dict(self.overview_service.get_overview("CN", stock_code) or {})
        financial_status = str((overview.get("data_status") or {}).get("financial") or "UNKNOWN")
        business_status = str((overview.get("data_status") or {}).get("business") or "UNKNOWN")
        answer = (
            f"财务资料：{financial_status}；经营资料：{business_status}。"
            "公司研究总览仅读取已保存的研究资料。"
        )
        return ResearchBrief(intent, research_as_of, answer, ("COMPANY_OVERVIEW",), stock_code, stock_name,
                            sources={"overview": overview})

    def handle_research_event(
        self,
        event: dict[str, Any],
        *,
        risk: dict[str, Any] | None = None,
        web_base_url: str = "",
    ) -> ResearchBrief:
        code = str(event.get("stock_code") or "")
        name = str(event.get("company_name") or code)
        industry = str(event.get("industry_name") or "暂无数据")
        entered = event.get("event_type") == "ENTER_LOW_VALUE"
        lines = [f"**{name} / {code}**", f"L3行业：{industry}"]
        if entered:
            lines.extend([
                f"状态：{_VALUATION_LABELS.get(str(event.get('after_status') or event.get('valuation_status') or ''), '资料不足')}",
                f"当前价格：{_number(event.get('current_price'))}",
                f"合理价值中枢：{_number(event.get('fair_value_mid'))}",
                f"风险复核：{_risk_label(risk)}",
            ])
        else:
            reason = str((event.get("metadata") or {}).get("reason") or "")
            lines.extend([
                f"退出原因：{_EXIT_REASON_LABELS.get(reason, _VALUATION_LABELS.get(str(event.get('after_status') or ''), '资料不足'))}",
                f"状态变化：{_VALUATION_LABELS.get(str(event.get('before_status') or ''), '资料不足')} → {_VALUATION_LABELS.get(str(event.get('after_status') or ''), '资料不足')}",
                f"当前价格：{_number(event.get('current_price'))}",
                f"合理价值中枢：{_number(event.get('fair_value_mid'))}",
            ])
        if _TRADING_LANGUAGE.search("\n".join(lines)):
            raise ValueError("notification content contains prohibited trading language")
        return ResearchBrief(
            "LOW_VALUE_ENTER" if entered else "LOW_VALUE_EXIT",
            str(event.get("source_as_of") or event.get("event_date") or "")[:10],
            "\n".join(lines),
            ("LOW_VALUE", "RISK") if entered else ("LOW_VALUE",),
            code, name, sources={"event": event, "risk": risk or {}, "web_base_url": web_base_url},
        )

    def build_low_value_notification_payload(
        self,
        *,
        research_as_of: str,
        events: list[dict[str, Any]],
        risks: dict[str, dict[str, Any]],
        web_base_url: str,
    ) -> NotificationPayload:
        entered = [item for item in events if item.get("event_type") == "ENTER_LOW_VALUE"]
        exited = [item for item in events if item.get("event_type") == "EXIT_LOW_VALUE"]
        elements: list[dict[str, Any]] = [{
            "tag": "markdown",
            "content": f"研究日期：{research_as_of}\n\n新增低估：{len(entered)} 家\n退出低估：{len(exited)} 家",
        }]
        briefs: list[ResearchBrief] = []
        for section, rows in (("新增低估", entered), ("退出低估", exited)):
            if not rows:
                continue
            elements.extend([{"tag": "hr"}, {"tag": "markdown", "content": f"**{section}**"}])
            for event in rows:
                brief = self.handle_research_event(
                    event,
                    risk=risks.get(str(event.get("stock_code") or "")) if event.get("event_type") == "ENTER_LOW_VALUE" else None,
                    web_base_url=web_base_url,
                )
                briefs.append(brief)
                elements.append({"tag": "markdown", "content": brief.answer})
                url = _company_url(str(event.get("stock_code") or ""), web_base_url)
                if url:
                    elements.append({
                        "tag": "action",
                        "actions": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看公司研究"},
                            "type": "default",
                            "url": url,
                        }],
                    })
        return NotificationPayload("今日低估龙头变化", elements, research_as_of, tuple(briefs))


_service: InvestmentResearchSupervisorService | None = None


def get_investment_research_supervisor_service() -> InvestmentResearchSupervisorService:
    global _service
    if _service is None:
        _service = InvestmentResearchSupervisorService()
    return _service

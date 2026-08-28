from __future__ import annotations

from typing import Any

from src.investment_research_supervisor import CAPABILITY_REGISTRY, InvestmentResearchSupervisorService


AS_OF = "2026-08-25"


class FakeTdx:
    def latest_qualified_close_snapshot(self):
        return True, "", {"market_date": AS_OF}


class FakeFinancial:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_saved_resolved_analysis(self, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((stock_code, as_of))
        return {"analysis": {"executive_summary": "收入与现金流资料已保存。"}}


class FakeBusiness:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_saved_research(self, stock_code: str) -> dict[str, Any]:
        self.calls.append(stock_code)
        return {"main_business": "企业数字化服务", "products": ["云服务"]}


class FakeOverview:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_overview(self, market: str, stock_code: str) -> dict[str, Any]:
        self.calls.append((market, stock_code))
        return {"data_status": {"financial": "READY", "business": "PARTIAL"}}


class FakePriceZones:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"plain_summary": "当前估值资料来自既有估值研究。"}


class FakeHistorical:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_valuation_history(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"historical_valuation_status": "CHEAP"}


class FakeEntryExit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_entry_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"plain_explanation": "既有入场研究"}

    def get_exit_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"plain_explanation": "既有退出研究"}


class FakeRisk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_risk_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"status": "READY", "summary": "存在一项需要复核的风险。"}


class FakeFinancialHistory:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, str | None]] = []

    def query(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        self.calls.append((stock_code, as_of))
        return {"items": self.rows}


def _service(*, events: list[dict[str, Any]] | None = None, history_rows: list[dict[str, Any]] | None = None):
    financial = FakeFinancial()
    business = FakeBusiness()
    overview = FakeOverview()
    zones = FakePriceZones()
    historical = FakeHistorical()
    entry = FakeEntryExit()
    exit_service = FakeEntryExit()
    risk = FakeRisk()
    financial_history = FakeFinancialHistory(history_rows)
    service = InvestmentResearchSupervisorService(
        financial_service=financial,
        business_service=business,
        overview_service=overview,
        price_zone_service=zones,
        historical_valuation_service=historical,
        entry_service=entry,
        exit_service=exit_service,
        risk_service=risk,
        financial_history_service=financial_history,
        low_value_event_reader=lambda _as_of: list(events or []),
        tdx_service=FakeTdx(),
        security_resolver=lambda _question, _entity: {"code": "000001.SZ", "name": "示例公司"},
        model_setting_reader=lambda: {
            "role": "research_lead", "model_name": "test-model", "enabled": True, "ready": True,
        },
    )
    return service, financial, business, overview, zones, historical, entry, exit_service, risk


def test_capability_registry_is_limited_to_existing_research_services() -> None:
    assert set(CAPABILITY_REGISTRY) == {"COMPANY_OVERVIEW", "FINANCIAL", "BUSINESS", "VALUATION", "RISK", "LOW_VALUE"}
    assert "估值研究员" not in " ".join(CAPABILITY_REGISTRY.values())


def test_self_intro_does_not_require_a_company_or_market_snapshot() -> None:
    service, financial, business, overview, zones, historical, entry, exit_service, risk = _service()

    brief = service.handle_question(question="你是什么模型，有什么功能")

    assert brief.intent == "SELF_INTRO"
    assert brief.research_as_of is None
    assert brief.stock_code is None
    assert "test-model" in brief.answer
    assert "公司整体、财务、经营、估值、风险和低估龙头池" in brief.answer
    assert "补充 A 股公司名称" not in brief.answer
    assert financial.calls == business.calls == overview.calls == zones.calls == []
    assert historical.calls == entry.calls == exit_service.calls == risk.calls == []


def test_financial_and_business_route_to_existing_saved_research() -> None:
    service, financial, business, *_ = _service()

    financial_brief = service.handle_question(question="看一下示例公司财务")
    business_brief = service.handle_question(question="它主要做什么")

    assert financial_brief.intent == "FINANCIAL"
    assert financial_brief.research_as_of == AS_OF
    assert financial.calls == [("000001.SZ", AS_OF)]
    assert "收入与现金流" in financial_brief.answer
    assert business_brief.intent == "BUSINESS"
    assert business.calls == ["000001.SZ"]
    assert "企业数字化服务" in business_brief.answer


def test_valuation_risk_and_low_value_reason_share_one_as_of() -> None:
    event = {
        "stock_code": "000001.SZ", "company_name": "示例公司", "event_type": "ENTER_LOW_VALUE",
        "after_status": "UNDERVALUED", "source_as_of": AS_OF, "event_date": AS_OF,
    }
    service, _financial, _business, _overview, zones, historical, entry, exit_service, risk = _service(events=[event])

    brief = service.handle_question(question="为什么进入低估池")

    assert brief.intent == "LOW_VALUE_REASON"
    assert brief.capabilities == ("LOW_VALUE", "VALUATION", "RISK")
    assert all(call[-1] == AS_OF for call in zones.calls + risk.calls)
    assert all(call[-1] == AS_OF for call in historical.calls + entry.calls + exit_service.calls)
    assert "低估事件依据" in brief.answer and "风险依据" in brief.answer


def test_explicit_historical_as_of_overrides_latest_close() -> None:
    service, financial, *_ = _service()

    brief = service.handle_question(question="看一下示例公司财务 2026-08-20")

    assert brief.research_as_of == "2026-08-20"
    assert financial.calls == [("000001.SZ", "2026-08-20")]


def test_company_overview_and_unknown_company_do_not_invent_research() -> None:
    service, _financial, _business, _overview, zones, _historical, entry, exit_service, risk = _service()
    summary = service.handle_question(question="总结一下这家公司")
    assert summary.intent == "COMPANY_OVERVIEW"
    # The overview question now renders the comprehensive composite instead of
    # a bare data-status line.
    assert "**财报研究员**" in summary.answer
    assert "**数据边界**" in summary.answer
    assert zones.calls and entry.calls and exit_service.calls and risk.calls

    unknown = InvestmentResearchSupervisorService(
        financial_service=FakeFinancial(), business_service=FakeBusiness(), overview_service=FakeOverview(),
        price_zone_service=FakePriceZones(), historical_valuation_service=FakeHistorical(), entry_service=FakeEntryExit(),
        exit_service=FakeEntryExit(), risk_service=FakeRisk(), financial_history_service=FakeFinancialHistory(),
        low_value_event_reader=lambda _as_of: [], tdx_service=FakeTdx(),
        security_resolver=lambda _question, _entity: None,
    ).handle_question(question="看一下财务")
    assert unknown.status == "UNKNOWN"
    assert "未能识别" in unknown.answer


def test_low_value_event_payload_is_deterministic_and_has_no_trading_terms() -> None:
    event = {
        "stock_code": "000001.SZ", "company_name": "示例公司", "industry_name": "示例L3行业",
        "event_type": "ENTER_LOW_VALUE", "after_status": "UNDERVALUED", "current_price": 12.34,
        "fair_value_mid": 18.9, "source_as_of": AS_OF, "event_date": AS_OF, "metadata": {},
    }
    service, *_ = _service()

    payload = service.build_low_value_notification_payload(
        research_as_of=AS_OF, events=[event], risks={"000001.SZ": {"overall_risk": "LOW"}},
        web_base_url="https://research.example.test",
    )

    text = "\n".join(str(item) for item in payload.elements)
    assert payload.title == "今日低估龙头变化"
    assert "进入低估区域" in text and "暂无明显风险" in text
    assert "查看公司研究" in text
    assert all(word not in text for word in ("买入", "卖出", "推荐", "止盈", "止损", "仓位"))


# ---------------------------------------------------------------------------
# Comprehensive composite answer (supervisor-composite-v1)
# ---------------------------------------------------------------------------

class RichFinancial:
    def __init__(self, forecast_mode: str = "limited", history_rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.history_rows = history_rows or []
        profit_by_scenario = {"BEAR": None, "BASE": None, "BULL": None}
        if forecast_mode == "ready":
            profit_by_scenario = {"BEAR": 400_000_000.0, "BASE": 600_000_000.0, "BULL": 800_000_000.0}
        self.forecast = {
            "status": "LIMITED" if forecast_mode == "limited" else "READY",
            "scenarios": {
                key: {"label": label, "forecast": [
                    {"year": "2026E", "revenue": revenue, "net_profit": None},
                    {"year": "2027E", "revenue": revenue * 1.1, "net_profit": None},
                    {"year": "2028E", "revenue": revenue * 1.2, "net_profit": profit_by_scenario[key]},
                ]}
                for key, label, revenue in (
                    ("BEAR", "谨慎", 11_000_000_000.0),
                    ("BASE", "基准", 13_500_000_000.0),
                    ("BULL", "乐观", 16_000_000_000.0),
                )
            },
        }

    def get_saved_resolved_analysis(self, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((stock_code, as_of))
        return {
            "as_of": as_of,
            "identity": {
                "stock_name": "示例公司", "level3_name": "半导体分立器件",
                "market_valuation": {"pe": 130.17, "pb": 4.85, "market_cap": 591.74, "as_of": "2026-08-24"},
                "data_dates": {
                    "financial_report_date": "2026-03-31",
                    "financial_announcement_date": "2026-04-30",
                    "valuation_as_of": "2026-08-24",
                },
            },
            "forecast": self.forecast,
            "history": self.history_rows,
            "feature": {
                "growth": {
                    "revenue": {"items": [
                        {"report_date": "2024-12-31", "period_type": "annual", "value": 11_230_000_000.0},
                        {"report_date": "2025-12-31", "period_type": "annual", "value": 13_052_000_000.0},
                    ]},
                    "net_profit": {"items": [
                        {"report_date": "2024-12-31", "period_type": "annual", "value": 220_000_000.0},
                        {"report_date": "2025-12-31", "period_type": "annual", "value": 399_000_000.0},
                    ]},
                    "revenue_cagr_5y": {"value": 25.0, "years": 5, "status": "ready"},
                },
                "profitability": {
                    "gross_margin": {"items": [{"report_date": "2025-12-31", "period_type": "annual", "value": 20.0}]},
                    "roe": {"items": [{"report_date": "2025-12-31", "period_type": "annual", "value": 3.29}]},
                },
                "balance_sheet": {
                    "debt_ratio": {"items": [{"report_date": "2026-03-31", "period_type": "q1", "value": 52.87}]},
                },
                "latest_changes": [{
                    "metric": "revenue", "previous": 3_000_000_000.0, "current": 3_519_000_000.0,
                    "change_percent": 17.3, "report_date": "2026-03-31",
                }, {
                    "metric": "roe", "previous": 2.3, "current": 3.29,
                    "change_percent": 43.0, "report_date": "2026-03-31",
                }],
            },
            "analysis": {"executive_summary": "收入延续修复，盈利能力仍低于2021年高点。"},
        }


class RichPriceZones:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_price_zones(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {
            "current_price": 34.53, "price_as_of": AS_OF,
            "valuation": {
                "status": "FAIR", "fair_value_low": 21.71, "fair_value_mid": 28.48, "fair_value_high": 36.13,
                "methods": [{
                    "name": "同三级行业 PB 可比", "status": "READY", "peer_count": 15,
                    "multiple_low": 3.05, "multiple_mid": 4.0, "multiple_high": 5.08,
                }],
            },
            "data_quality": {
                "daily_history": {"status": "MISSING", "message": "该公司尚无已缓存的前复权日线。"},
                "historical_valuation": {"status": "INSUFFICIENT"},
            },
            "plain_summary": "当前价格为 34.53，系统估算的合理价值区间为 21.71–36.13，当前属于FAIR。",
        }


class RichEntryExit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_entry_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"entry_level_label": "暂不具备明显优势", "plain_explanation": "估值处于合理区间，无充分安全边际。"}

    def get_exit_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"exit_level_label": "暂未出现明显退出压力", "plain_explanation": "估值与逻辑均无退出信号。"}


class RichRisk:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get_risk_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {
            "overall_risk": "MEDIUM", "summary": "当前发现 1 项需要复核的风险，其中 0 项为重点复核。",
            "is_current_l3_leader": False, "value_trap_risk": "NOT_APPLICABLE",
            "risks": [{"risk_type": "FINANCIAL_DEBT_RATIO", "severity": "MEDIUM", "text": "资产负债率由44.25%升至52.1%。"}],
            "data_quality": {"official_disclosure_sources": "NOT_COLLECTED", "missing": ["THESIS"], "thesis": "MISSING"},
        }


class EmptyBusiness:
    def get_saved_research(self, stock_code: str, *, as_of: str) -> dict[str, Any]:
        return {"analysis_status": "UNKNOWN"}


def _rich_service(
    *, history_rows: list[dict[str, Any]] | None = None, forecast_mode: str = "limited",
    financial_service: Any | None = None,
):
    service = InvestmentResearchSupervisorService(
        financial_service=financial_service or RichFinancial(forecast_mode, history_rows),
        business_service=EmptyBusiness(),
        overview_service=FakeOverview(),
        price_zone_service=RichPriceZones(),
        historical_valuation_service=FakeHistorical(),
        entry_service=RichEntryExit(),
        exit_service=RichEntryExit(),
        risk_service=RichRisk(),
        financial_history_service=FakeFinancialHistory(history_rows),
        low_value_event_reader=lambda _as_of: [],
        tdx_service=FakeTdx(),
        security_resolver=lambda _question, _entity: {"code": "600460.SH", "name": "士兰微"},
        model_setting_reader=lambda: {"role": "research_lead", "model_name": "test-model", "enabled": True, "ready": True},
    )
    return service


_PARTIAL_HISTORY_ROWS = [{
    "report_date": "2026-03-31", "announcement_date": "2026-04-30", "revenue": 3_519_000_000.0,
    "net_profit": 209_000_000.0, "operating_cash_flow": 25_000_000.0, "debt_ratio": 52.87,
    "cash_and_equivalents": None, "accounts_receivable": None, "inventory": None,
    "current_assets": None, "current_liabilities": None, "non_current_liabilities": None,
    "interest_bearing_debt_ratio": None,
}]


def test_composite_flags_missing_financial_narrative_layer() -> None:
    """Cache-first callers must see that the financial narrative layer is NOT_RUN."""
    class NarrativeMissingFinancial(RichFinancial):
        def get_saved_resolved_analysis(self, stock_code: str, *, as_of: str) -> dict[str, Any]:
            data = super().get_saved_resolved_analysis(stock_code, as_of=as_of)
            data.pop("analysis", None)
            data["analysis_status"] = "NOT_RUN"
            return data

    service = _rich_service(history_rows=_PARTIAL_HISTORY_ROWS, financial_service=NarrativeMissingFinancial())
    brief = service.compose_company_research_summary(
        "600460.SH", "士兰微", "2026-08-28", intent="COMPREHENSIVE",
    )
    assert "财务叙述层：尚未生成" in brief.answer
    assert "已保存财务研究结论" not in brief.answer


def test_comprehensive_intent_renders_versioned_composite_template() -> None:
    service = _rich_service(history_rows=_PARTIAL_HISTORY_ROWS)

    brief = service.handle_question(question="综合分析士兰微")

    assert brief.intent == "COMPREHENSIVE"
    assert brief.research_as_of == AS_OF
    assert brief.capabilities == ("FINANCIAL", "VALUATION", "RISK", "BUSINESS")
    assert brief.status == "READY"
    for section in (
        "**结论**", "**关键数字**", "**财报研究员**", "**估值研究员**", "**风险研究员**", "**数据边界**",
    ):
        assert section in brief.answer
    assert "| 指标 | 数值 | 说明 |" in brief.answer
    assert "supervisor-composite-v2" in brief.answer
    assert "34.53" in brief.answer and "21.71" in brief.answer and "36.13" in brief.answer
    assert "130.52 亿" in brief.answer and "+16.2%" in brief.answer
    assert "ROE 3.29%（同比 +43.0%）" in brief.answer
    assert "入场研究「暂不具备明显优势」" in brief.answer
    assert "总体风险 中" in brief.answer
    assert all(word not in brief.answer for word in ("买入", "卖出", "推荐", "止盈", "止损", "仓位"))


def test_composite_data_boundary_separates_four_gap_tiers() -> None:
    service = _rich_service(history_rows=_PARTIAL_HISTORY_ROWS)

    brief = service.handle_question(question="全面分析士兰微")

    boundary = brief.answer.split("**数据边界**", 1)[1]
    assert "该公司数据缺失" in boundary
    for label in ("货币资金", "应收账款", "存货", "有息负债率"):
        assert label in boundary
    assert "字段管道系统已接入" in boundary
    assert "范围未物化：前复权日线" in boundary and "不在" in boundary
    assert "系统未接入：官方公告材料采集" in boundary
    assert "未建立研究：公司经营研究" in boundary
    assert "未建立研究：公司核心逻辑（Thesis）" in boundary


def test_composite_boundary_omits_missing_tiers_when_data_is_ready() -> None:
    # Every vendor field present on the snapshot history rows → the
    # company-data-missing tier disappears from the boundary section.
    full_rows = [dict(_PARTIAL_HISTORY_ROWS[0], cash_and_equivalents=5_717_772_288.0,
                      accounts_receivable=3_313_701_632.0, inventory=3_876_184_576.0,
                      current_assets=15_398_586_368.0, current_liabilities=7_153_254_400.0,
                      non_current_liabilities=7_000_000_000.0, interest_bearing_debt_ratio=66.39,
                      capex=2_917_692_16.0, report_date="2025-12-31")]
    service = _rich_service(history_rows=full_rows)

    brief = service.handle_question(question="综合分析士兰微")

    boundary = brief.answer.split("**数据边界**", 1)[1]
    assert "该公司数据缺失" not in boundary


# ---------------------------------------------------------------------------
# Composite v2: five-year path, cycle positioning, scenarios, watch points
# ---------------------------------------------------------------------------

# Snapshot `history` rows: annualized by the feature engine (full-year flows,
# Q4 point-in-time balance fields) plus the latest interim period.
_SILAN_ANNUAL_ROWS = [
    {"report_date": "2021-12-31", "period_type": "annual", "flow_basis": "annualized_from_single_periods",
     "revenue": 7_190_000_000.0, "net_profit": 1_518_000_000.0, "gross_margin": 33.2,
     "roe": 27.9, "operating_cash_flow": 940_000_000.0, "debt_ratio": 46.5},
    {"report_date": "2022-12-31", "period_type": "annual", "flow_basis": "annualized_from_single_periods",
     "revenue": 8_280_000_000.0, "net_profit": 1_052_000_000.0, "gross_margin": 29.4,
     "roe": 15.4, "operating_cash_flow": 770_000_000.0, "debt_ratio": 49.8},
    {"report_date": "2023-12-31", "period_type": "annual", "flow_basis": "annualized_from_single_periods",
     "revenue": 9_340_000_000.0, "net_profit": -36_000_000.0, "gross_margin": 22.2,
     "roe": -0.5, "operating_cash_flow": 317_000_000.0, "debt_ratio": 51.2},
    {"report_date": "2024-12-31", "period_type": "annual", "flow_basis": "annualized_from_single_periods",
     "revenue": 11_220_000_000.0, "net_profit": 220_000_000.0, "gross_margin": 19.1,
     "roe": 1.9, "operating_cash_flow": 443_000_000.0, "debt_ratio": 52.1},
    {"report_date": "2025-12-31", "period_type": "annual", "flow_basis": "annualized_from_single_periods",
     "revenue": 13_052_000_000.0, "net_profit": 399_000_000.0, "gross_margin": 20.0,
     "roe": 3.29, "operating_cash_flow": 1_498_000_000.0, "debt_ratio": 52.87},
    {"report_date": "2026-03-31", "period_type": "q1",
     "revenue": 3_519_000_000.0, "net_profit": 209_000_000.0, "gross_margin": 19.8,
     "roe": 1.73, "operating_cash_flow": 25_000_000.0, "debt_ratio": 52.87},
]


def test_composite_v2_renders_five_year_table_with_ocf_ratio() -> None:
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS)

    brief = service.handle_question(question="综合分析士兰微")

    assert "**五年关键指标**" in brief.answer
    assert "| 2021 | 71.90 | 15.18 |" in brief.answer
    assert "| 2025 | 130.52 | 3.99 |" in brief.answer
    assert "| 2026-03 | 35.19 | 2.09 |" in brief.answer  # latest interim row
    # 2025 OCF/归母 = 14.98/3.99 = 3.75x；2023 亏损年为 —
    assert "3.75x" in brief.answer


def test_composite_v2_positions_the_profit_cycle_from_history() -> None:
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS)

    brief = service.handle_question(question="综合分析士兰微")

    assert "**盈利周期定位**" in brief.answer
    assert "深度回调后进入修复初期" in brief.answer
    assert "毛利率 33.2%（2021 年峰值）→ 20.0%（2025 年）" in brief.answer
    assert "15.18 亿（2021 年峰值）" in brief.answer
    assert "-0.36 亿（2023 年亏损）" in brief.answer


def test_composite_v2_scenario_section_anchors_on_peak_profit() -> None:
    # Silan's forecast is LIMITED (loss-making history blocks net-profit
    # scenarios), so the section must fall back to the peak-profit anchor.
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS, forecast_mode="limited")

    brief = service.handle_question(question="综合分析士兰微")

    assert "**情景推演**" in brief.answer
    assert "峰值净利对照" in brief.answer
    assert "15.18 亿（2021 年）" in brief.answer
    assert "对应 PE 39 倍（当前 PE-TTM 130）" in brief.answer  # 591.74/15.18≈39
    assert "情景营收路径" in brief.answer
    assert "净利情景受限" in brief.answer


def test_composite_v2_scenario_section_tables_implied_pe_when_forecast_ready() -> None:
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS, forecast_mode="ready")

    brief = service.handle_question(question="综合分析士兰微")

    assert "| 情景 | 归母净利(亿) | 现市值隐含PE |" in brief.answer
    assert "| 乐观（2028E） | 8.00 | 74 |" in brief.answer   # 591.74/8.00
    assert "| 谨慎（2028E） | 4.00 | 148 |" in brief.answer   # 591.74/4.00
    assert "净利情景受限" not in brief.answer


def test_composite_v2_derives_forward_watchpoints_without_model_analysis() -> None:
    # RichFinancial has no key_metrics_to_monitor, so watch points must be
    # derived from the historical path instead.
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS)

    brief = service.handle_question(question="综合分析士兰微")

    assert "**前瞻验证点**（由历史数据派生的观察项，非模型结论）" in brief.answer
    assert "毛利率能否延续回升（最新年度 20.0%）" in brief.answer
    assert "净利润修复的持续性" in brief.answer
    assert "经营现金流对净利润的高覆盖能否维持（最新 3.8 倍）" in brief.answer
    # 负债率同比仅抬升 0.77pp（52.87 vs 52.1），低于 3pp 派生阈值，不应出现。
    assert "资产负债率变化" not in brief.answer


def test_composite_v2_prefers_saved_model_watchpoints_when_available() -> None:
    financial = RichFinancial()
    financial.get_saved_resolved_analysis = lambda stock_code, *, as_of: {  # type: ignore[method-assign]
        "as_of": as_of,
        "identity": {"market_valuation": {"pe": 130.17, "pb": 4.85, "market_cap": 591.74}},
        "forecast": financial.forecast,
        "feature": {},
        "analysis": {
            "executive_summary": "修复中。",
            "key_metrics_to_monitor": ["营收增速", "毛利率回升", "经营现金流"],
        },
    }
    service = InvestmentResearchSupervisorService(
        financial_service=financial,
        business_service=EmptyBusiness(),
        overview_service=FakeOverview(),
        price_zone_service=RichPriceZones(),
        historical_valuation_service=FakeHistorical(),
        entry_service=RichEntryExit(),
        exit_service=RichEntryExit(),
        risk_service=RichRisk(),
        financial_history_service=FakeFinancialHistory(_SILAN_ANNUAL_ROWS),
        low_value_event_reader=lambda _as_of: [],
        tdx_service=FakeTdx(),
        security_resolver=lambda _question, _entity: {"code": "600460.SH", "name": "士兰微"},
        model_setting_reader=lambda: {"role": "research_lead", "model_name": "m", "enabled": True, "ready": True},
    )

    brief = service.handle_question(question="综合分析士兰微")

    assert "**前瞻验证点**（来自已保存的财务分析结论）" in brief.answer
    assert "- 营收增速" in brief.answer
    assert "由历史数据派生" not in brief.answer


def test_composite_v2_balance_sheet_structure_uses_newest_populated_row() -> None:
    rows = [dict(row) for row in _SILAN_ANNUAL_ROWS]
    rows[-2].update({
        "cash_and_equivalents": 5_717_772_288.0, "inventory": 3_876_184_576.0,
        "accounts_receivable": 3_313_701_632.0, "current_assets": 15_398_586_368.0,
        "current_liabilities": 7_153_254_400.0,
    })
    service = _rich_service(history_rows=rows)

    brief = service.handle_question(question="综合分析士兰微")

    assert "**资产负债结构**" in brief.answer
    assert "| 货币资金 | 57.18 |" in brief.answer
    assert "| 存货 | 38.76 |" in brief.answer
    assert "流动比率 2.15（报告期 2025-12-31）" in brief.answer


def test_composite_v2_sections_absent_when_history_is_missing() -> None:
    # Only the Q1 interim row exists — no annual path, so the v2 narrative
    # sections must disappear instead of rendering empty shells.
    service = _rich_service(history_rows=_PARTIAL_HISTORY_ROWS)

    brief = service.handle_question(question="综合分析士兰微")

    assert "**五年关键指标**" not in brief.answer
    assert "**盈利周期定位**" not in brief.answer
    for word in ("买入", "卖出", "推荐", "止盈", "止损", "仓位"):
        assert word not in brief.answer


def test_composite_skeleton_mode_keeps_data_sections_without_researchers() -> None:
    # The dispatch flow renders each researcher through its own bot, so the
    # final card reuses the composite without the researcher sections.
    service = _rich_service(history_rows=_SILAN_ANNUAL_ROWS)

    skeleton = service.compose_company_research_summary(
        "600460.SH", "士兰微", AS_OF, include_researchers=False,
    )

    for section in ("**财报研究员**", "**估值研究员**", "**风险研究员**"):
        assert section not in skeleton.answer
    for section in (
        "**关键数字**", "**五年关键指标**", "**盈利周期定位**",
        "**情景推演**", "**前瞻验证点**", "**数据边界**",
    ):
        assert section in skeleton.answer
    assert "supervisor-composite-v2" in skeleton.answer

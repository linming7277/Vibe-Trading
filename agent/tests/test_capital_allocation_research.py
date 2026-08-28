from __future__ import annotations

from copy import deepcopy
import json

from src.capital_allocation_research.service import CapitalAllocationResearchService


def financial_row(year, capex, revenue, profit, ocf, cash, liabilities, debt_ratio, interest_ratio, shares):
    report_date = f"{year}-12-31"
    source = [{"source_type": "TDX_PROFESSIONAL_FINANCE", "source_record_id": f"605108.SH:{report_date}:20{int(year[-2:]) + 1}-04-20", "report_date": report_date, "announcement_date": f"{int(year) + 1}-04-20", "pit_status": "STRICT"}]
    return {
        "year": year, "report_date": report_date, "announcement_date": f"{int(year) + 1}-04-20",
        "operating_cash_flow": ocf, "capex": capex, "cash_and_equivalents": cash,
        "revenue": revenue, "net_profit": profit, "liabilities": liabilities,
        "debt_ratio": debt_ratio, "interest_bearing_debt_ratio": interest_ratio,
        "total_shares": shares, "source_refs": source,
        "capex_to_ocf": {"value": capex / ocf * 100, "status": "READY"},
        "capex_to_revenue": {"value": capex / revenue * 100, "status": "READY"},
    }


def facts_fixture():
    timeline = [
        financial_row("2020", 10, 100, 10, 15, 20, 80, 40, 20, 100),
        financial_row("2021", 20, 115, 14, 20, 28, 82, 38, 19, 105),
        financial_row("2022", 30, 140, 20, 28, 38, 80, 35, 17, 111),
        financial_row("2023", 40, 180, 28, 40, 52, 76, 31, 14, 118),
        financial_row("2024", 50, 210, 33, 48, 62, 74, 29, 12, 125),
    ]
    dividends = []
    for year, shares in (("2021", 105), ("2022", 111), ("2023", 118), ("2024", 125)):
        event_date = f"{int(year) + 1}-05-20"
        dividends.append({
            "event_date": event_date, "normalization_status": "READY", "cash_dividend_per_ten_shares": 2.0,
            "cash_dividend_total": shares * .2, "linked_annual_report_date": f"{year}-12-31",
            "dividend_to_net_profit": {"value": 20.0, "status": "READY"},
            "dividend_to_ocf": {"value": 15.0, "status": "READY"},
            "source_refs": [{"source_type": "TDX_SECURITY_DETAILS_DIVID_FACTORS", "source_record_id": f"605108.SH:dividend:{year}", "event_date": event_date, "pit_status": "PIT_LIMITED"}],
        })
    return {
        "company": {"market": "CN", "stock_code": "605108.SH"}, "as_of": "2026-08-27",
        "formula_version": "capital-allocation-facts-v1.0.0", "pit_status": "PIT_LIMITED",
        "financial_timeline": {"status": "READY", "pit_status": "STRICT", "items": timeline},
        "dividend_history": {"status": "PARTIAL", "pit_status": "PIT_LIMITED", "events": dividends},
        "share_capital_history": {"status": "PARTIAL", "pit_status": "PIT_LIMITED", "events": [{"event_date": "2024-01-01", "change_reason": "UNKNOWN"}]},
        "data_gaps": [{"item": "buyback", "status": "MISSING", "reason": "no buyback"}],
    }


class FakeFactService:
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def get_history(self, market, stock_code, as_of=None):
        self.calls.append((market, stock_code, as_of))
        return deepcopy(self.facts)


def test_reinvestment_uses_only_lagged_outcomes_and_marks_latest_unobservable():
    service = CapitalAllocationResearchService(fact_service=FakeFactService(facts_fixture()))

    result = service.get_research("CN", "605108.SH", "2026-08-27")

    item = result["dimensions"]["reinvestment"]
    assert item["status"] == "SUPPORTED"
    assert item["direction"] == "POSITIVE"
    assert item["signal"] == "EFFECTIVE_SIGNAL"
    assert item["observations"][-1]["signal"] == "OUTCOME_NOT_YET_OBSERVABLE"
    for observation in item["observations"]:
        assert all(outcome["outcome_report_date"] > observation["capex_report_date"] for outcome in observation["outcomes"])


def test_dividend_continuity_and_pit_limited_source_are_preserved():
    service = CapitalAllocationResearchService(fact_service=FakeFactService(facts_fixture()))

    result = service.get_research("CN", "605108.SH", "2026-08-27")

    item = result["dimensions"]["dividend"]
    assert item["status"] == "SUPPORTED"
    assert item["continuity"]["longest_consecutive_years"] == 4
    assert item["matching"]["matched_event_count"] == 4
    assert item["pit_status"] == "PIT_LIMITED"
    assert all(ref["pit_status"] == "PIT_LIMITED" for ref in item["fact_refs"])


def test_debt_equity_cash_context_and_missing_boundaries():
    service = CapitalAllocationResearchService(fact_service=FakeFactService(facts_fixture()))

    result = service.get_research("CN", "605108.SH", "2026-08-27")

    assert result["dimensions"]["debt_management"]["status"] == "SUPPORTED"
    assert result["dimensions"]["equity_dilution"]["status"] == "SUPPORTED"
    assert result["dimensions"]["equity_dilution"]["trend"]["change_reason"] == "UNKNOWN"
    assert result["dimensions"]["cash_management"]["status"] == "SUPPORTED"
    assert result["dimensions"]["buyback"]["status"] == "UNKNOWN"
    assert result["dimensions"]["buyback"]["data_gaps"] == ["BUYBACK_DATA_MISSING"]
    assert result["dimensions"]["m_and_a"]["status"] == "UNKNOWN"
    assert result["dimensions"]["m_and_a"]["data_gaps"] == ["M_AND_A_DATA_MISSING"]


def test_research_is_read_only_and_never_makes_management_or_trading_judgement():
    facts = facts_fixture()
    before = deepcopy(facts)
    source = FakeFactService(facts)
    service = CapitalAllocationResearchService(fact_service=source)

    result = service.get_research("CN", "605108.SH", "2026-08-27")

    assert facts == before
    assert source.calls == [("CN", "605108.SH", "2026-08-27")]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "管理层优秀" not in rendered
    assert "管理层较差" not in rendered
    assert "买入" not in rendered
    assert "卖出" not in rendered
    assert "总分" not in rendered
    assert result["read_only"] is True

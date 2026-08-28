from __future__ import annotations

import json

from src.capital_allocation_facts.service import CapitalAllocationFactService
from src.tdx_data.store import TdxDataStore


class FakeFinancialHistory:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, symbol, *, as_of=None, period_type=None):
        self.calls.append((symbol, as_of, period_type))
        rows = [row for row in self.rows if not as_of or row["announcement_date"] <= as_of]
        if period_type:
            rows = [row for row in rows if row["period_type"] == period_type]
        return {"symbol": symbol, "items": rows}


def annual(report_date, announcement_date, **values):
    return {
        "report_date": report_date,
        "announcement_date": announcement_date,
        "data_as_of": announcement_date,
        "period_type": "annual",
        "source": "TongDaXin professional finance / TQ",
        "raw_version": "fixture-v1",
        "flow_basis": "single_period",
        "operating_cash_flow": 100.0,
        "capex": 40.0,
        "cash_and_equivalents": 80.0,
        "revenue": 200.0,
        "net_profit": 50.0,
        "assets": 300.0,
        "liabilities": 120.0,
        "current_liabilities": 70.0,
        "non_current_liabilities": 50.0,
        "debt_ratio": 40.0,
        "interest_bearing_debt_ratio": 20.0,
        "total_shares": 100.0,
        "roe": 10.0,
        **values,
    }


def build_service(tmp_path):
    store = TdxDataStore(tmp_path / "tdx.sqlite")
    financials = FakeFinancialHistory([
        annual("2023-12-31", "2024-04-20", operating_cash_flow=80.0, capex=20.0, cash_and_equivalents=60.0, total_shares=90.0),
        annual("2024-12-31", "2025-04-20"),
        annual("2025-12-31", "2026-04-20", operating_cash_flow=120.0, capex=60.0, cash_and_equivalents=100.0, total_shares=110.0),
    ])
    store.upsert_records("security_details", [{
        "key": "605108.SH",
        "payload": {
            "updated_at": "2026-08-24T08:00:00+08:00",
            "dividends": [
                {"Date": "20250520", "Type": "1", "Bonus": 10, "ShareBonus": 0, "Allotment": 0, "AllotPrice": 0},
                {"Date": "20250620", "Type": "9", "Bonus": 3},
            ],
            "capital": [
                {"Date": "20240101", "Zgb": 90, "Ltgb": 70},
                {"Date": "20250101", "Zgb": 100, "Ltgb": 80},
                {"Date": "20250501", "Zgb": 110, "Ltgb": 90},
            ],
        },
    }])
    return CapitalAllocationFactService(tdx_store=store, financial_history=financials, action_db_path=tmp_path / "actions.sqlite"), store, financials


def test_financial_timeline_is_annual_and_point_in_time(tmp_path):
    service, _, financials = build_service(tmp_path)

    result = service.get_history("CN", "605108.SH", "2025-12-31")

    assert financials.calls == [("605108.SH", "2025-12-31", "annual")]
    items = result["financial_timeline"]["items"]
    assert [item["report_date"] for item in items] == ["2023-12-31", "2024-12-31"]
    assert items[-1]["capex_to_ocf"] == {"value": 40.0, "status": "READY"}
    assert items[-1]["cash_change"] == {"value": 20.0, "status": "READY"}
    assert items[-1]["share_count_change"] == {"value": 11.1111, "status": "READY"}
    assert items[-1]["source_refs"][0]["pit_status"] == "STRICT"
    assert result["financial_timeline"]["pit_status"] == "STRICT"


def test_dividend_only_normalizes_confirmed_cash_field_and_marks_pit_limited(tmp_path):
    service, _, _ = build_service(tmp_path)

    result = service.get_history("CN", "605108.SH", "2025-12-31")

    dividends = result["dividend_history"]
    assert len(dividends["events"]) == 1
    event = dividends["events"][0]
    assert event["cash_dividend_per_ten_shares"] == 10.0
    assert event["cash_dividend_per_share"] == 1.0
    assert event["cash_dividend_total"] == 110.0
    assert event["linked_annual_report_date"] == "2024-12-31"
    assert event["dividend_to_net_profit"] == {"value": 220.0, "status": "READY"}
    assert event["pit_status"] == "PIT_LIMITED"
    assert len(dividends["raw_unknown_fields"]) == 1
    assert dividends["raw_unknown_fields"][0]["status"] == "UNKNOWN_RAW_FIELD"


def test_share_change_keeps_reason_unknown_and_does_not_accuse_dilution(tmp_path):
    service, _, _ = build_service(tmp_path)

    result = service.get_history("CN", "605108.SH", "2025-12-31")

    events = result["share_capital_history"]["events"]
    assert len(events) == 2
    assert events[-1]["total_shares_before"] == 100.0
    assert events[-1]["total_shares_after"] == 110.0
    assert events[-1]["change_reason"] == "UNKNOWN"
    assert events[-1]["change_pct"] == {"value": 10.0, "status": "READY"}
    assert events[-1]["pit_status"] == "PIT_LIMITED"
    rendered = json.dumps(result, ensure_ascii=False)
    assert "管理层优秀" not in rendered
    assert "管理层较差" not in rendered
    assert "公司进行了增发" not in rendered


def test_fact_service_is_read_only_and_exposes_unexplained_items(tmp_path):
    service, store, _ = build_service(tmp_path)
    before = store.list_records("security_details")["items"]

    result = service.get_history("CN", "605108.SH", "2025-12-31")

    after = store.list_records("security_details")["items"]
    assert before == after
    gaps = {item["item"]: item["status"] for item in result["data_gaps"]}
    assert gaps["investment_cash_flow"] == "MISSING"
    assert gaps["financing_cash_flow"] == "MISSING"
    assert gaps["buyback"] == "MISSING"
    assert gaps["m_and_a"] == "MISSING"
    assert gaps["equity_financing_reason"] == "RAW_NOT_STRUCTURED"
    assert result["allocation_completeness"] == "PARTIAL"
    assert all(item["allocation_completeness"] == "PARTIAL" for item in result["cash_allocation_snapshots"])

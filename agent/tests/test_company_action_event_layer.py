from __future__ import annotations

from copy import deepcopy
import json

from src.company_actions.service import CompanyActionEventService
from src.company_actions.store import CompanyActionEventStore
from src.tdx_data.store import TdxDataStore


class FakeDisclosureStore:
    def list_documents(self, stock_code):
        return [{"id": "periodic-document"}]


def seed_tdx(tmp_path):
    store = TdxDataStore(tmp_path / "tdx.db")
    store.upsert_records("security_details", [{
        "key": "605108.SH",
        "payload": {
            "updated_at": "2026-08-24T08:00:00+08:00",
            "dividends": [
                {"Date": "20240120", "Type": "1", "Bonus": 5, "ShareBonus": 2, "Allotment": 0, "AllotPrice": 0},
                {"Date": "20250520", "Type": "1", "Bonus": 3, "ShareBonus": 0, "Allotment": 3, "AllotPrice": 6},
                {"Date": "20250620", "Type": "9", "Bonus": 1},
            ],
            "capital": [
                {"Date": "20240101", "Zgb": 100},
                {"Date": "20240201", "Zgb": 120},
                {"Date": "20250601", "Zgb": 156},
            ],
        },
    }])
    return store


def service(tmp_path):
    return CompanyActionEventService(
        tdx_store=seed_tdx(tmp_path), db_path=tmp_path / "research.db", disclosure_store=FakeDisclosureStore(),
    )


def test_prepare_projects_confirmed_dividend_bonus_rights_and_share_changes(tmp_path):
    result = service(tmp_path).prepare_from_cached_details("CN", "605108.SH")

    event_types = [item["event_type"] for item in result["events"]]
    assert result["status"] == "READY"
    assert "CASH_DIVIDEND" in event_types
    assert "BONUS_SHARE" in event_types
    assert "RIGHTS_ISSUE" in event_types
    share_changes = [item for item in result["events"] if item["event_type"] == "SHARE_CAPITAL_CHANGE"]
    assert len(share_changes) == 2
    assert share_changes[0]["reason"] == "BONUS_SHARE"
    assert share_changes[0]["reason_source_event_id"]
    assert share_changes[1]["reason"] == "RIGHTS_ISSUE"
    assert all(item["pit_status"] == "PIT_LIMITED" for item in result["events"])
    assert result["unknown_raw_fields"][0]["status"] == "UNKNOWN_RAW_FIELD"
    assert result["capabilities"]["SHARE_REPURCHASE"]["status"] == "MISSING"
    assert result["capabilities"]["PRIVATE_PLACEMENT"]["status"] == "MISSING"
    assert result["capabilities"]["CONVERTIBLE_BOND"]["status"] == "MISSING"
    assert result["capabilities"]["EQUITY_INCENTIVE"]["status"] == "RAW_NOT_STRUCTURED"


def test_read_only_get_does_not_create_missing_event_database(tmp_path):
    db_path = tmp_path / "missing-research.db"
    action_service = CompanyActionEventService(tdx_store=seed_tdx(tmp_path), db_path=db_path, disclosure_store=FakeDisclosureStore())

    result = action_service.get_events("CN", "605108.SH")

    assert result["read_only"] is True
    assert result["events"] == []
    assert not db_path.exists()


def test_prepare_is_idempotent_and_get_filters_future_limited_events(tmp_path):
    action_service = service(tmp_path)
    first = action_service.prepare_from_cached_details("CN", "605108.SH")
    second = action_service.prepare_from_cached_details("CN", "605108.SH")

    assert first["created"] == 6
    assert second["created"] == 0
    current = action_service.get_events("CN", "605108.SH", as_of="2024-12-31")
    assert current["read_only"] is True
    assert current["event_count"] == 3
    assert all(item["event_date"] <= "2024-12-31" for item in current["events"])
    assert all(item["pit_status"] == "PIT_LIMITED" for item in current["events"])


def test_nearby_bonus_does_not_explain_an_unmatched_share_change(tmp_path):
    action_service = service(tmp_path)
    detail = action_service.tdx_store.get_record("security_details", "605108.SH")
    detail["payload"]["capital"] = [
        {"Date": "20240101", "Zgb": 100},
        {"Date": "20240201", "Zgb": 101},  # 1%, not the disclosed 20% bonus.
    ]
    action_service.tdx_store.upsert_records("security_details", [detail])

    result = action_service.prepare_from_cached_details("CN", "605108.SH")
    share_change = next(item for item in result["events"] if item["event_type"] == "SHARE_CAPITAL_CHANGE")

    assert share_change["reason"] == "UNKNOWN"
    assert share_change["reason_source_event_id"] is None


def manual_event(canonical, source_id, stage="IMPLEMENTED"):
    return {
        "canonical_key": canonical, "fingerprint": f"{canonical}:{source_id}", "market": "CN", "stock_code": "605108.SH",
        "event_type": "CASH_DIVIDEND", "event_status": "DISCLOSED_EVENT", "event_stage": stage, "parent_event_id": None,
        "announcement_date": "2024-03-01", "event_date": "2024-05-01", "effective_date": "2024-05-01", "research_visible_from": "2024-03-01",
        "source_type": "CNINFO", "source_id": source_id, "source_url": "https://example.test", "source_hash": source_id,
        "title": "现金分红", "summary": "测试事件", "cash_amount": 1.0, "share_count": None, "share_ratio": None, "price": None,
        "currency": "CNY", "shares_before": None, "shares_after": None, "purpose": None, "reason": None, "reason_source_event_id": None,
        "pit_status": "STRICT", "confidence": "HIGH", "data_quality": "READY", "extractor_version": "test-v1", "payload": {},
        "source_ref": {"source_type": "CNINFO", "source_id": source_id, "source_url": "https://example.test", "source_hash": source_id, "announcement_date": "2024-03-01", "event_date": "2024-05-01", "pit_status": "STRICT", "source_payload": {}},
    }


def test_canonical_event_merges_multiple_sources_but_keeps_event_stages(tmp_path):
    store = CompanyActionEventStore(tmp_path / "research.db")
    try:
        first, created_first = store.save_event(manual_event("same-economic-event", "tdx-source"))
        merged, created_second = store.save_event(manual_event("same-economic-event", "cninfo-source"))
        proposed, created_proposed = store.save_event(manual_event("same-event-proposed", "proposal", stage="PROPOSED"))
        completed, created_completed = store.save_event(manual_event("same-event-completed", "completed", stage="COMPLETED"))
        assert created_first is True
        assert created_second is False
        assert len(merged["source_refs"]) == 2
        assert created_proposed is True and created_completed is True
        events = store.list_events("CN", "605108.SH", as_of="2024-12-31")
        assert {item["event_stage"] for item in events} >= {"IMPLEMENTED", "PROPOSED", "COMPLETED"}
        assert first["id"] == merged["id"]
    finally:
        store.close()


def test_layer_does_not_mutate_management_thesis_risk_or_trading_state(tmp_path):
    action_service = service(tmp_path)
    raw_before = deepcopy(action_service.tdx_store.get_record("security_details", "605108.SH"))
    result = action_service.prepare_from_cached_details("CN", "605108.SH")
    raw_after = action_service.tdx_store.get_record("security_details", "605108.SH")

    assert raw_before == raw_after
    rendered = json.dumps(result, ensure_ascii=False)
    assert "管理层优秀" not in rendered
    assert "资本配置总分" not in rendered
    assert "买入" not in rendered and "卖出" not in rendered

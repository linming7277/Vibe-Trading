from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import risk_research_preparation_routes
from src.api.risk_research_preparation_routes import register_risk_research_preparation_routes
from src.risk_research_preparation.service import RiskResearchPreparationService
from src.risk_research_preparation.store import RiskResearchPreparationRepository


def _member(code: str = "605108.SH", *, as_of: str = "2026-08-25") -> dict:
    return {
        "market": "CN", "stock_code": code, "company_name": "同庆楼", "industry_name": "餐饮",
        "source_as_of": as_of, "pool_status": "ACTIVE",
    }


class _Pool:
    def __init__(self, items: list[dict]): self.items = items
    def active(self, _market: str): return list(self.items)


class _Financial:
    def get_saved_resolved_analysis(self, _code: str, *, as_of: str):
        return {"as_of": as_of, "feature_status": "READY", "forecast_status": "READY"}


class _History:
    def query(self, _code: str, *, as_of: str):
        return {"items": [{"announcement_date": as_of, "report_date": "2026-03-31"}]}


class _Profiles:
    def profile(self, code: str):
        return {
            "stock_code": code, "updated_at": "2026-08-25T16:00:00+08:00", "data_status": "REAL",
            "main_business": "餐饮服务", "main_products": "餐饮", "business_scope": "餐饮", "company_description": "餐饮公司",
        }


class _Business:
    def __init__(self, existing: dict | None = None, result: dict | None = None):
        self.existing = existing or {}
        self.result = result or {"id": "business-new", "data_as_of": "2026-08-25", "analysis_status": "COMPLETED"}
        self.calls = []
    def get_saved_research(self, _code: str, *, as_of: str): return self.existing
    def analyze(self, code: str, *, as_of: str | None = None):
        self.calls.append((code, as_of))
        return self.result


class _Disclosure:
    def __init__(self, initial: dict, synced: dict | None = None):
        self.initial, self.synced, self.sync_calls = initial, synced or initial, []
    def get_materials(self, _code: str, *, as_of: str): return self.initial
    def sync_periodic_reports(self, code: str, *, as_of: str, max_documents_per_kind: int):
        self.sync_calls.append((code, as_of, max_documents_per_kind))
        return self.synced


class _Thesis:
    def __init__(self, thesis: dict | None = None): self.thesis = thesis
    def get_current_thesis(self, _market: str, _code: str): return self.thesis


class _RiskSnapshot:
    def __init__(self): self.calls = []
    def refresh_company_snapshot(self, *, market: str, stock_code: str, source_as_of: str):
        self.calls.append((market, stock_code, source_as_of))
        return {"status": "READY", "snapshot": {"overall_risk": "MEDIUM"}}


def _documents(*kinds: str) -> dict:
    docs = [
        {"announcement_id": f"{kind}-1", "report_kind": kind, "announcement_date": "2026-08-20", "extraction_status": "READY"}
        for kind in kinds
    ]
    return {"documents": docs, "materials": []}


def _service(tmp_path, *, business: _Business | None = None, disclosure: _Disclosure | None = None,
             thesis: _Thesis | None = None, items: list[dict] | None = None, risk_snapshot: _RiskSnapshot | None = None) -> RiskResearchPreparationService:
    return RiskResearchPreparationService(
        repository=RiskResearchPreparationRepository(tmp_path / "preparation.db"), pool_repository=_Pool(items or [_member()]),
        financial_service=_Financial(), financial_history=_History(), business_profiles=_Profiles(),
        business_research=business or _Business(), disclosure_service=disclosure or _Disclosure(_documents(*("ANNUAL", "SEMIANNUAL", "Q1", "Q3"))),
        thesis_repository=thesis or _Thesis(), risk_snapshot_service=risk_snapshot or _RiskSnapshot(),
    )


def test_prepares_only_active_pool_reuses_completed_business_and_preserves_missing_thesis(tmp_path):
    business = _Business(existing={"id": "business-old", "data_as_of": "2026-08-25", "analysis_status": "COMPLETED"})
    disclosure = _Disclosure(_documents("ANNUAL", "SEMIANNUAL", "Q1", "Q3"))
    service = _service(tmp_path, business=business, disclosure=disclosure)
    result = service.prepare_current_active_low_value_pool(source_as_of="2026-08-25")
    row = result["items"][0]
    assert result["active_low_value_count"] == result["processed"] == 1
    assert row["financial_status"] == row["business_profile_status"] == row["business_research_status"] == row["disclosure_status"] == "READY"
    assert row["thesis_status"] == "MISSING" and row["overall_status"] == "READY"
    assert row["metadata"]["business_research"]["action"] == "REUSED"
    assert business.calls == [] and disclosure.sync_calls == []
    assert any(item["capability"] == "thesis" for item in row["missing_capabilities"])


def test_prepares_business_and_disclosure_with_pit_and_ppp_not_applicable(tmp_path):
    business = _Business()
    disclosure = _Disclosure(_documents("ANNUAL"), _documents("ANNUAL", "SEMIANNUAL", "Q1", "Q3"))
    service = _service(tmp_path, business=business, disclosure=disclosure)
    row = service.prepare_company(_member(), research_as_of="2026-08-25")
    assert business.calls == [("605108.SH", "2026-08-25")]
    assert disclosure.sync_calls == [("605108.SH", "2026-08-25", 1)]
    assert row["business_research_status"] == row["disclosure_status"] == "READY"
    coverage = row["metadata"]["disclosure"]["material_coverage"]
    assert coverage["PPP_COLLECTION"] == "NOT_APPLICABLE"
    assert coverage["CUSTOMER_CONCENTRATION"] == "NOT_FOUND_IN_COLLECTED_DOCUMENTS"


def test_failure_isolated_and_does_not_mutate_pool_or_thesis(tmp_path):
    class FailingDisclosure(_Disclosure):
        def sync_periodic_reports(self, *args, **kwargs): raise RuntimeError("cninfo down")
    members = [_member("605108.SH"), _member("000544.SZ")]
    service = _service(tmp_path, disclosure=FailingDisclosure({"documents": [], "materials": []}), items=members)
    result = service.prepare_current_active_low_value_pool(source_as_of="2026-08-25")
    assert result["processed"] == 2
    assert result["FAILED"] == 2
    assert {row["stock_code"] for row in result["items"]} == {"605108.SH", "000544.SZ"}
    assert all(row["disclosure_status"] == "FAILED" for row in result["items"])


def test_readonly_api_does_not_prepare(tmp_path, monkeypatch):
    service = _service(tmp_path)
    stored = service.repository.upsert({
        "market": "CN", "stock_code": "605108.SH", "research_as_of": "2026-08-25", "company_name": "同庆楼",
        "financial_status": "READY", "business_profile_status": "READY", "business_research_status": "READY",
        "disclosure_status": "PARTIAL", "thesis_status": "MISSING", "overall_status": "PARTIAL",
        "missing_capabilities": [], "metadata": {},
    })
    assert stored["stock_code"] == "605108.SH"
    monkeypatch.setattr(risk_research_preparation_routes, "get_risk_research_preparation_service", lambda: service)
    app = FastAPI()
    register_risk_research_preparation_routes(app, lambda: True)
    response = TestClient(app).get("/api/value/risk-research-preparation?research_as_of=2026-08-25")
    assert response.status_code == 200
    assert response.json()["items"][0]["stock_code"] == "605108.SH"


def test_existing_current_thesis_is_hard_stop_for_auto_provisional(tmp_path):
    existing = {"thesis_id": "human-v1", "authority_status": "HUMAN_CONFIRMED"}
    service = _service(tmp_path, thesis=_Thesis(existing))
    row = service.prepare_company(_member(), research_as_of="2026-08-25")
    assert row["provisional_thesis_status"] == "SKIPPED_EXISTING_THESIS"
    assert row["provisional_thesis_id"] == "human-v1"
    assert row["validation_status"] == "SKIPPED_EXISTING_THESIS"


def test_preparation_refreshes_the_same_company_risk_snapshot_after_sources_are_ready(tmp_path):
    snapshots = _RiskSnapshot()
    service = _service(tmp_path, risk_snapshot=snapshots)
    row = service.prepare_company(_member(), research_as_of="2026-08-25")
    assert snapshots.calls == [("CN", "605108.SH", "2026-08-25")]
    assert row["metadata"]["risk_snapshot_refresh"]["status"] == "READY"

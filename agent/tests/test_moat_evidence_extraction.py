from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import moat_evidence_routes
from src.api.moat_evidence_routes import register_moat_evidence_routes
from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.moat_evidence.service import MoatEvidenceExtractionService
from src.moat_evidence.store import MoatEvidenceStore


class ProfileStub:
    def profile(self, stock_code: str):
        return {"stock_code": stock_code, "updated_at": "2026-08-20T00:00:00+00:00", "main_business": "餐饮服务"}


def _leader_profile(_market: str, symbol: str, as_of: str | None):
    target = as_of or "2026-08-27"
    return {
        "company": {"stock_name": "同庆楼"}, "research_as_of": target,
        "leader_position": {"status": "READY", "run_id": "l3-run", "level3": {"name": "餐饮"}},
        "source_traceability": {"l3_run_id": "l3-run", "l3_run_as_of": target}, "formula_version": "leader-quality-profile-v1.0.0",
        "peer_advantage_categories": [{"dimension": "SCALE", "label": "规模", "status": "STRONG", "metrics": ["revenue"]}],
        "pricing_power_proxy": {"status": "MODERATE_PROXY", "peer_margin_percentile": 75},
        "moat_data_gaps": ["无市场份额数据", "无品牌强度数据", "无门店同店数据", "无专利质量数据"],
    }


def _document(store: DisclosureMaterialStore, root: Path, *, announcement: str, report_period: str, announcement_id: str, text: str):
    path = root / f"{announcement_id}.txt"
    path.write_text(text, encoding="utf-8")
    return store.save_document({
        "stock_code": "605108", "company_name": "同庆楼", "org_id": "gssh", "announcement_id": announcement_id,
        "report_kind": "ANNUAL", "report_period": report_period, "announcement_date": announcement,
        "title": f"{report_period[:4]}年年度报告", "source_url": f"https://example.test/{announcement_id}.pdf", "pdf_path": None,
        "pdf_sha256": "", "text_path": str(path), "text_sha256": f"hash-{announcement_id}", "page_count": 2,
        "extraction_status": "READY", "extraction_error": "",
    })


def _service(tmp_path: Path):
    evidence = MoatEvidenceStore(tmp_path / "research.db")
    disclosure = DisclosureMaterialStore(tmp_path / "research.db")
    business = BusinessResearchStore(tmp_path / "research.db")
    _document(disclosure, tmp_path, announcement="2026-04-22", report_period="2025-12-31", announcement_id="a1", text="公司拥有门店100家，形成品牌优势。公司认为品牌具有较强影响力。\f报告期内渠道收缩，市场竞争加剧。技术研发持续推进。")
    snapshot, _ = business.save({"stock_code": "605108.SH", "company_name": "同庆楼", "data_as_of": "2026-08-20", "source_hash": "business-hash", "sources": []}, configured=True, provider="", model="")
    business.update_analysis(snapshot["id"], status="COMPLETED", provider="", model="", analysis={"claims": [
        {"type": "FACT", "text": "公司品牌覆盖持续扩大。", "topic": "BUSINESS_MODEL", "confidence": "HIGH", "citations": [{"source_id": "annual:a1"}]},
        {"type": "UNKNOWN", "text": "无法判断客户留存。", "topic": "BUSINESS_MODEL"},
    ]})
    return MoatEvidenceExtractionService(evidence_store=evidence, disclosure_store=disclosure, business_store=business, business_profiles=ProfileStub(), leader_profile_loader=_leader_profile), evidence, disclosure, business


def test_extracts_typed_source_traced_evidence_with_industry_guard_and_pit(tmp_path):
    service, evidence, disclosure, business = _service(tmp_path)
    try:
        result = service.extract("CN", "605108", as_of="2026-08-27")
        types = {item["evidence_type"] for item in result["evidence"]}
        assert {"QUANTIFIED_FACT", "MANAGEMENT_CLAIM", "COUNTER_EVIDENCE", "INFERENCE", "UNKNOWN"} <= types
        quantified = next(item for item in result["evidence"] if item["evidence_type"] == "QUANTIFIED_FACT")
        assert quantified["value"]["value"] == 100
        assert quantified["page_number"] == 1 and quantified["report_date"] == "2025-12-31"
        assert not any(item["moat_dimension"] == "TECHNOLOGY" and item["source_type"] == "CNINFO_PERIODIC_REPORT" for item in result["evidence"])
        assert result["source_status"]["cninfo"] == "READY"
        repeated = service.extract("CN", "605108", as_of="2026-08-27")
        assert repeated["created"] == 0 and repeated["duplicates"] > 0
        active = service.get_evidence("CN", "605108", active=True)
        assert active["total"] >= len(result["evidence"])
        assert "WIDE_MOAT" not in str(active)
    finally:
        service.close(); evidence.close(); disclosure.close(); business.close()


def test_future_document_is_rejected_and_newer_same_fact_supersedes(tmp_path):
    service, evidence, disclosure, business = _service(tmp_path)
    try:
        future_document = _document(disclosure, tmp_path, announcement="2027-04-22", report_period="2026-12-31", announcement_id="a2", text="截至报告期末，公司拥有门店120家，门店网络持续覆盖重点区域。")
        before = service.extract("CN", "605108", as_of="2026-08-27")
        assert all(item.get("source_document_id") != future_document["id"] for item in before["evidence"])
        service.extract("CN", "605108", as_of="2027-08-27")
        current = service.get_evidence("CN", "605108", active=True, dimension="CHANNEL", evidence_type="QUANTIFIED_FACT")
        previous = service.get_evidence("CN", "605108", active=False, dimension="CHANNEL", evidence_type="QUANTIFIED_FACT")
        assert current["total"] == 1
        assert previous["total"] == 1
        assert current["items"][0]["value"]["value"] == 120
    finally:
        service.close(); evidence.close(); disclosure.close(); business.close()


def test_not_collected_cninfo_is_explicit_and_read_only_api(monkeypatch, tmp_path):
    service, evidence, disclosure, business = _service(tmp_path)
    try:
        # A different company has no CNINFO documents.  Its evidence may still
        # contain L3 data gaps/inferences, but never fabricated report facts.
        empty = service.extract("CN", "002371", as_of="2026-08-27")
        assert empty["source_status"]["cninfo"] == "NOT_COLLECTED"
        assert not any(item["source_type"].startswith("CNINFO") for item in empty["evidence"])

        class FakeService:
            def get_evidence(self, market, stock_code, **kwargs): return {"market": market, "stock_code": stock_code, **kwargs}
            def extract(self, market, stock_code, **kwargs): return {"market": market, "stock_code": stock_code, "created": 0, **kwargs}
        monkeypatch.setattr(moat_evidence_routes, "get_moat_evidence_extraction_service", lambda: FakeService())
        app = FastAPI(); register_moat_evidence_routes(app, require_auth=lambda: True)
        response = TestClient(app).get("/api/value/companies/605108/moat-evidence?market=CN&dimension=BRAND&active=true")
        assert response.status_code == 200 and response.json()["dimension"] == "BRAND"
    finally:
        service.close(); evidence.close(); disclosure.close(); business.close()

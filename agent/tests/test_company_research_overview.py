from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_research_overview_routes
from src.api.company_research_overview_routes import register_company_research_overview_routes
from src.business_research.store import BusinessResearchStore
from src.company_research.overview_service import CompanyResearchOverviewService
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService
from src.financial_analysis.store import FinancialAnalysisStore


def _financial(store: FinancialAnalysisStore, stock_code: str = "002371.SZ") -> dict:
    snapshot, _ = store.save_python_snapshot({
        "stock_code": stock_code, "stock_name": "测试公司", "as_of": "2026-08-21", "historical_cutoff": "2026-06-30",
        "financial_feature_version": "v1", "forecast_version": "v1", "feature_status": "READY", "forecast_status": "READY",
        "analysis_status": "NOT_RUN", "identity": {}, "history": [],
        "feature": {"latest_changes": [
            {"metric": "revenue", "change_percent": 12.5, "report_date": "2026-03-31"},
            {"metric": "net_profit", "change_percent": -8.0, "report_date": "2026-03-31"},
            {"metric": "operating_cash_flow", "change_percent": -15.0, "report_date": "2026-03-31"},
        ]},
        "forecast": {"scenarios": {}}, "data_gaps": [], "source_hash": f"financial-{stock_code}",
    })
    return store.update_agent_result(snapshot["id"], status="COMPLETED", provider="test", model="test", analysis={
        "executive_summary": "收入增长，但现金流需要继续观察。",
        "key_metrics_to_monitor": ["经营现金流", "净利润"],
        "claims": [{"type": "UNKNOWN", "text": "缺少客户结构资料。", "source_keys": [], "confidence": "LOW"}],
        "analysis_metadata": {},
    })


def _business(store: BusinessResearchStore, stock_code: str = "002371.SZ") -> dict:
    snapshot, _ = store.save({
        "stock_code": stock_code, "company_name": "测试公司", "data_as_of": "2026-08-21",
        "main_business": "发动机、动力系统和物流服务", "products": ["发动机", "动力系统", "物流服务"],
        "business_model": "UNKNOWN", "business_changes": ["UNKNOWN"], "source_hash": f"business-{stock_code}",
        "sources": {"BUSINESS_CURRENT_MAIN_BUSINESS": {
            "source_type": "TDX_BUSINESS_PROFILE", "source_id": f"fundamentals:{stock_code}", "data_as_of": "2026-08-21",
            "field": "main_business", "value": "发动机、动力系统和物流服务", "source_hash": "business-raw", "profile_role": "CURRENT",
        }}, "data_quality": {"status": "PARTIAL"}, "module_version": "v1",
    }, configured=True, provider="test", model="test")
    return store.update_analysis(snapshot["id"], status="COMPLETED", provider="test", model="test", analysis={
        "summary": "公司主要销售发动机、动力系统，并提供物流服务。",
        "claims": [
            {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "公司主要销售发动机、动力系统和物流服务。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
            {"type": "UNKNOWN", "topic": "BUSINESS_CHANGE", "text": "缺少前后两期可比较的经营资料，无法确认最近经营变化。", "source_keys": [], "confidence": "LOW"},
        ], "analysis_metadata": {"quality_status": "STRUCTURED"},
    })


def _thesis(service: CompanyThesisService, stock_code: str = "002371.SZ") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="增长待验证", core_thesis="主营业务增长可以持续。",
        status="FORMING", confidence="MEDIUM", invalid_conditions=[{"condition": "经营现金流持续弱于利润增长", "status": "ACTIVE"}],
        created_by="HUMAN", source_data_as_of="2026-08-21",
    )


def _services(tmp_path: Path):
    db_path = tmp_path / "research.db"
    financial, business = FinancialAnalysisStore(db_path), BusinessResearchStore(db_path)
    thesis, evidence, review = CompanyThesisService(db_path=db_path), CompanyThesisEvidenceService(db_path=db_path), CompanyThesisReviewService(db_path=db_path)
    overview = CompanyResearchOverviewService(
        financial_store=financial, business_store=business, thesis_repository=thesis.repository,
        evidence_repository=evidence.repository, review_repository=review.repository, db_path=db_path,
    )
    return db_path, financial, business, thesis, evidence, review, overview


def _close(financial, business, thesis, evidence, review, overview) -> None:
    overview.close(); review.close(); evidence.close(); thesis.close(); business.close(); financial.close()


def test_complete_overview_filters_evidence_builds_watch_items_and_is_read_only(tmp_path: Path) -> None:
    db_path, financial, business, thesis_service, evidence_service, review_service, overview = _services(tmp_path)
    try:
        _financial(financial); _business(business); thesis = _thesis(thesis_service)
        evidence_service.create_evidence(thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="SUPPORT", claim="收入保持增长。", summary="收入增长支持当前逻辑。", source_type="SYSTEM", source_id="fin-1", confidence="HIGH", created_by="SYSTEM", metadata={"resolved_citations": [{"status": "RESOLVED", "source_key": "FIN_REV"}]})
        evidence_service.create_evidence(thesis_id=thesis["thesis_id"], evidence_type="BUSINESS", effect="CHALLENGE", claim="经营现金流需要继续核验。", summary="现金没有跟上时，需要确认增长质量。", source_type="COMPANY_RESEARCH_SNAPSHOT", source_id="business-1", confidence="MEDIUM", created_by="AGENT_FINANCIAL", metadata={"research_domain": "BUSINESS", "resolved_citations": [{"status": "RESOLVED", "source_key": "BUSINESS_CURRENT_MAIN_BUSINESS"}]})
        evidence_service.create_evidence(thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL", claim="其他观察。", summary="暂不影响当前判断。", source_type="SYSTEM", source_id="neutral-1", confidence="LOW", created_by="SYSTEM")
        review = review_service.review_current_thesis("CN", "002371.SZ")["review"]
        review_service.repository.mark_stale_for_thesis(thesis["thesis_id"])
        with sqlite3.connect(db_path) as conn:
            before = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("company_theses", "company_thesis_evidence", "company_thesis_reviews", "company_thesis_history", "company_financial_analysis_snapshots", "company_business_research_snapshots"))
        result = overview.get_overview("CN", "002371.SZ")
        with sqlite3.connect(db_path) as conn:
            after = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("company_theses", "company_thesis_evidence", "company_thesis_reviews", "company_thesis_history", "company_financial_analysis_snapshots", "company_business_research_snapshots"))
        assert before == after
        assert result["company"]["stock_name"] == "测试公司"
        assert result["business_summary"]["main_business"] == "发动机、动力系统和物流服务"
        assert len(result["financial_summary"]["items"]) == 3
        assert result["supporting_evidence"][0]["effect"] == "SUPPORT"
        assert result["challenging_evidence"][0]["citations"][0]["status"] == "RESOLVED"
        assert result["neutral_evidence_count"] == 1
        assert result["review"]["review_id"] == review["review_id"] and result["review"]["is_stale"] is True
        assert result["data_status"]["review"] == "STALE"
        assert any(item["source"] in {"THESIS", "THESIS_INVALID_CONDITION", "FINANCIAL", "BUSINESS_UNKNOWN", "CHALLENGE_EVIDENCE"} for item in result["watch_items"])
        assert result["watch_items"]
    finally:
        _close(financial, business, thesis_service, evidence_service, review_service, overview)


def test_overview_without_thesis_review_business_or_financial(tmp_path: Path) -> None:
    _, financial, business, thesis_service, evidence_service, review_service, overview = _services(tmp_path)
    try:
        result = overview.get_overview("CN", "000338.SZ")
        assert result["thesis"] is None and result["review"] is None
        assert result["business_summary"]["status"] == "UNKNOWN"
        assert result["financial_summary"]["status"] == "UNKNOWN"
        assert result["data_status"] == {"financial": "UNKNOWN", "business": "UNKNOWN", "thesis": "NOT_CREATED", "review": "NOT_CREATED"}
    finally:
        _close(financial, business, thesis_service, evidence_service, review_service, overview)


def test_api_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, financial, business, thesis_service, evidence_service, review_service, overview = _services(tmp_path)
    app = FastAPI(); register_company_research_overview_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    monkeypatch.setattr(company_research_overview_routes, "get_company_research_overview_service", lambda: overview)
    try:
        _financial(financial); _business(business)
        response = client.get("/api/value/companies/002371.SZ/research-overview?market=CN")
        assert response.status_code == 200
        assert response.json()["company"]["stock_code"] == "002371.SZ"
    finally:
        _close(financial, business, thesis_service, evidence_service, review_service, overview)

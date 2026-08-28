from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import company_thesis_routes
from src.api.company_thesis_routes import register_company_thesis_routes
from src.business_research.store import BusinessResearchStore
from src.company_thesis.draft_service import CompanyThesisDraftService
from src.financial_analysis.store import FinancialAnalysisStore


def _unknown_moat(*_args):
    return {"research_as_of": "2026-08-25", "status": "UNKNOWN", "dimensions": [], "moat_data_gaps": ["竞争优势研究资料不足"], "formula_version": "test"}


def _seed_completed_research(db_path: Path, stock_code: str = "000544.SZ") -> None:
    financial = FinancialAnalysisStore(db_path)
    business = BusinessResearchStore(db_path)
    try:
        snapshot, _ = financial.save_python_snapshot({
            "stock_code": stock_code, "stock_name": "中原环保", "as_of": "2026-08-25",
            "historical_cutoff": "2026-08-25", "financial_feature_version": "test",
            "forecast_version": "test", "feature_status": "READY", "forecast_status": "READY",
            "analysis_status": "NOT_RUN", "identity": {}, "history": [], "feature": {}, "forecast": {},
            "data_gaps": [], "source_hash": "financial-source",
        })
        financial.update_agent_result(snapshot["id"], status="COMPLETED", provider="test", model="test", analysis={
            "claims": [
                {"type": "FACT", "text": "经营现金流保持为正。", "source_keys": ["FEATURE_CASH_FLOW"], "confidence": "HIGH"},
                {"type": "INFERENCE", "text": "盈利质量仍需结合应收账款变化持续核验。", "source_keys": ["FEATURE_RECEIVABLES"], "confidence": "MEDIUM"},
            ],
        })
        business_snapshot = {
            "stock_code": stock_code, "company_name": "中原环保", "data_as_of": "2026-08-25",
            "source_hash": "business-source",
            "profile": {
                "main_business": "污水处理与环保运营服务", "main_products": "污水处理服务",
                "business_scope": "环保运营", "company_description": "环保运营公司",
                "data_status": "PARTIAL", "updated_at": "2026-08-25",
            },
            "sources": {}, "data_quality": {"status": "READY"},
        }
        row, _ = business.save(business_snapshot, configured=True, provider="test", model="test")
        business.update_analysis(row["id"], status="COMPLETED", provider="test", model="test", analysis={
            "claims": [
                {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "公司主营污水处理与环保运营服务。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
                {"type": "INFERENCE", "topic": "BUSINESS_CHANGE", "text": "回款与项目运营情况需要持续跟踪。", "source_keys": ["DISCLOSURE_CURRENT_PPP_COLLECTION_SEMI"], "confidence": "MEDIUM"},
            ],
        })
    finally:
        financial.close()
        business.close()


def test_draft_is_idempotent_and_does_not_create_thesis(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _seed_completed_research(db_path)
    service = CompanyThesisDraftService(db_path=db_path, moat_research_loader=_unknown_moat)
    try:
        first = service.generate("CN", "000544.SZ")
        second = service.generate("CN", "000544.SZ")
        assert first["status"] == "CREATED"
        assert second["status"] == "EXISTING"
        assert first["draft"]["draft_status"] == "DRAFT"
        assert "买入" not in first["draft"]["core_thesis"]
        assert service.thesis_service.get_current_thesis("CN", "000544.SZ") is None
        assert len(first["draft"]["source_refs"]) >= 4
        assert first["draft"]["research_as_of"] == "2026-08-25"
        assert first["draft"]["workflow_status"] in {"DRAFT", "READY_FOR_REVIEW"}
        assert first["draft"]["core_drivers"]
        assert first["draft"]["competitive_advantages"][0]["type"] == "UNKNOWN"
        assert first["draft"]["key_metrics_to_monitor"]
    finally:
        service.close()


def test_only_explicit_confirmation_creates_initial_thesis(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    _seed_completed_research(db_path)
    service = CompanyThesisDraftService(db_path=db_path, moat_research_loader=_unknown_moat)
    try:
        draft = service.generate("CN", "000544.SZ")["draft"]
        confirmed = service.confirm(
            draft["draft_id"], title="人工确认后的逻辑", core_thesis="人工核对后的正式核心逻辑。",
            status="FORMING", confidence="MEDIUM", invalid_conditions=[],
        )
        assert confirmed["status"] == "APPROVED"
        assert confirmed["draft"]["draft_status"] == "CONFIRMED"
        assert confirmed["thesis"]["created_by"] == "HUMAN"
        assert service.thesis_service.get_current_thesis("CN", "000544.SZ")["title"] == "人工确认后的逻辑"
        assert service.generate("CN", "000544.SZ")["status"] == "THESIS_EXISTS"
    finally:
        service.close()


def test_draft_api_requires_confirm_before_thesis_creation(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_completed_research(db_path)
    service = CompanyThesisDraftService(db_path=db_path, moat_research_loader=_unknown_moat)
    monkeypatch.setattr(company_thesis_routes, "_draft_service", lambda: service)
    app = FastAPI()
    register_company_thesis_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        generated = client.post("/api/value/companies/000544.SZ/thesis/draft")
        assert generated.status_code == 200
        draft = generated.json()["draft"]
        assert service.thesis_service.get_current_thesis("CN", "000544.SZ") is None
        confirmed = client.post(
            f"/api/value/companies/000544.SZ/thesis/draft/{draft['draft_id']}/confirm",
            json={"title": draft["title"], "core_thesis": draft["core_thesis"], "status": "FORMING", "confidence": "LOW", "invalid_conditions": []},
        )
        assert confirmed.status_code == 201
        assert confirmed.json()["thesis"]["is_current"] is True
    finally:
        service.close()


def test_v1_aliases_keep_generate_explicit_and_approve_human_only(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "research.db"
    _seed_completed_research(db_path)
    service = CompanyThesisDraftService(db_path=db_path, moat_research_loader=_unknown_moat)
    monkeypatch.setattr(company_thesis_routes, "_draft_service", lambda: service)
    app = FastAPI()
    register_company_thesis_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        assert client.get("/api/value/companies/000544.SZ/thesis-draft").json()["status"] == "NOT_CREATED"
        generated = client.post("/api/value/companies/000544.SZ/thesis-draft/generate?research_as_of=2026-08-25").json()
        assert generated["status"] == "CREATED"
        assert service.thesis_service.get_current_thesis("CN", "000544.SZ") is None
        draft = generated["draft"]
        approved = client.post(
            f"/api/value/companies/000544.SZ/thesis-draft/approve?draft_id={draft['draft_id']}",
            json={"title": draft["title"], "core_thesis": draft["core_thesis"], "status": "FORMING", "confidence": "LOW"},
        )
        assert approved.status_code == 201
        assert approved.json()["status"] == "APPROVED"
        assert approved.json()["thesis"]["created_by"] == "HUMAN"
    finally:
        service.close()


def test_provisional_validation_rejects_unsourced_facts_and_industry_templates() -> None:
    base = {
        "research_as_of": "2026-08-25", "source_data_as_of": "2026-08-25", "source_snapshots": [],
        "thesis_summary": "测试草案", "core_drivers": [{"type": "FACT", "text": "已验证事实", "source_keys": ["F1"]}],
        "competitive_advantages": [{"type": "UNKNOWN", "text": "暂无足够资料确认长期竞争壁垒", "source_keys": []}],
        "key_assumptions": [{"type": "INFERENCE", "text": "趋势保持", "source_keys": ["F1"], "factual_basis": "已保存财务事实"}],
        "invalid_conditions": [{"type": "INFERENCE", "condition": "盈利持续恶化", "source_keys": ["F1"], "factual_basis": "已保存财务事实"}],
        "key_metrics_to_monitor": [{"type": "FACT", "text": "跟踪收入", "source_keys": ["F1"]}],
        "main_risks": [{"type": "UNKNOWN", "text": "资料不足", "source_keys": []}],
        "source_refs": [{"type": "FACT", "text": "来源", "source_keys": ["F1"]}],
    }
    valid, reason = CompanyThesisDraftService.validate_for_provisional(base, research_as_of="2026-08-25")
    assert valid and reason is None
    invalid = {**base, "core_drivers": [{"type": "FACT", "text": "没有来源", "source_keys": []}]}
    valid, reason = CompanyThesisDraftService.validate_for_provisional(invalid, research_as_of="2026-08-25")
    assert not valid and reason == "UNSOURCED_FACT:core_drivers"
    no_basis = {**base, "key_assumptions": [{"type": "INFERENCE", "text": "缺少推理依据", "source_keys": ["F1"]}]}
    valid, reason = CompanyThesisDraftService.validate_for_provisional(no_basis, research_as_of="2026-08-25")
    assert not valid and reason == "INFERENCE_WITHOUT_BASIS:key_assumptions"
    restaurant = {**base, "thesis_summary": "PPP回款需要观察"}
    valid, reason = CompanyThesisDraftService.validate_for_provisional(
        restaurant, research_as_of="2026-08-25", business_profile={"main_business": "餐饮服务"},
    )
    assert not valid and reason == "INDUSTRY_TEMPLATE_MISMATCH"

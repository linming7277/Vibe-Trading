from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_evidence_routes
from src.api.company_thesis_evidence_routes import register_company_thesis_evidence_routes
from src.business_research.store import BusinessResearchStore
from src.company_thesis.business_evidence_service import CompanyThesisBusinessEvidenceService
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService


def _thesis(service: CompanyThesisService, stock_code: str = "000338.SZ") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="多元业务待持续验证",
        core_thesis="公司多项主营业务能够持续带来经营收入。", status="FORMING", confidence="MEDIUM",
        invalid_conditions=[], created_by="HUMAN", source_data_as_of="2026-08-21",
    )


def _snapshot(
    store: BusinessResearchStore,
    *,
    stock_code: str = "000338.SZ",
    structured: bool = True,
    claims: list[dict] | None = None,
) -> dict:
    snapshot, _ = store.save({
        "stock_code": stock_code,
        "company_name": "测试公司",
        "data_as_of": "2026-08-21",
        "main_business": "动力总成、整车整机及关键零部件",
        "products": ["动力总成", "整车整机", "关键零部件"],
        "business_model": "UNKNOWN",
        "business_changes": ["UNKNOWN：缺少可比较历史资料。"],
        "sources": {
            "BUSINESS_CURRENT_MAIN_BUSINESS": {
                "source_type": "TDX_BUSINESS_PROFILE", "source_id": f"fundamentals:{stock_code}",
                "data_as_of": "2026-08-21", "field": "main_business",
                "value": "动力总成、整车整机及关键零部件", "source_hash": "business-hash", "profile_role": "CURRENT",
            },
        },
        "source_hash": f"business-source-{stock_code}-{structured}-{len(claims or [])}",
        "data_quality": {"status": "PARTIAL", "field_statuses": {}, "missing_fields": [], "limitations": []},
        "module_version": "financial-researcher-business-v1.0.0",
    }, configured=True, provider="test", model="test-model")
    default_claims = [
        {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "公司主要销售动力总成、整车整机及关键零部件。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
        {"type": "INFERENCE", "topic": "BUSINESS_MODEL", "text": "从主营业务看，公司可能通过销售动力总成、整车整机及关键零部件获得收入。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "MEDIUM"},
        {"type": "UNKNOWN", "topic": "BUSINESS_CHANGE", "text": "缺少前后两期可比较的经营资料，无法确认最近经营变化。", "source_keys": [], "confidence": "LOW"},
        {"type": "INFERENCE", "topic": "PRODUCT", "text": "低置信度的产品推断。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "LOW"},
    ]
    analysis = {
        "summary": "公司覆盖动力总成、整车整机和关键零部件等业务。现有资料不足以判断哪项产品贡献最大。",
        "claims": claims if claims is not None else default_claims,
        "analysis_metadata": {"quality_status": "STRUCTURED" if structured else "SUMMARY_ONLY", "module_version": "financial-researcher-business-v1.0.0"},
    }
    return store.update_analysis(snapshot["id"], status="COMPLETED", provider="test", model="test-model", analysis=analysis)


def _effects(_: dict, candidates: list[dict]) -> list[dict]:
    mapping = {0: "SUPPORT", 1: "CHALLENGE", 2: "NEUTRAL"}
    return [
        {"claim_index": item["claim_index"], "effect": mapping.get(item["claim_index"], "NEUTRAL"),
         "reason": f"这条经营资料与当前 Thesis 的关系已经核验（第 {item['claim_index']} 条）。"}
        for item in candidates
    ]


def _services(tmp_path: Path, **kwargs):
    db_path = tmp_path / "research.db"
    thesis = CompanyThesisService(db_path=db_path)
    evidence = CompanyThesisEvidenceService(db_path=db_path)
    business = BusinessResearchStore(db_path)
    relevance_resolver = kwargs.pop("relevance_resolver", _effects)
    bridge = CompanyThesisBusinessEvidenceService(
        evidence_service=evidence, business_store=business, relevance_resolver=relevance_resolver, **kwargs,
    )
    return db_path, thesis, evidence, business, bridge


def _close(thesis, evidence, business, bridge) -> None:
    bridge.close()
    business.close()
    evidence.close()
    thesis.close()


def test_filters_business_claims_maps_evidence_and_is_idempotent_without_thesis_history_mutation(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, business_store, bridge = _services(tmp_path)
    try:
        thesis = _thesis(thesis_service)
        _snapshot(business_store)
        first = bridge.extract_from_latest_business_research("CN", "000338.SZ")
        assert first["status"] == "OK" and first["created"] == 2
        assert (first["skipped_unknown"], first["skipped_low_confidence"], first["skipped_invalid"]) == (1, 1, 0)
        rows = evidence_service.repository.list_active_evidence_for_thesis(thesis["thesis_id"])
        assert {row["effect"] for row in rows} == {"SUPPORT", "CHALLENGE"}
        assert {row["created_by"] for row in rows} == {"AGENT_FINANCIAL"}
        assert {row["evidence_type"] for row in rows} == {"BUSINESS"}
        assert {row["source_type"] for row in rows} == {"COMPANY_RESEARCH_SNAPSHOT"}
        assert all(row["metadata"]["research_domain"] == "BUSINESS" for row in rows)
        assert all(row["metadata"]["resolved_citations"] for row in rows)
        assert all(row["metadata"]["source_hashes"] for row in rows)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM company_thesis_history").fetchone()[0] == 0
        after = thesis_service.get_current_thesis("CN", "000338.SZ")
        assert (after["thesis_id"], after["version"], after["status"], after["confidence"]) == (
            thesis["thesis_id"], 1, "FORMING", "MEDIUM")
        second = bridge.extract_from_latest_business_research("CN", "000338.SZ")
        assert second["created"] == 0 and second["unchanged"] == 2
    finally:
        _close(thesis_service, evidence_service, business_store, bridge)


def test_preconditions_traceability_and_all_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, thesis_service, evidence_service, business_store, bridge = _services(tmp_path)
    try:
        assert bridge.extract_from_latest_business_research("CN", "000338.SZ")["status"] == "THESIS_NOT_CREATED"
        _thesis(thesis_service)
        assert bridge.extract_from_latest_business_research("CN", "000338.SZ")["status"] == "BUSINESS_RESEARCH_NOT_READY"
        _snapshot(business_store, structured=False)
        assert bridge.extract_from_latest_business_research("CN", "000338.SZ")["status"] == "CLAIMS_NOT_EVIDENCE_READY"

        import src.company_thesis.business_evidence_service as module

        class _IncompleteResolver:
            def resolve_snapshot(self, value: dict) -> dict:
                return {**value, "traceability_status": "PARTIAL"}

        _snapshot(business_store, stock_code="000339.SZ")
        _thesis(thesis_service, "000339.SZ")
        monkeypatch.setattr(module, "BusinessClaimCitationResolver", _IncompleteResolver)
        assert bridge.extract_from_latest_business_research("CN", "000339.SZ")["status"] == "TRACEABILITY_INCOMPLETE"
        monkeypatch.undo()

        claims = [
            {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "公司主要销售动力总成。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
            {"type": "FACT", "topic": "PRODUCT", "text": "公司销售整车整机。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
            {"type": "FACT", "topic": "BUSINESS_MODEL", "text": "公司通过销售关键零部件获得收入。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "HIGH"},
        ]
        _snapshot(business_store, stock_code="000340.SZ", claims=claims)
        thesis = _thesis(thesis_service, "000340.SZ")
        outcome = bridge.extract_from_latest_business_research("CN", "000340.SZ")
        assert outcome["created"] == 3
        assert {row["effect"] for row in evidence_service.repository.list_active_evidence_for_thesis(thesis["thesis_id"])} == {"SUPPORT", "CHALLENGE", "NEUTRAL"}
    finally:
        _close(thesis_service, evidence_service, business_store, bridge)


def test_plain_reason_financial_evidence_preservation_review_stale_and_race_protection(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, business_store, bridge = _services(tmp_path)
    review = CompanyThesisReviewService(db_path=db_path)
    try:
        thesis = _thesis(thesis_service)
        financial = evidence_service.create_evidence(
            thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL", claim="已有财务证据。",
            summary="用于验证已有财务证据不受影响。", source_type="FINANCIAL_ANALYSIS", source_id="financial_1",
            confidence="LOW", created_by="AGENT_FINANCIAL", metadata={"research_domain": "FINANCIAL"},
        )
        existing_review = review.review_current_thesis("CN", "000338.SZ")["review"]
        _snapshot(business_store)
        outcome = bridge.extract_from_latest_business_research("CN", "000338.SZ")
        assert outcome["created"] == 2
        assert "Thesis" in outcome["evidence"][0]["summary"]
        assert evidence_service.repository.get_evidence_by_id(financial["evidence_id"])["is_active"] is True
        assert review.repository.get_review_by_id(existing_review["review_id"])["is_stale"] is True
        assert len(review.repository.list_reviews_for_thesis(thesis["thesis_id"])) == 1
    finally:
        review.close()
        _close(thesis_service, evidence_service, business_store, bridge)

    _, thesis_service, evidence_service, business_store, bridge = _services(
        tmp_path / "race",
        before_write_hook=lambda: thesis_service.create_new_version(
            market="CN", stock_code="000338.SZ", title="新版", core_thesis="人工变更。", status="UNCHANGED",
            confidence="MEDIUM", invalid_conditions=[], change_reason="测试版本竞态", updated_by="HUMAN",
        ),
    )
    try:
        _thesis(thesis_service)
        _snapshot(business_store)
        outcome = bridge.extract_from_latest_business_research("CN", "000338.SZ")
        assert outcome["status"] == "THESIS_CHANGED_DURING_EXTRACTION" and outcome["created"] == 0
        assert evidence_service.repository.list_evidence_for_company("CN", "000338.SZ") == []
    finally:
        _close(thesis_service, evidence_service, business_store, bridge)


def test_relevance_rejects_jargon_and_trading_language(tmp_path: Path) -> None:
    def invalid(_: dict, candidates: list[dict]) -> list[dict]:
        return [{"claim_index": item["claim_index"], "effect": "SUPPORT", "reason": "建议买入，护城河提升。"} for item in candidates]

    _, thesis_service, evidence_service, business_store, bridge = _services(tmp_path, relevance_resolver=invalid)
    try:
        _thesis(thesis_service)
        _snapshot(business_store)
        assert bridge.extract_from_latest_business_research("CN", "000338.SZ")["status"] == "RELEVANCE_NOT_READY"
        assert evidence_service.repository.list_evidence_for_company("CN", "000338.SZ") == []
    finally:
        _close(thesis_service, evidence_service, business_store, bridge)


def test_api_single_company_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, thesis_service, evidence_service, business_store, bridge = _services(tmp_path)
    app = FastAPI()
    register_company_thesis_evidence_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    monkeypatch.setattr(company_thesis_evidence_routes, "_business_extractor", lambda: bridge)
    try:
        _thesis(thesis_service)
        _snapshot(business_store)
        response = client.post("/api/value/companies/000338.SZ/thesis/evidence/from-business-research?market=CN")
        assert response.status_code == 200
        assert response.json()["created"] == 2
    finally:
        _close(thesis_service, evidence_service, business_store, bridge)

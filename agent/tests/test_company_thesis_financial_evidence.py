from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_evidence_routes
from src.api.company_thesis_evidence_routes import register_company_thesis_evidence_routes
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.financial_evidence_service import CompanyThesisFinancialEvidenceService
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService
from src.financial_analysis.store import FinancialAnalysisStore


def _thesis(service: CompanyThesisService, stock_code: str = "600001.SH") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="现金流支撑增长", core_thesis="盈利增长具有经营现金流支撑。",
        status="FORMING", confidence="MEDIUM", invalid_conditions=[], created_by="HUMAN",
        source_data_as_of="2026-08-17",
    )


def _snapshot(store: FinancialAnalysisStore, *, stock_code: str = "600001.SH", ready: bool = True,
              complete: bool = True, claims: list[dict] | None = None) -> dict:
    snapshot, _ = store.save_python_snapshot({
        "stock_code": stock_code, "stock_name": "测试公司", "as_of": "2026-06-30",
        "historical_cutoff": "2026-06-30", "financial_feature_version": "v1", "forecast_version": "v1",
        "feature_status": "READY", "forecast_status": "READY", "analysis_status": "NOT_RUN",
        "agent_provider": "test", "agent_model": "test-model", "identity": {}, "history": [], "feature": {},
        "forecast": {}, "data_gaps": [], "source_hash": f"hash-{stock_code}-{ready}-{complete}-{len(claims or [])}",
    })
    manifest = {
        "FIN_REV": {"source_snapshot_id": snapshot["id"], "source_hash": "history-hash", "data_as_of": "2026-06-30",
                    "metric": "revenue", "period": "2026H1", "value": 100, "unit": "CNY", "source_type": "PIT_FINANCIAL_HISTORY"},
        "FIN_OCF": {"source_snapshot_id": snapshot["id"], "source_hash": "cash-hash", "data_as_of": "2026-06-30",
                    "metric": "operating_cash_flow", "period": "2026H1", "value": -30, "unit": "CNY", "source_type": "PIT_FINANCIAL_HISTORY"},
        "FORECAST_NP": {"source_snapshot_id": snapshot["id"], "source_hash": "forecast-hash", "data_as_of": "2026-06-30",
                        "metric": "net_profit", "period": "2026", "value": 120, "unit": "CNY", "source_type": "DETERMINISTIC_FORECAST"},
    }
    default_claims = [
        {"type": "FACT", "text": "收入增长。", "source_keys": ["FIN_REV"], "confidence": "HIGH"},
        {"type": "INFERENCE", "text": "现金流压力需要持续核验。", "source_keys": ["FIN_OCF"], "confidence": "MEDIUM"},
        {"type": "FORECAST", "text": "预测净利润改善。", "source_keys": ["FORECAST_NP"], "confidence": "MEDIUM"},
        {"type": "UNKNOWN", "text": "客户集中度尚不明确。", "source_keys": [], "confidence": "LOW"},
        {"type": "INFERENCE", "text": "低置信度推断。", "source_keys": ["FIN_REV"], "confidence": "LOW"},
    ]
    if not complete:
        default_claims[0]["source_keys"] = ["MISSING"]
    analysis = {
        "claims": claims if claims is not None else default_claims,
        "analysis_metadata": {"evidence_ready": ready, "evidence_manifest": manifest,
                              "prompt_version": "financial-analysis-claims-v1.1.0", "provider": "test", "model": "test-model"},
    }
    return store.update_agent_result(snapshot["id"], status="COMPLETED", provider="test", model="test-model", analysis=analysis)


def _effects(_: dict, candidates: list[dict]) -> list[dict]:
    mapping = {0: "SUPPORT", 1: "CHALLENGE", 2: "NEUTRAL"}
    return [{"claim_index": item["claim_index"], "effect": mapping.get(item["claim_index"], "NEUTRAL"),
             "reason": f"Claim {item['claim_index']} 的可审计关联判断。"} for item in candidates]


def _services(tmp_path: Path, **kwargs):
    db_path = tmp_path / "research.db"
    thesis = CompanyThesisService(db_path=db_path)
    evidence = CompanyThesisEvidenceService(db_path=db_path)
    financial = FinancialAnalysisStore(db_path)
    relevance_resolver = kwargs.pop("relevance_resolver", _effects)
    bridge = CompanyThesisFinancialEvidenceService(
        evidence_service=evidence, financial_store=financial, relevance_resolver=relevance_resolver, **kwargs,
    )
    return db_path, thesis, evidence, financial, bridge


def _close(thesis, evidence, financial, bridge) -> None:
    bridge.close()
    financial.close()
    evidence.close()
    thesis.close()


def test_filters_claims_maps_effects_and_is_idempotent_without_thesis_history_mutation(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, financial_store, bridge = _services(tmp_path)
    try:
        thesis = _thesis(thesis_service)
        _snapshot(financial_store)
        first = bridge.extract_from_latest_financial_analysis("CN", "600001.SH")
        assert first["status"] == "OK" and first["created"] == 2
        assert (first["skipped_forecast"], first["skipped_unknown"], first["skipped_low_confidence"]) == (1, 1, 1)
        rows = evidence_service.repository.list_active_evidence_for_thesis(thesis["thesis_id"])
        assert {row["effect"] for row in rows} == {"SUPPORT", "CHALLENGE"}
        assert {row["created_by"] for row in rows} == {"AGENT_FINANCIAL"}
        assert {row["evidence_type"] for row in rows} == {"FINANCIAL"}
        assert all(row["source_type"] == "FINANCIAL_ANALYSIS" for row in rows)
        assert all(row["metadata"]["resolved_citations"] for row in rows)
        assert all(row["metadata"]["source_hashes"] for row in rows)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM company_thesis_history").fetchone()[0] == 0
        after = thesis_service.get_current_thesis("CN", "600001.SH")
        assert (after["thesis_id"], after["version"], after["status"], after["confidence"]) == (
            thesis["thesis_id"], 1, "FORMING", "MEDIUM")
        second = bridge.extract_from_latest_financial_analysis("CN", "600001.SH")
        assert second["created"] == 0 and second["unchanged"] == 2
    finally:
        _close(thesis_service, evidence_service, financial_store, bridge)


def test_preconditions_and_neutral_effect(tmp_path: Path) -> None:
    _, thesis_service, evidence_service, financial_store, bridge = _services(tmp_path)
    try:
        assert bridge.extract_from_latest_financial_analysis("CN", "600001.SH")["status"] == "THESIS_NOT_CREATED"
        _thesis(thesis_service)
        assert bridge.extract_from_latest_financial_analysis("CN", "600001.SH")["status"] == "FINANCIAL_ANALYSIS_NOT_READY"
        _snapshot(financial_store, ready=False)
        assert bridge.extract_from_latest_financial_analysis("CN", "600001.SH")["status"] == "CLAIMS_NOT_EVIDENCE_READY"
        _snapshot(financial_store, ready=True, complete=False, stock_code="600002.SH")
        _thesis(thesis_service, "600002.SH")
        assert bridge.extract_from_latest_financial_analysis("CN", "600002.SH")["status"] == "TRACEABILITY_INCOMPLETE"
        claims = [{"type": "FACT", "text": "收入增长。", "source_keys": ["FIN_REV"], "confidence": "HIGH"},
                  {"type": "FACT", "text": "现金流下降。", "source_keys": ["FIN_OCF"], "confidence": "HIGH"},
                  {"type": "FACT", "text": "利润质量变化。", "source_keys": ["FIN_REV"], "confidence": "HIGH"}]
        _snapshot(financial_store, stock_code="600003.SH", claims=claims)
        thesis3 = _thesis(thesis_service, "600003.SH")
        outcome = bridge.extract_from_latest_financial_analysis("CN", "600003.SH")
        assert outcome["created"] == 3
        assert {row["effect"] for row in evidence_service.repository.list_active_evidence_for_thesis(thesis3["thesis_id"])} == {"SUPPORT", "CHALLENGE", "NEUTRAL"}
    finally:
        _close(thesis_service, evidence_service, financial_store, bridge)


def test_idempotent_retry_does_not_rejudge_existing_evidence(tmp_path: Path) -> None:
    calls = 0

    def counted_effects(thesis: dict, candidates: list[dict]) -> list[dict]:
        nonlocal calls
        calls += 1
        return _effects(thesis, candidates)

    _, thesis_service, evidence_service, financial_store, bridge = _services(
        tmp_path, relevance_resolver=counted_effects,
    )
    try:
        _thesis(thesis_service)
        _snapshot(financial_store)
        assert bridge.extract_from_latest_financial_analysis("CN", "600001.SH")["created"] == 2
        assert bridge.extract_from_latest_financial_analysis("CN", "600001.SH")["unchanged"] == 2
        assert calls == 1
    finally:
        _close(thesis_service, evidence_service, financial_store, bridge)


def test_race_protection_stales_existing_review_without_creating_one(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, financial_store, bridge = _services(tmp_path)
    review = CompanyThesisReviewService(db_path=db_path)
    try:
        thesis = _thesis(thesis_service)
        evidence_service.create_evidence(
            thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL",
            claim="人工初始证据。", summary="用于建立初始 Review。", source_type="MANUAL",
            confidence="LOW", created_by="HUMAN",
        )
        existing_review = review.review_current_thesis("CN", "600001.SH")["review"]
        _snapshot(financial_store)
        first = bridge.extract_from_latest_financial_analysis("CN", "600001.SH")
        assert first["created"] == 2
        assert review.repository.get_review_by_id(existing_review["review_id"])["is_stale"] is True
        assert len(review.repository.list_reviews_for_thesis(thesis["thesis_id"])) == 1
    finally:
        review.close()
        _close(thesis_service, evidence_service, financial_store, bridge)

    db_path, thesis_service, evidence_service, financial_store, bridge = _services(
        tmp_path / "race", before_write_hook=lambda: thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="新版", core_thesis="人工变更。", status="UNCHANGED",
            confidence="MEDIUM", invalid_conditions=[], change_reason="测试版本竞态", updated_by="HUMAN",
        ),
    )
    try:
        _thesis(thesis_service)
        _snapshot(financial_store)
        outcome = bridge.extract_from_latest_financial_analysis("CN", "600001.SH")
        assert outcome["status"] == "THESIS_CHANGED_DURING_EXTRACTION" and outcome["created"] == 0
        assert evidence_service.repository.list_evidence_for_company("CN", "600001.SH") == []
    finally:
        _close(thesis_service, evidence_service, financial_store, bridge)


def test_api_single_company_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, thesis_service, evidence_service, financial_store, bridge = _services(tmp_path)
    app = FastAPI()
    register_company_thesis_evidence_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    monkeypatch.setattr(company_thesis_evidence_routes, "_financial_extractor", lambda: bridge)
    try:
        _thesis(thesis_service)
        _snapshot(financial_store)
        response = client.post("/api/value/companies/600001.SH/thesis/evidence/from-financial-agent?market=CN")
        assert response.status_code == 200
        assert response.json()["created"] == 2
    finally:
        _close(thesis_service, evidence_service, financial_store, bridge)

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_evidence_routes
from src.api.company_thesis_evidence_routes import register_company_thesis_evidence_routes
from src.company_thesis.evidence_extractor_service import CompanyThesisEvidenceExtractorService
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService


def _thesis(service: CompanyThesisService, stock_code: str = "600001.SH") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="核心逻辑", core_thesis="盈利质量持续改善。",
        status="FORMING", confidence="LOW", invalid_conditions=[], created_by="HUMAN",
        source_data_as_of="2026-08-17",
    )


def _financial_snapshot(db_path: Path, *, source_hash: str = "fin-hash-1", stock_code: str = "600001.SH") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO company_financial_analysis_snapshots(
               id,stock_code,stock_name,as_of,historical_cutoff,financial_feature_version,forecast_version,
               feature_status,forecast_status,analysis_status,identity_json,history_json,feature_json,
               forecast_json,data_gaps_json,source_hash,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("financial-snapshot-1", stock_code, "测试公司", "2026-06-30", "2026-06-30", "v1", "v1",
             "READY", "NOT_RUN", "READY", "{}", "{}", json.dumps({"latest_changes": [
                 {"metric": "revenue", "change_percent": 20.0, "report_date": "2026-06-30"},
                 {"metric": "net_profit", "change_percent": -25.0, "report_date": "2026-06-30"},
                 {"metric": "roe", "change_percent": 1.0, "report_date": "2026-06-30"},
             ]}), "{}", "[]", source_hash, "2026-08-17T00:00:00+00:00", "2026-08-17T00:00:00+00:00"),
        )


def _research_snapshot(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO l3_company_research_snapshots(
               id,source_snapshot_id,pool_id,stock_code,version,data_as_of,status,completeness,source_hash,
               payload_json,diff_json,missing_fields_json,sources_json,evidence_ids_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("research-1", "research-source-1", None, "600001.SH", 1, "2026-08-17", "READY", 1.0,
             "research-hash-1", json.dumps({
                 "financial_latest": {"revenue_yoy": 36.0, "net_profit_yoy": 8.0, "debt_ratio": 48.0},
                 "financial_previous": {"revenue_yoy": 10.0, "net_profit_yoy": 35.0, "debt_ratio": 40.0},
                 "free_text": "不得读取为证据",
             }), "{}", "[]", "[]", "[]", "2026-08-17T00:00:00+00:00"),
        )


def _valuation_snapshots(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        for identifier, date, percentile in (("valuation-1", "2026-08-16", 20.0), ("valuation-2", "2026-08-17", 42.0)):
            conn.execute(
                """INSERT INTO l3_company_valuation_snapshots(
                   id,source_snapshot_id,pool_id,stock_code,version,status,review_status,data_as_of,coverage,
                   source_hash,valuation_json,formula_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, f"{identifier}-source", None, "600001.SH", 1, "READY", "NOT_REVIEWED", date,
                 1.0, f"{identifier}-hash", json.dumps({"pe_percentile": percentile}), "v1",
                 f"{date}T00:00:00+00:00"),
            )


def _services(tmp_path: Path):
    db_path = tmp_path / "research.db"
    thesis = CompanyThesisService(db_path=db_path)
    evidence = CompanyThesisEvidenceService(db_path=db_path)
    extractor = CompanyThesisEvidenceExtractorService(evidence_service=evidence, db_path=db_path)
    return db_path, thesis, evidence, extractor


def _close(thesis, evidence, extractor) -> None:
    extractor.close()
    evidence.close()
    thesis.close()


def test_financial_rules_create_support_and_challenge_then_are_idempotent(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, extractor = _services(tmp_path)
    try:
        thesis = _thesis(thesis_service)
        _financial_snapshot(db_path)
        first = extractor.extract_for_company("CN", "600001.SH")
        assert first["created"] == 2
        assert {item["effect"] for item in first["evidence"]} == {"SUPPORT", "CHALLENGE"}
        assert all(item["created_by"] == "SYSTEM" for item in first["evidence"])
        assert all(item["metadata"]["extractor_version"] == "value-thesis-evidence-extractor-v1.0.0" for item in first["evidence"])
        second = extractor.extract_for_company("CN", "600001.SH")
        assert second["created"] == 0 and second["unchanged"] == 2
        assert evidence_service.get_evidence_summary(thesis["thesis_id"])["active"] == 2
    finally:
        _close(thesis_service, evidence_service, extractor)


def test_research_uses_structured_diffs_and_valuation_is_neutral(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, extractor = _services(tmp_path)
    try:
        thesis = _thesis(thesis_service)
        _research_snapshot(db_path)
        _valuation_snapshots(db_path)
        outcome = extractor.extract_for_company("CN", "600001.SH")
        evidence = evidence_service.repository.list_active_evidence_for_thesis(thesis["thesis_id"])
        assert outcome["created"] == 4
        assert {item["source_type"] for item in evidence} == {"COMPANY_RESEARCH_SNAPSHOT", "SYSTEM"}
        valuation = next(item for item in evidence if item["evidence_type"] == "VALUATION")
        assert valuation["metadata"]["source_snapshot_type"] == "VALUATION_SNAPSHOT"
        assert valuation["effect"] == "NEUTRAL"
        assert "交易" in valuation["summary"] and "买" not in valuation["summary"]
        assert all("不得读取" not in item["claim"] for item in evidence)
    finally:
        _close(thesis_service, evidence_service, extractor)


def test_no_thesis_source_revision_and_human_evidence_are_safe(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, extractor = _services(tmp_path)
    try:
        _financial_snapshot(db_path)
        assert extractor.extract_for_company("CN", "600001.SH")["status"] == "THESIS_NOT_CREATED"
        thesis = _thesis(thesis_service)
        human = evidence_service.create_evidence(
            thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL",
            claim="人工核验记录。", summary="保留人工判断。", source_type="MANUAL", confidence="LOW",
            created_by="HUMAN",
        )
        first = extractor.extract_for_company("CN", "600001.SH")
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE company_financial_analysis_snapshots SET source_hash='fin-hash-2' WHERE id='financial-snapshot-1'")
        revised = extractor.extract_for_company("CN", "600001.SH")
        assert first["created"] == 2
        assert revised["deactivated"] == 2 and revised["created"] == 2
        assert evidence_service.repository.get_evidence_by_id(human["evidence_id"])["is_active"] is True
        assert len(evidence_service.repository.list_evidence_for_thesis(thesis["thesis_id"])) == 5
    finally:
        _close(thesis_service, evidence_service, extractor)


def test_extraction_stales_review_but_never_changes_thesis_or_creates_review(tmp_path: Path) -> None:
    db_path, thesis_service, evidence_service, extractor = _services(tmp_path)
    review_service = CompanyThesisReviewService(db_path=db_path)
    try:
        thesis = _thesis(thesis_service)
        evidence_service.create_evidence(
            thesis_id=thesis["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL",
            claim="人工初始证据。", summary="用于建立初始 Review。", source_type="MANUAL",
            confidence="LOW", created_by="HUMAN",
        )
        existing_review = review_service.review_current_thesis("CN", "600001.SH")["review"]
        _financial_snapshot(db_path)
        before = thesis_service.get_current_thesis("CN", "600001.SH")
        extractor.extract_for_company("CN", "600001.SH")
        after = thesis_service.get_current_thesis("CN", "600001.SH")
        review = review_service.repository.get_review_by_id(existing_review["review_id"])
        assert (before["thesis_id"], before["version"], before["status"], before["confidence"]) == (
            after["thesis_id"], after["version"], after["status"], after["confidence"])
        assert review["is_stale"] is True
        assert len(review_service.repository.list_reviews_for_thesis(thesis["thesis_id"])) == 1
    finally:
        review_service.close()
        _close(thesis_service, evidence_service, extractor)


def test_current_pool_only_processes_active_new_reentered_and_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    thesis_service = CompanyThesisService(db_path=db_path)
    evidence_service = CompanyThesisEvidenceService(db_path=db_path)
    _thesis(thesis_service, "600001.SH")
    _financial_snapshot(db_path)
    pool = {"pool_id": "pool-1", "research_states": [
        {"stock_code": "600001.SH", "lifecycle_status": "ACTIVE"},
        {"stock_code": "600002.SH", "lifecycle_status": "OUT_OF_TOP2"},
        {"stock_code": "600001.SH", "lifecycle_status": "NEW"},
    ]}
    extractor = CompanyThesisEvidenceExtractorService(evidence_service=evidence_service, db_path=db_path, pool_loader=lambda: pool)
    monkeypatch.setattr(company_thesis_evidence_routes, "_extractor", lambda: extractor)
    app = FastAPI()
    register_company_thesis_evidence_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        batch = client.post("/api/value/thesis-evidence/extract-current-pool")
        assert batch.status_code == 200
        assert batch.json()["processed"] == 1 and batch.json()["skipped"] >= 1
        single = client.post("/api/value/thesis-evidence/extract/600002.SH?market=CN")
        assert single.status_code == 200 and single.json()["status"] == "THESIS_NOT_CREATED"
    finally:
        _close(thesis_service, evidence_service, extractor)

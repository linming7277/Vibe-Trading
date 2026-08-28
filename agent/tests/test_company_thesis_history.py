from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_history_routes, company_thesis_routes
from src.api.company_thesis_history_routes import register_company_thesis_history_routes
from src.api.company_thesis_routes import register_company_thesis_routes
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.history_service import CompanyThesisHistoryService
from src.company_thesis.service import CompanyThesisService


def create_initial(service: CompanyThesisService, *, stock_code: str = "600001.SH") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="初始 Thesis", core_thesis="持续核验经营质量。",
        status="FORMING", confidence="LOW", invalid_conditions=[], created_by="HUMAN",
        source_data_as_of="2026-08-17",
    )


def create_evidence(service: CompanyThesisEvidenceService, thesis_id: str) -> dict:
    return service.create_evidence(
        thesis_id=thesis_id, evidence_type="FINANCIAL", effect="SUPPORT",
        claim="2026H1 净利润同比增长 42%。", summary="支持盈利能力改善。",
        source_type="FINANCIAL_SNAPSHOT", source_id="financial_2026h1",
        confidence="HIGH", created_by="HUMAN", data_as_of="2026-06-30",
    )


def services(tmp_path: Path):
    db_path = tmp_path / "research.db"
    return (
        CompanyThesisService(db_path=db_path),
        CompanyThesisEvidenceService(db_path=db_path),
        CompanyThesisHistoryService(db_path=db_path),
    )


def test_new_version_creates_atomic_history_with_evidence_snapshot(tmp_path: Path) -> None:
    thesis_service, evidence_service, history_service = services(tmp_path)
    try:
        first = create_initial(thesis_service)
        evidence = create_evidence(evidence_service, first["thesis_id"])
        second = thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="已完成首次核验", core_thesis="财务事实支持经营质量改善。",
            status="UNCHANGED", confidence="MEDIUM", invalid_conditions=[],
            change_reason="完成首次核心业务和财务核验", updated_by="HUMAN",
            source_data_as_of="2026-08-18", evidence_ids=[evidence["evidence_id"]],
            trigger_ref="research-note-20260818", history_metadata={"review_scope": "financial"},
        )
        record = history_service.repository.get_history_for_to_thesis(second["thesis_id"])
        assert record is not None
        assert record["from_thesis_id"] == first["thesis_id"]
        assert record["to_thesis_id"] == second["thesis_id"]
        assert (record["from_version"], record["to_version"]) == (1, 2)
        assert (record["old_status"], record["new_status"]) == ("FORMING", "UNCHANGED")
        assert (record["old_confidence"], record["new_confidence"]) == ("LOW", "MEDIUM")
        assert record["change_reason"] == "完成首次核心业务和财务核验"
        assert record["change_type"] == "VERSION_CREATED"
        assert record["trigger_type"] == "MANUAL"
        assert record["trigger_ref"] == "research-note-20260818"
        assert record["evidence_ids"] == [evidence["evidence_id"]]
        assert record["metadata"] == {"review_scope": "financial"}
    finally:
        history_service.close()
        evidence_service.close()
        thesis_service.close()


def test_initial_thesis_has_no_history_and_history_is_append_only(tmp_path: Path) -> None:
    thesis_service, evidence_service, history_service = services(tmp_path)
    try:
        first = create_initial(thesis_service)
        assert history_service.list_history_for_company("CN", "600001.SH") == []
        second = thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="第二版", core_thesis="新版内容。",
            status="STRENGTHENING", confidence="MEDIUM", invalid_conditions=[],
            change_reason="人工复核", updated_by="SYSTEM",
        )
        third = thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="第三版", core_thesis="第三版内容。",
            status="WEAKENING", confidence="LOW", invalid_conditions=[],
            change_reason="出现新的风险事实", updated_by="HUMAN",
        )
        rows = history_service.list_history_for_company("CN", "600001.SH")
        assert [(row["from_version"], row["to_version"]) for row in rows] == [(2, 3), (1, 2)]
        assert history_service.list_history_for_thesis(first["thesis_id"])[0]["to_thesis_id"] == second["thesis_id"]
        assert history_service.list_history_for_thesis(third["thesis_id"])[0]["to_thesis_id"] == third["thesis_id"]
        with pytest.raises(AttributeError):
            getattr(history_service.repository, "delete_history")
    finally:
        history_service.close()
        evidence_service.close()
        thesis_service.close()


def test_history_evidence_must_belong_to_previous_thesis_and_snapshot_survives_deactivation(tmp_path: Path) -> None:
    thesis_service, evidence_service, history_service = services(tmp_path)
    try:
        first = create_initial(thesis_service)
        evidence = create_evidence(evidence_service, first["thesis_id"])
        other = create_initial(thesis_service, stock_code="600002.SH")
        other_evidence = create_evidence(evidence_service, other["thesis_id"])
        with pytest.raises(ValueError, match="previous thesis version"):
            thesis_service.create_new_version(
                market="CN", stock_code="600001.SH", title="第二版", core_thesis="新版内容。",
                status="UNCHANGED", confidence="MEDIUM", invalid_conditions=[],
                change_reason="人工复核", updated_by="HUMAN", evidence_ids=[other_evidence["evidence_id"]],
            )
        second = thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="第二版", core_thesis="新版内容。",
            status="UNCHANGED", confidence="MEDIUM", invalid_conditions=[],
            change_reason="人工复核", updated_by="HUMAN", evidence_ids=[evidence["evidence_id"]],
        )
        evidence_service.deactivate_evidence(evidence["evidence_id"], "源数据修正")
        record = history_service.repository.get_history_for_to_thesis(second["thesis_id"])
        assert record["evidence_ids"] == [evidence["evidence_id"]]
        assert evidence_service.repository.get_evidence_by_id(evidence["evidence_id"])["is_active"] is False
    finally:
        history_service.close()
        evidence_service.close()
        thesis_service.close()


def test_history_api_exposes_company_and_version_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thesis_service, evidence_service, history_service = services(tmp_path)
    monkeypatch.setattr(company_thesis_routes, "_service", lambda: thesis_service)
    monkeypatch.setattr(company_thesis_history_routes, "_service", lambda: history_service)
    app = FastAPI()
    register_company_thesis_routes(app, require_auth=lambda: True)
    register_company_thesis_history_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        initial = client.post("/api/value/companies/600001.SH/thesis", json={
            "title": "初始", "core_thesis": "初始内容。", "status": "FORMING", "confidence": "LOW",
        })
        assert initial.status_code == 201
        first_id = initial.json()["thesis_id"]
        created = client.post("/api/value/companies/600001.SH/thesis/version", json={
            "title": "第二版", "core_thesis": "第二版内容。", "status": "UNCHANGED", "confidence": "MEDIUM",
            "change_reason": "人工复核", "trigger_ref": "manual-note",
        })
        assert created.status_code == 201
        company_history = client.get("/api/value/companies/600001.SH/thesis/history")
        assert company_history.status_code == 200 and company_history.json()["total"] == 1
        by_first = client.get(f"/api/value/theses/{first_id}/history")
        assert by_first.status_code == 200 and by_first.json()["items"][0]["trigger_ref"] == "manual-note"
    finally:
        history_service.close()
        evidence_service.close()
        thesis_service.close()

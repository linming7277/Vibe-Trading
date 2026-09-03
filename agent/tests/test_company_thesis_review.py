from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_review_routes
from src.api.company_thesis_review_routes import register_company_thesis_review_routes
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.history_service import CompanyThesisHistoryService
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService


def services(tmp_path: Path):
    db_path = tmp_path / "research.db"
    return (
        CompanyThesisService(db_path=db_path),
        CompanyThesisEvidenceService(db_path=db_path),
        CompanyThesisHistoryService(db_path=db_path),
        CompanyThesisReviewService(db_path=db_path),
    )


def create_thesis(service: CompanyThesisService, *, status: str = "UNCHANGED",
                  confidence: str = "MEDIUM", stock_code: str = "600001.SH") -> dict:
    return service.create_initial_thesis(
        market="CN", stock_code=stock_code, title="核心投资逻辑",
        core_thesis="盈利质量改善需要持续证据验证。", status=status,
        confidence=confidence, invalid_conditions=[], created_by="HUMAN",
        source_data_as_of="2026-08-17",
    )


def add_evidence(service: CompanyThesisEvidenceService, thesis_id: str, effect: str,
                 index: int, confidence: str = "HIGH") -> dict:
    return service.create_evidence(
        thesis_id=thesis_id, evidence_type="FINANCIAL", effect=effect,
        claim=f"{effect} 事实 {index}", summary=f"{effect} 摘要 {index}",
        source_type="FINANCIAL_SNAPSHOT", source_id=f"snapshot-{effect}-{index}",
        confidence=confidence, created_by="HUMAN", data_as_of="2026-06-30",
    )


def close_all(*items) -> None:
    for item in reversed(items):
        item.close()


def test_review_without_thesis_returns_not_created(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        assert review.review_current_thesis("CN", "600001.SH") == {
            "status": "THESIS_NOT_CREATED", "review": None, "created": False,
        }
    finally:
        close_all(thesis, evidence, history, review)


def test_no_active_evidence_does_not_create_an_empty_review(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis, status="FORMING", confidence="LOW")
        result = review.refresh_current_review("CN", "600001.SH")
        assert result == {"status": "NO_ACTIVE_EVIDENCE", "review": None, "created": False}
        assert review.repository.list_reviews_for_thesis(current["thesis_id"]) == []
    finally:
        close_all(thesis, evidence, history, review)


@pytest.mark.parametrize(
    ("status", "confidence", "effects", "expected_status", "expected_confidence"),
    [
        ("FORMING", "LOW", ["SUPPORT"] * 3, "UNCHANGED", "MEDIUM"),
        ("UNCHANGED", "MEDIUM", ["SUPPORT"] * 3, "STRENGTHENING", "HIGH"),
        ("UNCHANGED", "HIGH", ["CHALLENGE"] * 2, "WEAKENING", "MEDIUM"),
        ("STRENGTHENING", "LOW", ["CHALLENGE"] * 3, "WEAKENING", "LOW"),
        ("STRENGTHENING", "HIGH", ["SUPPORT", "CHALLENGE"], "UNCHANGED", "HIGH"),
        ("UNCHANGED", "MEDIUM", ["CHALLENGE"], "UNCHANGED", "MEDIUM"),
        ("FALSIFIED", "LOW", ["SUPPORT"] * 4, "FALSIFIED", "LOW"),
        ("UNCHANGED", "LOW", ["CHALLENGE"] * 5, "WEAKENING", "LOW"),
    ],
)
def test_deterministic_status_and_confidence_rules(
    tmp_path: Path, status: str, confidence: str, effects: list[str],
    expected_status: str, expected_confidence: str,
) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis, status=status, confidence=confidence)
        created = [add_evidence(evidence, current["thesis_id"], effect, index)
                   for index, effect in enumerate(effects)]
        item = review.review_current_thesis("CN", "600001.SH")["review"]
        assert item["recommended_status"] == expected_status
        assert item["recommended_confidence"] == expected_confidence
        assert item["recommended_status"] != "FALSIFIED" or status == "FALSIFIED"
        expected_ids = {row["evidence_id"] for row in created}
        saved_ids = set(item["support_evidence_ids"] + item["challenge_evidence_ids"] + item["neutral_evidence_ids"])
        assert saved_ids == expected_ids
    finally:
        close_all(thesis, evidence, history, review)


def test_review_is_idempotent_and_does_not_mutate_thesis_or_history(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        before_versions = thesis.list_thesis_versions("CN", "600001.SH")
        before_history = history.list_history_for_company("CN", "600001.SH")
        first = review.refresh_current_review("CN", "600001.SH")
        second = review.refresh_current_review("CN", "600001.SH")
        assert first["status"] == "CREATED" and second["status"] == "EXISTING"
        assert first["created"] is True and second["created"] is False
        assert first["review"]["review_id"] == second["review"]["review_id"]
        assert thesis.get_current_thesis("CN", "600001.SH") == current
        assert thesis.list_thesis_versions("CN", "600001.SH") == before_versions
        assert history.list_history_for_company("CN", "600001.SH") == before_history == []
    finally:
        close_all(thesis, evidence, history, review)


def test_evidence_change_creates_new_review_and_marks_old_stale(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        first_evidence = add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        first = review.review_current_thesis("CN", "600001.SH")["review"]
        second_evidence = add_evidence(evidence, current["thesis_id"], "CHALLENGE", 2)
        assert review.repository.get_review_by_id(first["review_id"])["is_stale"] is True
        second = review.review_current_thesis("CN", "600001.SH")["review"]
        assert second["review_id"] != first["review_id"]
        assert second["evidence_set_hash"] != first["evidence_set_hash"]
        assert set(second["support_evidence_ids"] + second["challenge_evidence_ids"]) == {
            first_evidence["evidence_id"], second_evidence["evidence_id"],
        }
    finally:
        close_all(thesis, evidence, history, review)


def test_refresh_uses_all_active_evidence_and_audits_sources(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        human = add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        system = evidence.create_evidence(
            thesis_id=current["thesis_id"], evidence_type="FINANCIAL", effect="NEUTRAL",
            claim="系统确定性证据", summary="系统摘要", source_type="SYSTEM", source_id="system-1",
            confidence="HIGH", created_by="SYSTEM",
        )
        financial = evidence.create_evidence(
            thesis_id=current["thesis_id"], evidence_type="FINANCIAL", effect="CHALLENGE",
            claim="财报 Agent 证据", summary="财报摘要", source_type="FINANCIAL_ANALYSIS", source_id="financial-1",
            confidence="MEDIUM", created_by="AGENT_FINANCIAL",
        )
        result = review.refresh_current_review("CN", "600001.SH")
        item = result["review"]
        assert result["status"] == "CREATED"
        assert item["metadata"]["evidence_source_summary"] == {
            "AGENT_FINANCIAL": 1, "HUMAN": 1, "SYSTEM": 1,
        }
        assert set(item["support_evidence_ids"] + item["challenge_evidence_ids"] + item["neutral_evidence_ids"]) == {
            human["evidence_id"], system["evidence_id"], financial["evidence_id"],
        }
    finally:
        close_all(thesis, evidence, history, review)


@pytest.mark.parametrize("terminal_status", ["APPLIED", "DISMISSED"])
def test_applied_or_dismissed_review_can_refresh_after_evidence_changes(tmp_path: Path, terminal_status: str) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        old = review.refresh_current_review("CN", "600001.SH")["review"]
        if terminal_status == "APPLIED":
            review.repository.mark_applied(old["review_id"], applied_by="HUMAN")
        else:
            review.dismiss_review(old["review_id"], "人工暂不采纳")
        add_evidence(evidence, current["thesis_id"], "CHALLENGE", 2)
        fresh = review.refresh_current_review("CN", "600001.SH")
        assert fresh["status"] == "CREATED"
        assert fresh["review"]["review_status"] == "PENDING"
        assert fresh["review"]["review_id"] != old["review_id"]
        assert review.repository.get_review_by_id(old["review_id"])["is_stale"] is True
    finally:
        close_all(thesis, evidence, history, review)


def test_deactivated_evidence_is_removed_from_new_snapshot(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        support = add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        first = review.review_current_thesis("CN", "600001.SH")["review"]
        evidence.deactivate_evidence(support["evidence_id"], "源数据修正")
        assert review.repository.get_review_by_id(first["review_id"])["is_stale"] is True
        second = review.refresh_current_review("CN", "600001.SH")
        assert second == {"status": "NO_ACTIVE_EVIDENCE", "review": None, "created": False}
    finally:
        close_all(thesis, evidence, history, review)


def test_new_thesis_version_marks_previous_review_stale(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        initial_review = review.review_current_thesis("CN", "600001.SH")["review"]
        thesis.create_new_version(
            market="CN", stock_code="600001.SH", title="第二版", core_thesis="新的人工结论。",
            status="UNCHANGED", confidence="MEDIUM", invalid_conditions=[],
            change_reason="人工完成复核", updated_by="HUMAN",
        )
        assert review.repository.get_review_by_id(initial_review["review_id"])["is_stale"] is True
    finally:
        close_all(thesis, evidence, history, review)


def test_reviewed_and_dismissed_transitions_are_auditable(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        current = create_thesis(thesis)
        add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        reviewed = review.review_current_thesis("CN", "600001.SH")["review"]
        handled = review.mark_reviewed(reviewed["review_id"])
        assert handled["review_status"] == "REVIEWED" and handled["reviewed_by"] == "HUMAN"
        dismissed = review.dismiss_review(reviewed["review_id"], "现有证据不足以改变 Thesis")
        assert dismissed["review_status"] == "DISMISSED"
        assert dismissed["dismissal_reason"] == "现有证据不足以改变 Thesis"
        with pytest.raises(ValueError, match="cannot transition"):
            review.mark_reviewed(reviewed["review_id"])
        assert not hasattr(review.repository, "delete_review")
    finally:
        close_all(thesis, evidence, history, review)


def test_review_api_lifecycle_before_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thesis, evidence, history, review = services(tmp_path)
    monkeypatch.setattr(company_thesis_review_routes, "_service", lambda: review)
    app = FastAPI()
    register_company_thesis_review_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        no_thesis = client.post("/api/value/companies/600001.SH/thesis/review")
        assert no_thesis.status_code == 200 and no_thesis.json()["status"] == "THESIS_NOT_CREATED"
        current = create_thesis(thesis)
        add_evidence(evidence, current["thesis_id"], "SUPPORT", 1)
        created = client.post("/api/value/companies/600001.SH/thesis/review?trigger_ref=manual-check")
        assert created.status_code == 200 and created.json()["status"] == "CREATED" and created.json()["review"]["trigger_ref"] == "manual-check"
        review_id = created.json()["review"]["review_id"]
        latest = client.get("/api/value/companies/600001.SH/thesis/review")
        listed = client.get("/api/value/companies/600001.SH/thesis/reviews")
        assert latest.status_code == 200 and latest.json()["review"]["review_id"] == review_id
        assert listed.status_code == 200 and listed.json()["total"] == 1
        assert client.patch(f"/api/value/thesis-reviews/{review_id}/reviewed").json()["review_status"] == "REVIEWED"
        dismissed = client.patch(
            f"/api/value/thesis-reviews/{review_id}/dismiss",
            json={"reason": "暂不调整"},
        )
        assert dismissed.status_code == 200 and dismissed.json()["review_status"] == "DISMISSED"
    finally:
        close_all(thesis, evidence, history, review)


def test_review_table_schema_version_and_no_cross_object_side_effects(tmp_path: Path) -> None:
    thesis, evidence, history, review = services(tmp_path)
    try:
        with sqlite3.connect(review.repository.db_path) as conn:
            # The workspace schema version advances with additive migrations;
            # module tables must never require a separate bump to appear.
            from src.research_workspace.store import ResearchWorkspaceStore

            assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(
                ResearchWorkspaceStore.SCHEMA_VERSION
            )
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_thesis_reviews'"
            ).fetchone()
    finally:
        close_all(thesis, evidence, history, review)

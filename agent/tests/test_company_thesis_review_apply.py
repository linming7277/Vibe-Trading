from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_review_routes
from src.api.company_thesis_review_routes import register_company_thesis_review_routes
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.history_service import CompanyThesisHistoryService
from src.company_thesis.review_apply_service import CompanyThesisReviewApplyService, ReviewApplyError
from src.company_thesis.review_service import CompanyThesisReviewService
from src.company_thesis.service import CompanyThesisService


def setup_services(tmp_path: Path):
    db_path = tmp_path / "research.db"
    thesis = CompanyThesisService(db_path=db_path)
    evidence = CompanyThesisEvidenceService(db_path=db_path)
    history = CompanyThesisHistoryService(db_path=db_path)
    review = CompanyThesisReviewService(db_path=db_path)
    apply = CompanyThesisReviewApplyService(db_path=db_path)
    current = thesis.create_initial_thesis(
        market="CN", stock_code="600001.SH", title="核心逻辑", core_thesis="核心内容不得由 Apply 修改。",
        status="UNCHANGED", confidence="HIGH", invalid_conditions=[{"condition": "利润失速"}],
        created_by="HUMAN", source_data_as_of="2026-08-17",
    )
    return thesis, evidence, history, review, apply, current


def evidence(service: CompanyThesisEvidenceService, thesis_id: str, effect: str, number: int) -> dict:
    return service.create_evidence(
        thesis_id=thesis_id, evidence_type="FINANCIAL", effect=effect,
        claim=f"{effect} evidence {number}", summary="事实摘要", source_type="FINANCIAL_SNAPSHOT",
        source_id=f"source-{effect}-{number}", confidence="HIGH", created_by="HUMAN",
    )


def close(*services) -> None:
    for service in reversed(services):
        service.close()


def prepared(tmp_path: Path, *, reviewed: bool = False):
    thesis, evidence_service, history, review_service, apply, current = setup_services(tmp_path)
    records = [evidence(evidence_service, current["thesis_id"], "CHALLENGE", number) for number in (1, 2)]
    review = review_service.review_current_thesis("CN", "600001.SH")["review"]
    if reviewed:
        review = review_service.mark_reviewed(review["review_id"])
    return thesis, evidence_service, history, review_service, apply, current, review, records


@pytest.mark.parametrize("reviewed", [False, True])
def test_pending_or_reviewed_apply_creates_version_history_and_audit(tmp_path: Path, reviewed: bool) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, records = prepared(tmp_path, reviewed=reviewed)
    try:
        result = apply.apply_review(review["review_id"], apply_reason="人工核对挑战证据后确认弱化。")
        assert result["status"] == "APPLIED" and result["new_version"] == 2
        latest = thesis.get_current_thesis("CN", "600001.SH")
        assert latest["status"] == "WEAKENING" and latest["confidence"] == "MEDIUM"
        assert latest["core_thesis"] == current["core_thesis"] and latest["invalid_conditions"] == current["invalid_conditions"]
        assert thesis.get_thesis_by_id(current["thesis_id"])["is_current"] is False
        applied = review_service.repository.get_review_by_id(review["review_id"])
        assert applied["review_status"] == "APPLIED"
        assert (applied["applied_thesis_id"], applied["applied_thesis_version"]) == (latest["thesis_id"], 2)
        audit = history.repository.get_history_for_to_thesis(latest["thesis_id"])
        assert (audit["trigger_type"], audit["trigger_ref"], audit["change_reason"]) == ("THESIS_REVIEW", review["review_id"], "人工核对挑战证据后确认弱化。")
        assert audit["evidence_ids"] == (
            review["support_evidence_ids"] + review["challenge_evidence_ids"] + review["neutral_evidence_ids"]
        )
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_override_and_metadata_are_audited(tmp_path: Path) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        apply.apply_review(review["review_id"], apply_reason="人工保守处理。", applied_status="UNCHANGED", applied_confidence="HIGH")
        applied = review_service.repository.get_review_by_id(review["review_id"])
        assert applied["metadata"]["human_override"] is True
        assert applied["metadata"]["recommended_status"] == "WEAKENING"
        assert applied["metadata"]["applied_status"] == "UNCHANGED"
        assert thesis.get_current_thesis("CN", "600001.SH")["status"] == "UNCHANGED"
    finally:
        close(thesis, evidence_service, history, review_service, apply)


@pytest.mark.parametrize("action, expected", [("dismissed", "REVIEW_DISMISSED"), ("stale", "REVIEW_STALE")])
def test_dismissed_and_stale_reviews_cannot_apply(tmp_path: Path, action: str, expected: str) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        if action == "dismissed":
            review_service.dismiss_review(review["review_id"], "人工驳回")
        else:
            evidence(evidence_service, current["thesis_id"], "SUPPORT", 3)
        with pytest.raises(ReviewApplyError, match=expected):
            apply.apply_review(review["review_id"], apply_reason="不应成功")
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_evidence_hash_change_marks_review_stale_and_rejects(tmp_path: Path) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        # Direct SQL represents an external Evidence update that did not already mark stale.
        apply.thesis_repository._conn.execute("UPDATE company_thesis_evidence SET updated_at='2099-01-01T00:00:00+00:00' WHERE thesis_id=?", (current["thesis_id"],))
        apply.thesis_repository._conn.commit()
        with pytest.raises(ReviewApplyError, match="EVIDENCE_CHANGED_SINCE_REVIEW"):
            apply.apply_review(review["review_id"], apply_reason="证据已变化")
        assert review_service.repository.get_review_by_id(review["review_id"])["is_stale"] is True
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_thesis_change_and_duplicate_apply_are_rejected(tmp_path: Path) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        thesis.create_new_version(
            market="CN", stock_code="600001.SH", title="人工版本", core_thesis=current["core_thesis"],
            status="UNCHANGED", confidence="HIGH", invalid_conditions=current["invalid_conditions"],
            change_reason="其他人工变更", updated_by="HUMAN",
        )
        # Simulate a legacy write that did not proactively mark the Review stale.
        apply.thesis_repository._conn.execute("UPDATE company_thesis_reviews SET is_stale=0 WHERE review_id=?", (review["review_id"],))
        apply.thesis_repository._conn.commit()
        with pytest.raises(ReviewApplyError, match="THESIS_CHANGED_SINCE_REVIEW"):
            apply.apply_review(review["review_id"], apply_reason="旧版本不能应用")
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_atomic_rollback_when_history_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        monkeypatch.setattr(apply.thesis_repository, "_insert_history", lambda *args: (_ for _ in ()).throw(RuntimeError("history failed")))
        with pytest.raises(RuntimeError, match="history failed"):
            apply.apply_review(review["review_id"], apply_reason="应回滚")
        assert thesis.get_current_thesis("CN", "600001.SH")["thesis_id"] == current["thesis_id"]
        assert review_service.repository.get_review_by_id(review["review_id"])["review_status"] == "PENDING"
        assert history.list_history_for_company("CN", "600001.SH") == []
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_concurrent_apply_only_succeeds_once(tmp_path: Path) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    try:
        def invoke() -> str:
            local = CompanyThesisReviewApplyService(db_path=apply.thesis_repository.db_path)
            try:
                return local.apply_review(review["review_id"], apply_reason="并发人工确认")["status"]
            except ReviewApplyError as exc:
                return exc.code
            finally:
                local.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: invoke(), range(2)))
        assert outcomes.count("APPLIED") == 1 and outcomes.count("REVIEW_ALREADY_APPLIED") == 1
        assert len(thesis.list_thesis_versions("CN", "600001.SH")) == 2
    finally:
        close(thesis, evidence_service, history, review_service, apply)


def test_apply_api_and_invalid_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thesis, evidence_service, history, review_service, apply, current, review, _ = prepared(tmp_path)
    monkeypatch.setattr(company_thesis_review_routes, "_apply_service", lambda: apply)
    app = FastAPI()
    register_company_thesis_review_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        invalid = client.post(f"/api/value/thesis-reviews/{review['review_id']}/apply", json={"apply_reason": "x", "applied_status": "BAD"})
        assert invalid.status_code == 422 and invalid.json()["detail"] == "INVALID_APPLIED_STATUS"
        response = client.post(f"/api/value/thesis-reviews/{review['review_id']}/apply", json={"apply_reason": "人工确认"})
        assert response.status_code == 200 and response.json()["status"] == "APPLIED"
    finally:
        close(thesis, evidence_service, history, review_service, apply)

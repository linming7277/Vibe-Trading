from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_evidence_routes
from src.api.company_thesis_evidence_routes import register_company_thesis_evidence_routes
from src.company_thesis.evidence_service import CompanyThesisEvidenceService
from src.company_thesis.service import CompanyThesisService
from src.level3_leaders.store import Level3LeaderStore


def create_thesis(service: CompanyThesisService, **overrides):
    payload = {
        "market": "CN", "stock_code": "600001.SH", "title": "长期竞争力待验证",
        "core_thesis": "盈利质量、行业地位和现金流需要持续验证。",
        "status": "FORMING", "confidence": "LOW", "invalid_conditions": [],
        "created_by": "HUMAN", "source_data_as_of": "2026-08-17",
    }
    payload.update(overrides)
    return service.create_initial_thesis(**payload)


def create_evidence(service: CompanyThesisEvidenceService, thesis_id: str, **overrides):
    payload = {
        "thesis_id": thesis_id, "evidence_type": "FINANCIAL", "effect": "SUPPORT",
        "claim": "2026H1 净利润同比增长 42%。",
        "summary": "利润增速继续高于收入增速，支持盈利能力改善。",
        "source_type": "FINANCIAL_SNAPSHOT", "source_id": "financial_2026h1",
        "confidence": "HIGH", "created_by": "HUMAN", "data_as_of": "2026-06-30",
        "metadata": {"source_hash": "hash-1"},
    }
    payload.update(overrides)
    return service.create_evidence(**payload)


def services(tmp_path: Path) -> tuple[CompanyThesisService, CompanyThesisEvidenceService]:
    db_path = tmp_path / "research.db"
    return CompanyThesisService(db_path=db_path), CompanyThesisEvidenceService(db_path=db_path)


def test_support_challenge_neutral_and_version_binding(tmp_path: Path) -> None:
    thesis_service, evidence_service = services(tmp_path)
    try:
        first = create_thesis(thesis_service)
        support = create_evidence(evidence_service, first["thesis_id"])
        challenge = create_evidence(
            evidence_service, first["thesis_id"], evidence_type="RISK", effect="CHALLENGE",
            claim="主要客户资本开支下滑。", summary="客户预算变化挑战订单持续性。",
            source_type="EXTERNAL", source_id=None, source_ref="https://example.test/customer-capex",
            source_title="客户资本开支公告", confidence="MEDIUM",
        )
        neutral = create_evidence(
            evidence_service, first["thesis_id"], evidence_type="MANAGEMENT", effect="NEUTRAL",
            claim="公司披露新的治理安排。", summary="与长期研究相关，但尚不能判定方向。",
            source_type="MANUAL", source_id=None, confidence="LOW",
        )
        assert {support["effect"], challenge["effect"], neutral["effect"]} == {"SUPPORT", "CHALLENGE", "NEUTRAL"}
        assert all(item["thesis_id"] == first["thesis_id"] for item in evidence_service.repository.list_evidence_for_thesis(first["thesis_id"]))

        second = thesis_service.create_new_version(
            market="CN", stock_code="600001.SH", title="修订 Thesis", core_thesis="新的研究判断快照。",
            status="UNCHANGED", confidence="MEDIUM", invalid_conditions=[],
            change_reason="人工复核", updated_by="HUMAN",
        )
        assert evidence_service.repository.list_evidence_for_thesis(second["thesis_id"]) == []
    finally:
        evidence_service.close()
        thesis_service.close()


def test_invalid_values_source_and_company_mismatch_are_rejected(tmp_path: Path) -> None:
    thesis_service, evidence_service = services(tmp_path)
    try:
        thesis = create_thesis(thesis_service)
        with pytest.raises(ValueError, match="invalid evidence_type"):
            create_evidence(evidence_service, thesis["thesis_id"], evidence_type="BUY")
        with pytest.raises(ValueError, match="invalid effect"):
            create_evidence(evidence_service, thesis["thesis_id"], effect="BULLISH")
        with pytest.raises(ValueError, match="invalid confidence"):
            create_evidence(evidence_service, thesis["thesis_id"], confidence="CERTAIN")
        with pytest.raises(ValueError, match="source_id, source_ref or source_title"):
            create_evidence(evidence_service, thesis["thesis_id"], source_id=None)
        with pytest.raises(ValueError, match="MANUAL evidence must"):
            create_evidence(evidence_service, thesis["thesis_id"], source_type="MANUAL", source_id=None,
                            created_by="AGENT_FINANCIAL")
        with pytest.raises(ValueError, match="must match thesis company"):
            create_evidence(evidence_service, thesis["thesis_id"], market="CN", stock_code="600002.SH")
        with pytest.raises(KeyError, match="thesis not found"):
            create_evidence(evidence_service, "thesis_missing")
    finally:
        evidence_service.close()
        thesis_service.close()


def test_deactivation_preserves_row_and_active_summary(tmp_path: Path) -> None:
    thesis_service, evidence_service = services(tmp_path)
    try:
        thesis = create_thesis(thesis_service)
        support = create_evidence(evidence_service, thesis["thesis_id"])
        challenge = create_evidence(
            evidence_service, thesis["thesis_id"], evidence_type="RISK", effect="CHALLENGE",
            claim="行业供需转弱。", summary="景气下行挑战持续增长。", source_type="TDX",
            source_id="tdx:industry:I1", confidence="MEDIUM",
        )
        neutral = create_evidence(
            evidence_service, thesis["thesis_id"], evidence_type="OTHER", effect="NEUTRAL",
            claim="信息仍待进一步核验。", summary="暂不改变研究判断。", source_type="MANUAL",
            source_id=None, confidence="LOW",
        )
        deactivated = evidence_service.deactivate_evidence(challenge["evidence_id"], "源数据修正")
        assert deactivated["is_active"] is False
        assert deactivated["deactivation_reason"] == "源数据修正"
        assert evidence_service.repository.get_evidence_by_id(challenge["evidence_id"]) is not None
        summary = evidence_service.get_evidence_summary(thesis["thesis_id"])
        assert summary == {"total": 3, "active": 2, "support": 1, "challenge": 0, "neutral": 1,
                           "by_type": {"FINANCIAL": 1, "OTHER": 1}}
        assert len(evidence_service.repository.list_active_evidence_for_thesis(thesis["thesis_id"])) == 2
        assert support["is_active"] is True and neutral["is_active"] is True
    finally:
        evidence_service.close()
        thesis_service.close()


def _leader(as_of: str, symbol: str, rank: int) -> dict:
    return {
        "as_of": as_of, "level1_code": "L1", "level1_name": "一级",
        "level2_code": "L2", "level2_name": "二级", "level3_code": "I1",
        "level3_name": "三级行业", "stock_code": symbol, "stock_name": symbol,
        "leader_rank": rank, "leader_score": 90 - rank, "leader_formula_version": "test",
        "component_scores": {}, "coverage": 1.0, "eligibility_status": "eligible",
        "eligibility_reasons": [], "metric_applicability_notes": [], "raw_features": {},
        "provenance_key": f"{as_of}:{symbol}",
    }


def _pool(store: Level3LeaderStore, key: str, as_of: str, rows: list[dict]) -> None:
    run = store.start_run(idempotency_key=key, as_of=as_of, catalog_as_of=as_of, formula_version="test")
    store.finish_run(run["id"], rows=rows, statistics={})
    store.materialize_pool(run["id"])


def test_evidence_survives_out_of_top2_and_does_not_mutate_thesis(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    thesis_service = CompanyThesisService(db_path=db_path)
    evidence_service = CompanyThesisEvidenceService(db_path=db_path)
    pool_store = Level3LeaderStore(db_path)
    try:
        thesis = create_thesis(thesis_service, status="UNCHANGED", confidence="MEDIUM")
        before = thesis_service.get_thesis_by_id(thesis["thesis_id"])
        created = create_evidence(evidence_service, thesis["thesis_id"])
        after = thesis_service.get_thesis_by_id(thesis["thesis_id"])
        assert (after["version"], after["status"], after["confidence"]) == (before["version"], before["status"], before["confidence"])
        _pool(pool_store, "one", "2026-08-17", [_leader("2026-08-17", "600001.SH", 1)])
        _pool(pool_store, "two", "2026-08-18", [_leader("2026-08-18", "600002.SH", 1)])
        assert evidence_service.repository.get_evidence_by_id(created["evidence_id"])["claim"] == "2026H1 净利润同比增长 42%。"
    finally:
        pool_store.close()
        evidence_service.close()
        thesis_service.close()


def test_api_returns_no_thesis_then_exposes_evidence_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    thesis_service = CompanyThesisService(db_path=db_path)
    evidence_service = CompanyThesisEvidenceService(db_path=db_path)
    monkeypatch.setattr(company_thesis_evidence_routes, "_service", lambda: evidence_service)
    app = FastAPI()
    register_company_thesis_evidence_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        no_thesis = client.get("/api/value/companies/600001.SH/thesis/evidence")
        assert no_thesis.status_code == 200
        assert no_thesis.json()["status"] == "THESIS_NOT_CREATED"
        thesis = create_thesis(thesis_service)
        body = {
            "evidence_type": "FINANCIAL", "effect": "SUPPORT", "claim": "利润增长。",
            "summary": "支持盈利改善。", "source_type": "FINANCIAL_SNAPSHOT",
            "source_id": "snapshot-1", "confidence": "HIGH",
        }
        created = client.post(f"/api/value/theses/{thesis['thesis_id']}/evidence", json=body)
        assert created.status_code == 201
        evidence_id = created.json()["evidence_id"]
        current = client.get("/api/value/companies/600001.SH/thesis/evidence")
        assert current.json()["summary"]["support"] == 1
        explicit = client.get(f"/api/value/theses/{thesis['thesis_id']}/evidence")
        assert explicit.status_code == 200 and explicit.json()["summary"]["total"] == 1
        inactive = client.patch(f"/api/value/thesis-evidence/{evidence_id}/deactivate", json={"reason": "源数据修正"})
        assert inactive.status_code == 200 and inactive.json()["is_active"] is False
    finally:
        evidence_service.close()
        thesis_service.close()

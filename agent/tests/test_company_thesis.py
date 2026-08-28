from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import company_thesis_routes
from src.api.company_thesis_routes import register_company_thesis_routes
from src.company_thesis.service import CompanyThesisService
from src.level3_leaders.store import Level3LeaderStore
from src.research_workspace.store import ResearchWorkspaceStore


def create(service: CompanyThesisService, **overrides):
    payload = {
        "market": "CN", "stock_code": "600001.SH", "title": "长期竞争力待验证",
        "core_thesis": "盈利质量、行业地位和现金流需要持续验证。",
        "status": "FORMING", "confidence": "LOW",
        "invalid_conditions": [{"condition": "核心产品验证持续失败", "status": "ACTIVE"}],
        "created_by": "HUMAN", "source_data_as_of": "2026-08-17",
    }
    payload.update(overrides)
    return service.create_initial_thesis(**payload)


def test_create_initial_thesis_and_round_trip_invalid_conditions(tmp_path: Path) -> None:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        created = create(service)
        assert created["version"] == 1
        assert created["is_current"] is True
        assert created["status"] == "FORMING"
        assert created["confidence"] == "LOW"
        assert created["invalid_conditions"] == [{"condition": "核心产品验证持续失败", "status": "ACTIVE"}]
        assert service.get_current_thesis("CN", "600001.SH")["thesis_id"] == created["thesis_id"]
        assert service.get_thesis_by_id(created["thesis_id"])["stock_code"] == "600001.SH"
    finally:
        service.close()


def test_current_constraint_and_versioning_keep_history(tmp_path: Path) -> None:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        first = create(service)
        with pytest.raises(ValueError, match="create new version instead"):
            create(service)
        second = service.create_new_version(
            market="CN", stock_code="600001.SH", title="长期竞争力得到部分验证",
            core_thesis="现金流和行业地位的新事实强化研究逻辑。",
            status="STRENGTHENING", confidence="MEDIUM", invalid_conditions=[],
            change_reason="新增财务快照显示经营质量改善", updated_by="SYSTEM",
            source_data_as_of="2026-08-18",
        )
        assert second["version"] == 2
        assert second["is_current"] is True
        assert second["change_reason"] == "新增财务快照显示经营质量改善"
        assert service.get_current_thesis("CN", "600001.SH")["thesis_id"] == second["thesis_id"]
        versions = service.list_thesis_versions("CN", "600001.SH")
        assert [item["version"] for item in versions] == [2, 1]
        assert versions[1]["is_current"] is False
        assert versions[1]["thesis_id"] == first["thesis_id"]
    finally:
        service.close()


def test_invalid_status_confidence_and_conditions_are_rejected(tmp_path: Path) -> None:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        with pytest.raises(ValueError, match="invalid thesis status"):
            create(service, status="BUY")
        with pytest.raises(ValueError, match="invalid thesis confidence"):
            create(service, confidence="CERTAIN")
        with pytest.raises(ValueError, match="requires condition"):
            create(service, invalid_conditions=[{"status": "ACTIVE"}])
        with pytest.raises(ValueError, match="HUMAN or SYSTEM"):
            create(service, created_by="AGENT_RESEARCH_LEAD")
    finally:
        service.close()


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


def test_thesis_survives_when_company_exits_top2(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    service = CompanyThesisService(db_path=db_path)
    pool_store = Level3LeaderStore(db_path)
    try:
        create(service)
        _pool(pool_store, "one", "2026-08-17", [_leader("2026-08-17", "600001.SH", 1)])
        _pool(pool_store, "two", "2026-08-18", [_leader("2026-08-18", "600002.SH", 1)])
        pool = pool_store.get_pool(pool_store.current_pool()["id"])
        assert any(item["stock_code"] == "600001.SH" and item["lifecycle_status"] == "OUT_OF_TOP2" for item in pool["members"])
        assert service.get_current_thesis("CN", "600001.SH")["status"] == "FORMING"
    finally:
        pool_store.close()
        service.close()


def test_dossier_is_not_touched_by_thesis_creation(tmp_path: Path) -> None:
    db_path = tmp_path / "research.db"
    workspace = ResearchWorkspaceStore(db_path)
    service = CompanyThesisService(db_path=db_path)
    try:
        workspace.upsert_company_dossier(
            market="HK", symbol="00700.HK", name="腾讯控股", exchange="HKEX",
            sector_code="HSI-IT", sector_name="资讯科技业", overview="旧摘要",
            bull_thesis="旧正向摘要", bear_thesis="旧反向摘要", metrics={}, catalysts=[], risks=[],
            data_as_of="2026-08-17", source_status="live",
        )
        before = workspace._conn.execute(
            "SELECT bull_thesis,bear_thesis,overview FROM company_dossiers WHERE market='HK' AND symbol='00700.HK'"
        ).fetchone()
        create(service, market="HK", stock_code="00700.HK")
        after = workspace._conn.execute(
            "SELECT bull_thesis,bear_thesis,overview FROM company_dossiers WHERE market='HK' AND symbol='00700.HK'"
        ).fetchone()
        assert tuple(after) == tuple(before)
    finally:
        service.close()
        workspace.close()


def test_api_returns_not_created_then_creates_and_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    monkeypatch.setattr(company_thesis_routes, "_service", lambda: service)
    app = FastAPI()
    register_company_thesis_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    try:
        missing = client.get("/api/value/companies/600001.SH/thesis")
        assert missing.status_code == 200
        assert missing.json() == {"status": "NOT_CREATED", "thesis": None}
        body = {
            "title": "初始 Thesis", "core_thesis": "持续研究经营质量。",
            "status": "FORMING", "confidence": "LOW",
            "invalid_conditions": [{"condition": "客户流失", "status": "ACTIVE"}],
        }
        created = client.post("/api/value/companies/600001.SH/thesis", json=body)
        assert created.status_code == 201
        assert client.post("/api/value/companies/600001.SH/thesis", json=body).status_code == 409
        version = client.post("/api/value/companies/600001.SH/thesis/version", json={
            **body, "status": "UNCHANGED", "confidence": "MEDIUM", "change_reason": "人工复核后维持逻辑",
        })
        assert version.status_code == 201
        assert version.json()["version"] == 2
        assert client.get("/api/value/companies/600001.SH/thesis/versions").json()["total"] == 2
    finally:
        service.close()

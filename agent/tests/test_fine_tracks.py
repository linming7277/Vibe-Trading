from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import fine_track_routes
from src.api.fine_track_routes import register_fine_track_routes
from src.fine_tracks.classifier import validate_batch_result
from src.fine_tracks.industry_catalog import TdxResearchIndustryCatalog
from src.fine_tracks.models import TRACK_CLASSIFICATION_VERSION, stable_hash
from src.fine_tracks.service import FineTrackService
from src.fine_tracks.store import FineTrackStore
from src.research_tasks.store import ResearchTaskStore
from src.tdx_data.store import TdxDataStore


INDUSTRY = {
    "industry_code": "881320.SH", "industry_name": "半导体材料",
    "industry_level": "TDX_RESEARCH_TERMINAL", "raw_industry_level": "TDX_RESEARCH_LEVEL_3",
    "tdx_class_code": "X400101", "level": 3, "is_terminal": True,
    "level1_code": "881315.SH", "level1_name": "电子",
    "level2_code": "881319.SH", "level2_name": "半导体", "member_count": 1,
    "source": {}, "as_of": "2026-08-17T00:00:00+00:00",
}


def profile(code: str, business: str, *, status: str = "REAL") -> dict[str, Any]:
    row = {
        "stock_code": code, "stock_name": f"公司{code[:2]}",
        "third_level_industry_code": INDUSTRY["industry_code"],
        "third_level_industry_name": INDUSTRY["industry_name"],
        "business_scope": "", "main_business": business, "company_description": "", "main_products": "",
        "source": [{"provider": "TDX"}], "updated_at": "2026-08-17T00:00:00+00:00",
        "data_status": status,
    }
    row["source_hash"] = stable_hash([code, business, status])
    return row


class FakeProfiles:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def industries(self) -> list[dict[str, Any]]:
        return [INDUSTRY]

    def industry(self, code: str) -> dict[str, Any]:
        if code.upper() != INDUSTRY["industry_code"]:
            raise KeyError(code)
        return INDUSTRY

    def profiles(self, code: str) -> list[dict[str, Any]]:
        self.industry(code)
        return self.rows


class FakeRuntime:
    def __init__(self, outputs: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self.outputs = list(outputs or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.outputs.pop(0)


def output(track_name: str = "半导体设备", *, secondary: bool = False) -> dict[str, Any]:
    companies = [{
        "stock_code": "600001.SH", "membership_type": "PRIMARY", "confidence": .91,
        "reason": "主营晶圆制造设备，收入主要来自刻蚀与薄膜设备。",
    }]
    if secondary:
        companies.append({
            "stock_code": "600001.SH", "membership_type": "SECONDARY", "confidence": .72,
            "reason": "同时提供配套工艺零部件。",
        })
    return {
        "industry_code": INDUSTRY["industry_code"], "industry_name": INDUSTRY["industry_name"],
        "tracks": [{"track_name": track_name, "description": "面向晶圆制造环节提供关键设备。", "companies": companies}],
        "unclassified": [],
    }


@pytest.fixture()
def configured_store(tmp_path: Path):
    db = tmp_path / "research.db"
    fine = FineTrackStore(db)
    agents = ResearchTaskStore(db)
    agents.update_config("track_classifier", "openai", "gpt-5.5", True)
    yield db, fine, agents
    agents.close()
    fine.close()


def make_service(db: Path, rows: list[dict[str, Any]], runtime: FakeRuntime) -> FineTrackService:
    return FineTrackService(
        store=FineTrackStore(db), profiles=FakeProfiles(rows), runtime=runtime,
        agent_store=ResearchTaskStore(db), batch_size=50,
    )


def test_missing_business_is_insufficient_and_never_sent_to_model(configured_store) -> None:
    db, _, _ = configured_store
    runtime = FakeRuntime()
    service = make_service(db, [profile("600001.SH", "", status="MISSING")], runtime)
    try:
        result = service.classify_industry(INDUSTRY["industry_code"])
        assert result["status"] == "COMPLETED"
        assert runtime.calls == []
        assert service.tracks(INDUSTRY["industry_code"])["unclassified"][0]["classification_status"] == "INSUFFICIENT_DATA"
    finally:
        service.close()


def test_classification_persists_reason_confidence_and_is_idempotent(configured_store) -> None:
    db, _, _ = configured_store
    runtime = FakeRuntime([output()])
    service = make_service(db, [profile("600001.SH", "晶圆刻蚀、薄膜沉积设备研发销售")], runtime)
    try:
        first = service.classify_industry(INDUSTRY["industry_code"])
        second = service.classify_industry(INDUSTRY["industry_code"])
        assert first["status"] == second["status"] == "COMPLETED"
        assert second["idempotent_reuse"] is True and len(runtime.calls) == 1
        membership = service.tracks(INDUSTRY["industry_code"])["items"][0]["companies"][0]
        assert membership["membership_type"] == "PRIMARY"
        assert membership["confidence_level"] == "HIGH"
        assert membership["review_status"] == "AUTO_ACCEPTED"
        assert "晶圆" in membership["classification_reason"]
        assert membership["classification_version"] == TRACK_CLASSIFICATION_VERSION
    finally:
        service.close()


def test_existing_track_is_reused_for_normalized_synonym(configured_store) -> None:
    db, _, _ = configured_store
    rows = [profile("600001.SH", "晶圆设备研发销售")]
    runtime = FakeRuntime([output("半导体设备"), output("半导体制造设备")])
    service = make_service(db, rows, runtime)
    try:
        service.classify_industry(INDUSTRY["industry_code"])
        rows[0] = profile("600001.SH", "晶圆设备研发销售及技术服务")
        service.classify_industry(INDUSTRY["industry_code"])
        assert len(service.tracks(INDUSTRY["industry_code"])["items"]) == 1
        assert runtime.calls[1]["payload"]["existing_tracks"][0]["track_name"] == "半导体设备"
    finally:
        service.close()


def test_manual_confirmed_membership_is_protected_and_gets_suggestion(configured_store) -> None:
    db, _, _ = configured_store
    rows = [profile("600001.SH", "晶圆设备研发销售")]
    runtime = FakeRuntime([output("半导体设备"), output("工艺零部件")])
    service = make_service(db, rows, runtime)
    try:
        service.classify_industry(INDUSTRY["industry_code"])
        service.store._conn.execute("UPDATE company_track_memberships SET review_status='MANUAL_CONFIRMED'")
        service.store._conn.commit()
        rows[0] = profile("600001.SH", "晶圆设备与关键工艺零部件研发销售")
        service.classify_industry(INDUSTRY["industry_code"])
        tracks = service.tracks(INDUSTRY["industry_code"])
        membership = next(row for track in tracks["items"] for row in track["companies"])
        assert membership["review_status"] == "MANUAL_CONFIRMED"
        assert membership["track_name"] == "半导体设备"
        assert len(tracks["new_suggestions"]) == 1
    finally:
        service.close()


def test_one_primary_and_up_to_two_secondary_are_supported(configured_store) -> None:
    result = output(secondary=True)
    result["tracks"].extend([
        {"track_name": "设备零部件", "description": "提供设备关键零部件与配套服务。", "companies": [{
            "stock_code": "600001.SH", "membership_type": "SECONDARY", "confidence": .66, "reason": "提供真空零部件。",
        }]},
    ])
    clean = validate_batch_result(result, industry=INDUSTRY, company_codes={"600001.SH"})
    assert sum(len(row["companies"]) for row in clean["tracks"]) == 3
    result["tracks"].append({"track_name": "第三副业", "description": "第三个次要业务归属用于越界测试。", "companies": [{
        "stock_code": "600001.SH", "membership_type": "SECONDARY", "confidence": .61, "reason": "还有第三项业务。",
    }]})
    with pytest.raises(ValueError, match="membership limit"):
        validate_batch_result(result, industry=INDUSTRY, company_codes={"600001.SH"})


def test_model_failure_marks_run_failed_without_fake_memberships(configured_store) -> None:
    db, _, _ = configured_store
    runtime = FakeRuntime(error=RuntimeError("provider unavailable"))
    service = make_service(db, [profile("600001.SH", "晶圆设备研发销售")], runtime)
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            service.classify_industry(INDUSTRY["industry_code"])
        assert service.store._conn.execute("SELECT status FROM fine_track_classification_runs").fetchone()[0] == "FAILED"
        assert service.store._conn.execute("SELECT COUNT(*) FROM company_track_memberships").fetchone()[0] == 0
    finally:
        service.close()


def test_ollama_is_rejected_for_track_classification(configured_store) -> None:
    db, _, agents = configured_store
    agents.update_config("track_classifier", "ollama", "qwen3.5:9b", True)
    service = make_service(db, [profile("600001.SH", "晶圆设备研发销售")], FakeRuntime([output()]))
    try:
        with pytest.raises(ValueError, match="不允许使用 Ollama"):
            service.classify_industry(INDUSTRY["industry_code"])
    finally:
        service.close()


def test_database_business_text_classification_never_calls_model(configured_store) -> None:
    db, _, _ = configured_store
    runtime = FakeRuntime(error=AssertionError("model must not be called"))
    rows = [
        profile("600001.SH", "晶圆刻蚀设备,薄膜沉积设备"),
        profile("600002.SH", "模拟芯片设计"),
        profile("600003.SH", "集成电路封装测试"),
        profile("600004.SH", "功率半导体器件"),
    ]
    service = make_service(db, rows, runtime)
    try:
        result = service.classify_industry_from_database(INDUSTRY["industry_code"])
        assert result["status"] == "COMPLETED" and runtime.calls == []
        memberships = [row for track in result["tracks"]["items"] for row in track["companies"]]
        assert {row["stock_code"] for row in memberships} == {row["stock_code"] for row in rows}
        assert all(row["review_status"] == "NEEDS_REVIEW" for row in memberships)
        assert all("通达信主营业务" in row["classification_reason"] for row in memberships)
    finally:
        service.close()


def test_tdx_terminal_catalog_preserves_real_hierarchy_and_memberships(tmp_path: Path) -> None:
    cache = tmp_path / "T0002" / "hq_cache"
    cache.mkdir(parents=True)
    (cache / "tdxzs3.cfg").write_bytes("\r\n".join([
        "电子|881315|12|1|0|X40",
        "半导体|881319|12|1|0|X4001",
        "半导体材料|881320|12|1|1|X400101",
        "证券|881493|12|1|1|X5001",
    ]).encode("gbk"))
    (cache / "tdxhy.cfg").write_bytes("\r\n".join([
        "1|600001|T1001|||X400101",
        "0|300001|T1001|||X400101",
        "2|830001|T1001|||X5001",
    ]).encode("gbk"))
    catalog = TdxResearchIndustryCatalog(tmp_path)
    terminal = catalog.terminal_industries()
    material = next(row for row in terminal if row["industry_code"] == "881320.SH")
    assert material["level1_name"] == "电子"
    assert material["level2_name"] == "半导体"
    assert material["raw_industry_level"] == "TDX_RESEARCH_LEVEL_3"
    assert catalog.members("881320.SH") == ["300001.SZ", "600001.SH"]
    leaf = next(row for row in terminal if row["industry_code"] == "881493.SH")
    assert leaf["raw_industry_level"] == "TDX_RESEARCH_LEVEL_2_LEAF"
    assert catalog.members("881493.SH") == ["830001.BJ"]
    store = TdxDataStore(tmp_path / "tdx_data.db")
    try:
        counts = catalog.sync_cache(store)
        assert counts == {"levels": 4, "level1": 1, "level2": 2, "level3": 1, "terminal": 2, "memberships": 3}
        assert store.count("research_industry_hierarchy") == 4
        assert store.count("research_terminal_industry_members") == 3
    finally:
        store.close()


def test_reused_track_records_latest_classification_version(configured_store) -> None:
    _, fine, _ = configured_store
    rows = [profile("600001.SH", "硅片材料研发销售")]
    fine.upsert_profiles(rows)
    fine.apply_classification(
        industry=INDUSTRY, profiles=rows, result=output("硅片材料"),
        version="fine-track-db-old", profile_hash="old", classification_source="database",
    )
    fine.apply_classification(
        industry=INDUSTRY, profiles=rows, result=output("硅片材料"),
        version="fine-track-db-new", profile_hash="new", classification_source="database",
    )
    assert fine.list_tracks(INDUSTRY["industry_code"])[0]["classification_version"] == "fine-track-db-new"


def test_database_classifier_does_not_repeat_parent_as_fake_fine_track(configured_store) -> None:
    db, _, _ = configured_store
    service = make_service(
        db,
        [profile("600001.SH", "半导体材料")],
        FakeRuntime(error=AssertionError("model must not be called")),
    )
    try:
        result = service.classify_industry_from_database(INDUSTRY["industry_code"])
        assert result["tracks"]["items"] == []
        assert result["tracks"]["unclassified"][0]["classification_status"] == "UNCLASSIFIED"
        assert "未强行生成赛道" in result["tracks"]["unclassified"][0]["reason"]
    finally:
        service.close()


def test_minimum_authenticated_api_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class ApiService:
        def industries(self): return {"items": [INDUSTRY], "total": 1, "source": {}}
        def companies(self, code): return {"industry": INDUSTRY, "items": [], "total": 0}
        def tracks(self, code): return {"industry": INDUSTRY, "items": [], "total": 0}
        def classify_industry(self, code): return {"status": "COMPLETED", "run_id": "fine_test"}
        def classify_industry_from_database(self, code): return {"status": "COMPLETED", "run_id": "fine_db_test"}

    monkeypatch.setattr(fine_track_routes, "get_fine_track_service", lambda: ApiService())
    app = FastAPI()
    register_fine_track_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    base = f"/api/value/industries/{INDUSTRY['industry_code']}"
    assert client.get("/api/value/industries").json()["total"] == 1
    assert client.get(f"{base}/companies").status_code == 200
    assert client.get(f"{base}/tracks").status_code == 200
    assert client.post(f"{base}/classify-tracks").json()["run_id"] == "fine_db_test"

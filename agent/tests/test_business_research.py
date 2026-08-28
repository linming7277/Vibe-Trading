from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.business_research_routes import register_business_research_routes
from src.business_research.citations import BusinessClaimCitationResolver
from src.business_research.service import (
    BUSINESS_RESEARCH_VERSION,
    BusinessClaimValidationError,
    BusinessResearchService,
)
from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.tdx_data.store import TdxDataStore


def profile(*, main_business: str = "设备,耗材", main_products: str = "",
            business_scope: str = "", description: str = "",
            source_hash: str = "profile-v1") -> dict:
    values = {
        "main_business": main_business,
        "main_products": main_products,
        "business_scope": business_scope,
        "company_description": description,
    }
    fields = [key for key, value in values.items() if value]
    return {
        "stock_code": "000001.SZ", "stock_name": "测试公司",
        **values,
        "source": [{
            "provider": "通达信客户端缓存", "dataset": "fundamentals",
            "record_key": "000001.SZ", "data_as_of": "2026-08-21T10:00:00+08:00",
            "fields": fields,
        }],
        "updated_at": "2026-08-21T10:00:00+08:00", "data_status": "REAL",
        "source_hash": source_hash,
    }


class FakeProfiles:
    def __init__(self, value: dict | None) -> None:
        self.value = value

    def profile(self, stock_code: str):
        return self.value


class FakeConfigs:
    item = {
        "role": "financial_analyst", "provider": "openai", "model": "test-model",
        "enabled": True, "base_url": "http://provider.test/v1", "api_key": "secret",
    }

    def get_config(self, role: str):
        return dict(self.item)

    def list_configs(self):
        return [dict(self.item)]

    def close(self):
        return None


class FakeDisclosureStore:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])

    def list_materials(self, _stock_code: str):
        return list(self.rows)

    def close(self):
        return None


class GoodRuntime:
    def invoke_with_connection(self, **kwargs):
        manifest = kwargs["payload"]["business_source_manifest"]
        main = next(key for key in manifest if key.endswith("MAIN_BUSINESS") and "CURRENT" in key)
        return {
            "summary": "公司目前主要做设备和耗材。现有资料没有产品收入占比，还要继续观察各类产品的收入和利润。",
            "claims": [
                {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "公司目前披露的主营业务包括设备和耗材。", "source_keys": [main], "confidence": "HIGH"},
                {"type": "FACT", "topic": "PRODUCT", "text": "现有资料列出了设备和耗材，但没有说明哪一类贡献最大。", "source_keys": [main], "confidence": "MEDIUM"},
                {"type": "INFERENCE", "topic": "BUSINESS_MODEL", "text": "从现有业务名称看，公司主要通过销售设备和耗材获得收入；具体销售方式仍需补充资料。", "source_keys": [main], "confidence": "MEDIUM"},
                {"type": "UNKNOWN", "topic": "BUSINESS_CHANGE", "text": "缺少前后两期可比资料，目前无法确认经营范围是否发生变化。", "source_keys": [], "confidence": "LOW"},
            ],
        }


class CapturingRuntime(GoodRuntime):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke_with_connection(self, **kwargs):
        self.calls.append(dict(kwargs))
        return super().invoke_with_connection(**kwargs)


def make_service(tmp_path: Path, value: dict | None = None) -> BusinessResearchService:
    service = BusinessResearchService(
        store=BusinessResearchStore(tmp_path / "research.db"),
        profiles=FakeProfiles(profile() if value is None else value),
        config_store=FakeConfigs(), runtime=GoodRuntime(), disclosure_store=FakeDisclosureStore(),
    )
    service._agent_config = lambda: (dict(FakeConfigs.item), True)  # type: ignore[method-assign]
    return service


def test_company_business_profile_reuses_tdx_main_business(tmp_path: Path) -> None:
    store = TdxDataStore(tmp_path / "tdx.db")
    store.upsert_records("securities", [{"key": "000001.SZ", "name": "测试公司", "payload": {}}])
    store.upsert_records("fundamentals", [{
        "key": "000001.SZ", "name": "测试公司",
        "payload": {"main_business": "工业设备,配套耗材", "extended_raw": {"MainProducts": "设备,耗材"}},
    }])
    service = CompanyBusinessProfileService(
        tdx=SimpleNamespace(store=store), catalog=SimpleNamespace(),
    )
    result = service.profile("000001.SZ")
    assert result is not None
    assert result["main_business"] == "工业设备,配套耗材"
    assert result["main_products"] == "设备,耗材"
    assert result["data_status"] == "REAL"
    assert result["source"][0]["record_key"] == "000001.SZ"


def test_prepare_extracts_main_business_products_and_honest_unknowns(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    result = service.prepare("000001.SZ")
    assert result["main_business"] == "设备,耗材"
    assert result["products"] == ["设备", "耗材"]
    assert result["data_quality"]["field_statuses"] == {
        "main_business": "READY", "products": "PARTIAL",
        "business_model": "MISSING", "business_changes": "MISSING",
    }
    assert "没有产品收入占比" in result["product_note"]
    assert result["business_model"] == "UNKNOWN"
    assert result["business_changes"][0].startswith("UNKNOWN")
    assert result["module_version"] == BUSINESS_RESEARCH_VERSION


def test_missing_business_data_returns_unknown_not_fabrication(tmp_path: Path) -> None:
    service = make_service(tmp_path, profile(main_business="", main_products="", source_hash="empty"))
    result = service.prepare("000001.SZ")
    assert result["main_business"] == "UNKNOWN"
    assert result["products"] == []
    assert result["data_quality"]["status"] == "MISSING"
    assert set(result["data_quality"]["missing_fields"]) == {
        "main_business", "products", "business_model", "business_changes",
    }
    analyzed = service.analyze("000001.SZ", force=True)
    assert analyzed["analysis_status"] == "COMPLETED"
    assert {item["type"] for item in analyzed["analysis"]["claims"]} == {"UNKNOWN"}
    assert analyzed["traceability_status"] == "NOT_APPLICABLE"


def test_analyze_generates_plain_sourced_business_claims_and_citations(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    result = service.analyze("000001.SZ", force=True)
    assert result["analysis_status"] == "COMPLETED"
    assert result["analysis"]["analysis_metadata"]["quality_status"] == "STRUCTURED"
    assert {item["topic"] for item in result["analysis"]["claims"]} == {
        "MAIN_BUSINESS", "PRODUCT", "BUSINESS_MODEL", "BUSINESS_CHANGE",
    }
    assert result["traceability_status"] == "COMPLETE"
    sourced = [item for item in result["analysis"]["claims"] if item["type"] != "UNKNOWN"]
    assert all(item["source_keys"] and item["citations"][0]["status"] == "RESOLVED" for item in sourced)
    assert all("买入" not in item["text"] for item in result["analysis"]["claims"])


def test_business_analysis_requests_a_bounded_output_budget(tmp_path: Path) -> None:
    runtime = CapturingRuntime()
    service = BusinessResearchService(
        store=BusinessResearchStore(tmp_path / "research.db"), profiles=FakeProfiles(profile()),
        config_store=FakeConfigs(), runtime=runtime, disclosure_store=FakeDisclosureStore(),
    )
    service._agent_config = lambda: (dict(FakeConfigs.item), True)  # type: ignore[method-assign]
    service.analyze("000001.SZ", force=True)
    assert runtime.calls and runtime.calls[0]["max_tokens"] == 4096
    assert runtime.calls[0]["extra_body"] is None


def test_business_change_requires_current_and_previous_sources() -> None:
    manifest = {
        "BUSINESS_CURRENT_MAIN_BUSINESS": {"value": "设备", "profile_role": "CURRENT"},
        "BUSINESS_PREVIOUS_MAIN_BUSINESS": {"value": "软件", "profile_role": "PREVIOUS"},
    }
    valid = {
        "summary": "公司披露的主营业务从软件变为设备，还要继续核对收入结构。",
        "claims": [{
            "type": "FACT", "topic": "BUSINESS_CHANGE", "text": "主营业务原文从软件变为设备。",
            "source_keys": list(manifest), "confidence": "HIGH",
        }],
    }
    assert BusinessResearchService.validate_claims(valid, manifest)["claims"][0]["topic"] == "BUSINESS_CHANGE"
    valid["claims"][0]["source_keys"] = ["BUSINESS_CURRENT_MAIN_BUSINESS"]
    with pytest.raises(BusinessClaimValidationError) as caught:
        BusinessResearchService.validate_claims(valid, manifest)
    assert caught.value.code == "CHANGE_WITHOUT_COMPARISON"


@pytest.mark.parametrize(
    ("claim", "code"),
    [
        ({"type": "FACT", "topic": "PRODUCT", "text": "公司销售设备。", "source_keys": [], "confidence": "HIGH"}, "FACT_WITHOUT_SOURCE"),
        ({"type": "INFERENCE", "topic": "BUSINESS_MODEL", "text": "公司通过销售设备赚钱。", "source_keys": [], "confidence": "MEDIUM"}, "INFERENCE_WITHOUT_SOURCE"),
        ({"type": "FACT", "topic": "PRODUCT", "text": "设备收入占比最高。", "source_keys": ["SOURCE"], "confidence": "HIGH"}, "UNSUPPORTED_PRODUCT_SHARE"),
        ({"type": "FACT", "topic": "PRODUCT", "text": "设备占收入 80%。", "source_keys": ["SOURCE"], "confidence": "HIGH"}, "NUMERIC_MISMATCH"),
        ({"type": "INFERENCE", "topic": "BUSINESS_MODEL", "text": "客户集中度上升。", "source_keys": ["SOURCE"], "confidence": "LOW"}, "JARGON_WITHOUT_EXPLANATION"),
        ({"type": "FACT", "topic": "PRODUCT", "text": "建议买入设备公司。", "source_keys": ["SOURCE"], "confidence": "HIGH"}, "TRADING_LANGUAGE"),
    ],
)
def test_validator_enforces_sources_plain_language_no_share_and_no_trading(claim: dict, code: str) -> None:
    manifest = {"SOURCE": {"value": "设备,耗材", "profile_role": "CURRENT"}}
    with pytest.raises(BusinessClaimValidationError) as caught:
        BusinessResearchService.validate_claims({"summary": "公司销售设备和耗材。", "claims": [claim]}, manifest)
    assert caught.value.code == code


def test_jargon_is_allowed_only_when_explained() -> None:
    result = BusinessResearchService.validate_claims({
        "summary": "需要观察客户集中度（简单说就是公司是否依赖少数几个客户）。",
        "claims": [{
            "type": "INFERENCE", "topic": "BUSINESS_MODEL",
            "text": "客户集中度（简单说就是公司是否依赖少数几个客户）需要继续核实。",
            "source_keys": ["SOURCE"], "confidence": "LOW",
        }],
    }, {"SOURCE": {"value": "主要客户资料", "profile_role": "CURRENT"}})
    assert result["claims"][0]["type"] == "INFERENCE"


def test_citation_resolver_handles_resolved_and_unknown_claims() -> None:
    result = BusinessClaimCitationResolver().resolve_snapshot({
        "sources": {"SOURCE": {
            "source_type": "TDX_BUSINESS_PROFILE", "source_id": "fundamentals:000001.SZ",
            "data_as_of": "2026-08-21", "field": "main_business", "value": "设备",
            "source_hash": "hash", "profile_role": "CURRENT",
        }},
        "analysis": {"claims": [
            {"type": "FACT", "source_keys": ["SOURCE"]},
            {"type": "UNKNOWN", "source_keys": []},
        ]},
    })
    assert result["traceability_status"] == "COMPLETE"
    assert result["analysis"]["claims"][0]["citations"][0]["field"] == "main_business"


def test_runtime_remains_provider_and_model_agnostic() -> None:
    source = inspect.getsource(BusinessResearchService)
    assert "if model ==" not in source
    assert "if provider ==" not in source


def test_prepare_exposes_persisted_official_disclosure_as_cited_source(tmp_path: Path) -> None:
    disclosure = FakeDisclosureStore([{
        "id": "material-1", "status": "FOUND", "material_type": "CUSTOMER_CONCENTRATION",
        "report_kind": "ANNUAL", "report_period": "2025-12-31", "announcement_date": "2026-04-02",
        "announcement_id": "announcement-1", "source_url": "https://official.example/report.pdf", "text_sha256": "hash-1",
        "excerpts": [{"page": 88, "text": "前五名客户销售额及占比见本报告披露。"}],
    }])
    service = BusinessResearchService(
        store=BusinessResearchStore(tmp_path / "research.db"), profiles=FakeProfiles(profile()),
        config_store=FakeConfigs(), runtime=GoodRuntime(), disclosure_store=disclosure,  # type: ignore[arg-type]
    )
    service._agent_config = lambda: (dict(FakeConfigs.item), True)  # type: ignore[method-assign]
    result = service.prepare("000001.SZ")
    key = "DISCLOSURE_CURRENT_CUSTOMER_CONCENTRATION_ANNUAL"
    assert result["sources"][key]["source_type"] == "CNINFO_PERIODIC_REPORT"
    assert result["sources"][key]["pages"] == [88]
    assert result["data_quality"]["disclosure_field_statuses"]["customer_concentration_source"] == "READY"


def test_business_manifest_uses_only_newest_comparable_operating_report_pair(tmp_path: Path) -> None:
    rows = [
        {"id": "new", "status": "FOUND", "material_type": "BUSINESS_PRODUCT_STRUCTURE", "report_kind": "SEMIANNUAL",
         "report_period": "2026-06-30", "announcement_date": "2026-08-23", "announcement_id": "new", "source_url": "https://x/new",
         "text_sha256": "new", "excerpts": [{"page": 1, "text": "2026 年产品结构"}]},
        {"id": "old", "status": "FOUND", "material_type": "BUSINESS_PRODUCT_STRUCTURE", "report_kind": "SEMIANNUAL",
         "report_period": "2025-06-30", "announcement_date": "2025-08-27", "announcement_id": "old", "source_url": "https://x/old",
         "text_sha256": "old", "excerpts": [{"page": 1, "text": "2025 年产品结构"}]},
        {"id": "annual", "status": "FOUND", "material_type": "BUSINESS_PRODUCT_STRUCTURE", "report_kind": "ANNUAL",
         "report_period": "2025-12-31", "announcement_date": "2026-04-02", "announcement_id": "annual", "source_url": "https://x/annual",
         "text_sha256": "annual", "excerpts": [{"page": 1, "text": "年报产品结构"}]},
        {"id": "debt", "status": "FOUND", "material_type": "DEBT_MATURITY", "report_kind": "SEMIANNUAL",
         "report_period": "2026-06-30", "announcement_date": "2026-08-23", "announcement_id": "debt", "source_url": "https://x/debt",
         "text_sha256": "debt", "excerpts": [{"page": 1, "text": "债务到期"}]},
    ]
    service = BusinessResearchService(
        store=BusinessResearchStore(tmp_path / "research.db"), profiles=FakeProfiles(profile()),
        config_store=FakeConfigs(), runtime=GoodRuntime(), disclosure_store=FakeDisclosureStore(rows),  # type: ignore[arg-type]
    )
    manifest, _ = service._disclosure_manifest("000001.SZ")
    assert set(manifest) == {
        "DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL",
        "DISCLOSURE_PREVIOUS_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL",
    }


def test_business_api_get_and_analyze(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    import src.api.business_research_routes as routes
    monkeypatch.setattr(routes, "get_business_research_service", lambda: service)
    app = FastAPI()
    register_business_research_routes(app, lambda: True)
    client = TestClient(app)
    read = client.get("/api/value/companies/000001.SZ/business-research")
    assert read.status_code == 200
    assert read.json()["main_business"] == "设备,耗材"
    analyzed = client.post("/api/value/companies/000001.SZ/business-research/analyze", json={"force": True})
    assert analyzed.status_code == 200
    assert analyzed.json()["traceability_status"] == "COMPLETE"

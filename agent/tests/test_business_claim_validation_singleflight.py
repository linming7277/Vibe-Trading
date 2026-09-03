"""Per-claim validation + single-flight business analysis (2026-09-02 V1).

One violating claim must reject itself, never the batch; PARTIAL is a legal
terminal state reused by every caller; the same source fingerprint admits at
most one concurrent model analysis.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import src.business_research.service as business_module
from src.business_research.service import BusinessResearchService
from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.structured_output.runtime import StructuredOutputResult

CODE = "600001.SH"
AS_OF = "2026-09-01"

MANIFEST = {
    "BUSINESS_CURRENT_MAIN_BUSINESS": {
        "profile_role": "CURRENT", "value": "公司主营精密制造，业务方向保持稳定。"},
    "DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL": {
        "profile_role": "CURRENT",
        "value": "本期产品结构：产品甲收入占比百分之五十五；上期产品结构披露产品甲收入占比百分之六十。"},
    "DISCLOSURE_PREVIOUS_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL": {
        "profile_role": "PREVIOUS", "value": "上期产品结构披露产品甲收入占比百分之六十。"},
    "DISCLOSURE_CURRENT_CUSTOMER_CONCENTRATION_ANNUAL": {
        "profile_role": "CURRENT", "value": "按年度报告披露，前五名客户合计销售占年度销售总额百分之三十五。"},
}


def _valid_claim(topic: str = "MAIN_BUSINESS") -> dict:
    return {"type": "FACT", "topic": topic, "text": "公司主营精密制造，业务方向保持稳定。",
            "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "MEDIUM"}


def _validate(claims: list[dict], summary: str = "公司经营结构稳定。"):
    return BusinessResearchService.validate_claims({"summary": summary, "claims": claims}, MANIFEST)


# ---------------------------------------------------------------------------
# Per-claim validation
# ---------------------------------------------------------------------------

def test_one_invalid_claim_rejects_only_itself() -> None:
    claims = [_valid_claim() for _ in range(7)]
    claims.append({"type": "FACT", "topic": "BUSINESS_CHANGE",
                   "text": "本期收入较上期出现明显增长。",
                   "source_keys": ["DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL"],
                   "confidence": "MEDIUM"})
    out = _validate(claims)
    assert len(out["claims"]) == 7
    assert len(out["rejected_claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "CHANGE_WITHOUT_COMPARISON"
    assert out["rejected_claims"][0]["claim_index"] == 7


def test_missing_field_rejects_only_that_claim() -> None:
    broken = {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "缺少置信度字段的结论。", "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"]}
    out = _validate([_valid_claim(), broken, _valid_claim()])
    assert len(out["claims"]) == 2
    assert out["rejected_claims"][0]["reason_code"] == "CLAIM_SCHEMA_INVALID"
    assert out["rejected_claims"][0]["claim_index"] == 1


def test_empty_source_keys_fact_rejected_only() -> None:
    no_source = {"type": "FACT", "topic": "PRODUCT", "text": "公司产品结构保持稳定。", "source_keys": [], "confidence": "HIGH"}
    out = _validate([_valid_claim(), no_source])
    assert len(out["claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "FACT_WITHOUT_SOURCE"


def test_unknown_source_key_rejected_without_auto_strip() -> None:
    bogus = {"type": "FACT", "topic": "MAIN_BUSINESS", "text": "引用不存在的来源。",
             "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS", "NOT_IN_MANIFEST"], "confidence": "LOW"}
    out = _validate([_valid_claim(), bogus])
    assert len(out["claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "UNKNOWN_SOURCE_KEY"


def test_change_with_current_and_previous_accepted() -> None:
    change = {"type": "FACT", "topic": "BUSINESS_CHANGE",
              "text": "对比本期与上期产品结构披露，产品甲收入占比由上期的水平变动至本期水平。",
              "source_keys": ["DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL",
                              "DISCLOSURE_PREVIOUS_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL"],
              "confidence": "MEDIUM"}
    out = _validate([change])
    assert len(out["claims"]) == 1 and not out["rejected_claims"]


def test_change_only_current_rejected() -> None:
    change = {"type": "FACT", "topic": "BUSINESS_CHANGE", "text": "本期较上期收入出现增长。",
              "source_keys": ["DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL"], "confidence": "MEDIUM"}
    out = _validate([_valid_claim(), change])
    assert out["rejected_claims"][0]["reason_code"] == "CHANGE_WITHOUT_COMPARISON"


def test_change_only_previous_rejected() -> None:
    change = {"type": "FACT", "topic": "BUSINESS_CHANGE", "text": "较上期出现增长。",
              "source_keys": ["DISCLOSURE_PREVIOUS_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL"], "confidence": "MEDIUM"}
    out = _validate([change])
    assert out["rejected_claims"][0]["reason_code"] == "CHANGE_WITHOUT_COMPARISON"


def test_jargon_claim_rejected_only() -> None:
    jargon = {"type": "INFERENCE", "topic": "BUSINESS_MODEL", "text": "公司客户集中度较高，存在依赖。",
              "source_keys": ["DISCLOSURE_CURRENT_CUSTOMER_CONCENTRATION_ANNUAL"], "confidence": "LOW"}
    out = _validate([_valid_claim(), jargon])
    assert len(out["claims"]) == 1
    assert out["rejected_claims"][0]["reason_code"] == "JARGON_WITHOUT_EXPLANATION"


def test_unsupported_share_rejected_only() -> None:
    share = {"type": "INFERENCE", "topic": "PRODUCT", "text": "产品甲收入占比最高，是最大贡献来源。",
             "source_keys": ["BUSINESS_CURRENT_MAIN_BUSINESS"], "confidence": "LOW"}
    out = _validate([_valid_claim(), share])
    assert out["rejected_claims"][0]["reason_code"] == "UNSUPPORTED_PRODUCT_SHARE"


def test_all_valid_batch_unchanged() -> None:
    out = _validate([_valid_claim() for _ in range(6)])
    assert len(out["claims"]) == 6 and out["rejected_claims"] == []


def test_all_invalid_batch_rejected() -> None:
    bad = [{"type": "FACT", "topic": "PRODUCT", "text": "产品收入占比最高。", "source_keys": [], "confidence": "LOW"}]
    out = _validate([dict(item) for item in bad * 3])
    assert out["claims"] == [] and len(out["rejected_claims"]) == 3


# ---------------------------------------------------------------------------
# Fixture service with a counting stub model runtime
# ---------------------------------------------------------------------------

class _StubTdxStore:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def get_record(self, dataset: str, key: str, **_: object) -> dict:
        return self.rows.get((dataset, key)) or {}


def _service(tmp_path: Path, model_response: dict) -> tuple[BusinessResearchService, SimpleNamespace]:
    rows = {
        ("securities", CODE): {"updated_at": "2026-09-02T08:00:00+00:00", "payload": {"name": "测试股份"}},
        ("fundamentals", CODE): {"updated_at": "2026-08-31T12:00:00+00:00",
                                 "payload": {"main_business": "精密制造", "base_raw": {"ZYCP": "产品甲"}}},
        ("security_details", CODE): {"updated_at": "2026-08-18T03:00:00+00:00", "payload": {}},
    }
    db = tmp_path / "research.db"
    counter = SimpleNamespace(model_requests=0)

    def _run(**kwargs: object) -> StructuredOutputResult:
        counter.model_requests += 1
        time.sleep(0.8)  # leave the window open for concurrent callers
        validate = kwargs["validate"]  # type: ignore[index]
        parsed = validate(dict(model_response))
        return StructuredOutputResult(
            parsed=parsed, text=None, mode_requested="JSON_OBJECT", mode_used="JSON_OBJECT",
            fallback_path=[], attempts=[{"mode": "JSON_OBJECT", "success": True, "latency_ms": 1}],
            error_types=[], capability_profile={}, capability_source="test",
        )

    service = BusinessResearchService(
        store=BusinessResearchStore(db),
        disclosure_store=DisclosureMaterialStore(db),
        profiles=CompanyBusinessProfileService(
            tdx=SimpleNamespace(store=_StubTdxStore(rows)), catalog=SimpleNamespace()),
        runtime=SimpleNamespace(),
        structured_runtime=SimpleNamespace(run=_run),
        config_store=_StubConfigStore(),
    )
    return service, counter


class _StubConfigStore:
    def get_runtime_config(self, role: str = "financial_analyst") -> dict:
        return {"provider": "openai", "model": "stub", "enabled": True,
                "base_url": "http://stub.local", "api_key": "k", "structured_output": {}}

    get_config = get_runtime_config

    def list_configs(self) -> list[dict]:
        return [{"provider": "openai", "available": True}]

    def close(self) -> None:
        return None


_RESPONSE_7_1 = {"summary": "公司经营结构稳定。", "claims": [
    *[_valid_claim() for _ in range(7)],
    {"type": "FACT", "topic": "BUSINESS_CHANGE", "text": "本期较上期出现增长。",
     "source_keys": ["DISCLOSURE_CURRENT_BUSINESS_PRODUCT_STRUCTURE_SEMIANNUAL"], "confidence": "MEDIUM"},
]}


def _seed_disclosure(service: BusinessResearchService, *, ann_id: str = "h1", ann_date: str = "2026-08-30") -> None:
    doc = service.disclosure_store.save_document({
        "stock_code": CODE, "company_name": "测试股份", "org_id": "org1",
        "announcement_id": ann_id, "report_kind": "SEMIANNUAL", "report_period": "2026-06-30",
        "announcement_date": ann_date, "title": "半年度报告", "source_url": f"https://x/{ann_id}",
        "pdf_path": "", "pdf_sha256": f"pdf-{ann_id}", "text_path": "", "text_sha256": f"sha-{ann_id}",
        "page_count": 10, "extraction_status": "READY", "extraction_error": "",
    })
    service.disclosure_store.save_materials(str(doc["id"]), CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 20, "text": "本期产品结构披露产品甲收入占比百分之五十五。"}],
    }])


def test_analyze_status_semantics_one_invalid_still_completed(tmp_path: Path) -> None:
    service, counter = _service(tmp_path, _RESPONSE_7_1)
    _seed_disclosure(service)
    result = service.analyze(CODE, as_of=AS_OF)
    assert result["analysis_status"] == "COMPLETED"
    assert len(result["analysis"]["claims"]) == 7
    assert len(result["analysis"]["rejected_claims"]) == 1
    assert counter.model_requests == 1


def test_analyze_all_rejected_is_partial_never_completed(tmp_path: Path) -> None:
    response = {"summary": "资料不足。", "claims": [
        {"type": "FACT", "topic": "PRODUCT", "text": "产品收入占比最高。", "source_keys": [], "confidence": "LOW"},
    ]}
    service, counter = _service(tmp_path, response)
    _seed_disclosure(service)
    result = service.analyze(CODE, as_of=AS_OF)
    assert result["analysis_status"] == "PARTIAL"
    assert result["analysis"]["claims"] == []
    assert counter.model_requests == 1


def test_completed_reused_without_model(tmp_path: Path) -> None:
    service, counter = _service(tmp_path, _RESPONSE_7_1)
    _seed_disclosure(service)
    first = service.analyze(CODE, as_of=AS_OF)
    second = service.analyze(CODE, as_of=AS_OF)
    assert counter.model_requests == 1
    assert second["id"] == first["id"]
    assert second.get("idempotent_reuse") is True


def test_partial_is_terminal_no_auto_rerun(tmp_path: Path) -> None:
    response = {"summary": "资料不足。", "claims": []}
    service, counter = _service(tmp_path, response)
    _seed_disclosure(service)
    first = service.analyze(CODE, as_of=AS_OF)
    assert first["analysis_status"] == "PARTIAL"
    second = service.analyze(CODE, as_of=AS_OF)  # e.g. risk preparation caller
    assert counter.model_requests == 1
    assert second["analysis_status"] == "PARTIAL"
    assert second.get("idempotent_reuse") is True


def test_explicit_force_reanalyzes(tmp_path: Path) -> None:
    service, counter = _service(tmp_path, _RESPONSE_7_1)
    _seed_disclosure(service)
    service.analyze(CODE, as_of=AS_OF)
    again = service.analyze(CODE, as_of=AS_OF, force=True)
    assert counter.model_requests == 2
    assert again["analysis_status"] == "COMPLETED"


def test_changed_source_allows_new_analysis(tmp_path: Path) -> None:
    response = {"summary": "资料不足。", "claims": []}
    service, counter = _service(tmp_path, response)
    _seed_disclosure(service, ann_id="h1")
    assert service.analyze(CODE, as_of=AS_OF)["analysis_status"] == "PARTIAL"
    _seed_disclosure(service, ann_id="h1b", ann_date="2026-08-31")  # new filing → new fingerprint
    result = service.analyze(CODE, as_of=AS_OF)
    assert counter.model_requests == 2  # new source fingerprint: not a duplicate
    assert result["idempotent_reuse"] is False


def test_version_change_allows_new_analysis(tmp_path: Path, monkeypatch) -> None:
    service, counter = _service(tmp_path, _RESPONSE_7_1)
    _seed_disclosure(service)
    service.analyze(CODE, as_of=AS_OF)
    monkeypatch.setattr(business_module, "BUSINESS_RESEARCH_VERSION", "financial-researcher-business-v1.2.0-test")
    result = service.analyze(CODE, as_of=AS_OF)
    assert counter.model_requests == 2
    assert result["idempotent_reuse"] is False


def test_concurrent_same_source_single_model_request(tmp_path: Path) -> None:
    service, counter = _service(tmp_path, _RESPONSE_7_1)
    _seed_disclosure(service)
    results: list[dict] = []
    lock = threading.Lock()

    def _call() -> None:
        out = service.analyze(CODE, as_of=AS_OF)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert counter.model_requests == 1
    statuses = [str(r.get("analysis_status")) for r in results]
    assert all(s in {"COMPLETED", "BUSINESS_ANALYSIS_IN_PROGRESS"} for s in statuses)
    completed = [r for r in results if r.get("analysis_status") == "COMPLETED"]
    assert completed and all(r.get("id") == completed[0].get("id") for r in completed)
    assert sum(1 for r in completed if r.get("idempotent_reuse") is True) == len(completed) - 1


def test_expired_lease_is_recoverable(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, _RESPONSE_7_1)
    store = service.store
    assert store.acquire_analysis_lease("k1", CODE, "hash-a", "owner-1", ttl_seconds=600)
    assert not store.acquire_analysis_lease("k1", CODE, "hash-a", "owner-2")
    with store._lock, store._conn:
        store._conn.execute("UPDATE business_analysis_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE lease_key='k1'")
    assert store.acquire_analysis_lease("k1", CODE, "hash-a", "owner-2", ttl_seconds=600)
    store.release_analysis_lease("k1", "owner-2")

from __future__ import annotations

from pathlib import Path

from src.moat_evidence.store import MoatEvidenceStore
from src.moat_research.service import MoatResearchService


class _Profiles:
    def profile(self, symbol: str):
        return {"stock_code": symbol, "stock_name": "测试公司", "data_status": "REAL", "main_business": "餐饮服务"}


def _leader(_market: str, symbol: str, as_of: str | None):
    return {
        "company": {"market": "CN", "stock_code": symbol, "stock_name": "测试公司"}, "research_as_of": as_of or "2026-08-20",
        "leader_position": {"status": "READY", "level3": {"name": "餐饮"}, "rank": 1},
        "peer_advantage_categories": [{"dimension": "SCALE", "status": "STRONG"}],
        "strengths": [], "pricing_power_proxy": {"status": "UNKNOWN"}, "moat_data_gaps": ["市场份额"],
    }


def _save(store: MoatEvidenceStore, *, dimension="BRAND", kind="QUANTIFIED_FACT", doc="annual-2024", period="2024-12-31", announcement="2025-03-20", claim="门店数 100 家", confidence="HIGH"):
    return store.save({
        "fingerprint": f"{dimension}-{kind}-{doc}-{claim}", "market": "CN", "stock_code": "605108.SH", "research_as_of": announcement,
        "moat_dimension": dimension, "evidence_type": kind, "claim": claim, "normalized_claim": claim,
        "summary": claim, "supports_or_challenges": "SUPPORT" if kind != "COUNTER_EVIDENCE" else "CHALLENGE",
        "source_type": "CNINFO_PERIODIC_REPORT", "source_document_id": doc, "source_material_id": doc, "source_url": "https://example.test/report",
        "report_date": period, "announcement_date": announcement, "page_number": 1, "source_quote_ref": "page:1", "source_hash": doc,
        "value": {"metric": "stores", "value": 100, "unit": "家"}, "confidence": confidence, "data_quality": "VERIFIED",
        "extractor_version": "moat-evidence-extraction-v1.0.0", "fact_key": claim, "status": "ACTIVE",
    })[0]


def _service(tmp_path: Path):
    store = MoatEvidenceStore(tmp_path / "research.db")
    return MoatResearchService(evidence_store=store, leader_profile_loader=_leader, business_profiles=_Profiles()), store


def test_management_claim_and_single_inference_never_supported(tmp_path: Path):
    service, store = _service(tmp_path)
    _save(store, kind="MANAGEMENT_CLAIM", claim="公司称品牌影响力强")
    _save(store, kind="INFERENCE", claim="财务表现或许支持品牌")
    brand = next(item for item in service.get_research("CN", "605108.SH", "2026-08-20")["dimensions"] if item["dimension"] == "BRAND")
    assert brand["status"] == "PARTIAL"
    assert brand["evidence_balance"] == "SUPPORTING"
    service.close()


def test_supported_requires_cross_period_facts_and_counter_blocks_it(tmp_path: Path):
    service, store = _service(tmp_path)
    _save(store, doc="annual-2023", period="2023-12-31", announcement="2024-03-20", claim="门店数 80 家")
    _save(store, doc="annual-2024", period="2024-12-31", announcement="2025-03-20", claim="门店数 100 家")
    first = next(item for item in service.get_research("CN", "605108.SH", "2026-08-20")["dimensions"] if item["dimension"] == "BRAND")
    assert first["status"] == "SUPPORTED"
    assert first["persistence"] == "REPEATED"
    _save(store, kind="COUNTER_EVIDENCE", doc="annual-2025", period="2025-12-31", announcement="2026-03-20", claim="关店增加", confidence="HIGH")
    second = next(item for item in service.get_research("CN", "605108.SH", "2026-08-20")["dimensions"] if item["dimension"] == "BRAND")
    assert second["status"] == "PARTIAL"
    assert second["evidence_balance"] == "MIXED"
    assert second["persistence"] == "DETERIORATING"
    service.close()


def test_industry_guard_pit_and_no_score_or_mutation(tmp_path: Path):
    service, store = _service(tmp_path)
    _save(store, dimension="NETWORK_EFFECT", claim="平台用户 100 万", doc="future", period="2026-12-31", announcement="2027-03-20")
    data = service.get_research("CN", "605108.SH", "2026-08-20")
    network = next(item for item in data["dimensions"] if item["dimension"] == "NETWORK_EFFECT")
    assert network["applicability"] == "NOT_APPLICABLE"
    assert network["status"] == "UNKNOWN"
    assert all("score" not in key.lower() for key in data)
    assert data["source_status"]["moat_evidence"] == "NOT_COLLECTED"
    assert store.list("CN", "605108.SH", as_of="2026-08-20") == []
    service.close()

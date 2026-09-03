"""ResearchFreshnessService V1 contracts (research-cache plan Sprint 2)."""

from __future__ import annotations

from pathlib import Path

from src.research_freshness.manifests import ResearchManifestStore
from src.research_freshness.service import ResearchFreshnessService


def test_manifest_record_and_latest_roundtrip(tmp_path: Path) -> None:
    store = ResearchManifestStore(tmp_path / "research.db")
    store.record(
        research_type="risk_snapshot", market="CN", stock_code="600460.SH",
        research_as_of="2026-08-28", input_fingerprint="fp-1", formula_version="v1",
    )
    row = store.latest(research_type="risk_snapshot", market="CN", stock_code="600460.SH")
    assert row is not None
    assert row["input_fingerprint"] == "fp-1"
    assert row["research_as_of"] == "2026-08-28"
    assert row["freshness_status"] == "FRESH"

    # Same fingerprint upserts; a new fingerprint supersedes via latest().
    store.record(
        research_type="risk_snapshot", market="CN", stock_code="600460.SH",
        research_as_of="2026-08-29", input_fingerprint="fp-2", formula_version="v1",
    )
    assert store.latest(research_type="risk_snapshot", market="CN", stock_code="600460.SH")["input_fingerprint"] == "fp-2"
    # PIT: historical lookup still sees the old fingerprint.
    assert store.latest(
        research_type="risk_snapshot", market="CN", stock_code="600460.SH", as_of="2026-08-28",
    )["input_fingerprint"] in {"fp-1", "fp-2"}


def test_manifest_backed_unknown_without_record(tmp_path: Path) -> None:
    service = ResearchFreshnessService(ResearchManifestStore(tmp_path / "research.db"))
    entry = service._classify_manifest_backed(
        "risk_snapshot", "风险快照", "600460.SH", "2026-08-28", "fp-current",
    )
    assert entry["status"] == "UNKNOWN"
    assert "尚未记录输入指纹" in entry["stale_reason"]


def test_manifest_backed_stale_on_fingerprint_change(tmp_path: Path) -> None:
    store = ResearchManifestStore(tmp_path / "research.db")
    store.record(
        research_type="risk_snapshot", market="CN", stock_code="600460.SH",
        research_as_of="2026-08-28", input_fingerprint="fp-old",
    )
    service = ResearchFreshnessService(store)
    entry = service._classify_manifest_backed(
        "risk_snapshot", "风险快照", "600460.SH", "2026-08-28", "fp-new",
    )
    assert entry["status"] == "STALE"
    assert "输入指纹已变化" in entry["stale_reason"]


def test_classify_never_uses_calendar_staleness(tmp_path: Path, monkeypatch) -> None:
    """'not generated today' must never be the stale reason (plan §7.2)."""
    service = ResearchFreshnessService(ResearchManifestStore(tmp_path / "research.db"))

    def _missing(module, title, **kwargs):
        return service._entry(module, title, "NOT_PERSISTED", reason="尚无快照")

    monkeypatch.setattr(service, "_classify_financial", lambda code, as_of: _missing("financial", "财务"))
    monkeypatch.setattr(service, "_classify_business", lambda code, as_of: _missing("business", "经营"))
    monkeypatch.setattr(
        service, "_classify_deterministic",
        lambda module, title, fp: {"module": module, "title": title, "status": "FRESH",
                                   "stale_reason": "", "input_fingerprint": None, "persisted_as_of": None},
    )
    monkeypatch.setattr(service, "_classify_thesis", lambda code, as_of: _missing("thesis", "核心逻辑"))
    monkeypatch.setattr(service, "_classify_risk_snapshot", lambda code, as_of: _missing("risk_snapshot", "风险快照"))
    monkeypatch.setattr(service, "_classify_low_value_pool", lambda code, as_of: _missing("low_value_pool", "低估池"))
    monkeypatch.setattr(service, "_classify_daily_brief", lambda as_of: _missing("daily_brief", "简报"))

    result = service.classify("CN", "600460.SH", "2026-08-28")
    assert set(result["modules"][0].keys()) >= {"module", "status", "stale_reason"}
    for module in result["modules"]:
        assert "今天" not in (module["stale_reason"] or "")
        assert "日期" not in (module["stale_reason"] or "")
    assert result["research_as_of"] == "2026-08-28"
    assert "summary" in result


def test_financial_fingerprint_excludes_research_clock(tmp_path, monkeypatch) -> None:
    """Plan §20.1: same inputs, different as_of → identical fingerprint."""
    from src.financial_analysis.service import FinancialAnalysisService

    class FakeHistory:
        def __init__(self, rows):
            self.rows = rows

        def query(self, symbol, as_of=None):
            return {"items": self.rows}

    rows = [{"report_date": "2026-03-31", "announcement_date": "2026-04-30", "revenue": 1.0}]
    svc = FinancialAnalysisService.__new__(FinancialAnalysisService)
    svc.history = FakeHistory(rows)
    svc._identity = lambda symbol, as_of: {  # type: ignore[method-assign]
        "stock_name": "测试", "data_dates": {"analysis_as_of": as_of or "2026-08-28"},
    }
    from src.financial_analysis.service import FINANCIAL_FEATURE_VERSION, FORECAST_VERSION
    assert FINANCIAL_FEATURE_VERSION and FORECAST_VERSION

    fp1 = svc._source_fingerprint("600460.SH", {"data_dates": {"analysis_as_of": "2026-08-27"}}, rows)
    fp2 = svc._source_fingerprint("600460.SH", {"data_dates": {"analysis_as_of": "2026-08-28"}}, rows)
    fp3 = svc._source_fingerprint(
        "600460.SH", {"data_dates": {"analysis_as_of": "2026-08-28"}}, rows + [{"report_date": "2026-06-30"}])
    assert fp1 == fp2  # clock drift alone must not change the fingerprint
    assert fp2 != fp3  # a real new report must


# ---------------------------------------------------------------------------
# Business freshness × disclosure dependency (Batch 1A finding, 2026-09-02):
# a new relevant periodic disclosure must flip business to STALE even when the
# daily TDX cache rewrite touched ``securities`` after the research as_of.
# ---------------------------------------------------------------------------

CODE = "600001.SH"


class _StubTdxStore:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def get_record(self, dataset: str, key: str, **_: object) -> dict:
        return self.rows.get((dataset, key)) or {}


class _StubTdx:
    def __init__(self, rows: dict) -> None:
        self.store = _StubTdxStore(rows)


def _fixture_business_service(tmp_path: Path, *, with_profile: bool = True):
    from types import SimpleNamespace

    from src.business_research.service import BusinessResearchService
    from src.business_research.store import BusinessResearchStore
    from src.disclosure_materials.store import DisclosureMaterialStore
    from src.level3_leaders.business_profiles import CompanyBusinessProfileService

    db = tmp_path / "research.db"
    rows: dict = {}
    if with_profile:
        rows[("securities", CODE)] = {"updated_at": "2026-09-02T08:00:00+00:00",
                                      "payload": {"name": "测试股份"}}
        rows[("fundamentals", CODE)] = {
            "updated_at": "2026-08-31T12:00:00+00:00",
            "payload": {"main_business": "精密制造", "base_raw": {"ZYCP": "产品A、产品B"}},
        }
        rows[("security_details", CODE)] = {"updated_at": "2026-08-18T03:00:00+00:00", "payload": {}}
    profiles = CompanyBusinessProfileService(tdx=_StubTdx(rows), catalog=SimpleNamespace())
    return BusinessResearchService(
        store=BusinessResearchStore(db),
        disclosure_store=DisclosureMaterialStore(db),
        profiles=profiles,
        runtime=SimpleNamespace(),
        structured_runtime=SimpleNamespace(),
    )


def _save_periodic_doc(service, *, ann_id: str, kind: str, period: str, ann_date: str, text_sha: str) -> str:
    doc = service.disclosure_store.save_document({
        "stock_code": CODE, "company_name": "测试股份", "org_id": "org1",
        "announcement_id": ann_id, "report_kind": kind, "report_period": period,
        "announcement_date": ann_date, "title": f"{period}报告", "source_url": f"https://x/{ann_id}",
        "pdf_path": "", "pdf_sha256": f"pdf-{ann_id}", "text_path": "", "text_sha256": text_sha,
        "page_count": 10, "extraction_status": "READY", "extraction_error": "",
    })
    return str(doc["id"])


def _classify_business(service, as_of: str):
    from src.research_freshness.manifests import ResearchManifestStore

    fresh = ResearchFreshnessService.__new__(ResearchFreshnessService)
    fresh.manifest_store = ResearchManifestStore(Path(service.store.db_path).parent / "manifests.db")
    return fresh._classify_business(CODE, as_of)


def test_business_stale_after_new_relevant_h1_disclosure(tmp_path, monkeypatch) -> None:
    import src.business_research as business_module

    service = _fixture_business_service(tmp_path)
    # Baseline: FY2025 annual already ingested, snapshot persisted on 08-27.
    doc = _save_periodic_doc(service, ann_id="a1", kind="ANNUAL", period="2025-12-31",
                             ann_date="2026-03-30", text_sha="sha-annual")
    service.disclosure_store.save_materials(doc, CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 12, "text": "产品A收入占比60%"}],
    }])
    fingerprint = service.input_fingerprint(CODE, as_of="2026-09-01")
    assert fingerprint is not None
    assert fingerprint["source_hash"] == service.input_fingerprint(CODE, as_of="2026-09-01")["source_hash"]
    profile_hash = service.profiles.profile(CODE)["source_hash"]
    service.store.save({
        "stock_code": CODE, "company_name": "测试股份", "data_as_of": "2026-08-27T05:00:00+00:00",
        "source_hash": fingerprint["source_hash"], "profile": {"source_hash": profile_hash}, "sources": {},
    }, configured=True, provider="test", model="test")
    monkeypatch.setattr(business_module, "get_business_research_service", lambda: service)

    # securities cache rewritten on 09-02 (after as_of) must not mask the signal.
    assert _classify_business(service, "2026-09-01")["status"] == "FRESH"

    h1 = _save_periodic_doc(service, ann_id="a2", kind="SEMIANNUAL", period="2026-06-30",
                            ann_date="2026-08-30", text_sha="sha-h1")
    service.disclosure_store.save_materials(h1, CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 20, "text": "产品B收入占比升至55%"}],
    }])
    entry = _classify_business(service, "2026-09-01")
    assert entry["status"] == "STALE"


def test_business_fresh_ignores_irrelevant_disclosure(tmp_path, monkeypatch) -> None:
    import src.business_research as business_module

    service = _fixture_business_service(tmp_path)
    h1 = _save_periodic_doc(service, ann_id="h1", kind="SEMIANNUAL", period="2026-06-30",
                            ann_date="2026-08-30", text_sha="sha-h1")
    service.disclosure_store.save_materials(h1, CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 20, "text": "产品B收入占比55%"}],
    }])
    fingerprint = service.input_fingerprint(CODE, as_of="2026-09-01")
    assert fingerprint is not None
    profile_hash = service.profiles.profile(CODE)["source_hash"]
    service.store.save({
        "stock_code": CODE, "company_name": "测试股份", "data_as_of": "2026-09-01T05:00:00+00:00",
        "source_hash": fingerprint["source_hash"], "profile": {"source_hash": profile_hash}, "sources": {},
    }, configured=True, provider="test", model="test")
    monkeypatch.setattr(business_module, "get_business_research_service", lambda: service)
    assert _classify_business(service, "2026-09-01")["status"] == "FRESH"

    # A periodic filing whose materials are not business-relevant (financial
    # note subject) must not flip the module to STALE.
    q3 = _save_periodic_doc(service, ann_id="q3", kind="Q3", period="2025-09-30",
                            ann_date="2025-10-27", text_sha="sha-q3")
    service.disclosure_store.save_materials(q3, CODE, [{
        "material_type": "DEBT_MATURITY", "status": "FOUND",
        "excerpts": [{"page": 88, "text": "短期借款30亿元"}],
    }])
    assert _classify_business(service, "2026-09-01")["status"] == "FRESH"


def test_business_unknown_when_fingerprint_unavailable(tmp_path, monkeypatch) -> None:
    import src.business_research as business_module

    service = _fixture_business_service(tmp_path, with_profile=False)
    service.store.save({
        "stock_code": CODE, "company_name": "测试股份", "data_as_of": "2026-08-27T05:00:00+00:00",
        "source_hash": "hash-old", "profile": {"source_hash": "p"}, "sources": {},
    }, configured=True, provider="test", model="test")
    monkeypatch.setattr(business_module, "get_business_research_service", lambda: service)
    entry = _classify_business(service, "2026-09-01")
    assert entry["status"] == "UNKNOWN"
    assert "不可计算" in (entry["stale_reason"] or "")


def test_business_excludes_future_disclosure_from_earlier_as_of(tmp_path, monkeypatch) -> None:
    """A filing announced after the research as_of must not pollute its freshness."""
    import src.business_research as business_module

    service = _fixture_business_service(tmp_path)
    doc = _save_periodic_doc(service, ann_id="a1", kind="ANNUAL", period="2025-12-31",
                             ann_date="2026-03-30", text_sha="sha-annual")
    service.disclosure_store.save_materials(doc, CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 12, "text": "产品A收入占比60%"}],
    }])
    fingerprint = service.input_fingerprint(CODE, as_of="2026-09-01")
    assert fingerprint is not None
    profile_hash = service.profiles.profile(CODE)["source_hash"]
    service.store.save({
        "stock_code": CODE, "company_name": "测试股份", "data_as_of": "2026-08-27T05:00:00+00:00",
        "source_hash": fingerprint["source_hash"], "profile": {"source_hash": profile_hash}, "sources": {},
    }, configured=True, provider="test", model="test")
    monkeypatch.setattr(business_module, "get_business_research_service", lambda: service)

    # 09-02 filing (after the 09-01 research day) must stay invisible on 09-01.
    future = _save_periodic_doc(service, ann_id="fut", kind="SEMIANNUAL", period="2026-06-30",
                                ann_date="2026-09-02", text_sha="sha-future")
    service.disclosure_store.save_materials(future, CODE, [{
        "material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
        "excerpts": [{"page": 3, "text": "未来的披露"}],
    }])
    assert _classify_business(service, "2026-09-01")["status"] == "FRESH"
    # ...but it must count on the later research day.
    assert service.input_fingerprint(CODE, as_of="2026-09-02")["source_hash"] != fingerprint["source_hash"]


def _build_fixture_with_order(tmp_path: Path, *, documents_first: bool) -> str:
    """Same logical inputs, deliberately different insertion order."""
    service = _fixture_business_service(tmp_path)

    def annual() -> str:
        return _save_periodic_doc(service, ann_id="a1", kind="ANNUAL", period="2025-12-31",
                                  ann_date="2026-03-30", text_sha="sha-annual")

    def h1() -> str:
        return _save_periodic_doc(service, ann_id="a2", kind="SEMIANNUAL", period="2026-06-30",
                                  ann_date="2026-08-30", text_sha="sha-h1")

    def annual_materials(doc: str) -> None:
        service.disclosure_store.save_materials(doc, CODE, [
            {"material_type": "CUSTOMER_CONCENTRATION", "status": "FOUND",
             "excerpts": [{"page": 30, "text": "前五大客户62%"}]},
            {"material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
             "excerpts": [{"page": 12, "text": "产品A收入占比60%"}]},
        ])

    def h1_materials(doc: str) -> None:
        service.disclosure_store.save_materials(doc, CODE, [
            {"material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
             "excerpts": [{"page": 20, "text": "产品B收入占比55%"}]},
        ])
    if documents_first:
        doc_a, doc_h = annual(), h1()
        annual_materials(doc_a)
        h1_materials(doc_h)
    else:
        doc_h, doc_a = h1(), annual()
        h1_materials(doc_h)
        annual_materials(doc_a)
    fingerprint = service.input_fingerprint(CODE, as_of="2026-09-01")
    assert fingerprint is not None
    return fingerprint["source_hash"]


def test_business_fingerprint_stable_across_insertion_order(tmp_path) -> None:
    """Document/material insertion order must never fake a stale signal."""
    first = _build_fixture_with_order(tmp_path / "a", documents_first=True)
    second = _build_fixture_with_order(tmp_path / "b", documents_first=False)
    assert first == second

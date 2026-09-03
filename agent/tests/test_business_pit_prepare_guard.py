"""Business prepare() PIT profile-selection guard (2026-09-02 remediation).

The old guard rejected any profile whose ``securities`` cache row was
rewritten after ``as_of`` — a technical touch, not a business date.  The
shared ``_pit_profile`` selection is content-based: content-source dates,
content proof via persisted snapshots, historical restore, or an explicit
PIT_DATA_UNAVAILABLE refusal.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.business_research.service import BusinessResearchService
from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.level3_leaders.business_profiles import CompanyBusinessProfileService

CODE = "600001.SH"
AS_OF = "2026-09-01"


class _RecordStore:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def get_record(self, dataset: str, key: str, **_: object) -> dict:
        return self.rows.get((dataset, key)) or {}


def _fixture(tmp_path: Path, *, fundamentals, details=None) -> BusinessResearchService:
    rows = {
        ("securities", CODE): {"updated_at": "2026-09-02T08:00:00+00:00", "payload": {"name": "测试股份"}},
        ("fundamentals", CODE): fundamentals,
        ("security_details", CODE): details
        or {"updated_at": "2026-08-18T03:00:00+00:00", "payload": {}},
    }
    profiles = CompanyBusinessProfileService(
        tdx=SimpleNamespace(store=_RecordStore(rows)), catalog=SimpleNamespace(),
    )
    db = tmp_path / "research.db"
    return BusinessResearchService(
        store=BusinessResearchStore(db),
        disclosure_store=DisclosureMaterialStore(db),
        profiles=profiles,
        runtime=SimpleNamespace(),
        structured_runtime=SimpleNamespace(),
    )


def _fundamentals(*, updated_at: str, business: str = "精密制造") -> dict:
    return {"updated_at": updated_at, "payload": {"main_business": business, "base_raw": {"ZYCP": "产品A"}}}


def _save_doc(service: BusinessResearchService, *, ann_id: str, kind: str, period: str,
              ann_date: str, text_sha: str, materials: list[dict]) -> None:
    doc = service.disclosure_store.save_document({
        "stock_code": CODE, "company_name": "测试股份", "org_id": "org1",
        "announcement_id": ann_id, "report_kind": kind, "report_period": period,
        "announcement_date": ann_date, "title": f"{period}报告", "source_url": f"https://x/{ann_id}",
        "pdf_path": "", "pdf_sha256": f"pdf-{ann_id}", "text_path": "", "text_sha256": text_sha,
        "page_count": 10, "extraction_status": "READY", "extraction_error": "",
    })
    service.disclosure_store.save_materials(str(doc["id"]), CODE, materials)


def _h1_materials() -> list[dict]:
    return [{"material_type": "BUSINESS_PRODUCT_STRUCTURE", "status": "FOUND",
             "excerpts": [{"page": 20, "text": "产品B收入占比55%"}]}]


def test_technical_securities_rewrite_after_as_of_no_longer_blocks(tmp_path: Path) -> None:
    """Case 16: identical business content, securities row touched 09-02."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    profile, guard = service._pit_profile(CODE, as_of=AS_OF)
    assert guard == "OK"
    result = service.prepare(CODE, as_of=AS_OF)
    assert result["idempotent_reuse"] is False  # prepared, not blocked


def _rebind(service: BusinessResearchService, profiles) -> BusinessResearchService:
    """Same persisted stores, a different profile view of the TDX cache."""
    return BusinessResearchService(
        store=service.store, disclosure_store=service.disclosure_store,
        profiles=profiles, runtime=SimpleNamespace(), structured_runtime=SimpleNamespace(),
    )


def _profiles_for(tmp_path: Path, *, fundamentals, details=None) -> CompanyBusinessProfileService:
    rows = {
        ("securities", CODE): {"updated_at": "2026-09-02T08:00:00+00:00", "payload": {"name": "测试股份"}},
        ("fundamentals", CODE): fundamentals,
        ("security_details", CODE): details
        or {"updated_at": "2026-08-18T03:00:00+00:00", "payload": {}},
    }
    return CompanyBusinessProfileService(
        tdx=SimpleNamespace(store=_RecordStore(rows)), catalog=SimpleNamespace(),
    )


def test_unchanged_content_technical_rewrite_restores_proven_profile(tmp_path: Path) -> None:
    """Case 2: fundamentals re-stamped 09-02 with identical fields."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    first = service.prepare(CODE, as_of=AS_OF)
    original_hash = first["profile"]["source_hash"]

    rebuilt = _rebind(service, _profiles_for(
        tmp_path, fundamentals=_fundamentals(updated_at="2026-09-02T08:00:00+00:00")))
    profile, guard = rebuilt._pit_profile(CODE, as_of=AS_OF)
    assert guard in {"PROVEN_BY_SNAPSHOT", "HISTORICAL_PROFILE"}
    assert profile["source_hash"] == original_hash
    result = rebuilt.prepare(CODE, as_of=AS_OF)
    assert result["profile"]["source_hash"] == original_hash


def test_future_content_excluded_via_historical_restore(tmp_path: Path) -> None:
    """Case 15/17: business description truly changed on 09-02 -> 09-01 keeps A."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    first = service.prepare(CODE, as_of=AS_OF)
    original_hash = first["profile"]["source_hash"]

    changed = _rebind(service, _profiles_for(tmp_path, fundamentals=_fundamentals(
        updated_at="2026-09-02T09:00:00+00:00", business="全新主营业务描述")))
    profile, guard = changed._pit_profile(CODE, as_of=AS_OF)
    assert guard == "HISTORICAL_PROFILE"
    assert profile["source_hash"] == original_hash
    assert "全新主营业务描述" not in str(changed.prepare(CODE, as_of=AS_OF)["main_business"])


def test_future_content_without_history_refused(tmp_path: Path) -> None:
    """Case 17: no provably-valid version -> PIT_DATA_UNAVAILABLE, never B."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(
        updated_at="2026-09-02T09:00:00+00:00", business="09-02 才出现的新描述"))
    profile, guard = service._pit_profile(CODE, as_of=AS_OF)
    assert (profile, guard) == (None, "PIT_DATA_UNAVAILABLE")
    try:
        service.prepare(CODE, as_of=AS_OF)
    except ValueError as exc:
        assert "business_profile_pit_unavailable" in str(exc)
    else:
        raise AssertionError("prepare must refuse unprovable future content")
    assert service.input_fingerprint(CODE, as_of=AS_OF) is None


def test_disclosure_future_excluded_but_relevant_h1_included(tmp_path: Path) -> None:
    """Cases 6+7: 09-02 filing invisible on 09-01; 08-30 H1 included."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    _save_doc(service, ann_id="h1", kind="SEMIANNUAL", period="2026-06-30",
              ann_date="2026-08-30", text_sha="sha-h1", materials=_h1_materials())
    _save_doc(service, ann_id="fut", kind="Q3", period="2026-09-30",
              ann_date="2026-09-02", text_sha="sha-future", materials=_h1_materials())
    result = service.prepare(CODE, as_of=AS_OF)
    hashes = {value.get("source_hash") for value in result["sources"].values()}
    assert "sha-h1" in hashes
    assert "sha-future" not in hashes


def test_irrelevant_material_excluded(tmp_path: Path) -> None:
    """Case 8: financial-note subjects never enter business sources."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    _save_doc(service, ann_id="q3", kind="Q3", period="2025-09-30",
              ann_date="2025-10-27", text_sha="sha-q3",
              materials=[{"material_type": "DEBT_MATURITY", "status": "FOUND",
                          "excerpts": [{"page": 88, "text": "短期借款30亿元"}]}])
    result = service.prepare(CODE, as_of=AS_OF)
    assert all("DEBT_MATURITY" not in key for key in result["sources"])


def test_fingerprint_matches_prepare_input_contract(tmp_path: Path) -> None:
    """Case 9: freshness fingerprint and prepare see the same inputs."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    _save_doc(service, ann_id="h1", kind="SEMIANNUAL", period="2026-06-30",
              ann_date="2026-08-30", text_sha="sha-h1", materials=_h1_materials())
    fingerprint = service.input_fingerprint(CODE, as_of=AS_OF)
    assert fingerprint is not None
    result = service.prepare(CODE, as_of=AS_OF)
    assert result["source_hash"] == fingerprint["source_hash"]


def test_fresh_snapshot_reuses_and_stale_prepares(tmp_path: Path) -> None:
    """Cases 10+11: identical inputs reuse; new disclosure prepares a new row."""
    service = _fixture(tmp_path, fundamentals=_fundamentals(updated_at="2026-08-31T12:48:00+00:00"))
    first = service.prepare(CODE, as_of=AS_OF)
    assert first["idempotent_reuse"] is False
    again = service.prepare(CODE, as_of=AS_OF)
    assert again["idempotent_reuse"] is True
    assert again["id"] == first["id"]

    _save_doc(service, ann_id="h1", kind="SEMIANNUAL", period="2026-06-30",
              ann_date="2026-08-30", text_sha="sha-h1", materials=_h1_materials())
    refreshed = service.prepare(CODE, as_of=AS_OF)
    assert refreshed["idempotent_reuse"] is False
    assert refreshed["id"] != first["id"]

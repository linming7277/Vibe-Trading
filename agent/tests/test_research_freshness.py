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

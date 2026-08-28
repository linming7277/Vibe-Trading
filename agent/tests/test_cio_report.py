"""CIO Deep Research Report V1 contracts (research-cache plan Sprint 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.cio_report.builder import SECTION_TITLES, build_all_sections, template_report_markdown
from src.cio_report.service import CioReportService
from src.cio_report.store import CioReportStore


def _store(tmp_path: Path) -> CioReportStore:
    return CioReportStore(tmp_path / "research.db")


def test_store_roundtrip_and_idempotent_save(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sections = [
        {"section_type": "company_position", "input_fingerprint": "fp1", "freshness_status": "FRESH",
         "structured_payload": {"a": 1}, "narrative_md": "n1", "source_refs": ["s1"]},
        {"section_type": "cio_conclusion", "input_fingerprint": "fp2", "freshness_status": "FRESH",
         "structured_payload": {"verdict": "继续观察"}, "narrative_md": "n2", "source_refs": []},
    ]
    saved = store.save_report(
        market="CN", stock_code="605108.SH", research_as_of="2026-08-28",
        overall_freshness="FRESH", input_fingerprint="R1", module_hashes={"financial": "f"},
        sections=sections, narrative_report_md="report", synthesis_source="TEMPLATE",
        formula_version="cio-report-v1", prompt_version="p", model_version="",
        previous_report_id=None,
    )
    assert saved["idempotent_reuse"] is False
    again = store.save_report(
        market="CN", stock_code="605108.SH", research_as_of="2026-08-28",
        overall_freshness="FRESH", input_fingerprint="R1", module_hashes={"financial": "f"},
        sections=sections, narrative_report_md="report", synthesis_source="TEMPLATE",
        formula_version="cio-report-v1", prompt_version="p", model_version="",
        previous_report_id=None,
    )
    assert again["idempotent_reuse"] is True
    report = store.latest_report("CN", "605108.SH")
    assert report is not None and report["narrative_report_md"] == "report"
    by_type = {s["section_type"]: s for s in report["sections"]}
    assert by_type["company_position"]["structured_payload"] == {"a": 1}
    assert by_type["company_position"]["source_refs"] == ["s1"]


def test_builder_degrades_per_section_instead_of_failing(tmp_path, monkeypatch) -> None:
    """A broken source yields one MISSING section, never a failed report (plan §24)."""
    import src.cio_report.builder as builder

    def _boom(self, *args: Any, **kwargs: Any):
        raise RuntimeError("source down")

    monkeypatch.setattr(builder.CioSectionBuilder, "_financial", _boom)
    sections = build_all_sections("CN", "605108.SH", "2026-08-28")
    assert len(sections) == len(SECTION_TITLES) == 14
    statuses = {s["section_type"]: s["structured_payload"].get("status") for s in sections}
    # financial-dependent sections degrade; independent ones still build
    assert statuses["financial_path"] == "MISSING"
    markdown = template_report_markdown(sections, stock_code="605108.SH", as_of="2026-08-28")
    assert "CIO 深度研究报告" in markdown and "资料不足" in markdown


def test_template_fallback_when_synthesis_model_unavailable(tmp_path, monkeypatch) -> None:
    """Plan §15.2: model unavailable → deterministic template narrative, status READY."""
    import src.cio_report.service as service_mod

    class _UnavailableFreshness:
        def classify(self, market, code, as_of=None):
            return {"overall_freshness": "FRESH", "modules": []}

    monkeypatch.setattr(
        "src.research_freshness.get_research_freshness_service", lambda: _UnavailableFreshness())
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: _simple_sections())

    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(
        svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model off")))
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert report["synthesis_source"] == "TEMPLATE"
    assert report["status"] == "READY"
    assert "# CIO 深度研究报告" in report["narrative_report_md"]


def _simple_sections() -> list[dict[str, Any]]:
    return [
        {"section_type": name, "title": title, "input_fingerprint": f"fp-{i}",
         "freshness_status": "FRESH", "structured_payload": {"k": i},
         "narrative_md": f"- {title} 模板行", "source_refs": []}
        for i, (name, title) in enumerate(SECTION_TITLES.items())
    ]


def test_section_reuse_marking_and_stale_classification(tmp_path, monkeypatch) -> None:
    """Plan §16/§17: unchanged sections mark REUSED; per-section stale is visible."""
    import src.cio_report.service as service_mod

    class _Freshness:
        def classify(self, market, code, as_of=None):
            return {"overall_freshness": "FRESH", "modules": []}

    monkeypatch.setattr(
        "src.research_freshness.get_research_freshness_service", lambda: _Freshness())
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: _simple_sections())

    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    first = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert first["synthesis_source"] == "TEMPLATE"

    # Simulate a changed upstream: bump one section fingerprint.
    changed = _simple_sections()
    changed[8]["input_fingerprint"] = "fp-moved"  # valuation section
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: changed)
    rebuilt = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    statuses = {s["section_type"]: s["freshness_status"] for s in rebuilt["sections"]}
    assert statuses["valuation"] == "REFRESHED"
    assert statuses["financial_path"] == "REUSED"

    live = svc.classify_report_sections("CN", "600460.SH", as_of="2026-08-28")
    assert live["overall"] == "FRESH"
    # Move the source again → that section reads STALE without a rebuild.
    moved_again = _simple_sections()
    moved_again[8]["input_fingerprint"] = "fp-moved-2"
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: moved_again)
    live2 = svc.classify_report_sections("CN", "600460.SH", as_of="2026-08-28")
    assert live2["overall"] == "PARTIALLY_STALE"
    assert live2["stale_sections"] == ["valuation"]


def test_focus_tier_policy_builds_a_missing_and_skips_c(tmp_path, monkeypatch) -> None:
    """Plan §11: A 档始终 READY；B 档缺失才建；C 档不自动生成。"""
    import src.cio_report.service as service_mod

    class _Freshness:
        def classify(self, market, code, as_of=None):
            return {"overall_freshness": "FRESH", "modules": []}

    monkeypatch.setattr(
        "src.research_freshness.get_research_freshness_service", lambda: _Freshness())
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: _simple_sections())

    class _FakeFocus:
        def get_focus_selection(self, *, as_of=None):
            return {"focus_a": [{"stock_code": "605108.SH"}],
                    "focus_b": [{"stock_code": "600460.SH"}]}

    monkeypatch.setattr("src.focus_selection.get_focus_selection_service", lambda: _FakeFocus())

    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    summary = svc.ensure_focus_tier_reports(as_of="2026-08-28")
    assert summary["built_a"] == 1 and summary["built_b"] == 1
    assert svc.get_report("CN", "605108.SH", as_of="2026-08-28") is not None
    assert svc.get_report("CN", "600460.SH", as_of="2026-08-28") is not None
    # Second run: A already READY → reuse; B exists → no build.
    summary2 = svc.ensure_focus_tier_reports(as_of="2026-08-28")
    assert summary2["reused_a"] == 1 and summary2["built_b"] == 0

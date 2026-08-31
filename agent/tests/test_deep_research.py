"""Deep Research On-Demand V1 contracts (task §十八)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from src.deep_research.coverage import MISSING, READY, DeepResearchCoverageService
from src.deep_research.preparation import (
    DeepResearchBusyError,
    DeepResearchPreparationService,
)

_TRADING = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓|不碰|试仓")


class FakeCoverage(DeepResearchCoverageService):
    """Deterministic coverage states driven by a mutable dict."""

    def __init__(self, dimensions: dict[str, str]) -> None:  # noqa: D107
        self.state = dict(dimensions)

    def coverage(self, market, stock_code, *, as_of=None):  # noqa: D102
        return {
            "market": market, "stock_code": stock_code, "research_as_of": as_of,
            "dimensions": dict(self.state),
            "overall_coverage": self._overall(self.state),
            "layers": {"P0": [], "P1": [], "P2": []},
        }


def _service(tmp_path: Path, dimensions: dict[str, str]) -> DeepResearchPreparationService:
    return DeepResearchPreparationService(
        coverage_service=FakeCoverage(dimensions),
        state_path=tmp_path / "usage.json",
    )


_P0_READY = {key: READY for key in
             ("financial", "business_profile", "business_research", "valuation", "risk")}


# --- 1 coverage states -------------------------------------------------------

def test_coverage_overall_states() -> None:
    svc = FakeCoverage(dict(_P0_READY, disclosure=READY, moat_evidence=READY, moat_research=READY, thesis=READY))
    assert svc.coverage("CN", "X")["overall_coverage"] == "COMPLETE"
    svc2 = FakeCoverage(dict(_P0_READY, disclosure=MISSING))
    assert svc2.coverage("CN", "X")["overall_coverage"] == "USABLE"
    svc3 = FakeCoverage(dict(_P0_READY, business_research=MISSING))
    assert svc3.coverage("CN", "X")["overall_coverage"] == "PARTIAL"


def test_coverage_pool_external_company_and_pool_untouched(tmp_path, monkeypatch) -> None:
    """1/2/3: an out-of-pool company prepares; pool & focus rows never change."""
    svc = _service(tmp_path, dict(_P0_READY, business_research=MISSING, disclosure=MISSING,
                                  moat_evidence=MISSING, moat_research=MISSING, thesis=MISSING))
    calls: list[str] = []

    def _noop(name):
        def _run(*a, **k):
            calls.append(name)
            return {}
        return _run

    import src.business_research as br
    import src.disclosure_materials as dm
    import src.moat_evidence as me

    monkeypatch.setattr(br, "get_business_research_service", lambda: type("S", (), {
        "get": staticmethod(lambda *a, **k: None),
        "analyze": staticmethod(lambda *a, **k: {"analysis_status": "COMPLETED"}),
    })())
    monkeypatch.setattr(dm, "get_disclosure_material_service", lambda: type("S", (), {
        "sync_periodic_reports": staticmethod(lambda *a, **k: {"selected_reports": 4}),
    })())
    monkeypatch.setattr(me, "get_moat_evidence_extraction_service", lambda: type("S", (), {
        "extract": staticmethod(lambda *a, **k: {"created": 0}),
    })())
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    result = svc.prepare("CN", "600460.SH", as_of="2026-08-28", skip_usage_guard=True)
    assert "business_research" in result["prepared"]
    assert result["llm_calls"] == 1  # business analyze only, zero specialists
    # pool / focus tables untouched: the preparation wrote nothing to them
    # (guard skipped; even a guarded run only appends to its usage file).
    import sqlite3

    conn = sqlite3.connect(r"C:\Users\Administrator\.vibe-trading\research.db")
    pool_rows = conn.execute(
        "SELECT count(*) FROM company_low_value_leader_pool WHERE stock_code='600460.SH'").fetchone()[0]
    assert pool_rows == 0  # still out of the low-value pool


def test_business_existing_is_reused_no_llm(tmp_path, monkeypatch) -> None:
    """5: READY business research → REUSED, analyze never invoked."""
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=READY))
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert "business_research" in result["reused"]
    assert result["llm_calls"] == 0


def test_disclosure_bounded_to_two_per_kind(tmp_path, monkeypatch) -> None:
    """6: sync receives max_documents_per_kind=2, never more."""
    captured: dict[str, Any] = {}

    class FakeSync:
        def sync_periodic_reports(self, code, *, as_of=None, max_documents_per_kind=2):
            captured["bound"] = max_documents_per_kind
            return {"selected_reports": 8}

    import src.disclosure_materials as dm

    monkeypatch.setattr(dm, "get_disclosure_material_service", lambda: FakeSync())
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc = _service(tmp_path, dict(_P0_READY, disclosure=MISSING, moat_evidence=MISSING,
                                  moat_research=MISSING, thesis=MISSING))
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert captured["bound"] == 2
    assert result["network_documents_synced"] == 8


def test_moat_extraction_idempotent_and_zero_legal(tmp_path, monkeypatch) -> None:
    """7/8: READY moat evidence is reused; a zero-evidence extraction is legal."""
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=MISSING))
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert "moat_evidence" in result["reused"]  # idempotent reuse, no re-extract


def test_thesis_draft_generated_but_not_promoted(tmp_path, monkeypatch) -> None:
    """9: draft is generated via service.generate; promote is never called."""
    promoted: list[str] = []
    generated: list[str] = []

    def _must_not_promote(_promoted):
        def _run(self, *a, **k):  # pragma: no cover
            _promoted.append("called")
            raise AssertionError("promote must never be called")
        return _run

    import src.company_thesis.draft_service as ds
    import src.company_thesis.store as ts

    # Patch instance methods directly (never __new__, which poisons the class
    # for later tests in the same pytest session).
    monkeypatch.setattr(
        ds.CompanyThesisDraftService, "generate",
        lambda self, market, code, *, research_as_of=None, industry_context=None: (
            generated.append(code) or {"status": "GENERATED", "draft": {"draft_id": "d1"}}))
    monkeypatch.setattr(
        ds.CompanyThesisDraftService, "promote_to_provisional", _must_not_promote(promoted))
    monkeypatch.setattr(ts.CompanyThesisRepository, "get_current_thesis", lambda self, m, c: None)
    # repository.latest on the real draft store returns None for company X.
    monkeypatch.setattr(
        ds.CompanyThesisDraftRepository, "latest", lambda self, m, c: None)
    import src.financial_analysis.service as fas

    monkeypatch.setattr(fas, "get_financial_analysis_service", lambda: type("S", (), {
        "store": type("ST", (), {"latest": staticmethod(lambda code, as_of=None: {"identity": {"level3_name": "测试行业"}})}),
    })())
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=MISSING))
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert generated == ["X"]
    assert result["thesis_draft_status"] == "GENERATED"
    assert not promoted


def test_human_confirmed_thesis_hard_stops(tmp_path, monkeypatch) -> None:
    """10: HUMAN_CONFIRMED current thesis → locked, no draft path at all."""
    import src.company_thesis.draft_service as ds
    import src.company_thesis.store as ts

    monkeypatch.setattr(ts.CompanyThesisRepository, "get_current_thesis", lambda self, m, c: {
        "thesis_id": "t1", "authority_status": "HUMAN_CONFIRMED",
    })

    def _must_not_generate(self, *a, **k):  # pragma: no cover
        raise AssertionError("generate must not run under HUMAN_CONFIRMED")

    monkeypatch.setattr(ds.CompanyThesisDraftService, "generate", _must_not_generate)
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=READY))
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert result["thesis_draft_status"] == "HUMAN_CONFIRMED_LOCKED"


def test_daily_limit_and_busy_guard(tmp_path, monkeypatch) -> None:
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=READY))
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc.prepare("CN", "X")
    svc.prepare("CN", "X")
    with pytest.raises(RuntimeError, match="DAILY_LIMIT"):
        svc.prepare("CN", "X")

    fresh = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                    moat_research=READY, thesis=READY))
    fresh._in_flight.add("CN:Y")
    with pytest.raises(DeepResearchBusyError):
        fresh.prepare("CN", "Y")


def test_partial_failure_keeps_previous_research(tmp_path, monkeypatch) -> None:
    """13: a business failure is recorded; disclosure/moat still run; nothing is rolled back."""
    import src.business_research as br
    import src.disclosure_materials as dm

    def _boom_service():
        raise RuntimeError("business LLM down")

    monkeypatch.setattr(br, "get_business_research_service", _boom_service)
    monkeypatch.setattr(dm, "get_disclosure_material_service", lambda: type("S", (), {
        "sync_periodic_reports": staticmethod(lambda *a, **k: {"selected_reports": 2}),
    })())
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc = _service(tmp_path, dict(_P0_READY, business_research=MISSING, disclosure=MISSING,
                                  moat_evidence=MISSING, moat_research=MISSING, thesis=MISSING))
    result = svc.prepare("CN", "X", as_of="2026-08-28", skip_usage_guard=True)
    assert any(f["capability"] == "business_research" for f in result["failed"])
    assert "disclosure" in result["prepared"]
    assert result["coverage_after"]["dimensions"]["financial"] == READY  # old results intact


def test_financial_missing_gates_everything(tmp_path, monkeypatch) -> None:
    svc = _service(tmp_path, dict(_P0_READY, financial=MISSING))
    result = svc.prepare("CN", "X", skip_usage_guard=True)
    assert result["prepared"] == [] and result["failed"] == [
        {"capability": "financial", "error": "FINANCIAL_MISSING"}]


def test_no_specialist_fanout_and_no_trading_language(tmp_path, monkeypatch) -> None:
    """14/15: the preparation result never mentions specialists or trading words."""
    import src.cio_report as cio

    monkeypatch.setattr(cio, "get_cio_report_service", lambda: type("S", (), {
        "build_report": staticmethod(lambda *a, **k: {"idempotent_reuse": True, "synthesis_status": "TEMPLATE_FALLBACK"}),
    })())
    svc = _service(tmp_path, dict(_P0_READY, disclosure=READY, moat_evidence=READY,
                                  moat_research=READY, thesis=READY))
    result = svc.prepare("CN", "X", skip_usage_guard=True)
    text = json.dumps(result, ensure_ascii=False)
    for tool in ("ask_financial_analyst", "ask_valuation_researcher", "ask_risk_researcher"):
        assert tool not in text
    assert not _TRADING.search(text)


# --- 4 business profile fallback ----------------------------------------------

def test_company_position_falls_back_to_business_profile(monkeypatch) -> None:
    """Deep-research audit §3: TDX profile fills the main business text."""
    from src.cio_report.builder import CioSectionBuilder

    builder = CioSectionBuilder("CN", "600460.SH", "2026-08-28")
    builder._financial = lambda: {"identity": {"stock_name": "士兰微", "level3_name": "功率半导体"}}  # type: ignore[method-assign]
    builder._business = lambda: {}  # type: ignore[method-assign]  # no business research yet

    import src.level3_leaders.business_profiles as bp

    monkeypatch.setattr(
        bp.CompanyBusinessProfileService, "profile",
        lambda self, code: {"main_business": "集成电路,分立器件产品"})
    section = builder.build_company_position()
    payload = section["structured_payload"]
    assert payload["main_business"] == "集成电路,分立器件产品"
    assert payload["main_business_source"] == "business_profile"
    assert "集成电路" in section["narrative_md"]


def test_thesis_draft_middle_state_rendering(monkeypatch) -> None:
    """On-Demand §8: a valid draft renders as AI 研究草稿, never a formal thesis."""
    from src.cio_report.builder import CioSectionBuilder
    from src.cio_report.narrative import BossRenderer

    builder = CioSectionBuilder("CN", "X", "2026-08-28")
    builder._thesis = lambda: {}  # type: ignore[method-assign]
    builder._financial = lambda: {}  # type: ignore[method-assign]

    import src.company_thesis.draft_store as dst

    monkeypatch.setattr(dst.CompanyThesisDraftRepository, "latest", lambda self, m, c: {
        "draft_status": "DRAFT", "title": "草稿逻辑", "core_thesis": "收入扩张能否转化为利润。",
        "core_drivers": ["功率半导体需求"], "key_assumptions": ["产能爬坡"],
        "invalid_conditions": [{"condition": "毛利率跌破前低"}],
        "key_metrics_to_monitor": ["毛利率"], "source_data_as_of": "2026-08-28",
    })
    section = builder.build_thesis_watchpoints()
    payload = section["structured_payload"]
    assert payload["thesis_draft"]["title"] == "草稿逻辑"

    renderer = BossRenderer([section], "X", "2026-08-28")
    renderer.thesis = payload
    text = renderer.section_watchpoints()
    assert "AI 研究草稿 · 待人工确认" in text
    assert "草稿不等于系统认定" in text
    assert "核心驱动：功率半导体需求" in text
    assert "人工确认" in text and "最重要验证点" in text

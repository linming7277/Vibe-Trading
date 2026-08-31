"""CIO Deep Research Report V1 contracts (research-cache plan Sprint 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.cio_report.builder import (
    SECTION_TITLES,
    CioSectionBuilder,
    build_all_sections,
    template_report_markdown,
)
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
    assert report["synthesis_source"] == "TEMPLATE_FALLBACK"
    assert report["status"] == "READY"
    assert "投研主管深度研究报告" in report["narrative_report_md"]


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
    assert first["synthesis_source"] == "TEMPLATE_FALLBACK"

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
            # Real contract: tier keys are "A"/"B"/"C" (quality fix §1).
            return {"A": [{"stock_code": "605108.SH", "focus_reasons": ["A档理由"]}],
                    "B": [{"stock_code": "600460.SH", "focus_reasons": ["B档理由"]}],
                    "C": [{"stock_code": "000544.SZ"}]}

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


# ---------------------------------------------------------------------------
# Quality Fix Round 1 contracts (task §13)
# ---------------------------------------------------------------------------

def _focus_contract() -> dict:
    return {
        "A": [{"stock_code": "605108.SH", "focus_reasons": ["低估+龙头质量"]}],
        "B": [{"stock_code": "600210.SH", "focus_reasons": ["正常观察"]}],
        "C": [{"stock_code": "000544.SZ", "focus_reasons": ["价值陷阱风险"]}],
    }


def _patch_focus(monkeypatch, selection: dict | None = None) -> None:
    class _FakeFocus:
        def get_focus_selection(self, *, as_of=None):
            return selection if selection is not None else _focus_contract()

    monkeypatch.setattr("src.focus_selection.get_focus_selection_service", lambda: _FakeFocus())


def test_focus_tier_mapping_and_conclusion_verdicts(monkeypatch) -> None:
    """Fix §1: read the real "A"/"B"/"C" contract; tier drives the verdict."""
    _patch_focus(monkeypatch)
    builder = CioSectionBuilder("CN", "605108.SH", "2026-08-28")
    entry = builder._focus_entry()
    assert entry["tier"] == "A" and "重点研究" in entry["label"]
    conclusion = builder.build_cio_conclusion()
    assert conclusion["structured_payload"]["verdict"] == "重点研究"
    assert conclusion["structured_payload"]["focus_tier"] == "A"

    # C 档 never escalates to 重点研究 even under deep undervaluation (000544).
    c_builder = CioSectionBuilder("CN", "000544.SZ", "2026-08-28")
    c_builder._zones = lambda: {"current_price": 5.0, "valuation": {"status": "DEEPLY_UNDERVALUED"}}  # type: ignore[method-assign]
    assert c_builder.build_cio_conclusion()["structured_payload"]["verdict"] == "暂缓优先研究"

    b_builder = CioSectionBuilder("CN", "600210.SH", "2026-08-28")
    assert b_builder.build_cio_conclusion()["structured_payload"]["verdict"] == "继续观察"


def test_operating_stage_handles_real_growth_list_payload(monkeypatch) -> None:
    """Fix §2: feature.growth.revenue is a LIST of point dicts — no crash."""
    builder = CioSectionBuilder("CN", "600460.SH", "2026-08-28")
    builder._financial = lambda: {  # type: ignore[method-assign]
        "feature": {"growth": {"revenue": [
            {"report_date": "2022-12-31", "announcement_date": "2023-03-31", "period_type": "annual", "value": 82.0},
            {"report_date": "2023-12-31", "announcement_date": "2024-03-29", "period_type": "annual", "value": 93.0},
            {"report_date": "2024-12-31", "announcement_date": "2025-03-28", "period_type": "annual", "value": 112.0},
            {"report_date": "2025-12-31", "announcement_date": "2026-04-24", "period_type": "annual", "value": 130.0},
        ], "net_profit": []}},
        "forecast": {"status": "LIMITED"},
    }
    section = builder.build_operating_stage()
    assert section["structured_payload"]["stage"] == "GROWTH"
    assert len(section["structured_payload"]["revenue_yoy_series"]) == 3

    short = CioSectionBuilder("CN", "X", "2026-08-28")
    short._financial = lambda: {"feature": {"growth": {"revenue": [  # type: ignore[method-assign]
        {"report_date": "2025-12-31", "period_type": "annual", "value": 1.0}]}}}
    assert short.build_operating_stage()["structured_payload"]["stage"] == "UNKNOWN"


def test_valuation_maps_pe_pb_from_identity(monkeypatch) -> None:
    """Fix §3: PE/PB/dividend yield come from identity.market_valuation + peer methods."""
    builder = CioSectionBuilder("CN", "600460.SH", "2026-08-28")
    builder._financial = lambda: {"identity": {"market_valuation": {  # type: ignore[method-assign]
        "pe": 130.17, "pb": 4.85, "dividend_yield": 0.22, "market_cap": 591.74}}}
    builder._zones = lambda: {  # type: ignore[method-assign]
        "current_price": 34.17, "as_of": "2026-08-28",
        "valuation": {"status": "FAIR", "fair_value_low": 21.49, "fair_value_mid": 28.18,
                      "fair_value_high": 35.76, "methods": [
                          {"name": "同三级行业 PB 可比", "status": "READY", "peer_count": 15,
                           "multiple_low": 3.05, "multiple_mid": 4.00, "multiple_high": 5.08}]},
        "plain_summary": "历史估值分位暂缺（序列未物化）",
    }
    section = builder.build_valuation()
    payload = section["structured_payload"]
    assert payload["pe"] == 130.17 and payload["pb"] == 4.85
    assert payload["peer_methods"][0]["peer_count"] == 15
    text = section["narrative_md"]
    assert "130.17" in text and "4.85" in text and "0.22%" in text
    assert "P25/P50/P75 = 3.05 / 4.00 / 5.08" in text
    assert "未物化" in text  # historical valuation stays honestly missing


def test_financial_path_renders_ten_columns() -> None:
    """Fix §4: net margin / receivable / inventory / capex columns render; missing → —."""
    builder = CioSectionBuilder("CN", "X", "2026-08-28")
    builder._financial = lambda: {"history": [  # type: ignore[method-assign]
        {"period_type": "annual", "report_date": "2025-12-31", "revenue": 130.0,
         "net_profit": 4.0, "gross_margin": 20.0, "net_margin": 3.1, "roe": 3.3,
         "operating_cash_flow": 15.0, "accounts_receivable": 30.0, "inventory": 40.0,
         "debt_ratio": 52.1, "capex": 16.0},
        {"period_type": "annual", "report_date": "2024-12-31", "revenue": 112.0},
    ]}
    narrative = builder.build_financial_path()["narrative_md"]
    header = narrative.splitlines()[0]
    for column in ("净利率", "应收账款", "存货", "资本开支"):
        assert column in header
    assert "—" in narrative.splitlines()[3]  # 2024 row missing fields render as —


def test_leader_quality_renders_labels_not_dict_repr() -> None:
    """Fix §5: strengths/weaknesses become boss-readable labels."""
    builder = CioSectionBuilder("CN", "X", "2026-08-28")
    narrative = builder._leader_dim_lines(
        [{"dimension": "SCALE", "label": "规模", "status": "STRONG", "metrics": ["revenue"]}], "")
    assert narrative == ["规模强（revenue）"]
    assert "{'dimension'" not in "".join(narrative)


def test_no_thesis_watchpoint_fallback_is_deterministic(monkeypatch) -> None:
    """Fix §7: without a thesis, watchpoints degrade from persisted facts only."""
    builder = CioSectionBuilder("CN", "600460.SH", "2026-08-28")
    builder._thesis = lambda: {}  # type: ignore[method-assign]
    # Chronological ascending rows in yuan (matching persisted history shape).
    builder._financial = lambda: {  # type: ignore[method-assign]
        "history": [
            {"period_type": "annual", "report_date": "2024-12-31", "revenue": 112e8,
             "net_profit": 2.2e8, "gross_margin": 20.0, "operating_cash_flow": 4.4e8,
             "accounts_receivable": 33e8, "inventory": 38e8},
            {"period_type": "annual", "report_date": "2025-12-31", "revenue": 130e8,
             "net_profit": 4.0e8, "gross_margin": 21.0, "operating_cash_flow": 15.0e8,
             "accounts_receivable": 30e8, "inventory": 40e8},
        ],
        "forecast": {"scenarios": {"BASE": {"forecast": [{"year": "2028E", "revenue": 205.9e8}]}}},
    }
    builder._zones = lambda: {"valuation": {"fair_value_mid": 28.18}}  # type: ignore[method-assign]
    section = builder.build_thesis_watchpoints()
    text = section["narrative_md"]
    assert "尚未建立" in text and "确定性降级生成" in text
    assert "毛利率" in text and "经营现金流" in text and "205.90 亿" in text
    assert "2025年报" in text  # latest annual row drives the anchors
    import re as _re
    assert not _re.search(r"MA\d|止损|止盈|建仓|仓位", text)


def test_section_failure_never_leaks_exception_text(monkeypatch) -> None:
    """Fix §2.5: a broken section degrades to a friendly gap, no stack traces."""
    import src.cio_report.builder as builder_mod

    def _boom(_builder):
        raise ValueError("dictionary update sequence element #0 has length 4; 2 is required")

    # _BUILDERS captured the function references at import time, so patch the
    # registry entry itself.
    monkeypatch.setitem(builder_mod._BUILDERS, "leader_quality", _boom)
    sections = build_all_sections("CN", "600460.SH", "2026-08-28")
    all_text = "\n".join(s["narrative_md"] for s in sections)
    assert len(sections) == 14
    assert "ValueError" not in all_text and "Traceback" not in all_text
    assert "数据处理暂不可用" in all_text


def test_fingerprint_ignores_timestamp_drift_but_not_price_change() -> None:
    """Fix §9: quote/clock timestamps must not churn section fingerprints."""
    from src.cio_report.builder import _digest, _fingerprint_safe

    base = {"current_price": 34.17, "as_of": "2026-08-28",
            "data_dates": {"quote_as_of": "2026-08-28T14:20:28+08:00"}}
    drifted = {"current_price": 34.17, "as_of": "2026-08-29",
               "data_dates": {"quote_as_of": "2026-08-29T09:31:00+08:00"}}
    repriced = {**base, "current_price": 35.10}
    assert _digest({"p": _fingerprint_safe(base)}) == _digest({"p": _fingerprint_safe(drifted)})
    assert _digest({"p": _fingerprint_safe(base)}) != _digest({"p": _fingerprint_safe(repriced)})


def test_template_fallback_is_logged(tmp_path, monkeypatch, caplog) -> None:
    """Fix §10: synthesis failure logs context, never reaches the narrative."""
    import logging

    import src.cio_report.service as service_mod

    class _Freshness:
        def classify(self, market, code, as_of=None):
            return {"overall_freshness": "FRESH", "modules": []}

    monkeypatch.setattr(
        "src.research_freshness.get_research_freshness_service", lambda: _Freshness())
    monkeypatch.setattr(service_mod, "build_all_sections", lambda market, code, as_of: _simple_sections())
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model timeout")))
    with caplog.at_level(logging.WARNING, logger="src.cio_report.service"):
        report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert report["synthesis_source"] == "TEMPLATE_FALLBACK"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "TEMPLATE_FALLBACK" in joined and "600460.SH" in joined and "RuntimeError" in joined
    assert "model timeout" not in report["narrative_report_md"]


def test_trading_language_still_filtered_from_all_sections() -> None:
    section = CioSectionBuilder("CN", "X", "2026-08-28")._section(
        "cio_conclusion", {"v": 1}, "结论：继续观察；建议买入并控制仓位。")
    assert "买入" not in section["narrative_md"] and "仓位" not in section["narrative_md"]


def test_thesis_invalid_conditions_render_dict_entries_as_text() -> None:
    """Round1 follow-up: invalid_conditions may be {condition,status} dicts."""
    builder = CioSectionBuilder("CN", "605108.SH", "2026-08-28")
    builder._thesis = lambda: {  # type: ignore[method-assign]
        "title": "初步核心逻辑", "status": "FORMING", "authority_status": "AI_PROVISIONAL",
        "core_thesis": "草案。",
        "invalid_conditions": [
            {"condition": "盈利或经营现金流连续恶化。", "status": "ACTIVE"},
            {"condition": "核心业务持续收缩。", "status": "ACTIVE"},
        ],
    }
    builder._financial = lambda: {"analysis": {"key_metrics_to_monitor": ["毛利率"]}}  # type: ignore[method-assign]
    text = builder.build_thesis_watchpoints()["narrative_md"]
    assert "- 盈利或经营现金流连续恶化。" in text
    assert "{'condition'" not in text and "AI 初步待复核" in text

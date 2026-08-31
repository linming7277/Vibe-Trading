"""CIO Delivery Polish V1 contracts: synthesis retry + Quick Brief projection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import src.cio_report.service as service_mod
from src.cio_report.builder import SECTION_TITLES
from src.cio_report.quick_brief import build_quick_brief, render_quick_brief_md
from src.cio_report.service import CioReportService
from src.cio_report.store import CioReportStore

_TRADING = re.compile(r"买入|卖出|推荐|止盈|止损|仓位|加仓|减仓|建仓|不碰|试仓")


def _store(tmp_path: Path) -> CioReportStore:
    return CioReportStore(tmp_path / "research.db")


def _exc_class(name: str) -> type[Exception]:
    return type(name, (Exception,), {})


class _Freshness:
    def classify(self, market, code, as_of=None):
        return {"overall_freshness": "FRESH", "modules": []}


def _fixture_sections(*, tier: str | None = None, risk: str = "MEDIUM",
                      thesis: bool = True, unknown_risk: bool = False) -> list[dict[str, Any]]:
    conclusion_verdict = {"A": "重点研究", "B": "继续观察", "C": "暂缓优先研究"}.get(tier or "", "继续观察")
    risk_payload = {"overall_risk": "UNKNOWN" if unknown_risk else risk,
                    "value_trap_risk": "HIGH_TRAP_RISK" if risk == "HIGH" else "NONE"}
    sections = [
        {"section_type": "company_position", "title": SECTION_TITLES["company_position"],
         "input_fingerprint": "fp0", "freshness_status": "REFRESHED",
         "structured_payload": {"stock_name": "样本公司"}, "narrative_md": "定位", "source_refs": []},
        {"section_type": "quality_risk", "title": SECTION_TITLES["quality_risk"],
         "input_fingerprint": "fp4", "freshness_status": "REFRESHED",
         "structured_payload": risk_payload, "narrative_md": "风险", "source_refs": []},
        {"section_type": "why_research", "title": SECTION_TITLES["why_research"],
         "input_fingerprint": "fp10", "freshness_status": "REFRESHED",
         "structured_payload": {"reasons": ["估值处于低估区域（DEEPLY_UNDERVALUED）",
                                            "估值处于低估区域（UNDERVALUED）",
                                            "规模优势强"]},
         "narrative_md": "", "source_refs": []},
        {"section_type": "why_caution", "title": SECTION_TITLES["why_caution"],
         "input_fingerprint": "fp11", "freshness_status": "REFRESHED",
         "structured_payload": {"cautions": ["[HIGH] VALUE_TRAP：多项风险并存",
                                             "[MEDIUM] 负债率上升",
                                             "当前规则层无明确谨慎信号"]},
         "narrative_md": "", "source_refs": []},
        {"section_type": "valuation", "title": SECTION_TITLES["valuation"],
         "input_fingerprint": "fp8", "freshness_status": "REFRESHED",
         "structured_payload": {"current_price": 34.17, "valuation_status": "FAIR",
                                "fair_value_low": 21.49, "fair_value_mid": 28.18,
                                "fair_value_high": 35.76, "pe": 130.17, "pb": 4.85,
                                "peer_methods": [{"name": "同三级行业 PB 可比", "status": "READY",
                                                  "peer_count": 15, "multiple_low": 3.05,
                                                  "multiple_mid": 4.00, "multiple_high": 5.08}],
                                "plain_summary": "历史估值分位暂缺（序列未物化）"},
         "narrative_md": "", "source_refs": []},
        {"section_type": "thesis_watchpoints", "title": SECTION_TITLES["thesis_watchpoints"],
         "input_fingerprint": "fp12", "freshness_status": "REFRESHED",
         "structured_payload": (
             {"thesis_title": "初步核心逻辑", "thesis_status": "FORMING",
              "authority_status": "AI_PROVISIONAL", "authority_label": "AI 初步待复核",
              "key_metrics_to_monitor": ["毛利率", "经营现金流"], "invalid_conditions": [], "fallback_watchpoints": []}
             if thesis else
             {"thesis_title": None, "key_metrics_to_monitor": [], "invalid_conditions": [],
              "fallback_watchpoints": ["毛利率是否延续修复（2025年报 20.00%）", "OCF 与净利匹配程度", "Base 情景末年营收兑现"]}
         ), "narrative_md": "", "source_refs": []},
        {"section_type": "cio_conclusion", "title": SECTION_TITLES["cio_conclusion"],
         "input_fingerprint": f"fp13-{conclusion_verdict}", "freshness_status": "REFRESHED",
         "structured_payload": {"verdict": conclusion_verdict, "focus_tier": tier,
                                "valuation_status": "DEEPLY_UNDERVALUED" if tier == "C" else "FAIR"},
         "narrative_md": "", "source_refs": []},
    ]
    present = {s["section_type"] for s in sections}
    for name, title in SECTION_TITLES.items():
        if name not in present:
            sections.insert(0, {"section_type": name, "title": title, "input_fingerprint": f"fp-{name}",
                                "freshness_status": "REUSED", "structured_payload": {}, "narrative_md": "", "source_refs": []})
    return sections


def _patch_common(monkeypatch, sections: list[dict[str, Any]] | None = None) -> None:
    monkeypatch.setattr(
        "src.research_freshness.get_research_freshness_service", lambda: _Freshness())
    monkeypatch.setattr(service_mod, "build_all_sections",
                        lambda market, code, as_of: sections if sections is not None else _fixture_sections())
    monkeypatch.setattr(service_mod.time, "sleep", lambda _s: None)  # no real backoff in tests


# --- synthesis retry --------------------------------------------------------

def test_transient_timeout_retries_once_then_succeeds(tmp_path, monkeypatch) -> None:
    calls: list[int] = []

    def _flaky(stock_code, as_of, sections):
        calls.append(1)
        if len(calls) == 1:
            raise _exc_class("APITimeoutError")("Request timed out.")
        return "report-md", "glm-5.3"

    _patch_common(monkeypatch)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", _flaky)
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert len(calls) == 2
    assert report["synthesis_status"] == "LLM_COMPLETED"
    assert report["narrative_report_md"] == "report-md"


def test_transient_timeout_twice_falls_back_to_template(tmp_path, monkeypatch) -> None:
    calls: list[int] = []

    def _always_timeout(stock_code, as_of, sections):
        calls.append(1)
        raise _exc_class("APITimeoutError")("Request timed out.")

    _patch_common(monkeypatch)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", _always_timeout)
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert len(calls) == 2  # exactly one retry, never more
    assert report["synthesis_status"] == "TEMPLATE_FALLBACK"
    assert "投研主管深度研究报告" in report["narrative_report_md"]


def test_non_retryable_error_never_retries(tmp_path, monkeypatch) -> None:
    calls: list[int] = []

    def _auth_error(stock_code, as_of, sections):
        calls.append(1)
        raise _exc_class("AuthenticationError")("bad key")

    _patch_common(monkeypatch)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", _auth_error)
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert len(calls) == 1
    assert report["synthesis_status"] == "TEMPLATE_FALLBACK"


def test_fresh_data_and_template_fallback_coexist(tmp_path, monkeypatch) -> None:
    """research_freshness=FRESH + synthesis_status=TEMPLATE_FALLBACK is a usable report."""
    _patch_common(monkeypatch)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(
        _exc_class("APIConnectionError")("Connection error.")))
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert report["research_freshness"] == "FRESH"
    assert report["synthesis_status"] == "TEMPLATE_FALLBACK"


def test_explicit_full_report_request_retries_synthesis_only(tmp_path, monkeypatch) -> None:
    """FRESH+TEMPLATE report: force_synthesis retries the LLM, never the research."""
    sections = _fixture_sections()
    _patch_common(monkeypatch, sections)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(
        _exc_class("APITimeoutError")("timeout")))
    first = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert first["synthesis_status"] == "TEMPLATE_FALLBACK"

    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: ("恢复后的综合", "glm-5.3"))
    recovered = svc.build_report("CN", "600460.SH", as_of="2026-08-28", force_synthesis=True)
    assert recovered["synthesis_status"] == "LLM_COMPLETED"
    assert recovered["narrative_report_md"] == "恢复后的综合"
    assert recovered["input_fingerprint"] == first["input_fingerprint"]  # research untouched

    # Ordinary read of the same fingerprint never retries the model.
    def _must_not_run(*a, **k):  # pragma: no cover
        raise AssertionError("plain read must not call synthesis")
    monkeypatch.setattr(svc, "_synthesize", _must_not_run)
    again = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    assert again["idempotent_reuse"] is True


# --- quick brief ------------------------------------------------------------

def _saved_report(tmp_path: Path, monkeypatch, *, tier: str | None = "A",
                  thesis: bool = True, unknown_risk: bool = False) -> dict[str, Any]:
    sections = _fixture_sections(tier=tier, thesis=thesis, unknown_risk=unknown_risk)
    _patch_common(monkeypatch, sections)
    svc = CioReportService(_store(tmp_path))
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: ("md", "glm-5.3"))
    return svc.build_report("CN", "600460.SH", as_of="2026-08-28")


def test_quick_brief_zero_llm_zero_refresh(tmp_path, monkeypatch) -> None:
    svc = CioReportService(_store(tmp_path))
    sections = _fixture_sections(tier="A")
    _patch_common(monkeypatch, sections)
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: ("md", "glm-5.3"))
    svc.build_report("CN", "600460.SH", as_of="2026-08-28")

    def _must_not_run(*a, **k):  # pragma: no cover
        raise AssertionError("quick brief must not call synthesis or rebuild sections")
    monkeypatch.setattr(svc, "_synthesize", _must_not_run)
    monkeypatch.setattr(service_mod, "build_all_sections", _must_not_run)
    brief = svc.get_quick_brief("CN", "600460.SH", as_of="2026-08-28")
    assert brief["verdict"] == "重点研究"


def test_quick_brief_missing_report_raises_not_found(tmp_path) -> None:
    import pytest

    with pytest.raises(ValueError, match="CIO_REPORT_NOT_FOUND"):
        CioReportService(_store(tmp_path)).get_quick_brief("CN", "999999.SH")


def test_quick_brief_tier_verdict_mapping(tmp_path, monkeypatch) -> None:
    for tier, verdict in (("A", "重点研究"), ("B", "继续观察"), ("C", "暂缓优先研究")):
        report = _saved_report(tmp_path, monkeypatch, tier=tier)
        brief = build_quick_brief(report)
        assert brief.verdict == verdict
        assert brief.focus_tier == tier


def test_quick_brief_unknown_risk_worded_as_data_gap(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="B", unknown_risk=True)
    brief = build_quick_brief(report)
    assert any("资料不足" in c and "并非低风险" in c for c in brief.cautions)
    assert not any("无明确谨慎信号" in c for c in brief.cautions)


def test_quick_brief_ai_provisional_wording(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="A")
    brief = build_quick_brief(report)
    assert brief.thesis_authority == "AI初步核心逻辑 · 待人工复核"
    assert brief.thesis_summary.startswith("初步核心逻辑")
    # why/cautions must actually project from the section payload (a past bug
    # read them off the section dict itself and silently produced empty lists)
    assert len(brief.why_research) == 2  # the duplicate 低估 phrasing collapses to 1 + 规模优势
    assert brief.why_research[0].startswith("估值处于低估区域")
    assert any("VALUE_TRAP" in c for c in brief.cautions)
    # dict-shaped invalid_conditions never leak as Python repr
    assert not any("{'condition'" in w for w in brief.watchpoints)


def test_quick_brief_no_thesis_fallback(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="B", thesis=False)
    brief = build_quick_brief(report)
    assert brief.thesis_summary == "当前尚未建立正式核心逻辑。"
    assert any("毛利率" in w for w in brief.watchpoints)


def test_quick_brief_valuation_fields(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="A")
    summary = build_quick_brief(report).valuation_summary
    for expected in ("34.17", "21.49", "35.76", "28.18", "+21.3%", "130.17", "4.85", "15 家"):
        assert expected in summary
    assert "尚未物化" in summary  # historical percentile stays honest


def test_quick_brief_inherits_report_fingerprint_and_status(tmp_path, monkeypatch) -> None:
    svc = CioReportService(_store(tmp_path))
    sections = _fixture_sections()
    _patch_common(monkeypatch, sections)
    monkeypatch.setattr(svc, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(
        _exc_class("APITimeoutError")("timeout")))
    report = svc.build_report("CN", "600460.SH", as_of="2026-08-28")
    brief = build_quick_brief(report)
    assert brief.source_report_id == report["id"]
    assert brief.input_fingerprint == report["input_fingerprint"]
    assert brief.research_freshness == "FRESH"
    assert brief.synthesis_status == "TEMPLATE_FALLBACK"


def test_quick_brief_repeated_calls_deterministic(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="A")
    first = build_quick_brief(report).as_dict()
    second = build_quick_brief(report).as_dict()
    assert first == second


def test_quick_brief_trading_language_absent_and_feishu_shape(tmp_path, monkeypatch) -> None:
    report = _saved_report(tmp_path, monkeypatch, tier="A")
    md = render_quick_brief_md(build_quick_brief(report))
    assert not _TRADING.search(md)
    for block in ("【研究结论】", "【为什么值得看】", "【主要风险】", "【当前估值】", "【核心逻辑】", "【接下来重点看】"):
        assert block in md
    assert "需要完整分析时可查看 CIO 深度报告" in md
    assert "01 公司与产业位置" not in md  # never inlines the 14 sections


def test_legacy_synthesis_values_normalized_on_read(tmp_path) -> None:
    store = _store(tmp_path)
    store.save_report(
        market="CN", stock_code="600460.SH", research_as_of="2026-08-28",
        overall_freshness="FRESH", input_fingerprint="R1", module_hashes={},
        sections=[], narrative_report_md="x", synthesis_source="LLM",
        formula_version="v", prompt_version="p", model_version="", previous_report_id=None,
    )
    report = CioReportService(store).get_report("CN", "600460.SH")
    assert report["synthesis_status"] == "LLM_COMPLETED"

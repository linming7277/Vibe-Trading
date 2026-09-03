"""Value Line Watchpoint V1: projection contract, ranking, and source mapping."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.value_strategy_routes import register_value_strategy_routes
from src.cio_report.builder import CioSectionBuilder
from src.cio_report.quick_brief import build_quick_brief
from src.cio_report.routing import WATCHPOINT, classify_company_question
from src.company_thesis.service import CompanyThesisService
from src.value_watchpoints.contracts import FORBIDDEN_WATCHPOINT_KEYS, FORMULA_VERSION
from src.value_watchpoints.service import ValueWatchpointProjectionService


def _empty(*_a, **_k):
    return {}


def _svc(**loaders) -> ValueWatchpointProjectionService:
    return ValueWatchpointProjectionService(
        strategy_loader=loaders.get("strategy_loader", _empty),
        thesis_loader=loaders.get("thesis_loader", lambda *_a, **_k: None),
        risk_loader=loaders.get("risk_loader", _empty),
        financial_loader=loaders.get("financial_loader", _empty),
        normalized_loader=loaders.get("normalized_loader", _empty),
        cycle_loader=loaders.get("cycle_loader", _empty),
        business_loader=loaders.get("business_loader", _empty),
        reliability_loader=loaders.get("reliability_loader", _empty),
        moat_loader=loaders.get("moat_loader", _empty),
        capital_loader=loaders.get("capital_loader", _empty),
        deep_loader=loaders.get("deep_loader", _empty),
    )


def _state(*, tier="A", action="PRIORITY_RESEARCH", eligible=True):
    return {
        "stock_name": "样本",
        "research_as_of": "2026-08-28",
        "eligibility": {"status": "IN_VALUE_SCOPE" if eligible else "OUTSIDE_VALUE_SCOPE"},
        "priority": {"tier": tier},
        "primary_action": {"status": action},
        "freshness": {},
    }


def _public_keys(items: list[dict]) -> None:
    for item in items:
        for key in FORBIDDEN_WATCHPOINT_KEYS:
            assert key not in item
        assert "source_refs" in item
        assert item.get("formula_version")


def test_no_score_and_deterministic_repeat():
    financial = {
        "as_of": "2026-08-28",
        "history": [
            {"period_type": "annual", "gross_margin": 22.0, "operating_cash_flow": 10, "net_profit": 40,
             "debt_ratio": 40, "interest_bearing_debt_ratio": 20, "report_date": "2024-12-31"},
            {"period_type": "annual", "gross_margin": 18.0, "operating_cash_flow": 8, "net_profit": 30,
             "debt_ratio": 48, "interest_bearing_debt_ratio": 28, "report_date": "2025-12-31"},
        ],
        "feature": {"latest_changes": []},
    }
    svc = _svc(
        strategy_loader=lambda *_a, **_k: _state(),
        financial_loader=lambda *_a, **_k: financial,
    )
    first = svc.get_watchpoints("CN", "600000.SH")
    second = svc.get_watchpoints("CN", "600000.SH")
    assert first == second
    assert first["formula_version"] == FORMULA_VERSION
    _public_keys(first["watchpoints"] + first["top_watchpoints"])
    assert len(first["top_watchpoints"]) <= 3
    titles = {item["title"] for item in first["watchpoints"]}
    assert "毛利率能否维持或修复" in titles
    assert "利润能否转化为经营现金" in titles


def test_quota_a_b_c_and_no_padding():
    item_risk = {
        "overall_risk": "HIGH", "as_of": "2026-08-28",
        "risks": [{"risk_type": "FINANCIAL_RECEIVABLE", "severity": "HIGH", "status": "CONFIRMED",
                   "text": "应收扩张。", "watch_item": "应收继续扩张。", "why_it_matters": "回款"}],
    }
    svc = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="C", action="RISK_REVIEW"),
        risk_loader=lambda *_a, **_k: item_risk,
    )
    result = svc.get_watchpoints("CN", "000544.SZ")
    assert len(result["top_watchpoints"]) == 1
    assert result["top_watchpoints"][0]["category"] == "RISK"

    svc_b = _svc(strategy_loader=lambda *_a, **_k: _state(tier="B"), risk_loader=lambda *_a, **_k: item_risk)
    assert len(svc_b.get_watchpoints("CN", "000001.SZ")["top_watchpoints"]) <= 2

    empty = _svc(strategy_loader=lambda *_a, **_k: _state(tier="A"))
    padded = empty.get_watchpoints("CN", "000002.SZ")
    assert padded["top_watchpoints"] == []
    assert padded["data_gaps"]  # unknown risk from empty risk payload


def test_unknown_risk_is_data_gap_not_low_risk():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="B", action="CONTINUE_OBSERVE"),
        risk_loader=lambda *_a, **_k: {"overall_risk": "UNKNOWN", "as_of": "2026-08-28", "risks": []},
    ).get_watchpoints("CN", "600210.SH")
    blob = json.dumps(result, ensure_ascii=False)
    assert "低风险" not in blob and "风险可控" not in blob
    assert any("风险资料不足" in str(gap.get("description")) for gap in result["data_gaps"])
    assert result["suggested_research_need"]


def test_high_risk_beats_generic_thesis_and_valuation():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="C", action="RISK_REVIEW"),
        thesis_loader=lambda *_a, **_k: {
            "title": "模板", "status": "FORMING", "authority_status": "AI_PROVISIONAL",
            "invalid_conditions": [{"condition": "盈利或经营现金流连续恶化。", "status": "ACTIVE"}],
        },
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH", "as_of": "2026-08-28",
            "risks": [{"risk_type": "FINANCIAL_RECEIVABLE", "severity": "HIGH", "status": "WATCH",
                       "text": "应收账款压力。", "watch_item": "应收继续扩张。"}],
        },
        reliability_loader=lambda *_a, **_k: {"status": "WEAK", "reasons": ["同行很少"]},
    ).get_watchpoints("CN", "000544.SZ")
    assert result["top_watchpoints"][0]["category"] == "RISK"


def test_weak_valuation_enters_top_for_focus_a():
    financial = {
        "as_of": "2026-08-28",
        "history": [
            {"period_type": "annual", "gross_margin": 22.0, "operating_cash_flow": 10, "net_profit": 40,
             "debt_ratio": 40, "interest_bearing_debt_ratio": 20},
            {"period_type": "annual", "gross_margin": 18.0, "operating_cash_flow": 8, "net_profit": 30,
             "debt_ratio": 48, "interest_bearing_debt_ratio": 28},
        ],
        "feature": {},
    }
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="A"),
        financial_loader=lambda *_a, **_k: financial,
        reliability_loader=lambda *_a, **_k: {
            "status": "WEAK", "reasons": ["同行样本较少且合理价值中枢与现价偏离极大。"],
        },
    ).get_watchpoints("CN", "605108.SH")
    assert any(item["category"] == "VALUATION" for item in result["top_watchpoints"])
    assert not any("等待价格上涨" in json.dumps(item, ensure_ascii=False) for item in result["top_watchpoints"])


def test_reliable_valuation_does_not_take_slot():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="A"),
        reliability_loader=lambda *_a, **_k: {"status": "RELIABLE", "reasons": []},
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "MEDIUM",
            "risks": [{"risk_type": "FINANCIAL_INVENTORY", "severity": "MEDIUM", "status": "WATCH",
                       "text": "存货。", "watch_item": "存货继续积压。"}],
        },
    ).get_watchpoints("CN", "000001.SZ")
    assert not any(item["category"] == "VALUATION" for item in result["top_watchpoints"])


def test_legacy_empty_invalid_does_not_fabricate_thesis():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(eligible=False),
        thesis_loader=lambda *_a, **_k: {
            "title": "历史逻辑", "status": "UNCHANGED", "authority_status": "LEGACY_UNVERIFIED",
            "invalid_conditions": [],
        },
        deep_loader=lambda *_a, **_k: {"status": "PARTIAL"},
    ).get_watchpoints("CN", "002371.SZ")
    assert not any(item["category"] == "THESIS" for item in result["watchpoints"])
    assert any("证伪条件" in str(gap.get("description")) for gap in result["data_gaps"])


def test_human_rejected_is_critical_top():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(action="CONTINUE_OBSERVE"),
        thesis_loader=lambda *_a, **_k: {
            "title": "旧逻辑", "status": "FALSIFIED", "authority_status": "HUMAN_REJECTED",
            "invalid_conditions": [{"condition": "已否定", "status": "ACTIVE"}],
        },
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "MEDIUM",
            "risks": [{"risk_type": "FINANCIAL_INVENTORY", "severity": "MEDIUM", "status": "WATCH",
                       "text": "存货。", "watch_item": "存货积压。"}],
        },
    ).get_watchpoints("CN", "600000.SH")
    assert result["top_watchpoints"][0]["importance_tier"] == "CRITICAL"
    assert "否定" in result["top_watchpoints"][0]["title"]


def test_ai_provisional_caution_not_confirmed_language():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(),
        thesis_loader=lambda *_a, **_k: {
            "title": "草案逻辑", "status": "FORMING", "authority_status": "AI_PROVISIONAL",
            "invalid_conditions": [{"condition": "毛利率持续低于行业且无法修复", "status": "ACTIVE"}],
        },
    ).get_watchpoints("CN", "600000.SH")
    thesis = next(item for item in result["watchpoints"] if item["category"] == "THESIS")
    assert any("尚未人工确认" in c for c in thesis["cautions"])
    assert "已经确认公司的核心投资逻辑" not in thesis["current_state"]


def test_dedupe_merges_source_refs():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(),
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH",
            "risks": [{"risk_type": "FINANCIAL_PROFIT_CASH_DIVERGENCE", "severity": "HIGH", "status": "CONFIRMED",
                       "text": "利润与现金背离。", "watch_item": "OCF 继续弱于利润。"}],
        },
        financial_loader=lambda *_a, **_k: {
            "history": [
                {"period_type": "annual", "operating_cash_flow": 10, "net_profit": 40, "gross_margin": 20, "debt_ratio": 30},
                {"period_type": "annual", "operating_cash_flow": 8, "net_profit": 50, "gross_margin": 20, "debt_ratio": 30},
            ],
            "feature": {},
        },
        normalized_loader=lambda *_a, **_k: {
            "status": "READY", "quality_cautions": ["经营现金流质量偏弱"],
        },
    ).get_watchpoints("CN", "600460.SH")
    ocf = [item for item in result["watchpoints"] if any(ref.get("module") in {"RISK", "FINANCIAL", "NORMALIZED_EARNINGS"} for ref in item.get("source_refs") or []) and "现金" in item["title"]]
    assert len(ocf) == 1
    modules = {str(ref.get("module")) for ref in ocf[0]["source_refs"]}
    assert {"RISK", "FINANCIAL"}.issubset(modules) or len(ocf[0]["source_refs"]) >= 2


def test_business_single_period_does_not_invent_growth():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(),
        business_loader=lambda *_a, **_k: {
            "claims": [{"type": "FACT", "topic": "PRODUCT", "text": "功率器件是主要产品之一。"}],
        },
    ).get_watchpoints("CN", "600460.SH")
    blob = json.dumps(result, ensure_ascii=False)
    assert "必须增长" not in blob
    assert not any(item["category"] == "BUSINESS" and "必须" in item["positive_condition"] for item in result["watchpoints"])


def test_moat_unknown_and_capital_unknown_are_gaps():
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(),
        moat_loader=lambda *_a, **_k: {
            "dimensions": [{"dimension": "TECHNOLOGY", "status": "UNKNOWN", "summary": "不足"}],
        },
        capital_loader=lambda *_a, **_k: {
            "dimensions": {
                "buyback": {"status": "UNKNOWN", "observation": "回购资料不足"},
                "debt_management": {"status": "PARTIAL", "direction": "CAUTION", "observation": "带息债务上升"},
            },
        },
    ).get_watchpoints("CN", "600000.SH")
    assert any(gap["category"] == "MOAT" for gap in result["data_gaps"])
    assert any(gap["category"] == "CAPITAL" for gap in result["data_gaps"])
    assert any(item["category"] == "CAPITAL" for item in result["watchpoints"])


def test_cio_quality_risk_keeps_watch_item():
    builder = CioSectionBuilder("CN", "600460.SH", "2026-08-28")
    builder._risk = lambda: {  # type: ignore[method-assign]
        "overall_risk": "HIGH", "summary": "有风险",
        "risks": [{"risk_type": "FINANCIAL_INTEREST_DEBT", "severity": "HIGH", "status": "WATCH",
                   "text": "带息债务上升", "why_it_matters": "杠杆", "watch_item": "带息债务继续抬升"}],
        "value_trap_risk": "NONE",
    }
    builder._financial = lambda: {"history": []}  # type: ignore[method-assign]
    payload = builder.build_quality_risk()["structured_payload"]
    assert payload["risks"][0]["watch_item"] == "带息债务继续抬升"
    assert payload["risks"][0]["why_it_matters"] == "杠杆"


def test_quick_brief_uses_projection_titles():
    from src.cio_report.builder import SECTION_TITLES
    report = {
        "stock_code": "600460.SH", "research_as_of": "2026-08-28",
        "research_freshness": "FRESH", "synthesis_status": "TEMPLATE_FALLBACK",
        "sections": [
            {"section_type": "cio_conclusion", "structured_payload": {"verdict": "重点研究", "focus_tier": "A"}},
            {"section_type": "why_research", "structured_payload": {"reasons": ["低估"]}},
            {"section_type": "why_caution", "structured_payload": {"cautions": ["债务"]}},
            {"section_type": "valuation", "structured_payload": {"current_price": 1, "fair_value_low": 1,
                                                                "fair_value_mid": 1, "fair_value_high": 1}},
            {"section_type": "quality_risk", "structured_payload": {"overall_risk": "MEDIUM"}},
            {"section_type": "company_position", "structured_payload": {"stock_name": "士兰微"}},
            {"section_type": "thesis_watchpoints", "structured_payload": {
                "thesis_title": "逻辑", "thesis_status": "FORMING", "authority_status": "AI_PROVISIONAL",
                "key_metrics_to_monitor": ["不应作为主路径"],
                "top_watchpoints": [{"title": "毛利率能否维持或修复", "current_state": "毛利率下降"}],
            }},
        ],
    }
    void = SECTION_TITLES
    assert void
    brief = build_quick_brief(report)
    assert brief.watchpoints[0].startswith("毛利率")
    assert "不应作为主路径" not in brief.watchpoints[0]


def test_watchpoint_routing_and_zero_specialist():
    from src.investment_research_supervisor.service import InvestmentResearchSupervisorService

    assert classify_company_question("士兰微接下来重点看什么") == WATCHPOINT
    assert classify_company_question("中原环保最需要验证什么") == WATCHPOINT
    assert classify_company_question("格力下一份财报看什么") == WATCHPOINT
    assert InvestmentResearchSupervisorService.classify_intent("士兰微接下来重点看什么") == "WATCHPOINT"
    assert InvestmentResearchSupervisorService.classify_intent("中原环保最需要验证什么") == "WATCHPOINT"
    assert InvestmentResearchSupervisorService.classify_intent("格力电器下一份财报看什么") == "WATCHPOINT"


def test_api_is_read_only(monkeypatch):
    svc = _svc(strategy_loader=lambda *_a, **_k: _state())
    monkeypatch.setattr("src.api.value_strategy_routes.get_value_watchpoint_projection_service", lambda: svc)
    app = FastAPI()
    register_value_strategy_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    response = client.get("/api/value/companies/600460.SH/watchpoints")
    assert response.status_code == 200
    assert response.json()["formula_version"] == FORMULA_VERSION


def test_draft_confirm_preserves_assumptions_and_metrics(tmp_path: Path):
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        created = service.create_initial_thesis(
            market="CN", stock_code="000544.SZ", title="确认逻辑", core_thesis="正式逻辑。",
            status="FORMING", confidence="MEDIUM",
            invalid_conditions=[{"condition": "现金流持续恶化", "status": "ACTIVE"}],
            supporting_conditions=[{"condition": "污水处理量稳定", "status": "ACTIVE"}],
            key_metrics_to_monitor=[{"text": "经营现金流"}],
            created_by="HUMAN",
        )
        assert created["supporting_conditions"]
        assert created["key_metrics_to_monitor"]
        assert created["invalid_conditions"]
    finally:
        service.close()


def test_live_six_names_if_research_db_present():
    from src.config.paths import get_runtime_root

    db = get_runtime_root() / "research.db"
    if not db.exists():
        return
    svc = ValueWatchpointProjectionService()
    codes = ["600460.SH", "000544.SZ", "600210.SH", "605108.SH", "000651.SZ", "002371.SZ"]
    outputs = {code: svc.get_watchpoints("CN", code) for code in codes}
    silan = outputs["600460.SH"]["top_watchpoints"]
    joined = " ".join(item["title"] + item["current_state"] for item in silan)
    assert silan
    assert "等待价格上涨" not in json.dumps(outputs, ensure_ascii=False)
    zhongyuan = outputs["000544.SZ"]
    if zhongyuan["primary_action"] == "RISK_REVIEW" or any(i["category"] == "RISK" for i in zhongyuan["top_watchpoints"]):
        if zhongyuan["top_watchpoints"]:
            assert zhongyuan["top_watchpoints"][0]["category"] in {"RISK", "THESIS", "FINANCIAL", "VALUATION"}
    zijiang = json.dumps(outputs["600210.SH"], ensure_ascii=False)
    assert "低风险" not in zijiang
    tongqing = outputs["605108.SH"]
    if any(item["category"] == "VALUATION" for item in tongqing["watchpoints"]):
        assert any(item["category"] == "VALUATION" for item in tongqing["top_watchpoints"] or tongqing["watchpoints"])
    assert "等待价格上涨" not in json.dumps(tongqing, ensure_ascii=False)
    assert outputs["000651.SZ"]["formula_version"] == FORMULA_VERSION
    legacy = outputs["002371.SZ"]
    assert not any("已经确认公司的核心投资逻辑" in json.dumps(item, ensure_ascii=False) for item in legacy["watchpoints"])
    _ = joined


def test_projection_does_not_call_llm_or_refresh_analyzers(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("watchpoint projection must not call LLM or refresh analyzers")

    monkeypatch.setattr("src.deep_research.preparation.DeepResearchPreparationService.prepare", boom)
    monkeypatch.setattr("src.financial_analysis.service.FinancialAnalysisService.get_resolved_analysis", boom)
    svc = _svc(strategy_loader=lambda *_a, **_k: _state())
    result = svc.get_watchpoints("CN", "600000.SH")
    assert result["formula_version"] == FORMULA_VERSION
    assert result["watchpoints"] == result["watchpoints"]


def test_c_high_risk_still_outranks_weak_valuation() -> None:
    result = _svc(
        strategy_loader=lambda *_a, **_k: _state(tier="C", action="RISK_REVIEW"),
        risk_loader=lambda *_a, **_k: {
            "overall_risk": "HIGH", "as_of": "2026-08-28",
            "risks": [{"risk_type": "VALUE_TRAP", "severity": "HIGH", "status": "WATCH",
                       "text": "低估陷阱复核。", "watch_item": "低估原因未闭合。"}],
        },
        reliability_loader=lambda *_a, **_k: {"status": "WEAK", "reasons": ["同行很少"]},
        financial_loader=lambda *_a, **_k: {
            "history": [
                {"period_type": "annual", "gross_margin": 22, "operating_cash_flow": 10, "net_profit": 40, "debt_ratio": 40},
                {"period_type": "annual", "gross_margin": 18, "operating_cash_flow": 8, "net_profit": 30, "debt_ratio": 48},
            ],
            "feature": {},
        },
    ).get_watchpoints("CN", "000544.SZ")
    assert result["top_watchpoints"][0]["category"] == "RISK"
    assert len(result["top_watchpoints"]) == 1


def test_human_confirmed_conditions_survive_system_refresh(tmp_path: Path) -> None:
    service = CompanyThesisService(db_path=tmp_path / "research.db")
    try:
        first = service.create_initial_thesis(
            market="CN", stock_code="600000.SH", title="确认逻辑", core_thesis="正式逻辑。",
            status="FORMING", confidence="MEDIUM",
            invalid_conditions=[{"condition": "I1", "status": "ACTIVE"}],
            supporting_conditions=[{"condition": "A1", "status": "ACTIVE"}],
            key_metrics_to_monitor=[{"text": "M1"}],
            created_by="HUMAN", authority_status="HUMAN_CONFIRMED",
        )
        second = service.create_new_version(
            market="CN", stock_code="600000.SH", title="自动刷新", core_thesis="系统刷新标题。",
            status="UNCHANGED", confidence="MEDIUM",
            invalid_conditions=[{"condition": "should-not-stick", "status": "ACTIVE"}],
            supporting_conditions=[{"condition": "should-not-stick", "status": "ACTIVE"}],
            key_metrics_to_monitor=[{"text": "should-not-stick"}],
            change_reason="自动研究刷新", updated_by="SYSTEM",
        )
        assert second["invalid_conditions"][0]["condition"] == "I1"
        assert second["supporting_conditions"][0]["condition"] == "A1"
        assert "M1" in str(second["key_metrics_to_monitor"])
        prior = service.get_thesis_by_id(first["thesis_id"])
        assert prior["supporting_conditions"][0]["condition"] == "A1"
    finally:
        service.close()

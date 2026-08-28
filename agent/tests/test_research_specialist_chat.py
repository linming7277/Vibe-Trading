from __future__ import annotations

from typing import Any

import pytest

from src.research_specialist_chat import ResearchSpecialistChatService


AS_OF = "2026-08-25"


class FakeStore:
    def get_runtime_config(self, role: str) -> dict[str, Any]:
        return {
            "role": role,
            "provider": "test",
            "model": f"{role}-model",
            "base_url": "",
            "api_key": "",
            "enabled": True,
        }


class FakeRuntime:
    def __init__(self, answer: str = "已根据本地研究数据完成解释。") -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        context = dict(kwargs["payload"]["context"])
        return {"answer": self.answer, "source_keys": list(context), "data_gaps": []}


class FakeRisk:
    def __init__(self, *, unknown: bool = False) -> None:
        self.unknown = unknown
        self.calls: list[tuple[str, str, str]] = []

    def get_risk_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {
            "stock_code": stock_code,
            "as_of": as_of,
            "status": "PARTIAL" if self.unknown else "READY",
            "overall_risk": "UNKNOWN" if self.unknown else "MEDIUM",
            "summary": "存在一项需要复核的风险。",
            "data_quality": {"financial": "READY", "business": "MISSING", "thesis": "MISSING"},
            "risks": [{"risk_type": "CASH_FLOW", "severity": "MEDIUM"}],
        }


class FakeZones:
    def get_price_zones(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        return {
            "stock_code": stock_code,
            "as_of": as_of,
            "current_price": 10,
            "valuation": {"status": "UNDERVALUED", "fair_value_mid": 15},
            "plain_summary": "当前价格低于合理价值中枢。",
        }


class FakeHistorical:
    def get_valuation_history(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        return {"as_of": as_of, "historical_valuation_status": "CHEAP", "pe_percentile": 20}


class FakeEntryExit:
    def get_entry_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        return {"as_of": as_of, "entry_level": "ATTENTION", "plain_explanation": "值得继续研究。"}

    def get_exit_research(self, market: str, stock_code: str, *, as_of: str) -> dict[str, Any]:
        return {"as_of": as_of, "exit_level": "NORMAL", "plain_explanation": "暂无明显压力。"}


class FakeMacro:
    def get(self, as_of: str | None = None) -> dict[str, Any]:
        return {
            "as_of": AS_OF,
            "regime": "NEUTRAL",
            "score": 55,
            "coverage": 0.8,
            "axes": {"growth": 52, "liquidity": 60},
            "details": {},
        }


class FakeTdx:
    def latest_qualified_close_snapshot(self):
        return True, "", {"market_date": AS_OF}


class FakeFinancialStore:
    def __init__(self) -> None: self.calls: list[tuple[str, str | None]] = []
    def latest(self, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        self.calls.append((stock_code, as_of))
        return {
            "as_of": AS_OF, "created_at": "2026-08-24T08:00:00+00:00", "feature_status": "READY",
            "forecast_status": "READY", "analysis_status": "COMPLETED", "data_gaps": [],
            "history": [{"report_date": "2025-12-31", "announcement_date": "2026-04-20", "revenue": 100,
                         "net_profit": 10, "operating_cash_flow": 12, "accounts_receivable": 8, "inventory": 6,
                         "cash_and_equivalents": 20, "current_assets": 40, "current_liabilities": 30,
                         "non_current_liabilities": 10, "interest_bearing_debt_ratio": 20, "debt_ratio": 40,
                         "capex": 5, "gross_margin": 30, "roe": 10}],
            "feature": {"latest_changes": [{"metric": "revenue", "change_percent": 5}]},
            "forecast": {"scenarios": {"BASE": {"forecast": [{"year": "2026E", "net_profit": 12}]}}},
            "analysis": {"executive_summary": "财务资料已保存。", "claims": [], "key_metrics_to_monitor": ["经营现金流"]},
        }


class FakeFinancialHistory:
    def __init__(self) -> None: self.calls: list[tuple[str, str | None]] = []
    def query(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        self.calls.append((stock_code, as_of))
        return {"symbol": stock_code, "as_of": as_of, "items": [{"report_date": "2025-12-31", "announcement_date": "2026-04-20",
            "revenue": 100, "net_profit": 10, "operating_cash_flow": 12, "accounts_receivable": 8, "inventory": 6,
            "cash_and_equivalents": 20, "current_assets": 40, "current_liabilities": 30, "non_current_liabilities": 10,
            "interest_bearing_debt_ratio": 20, "debt_ratio": 40, "capex": 5, "gross_margin": 30, "roe": 10}]}


class FakeBusinessProfile:
    def __init__(self) -> None: self.calls: list[str] = []
    def profile(self, stock_code: str) -> dict[str, Any]:
        self.calls.append(stock_code)
        return {"stock_code": stock_code, "stock_name": "贵州茅台", "main_business": "白酒生产销售",
                "updated_at": "2026-08-24T08:00:00+00:00", "data_status": "REAL", "source": [{"dataset": "fundamentals"}]}


class FakeBusinessStore:
    def __init__(self, value: dict[str, Any] | None = None) -> None: self.value, self.calls = value, []
    def latest(self, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        self.calls.append((stock_code, as_of)); return self.value


class FakeOverview:
    def __init__(self) -> None: self.calls: list[tuple[str, str, str | None]] = []
    def get_overview(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        self.calls.append((market, stock_code, as_of))
        return {"research_as_of": as_of, "data_status": {"financial": "READY", "business": "UNKNOWN", "thesis": "NOT_CREATED"},
                "watch_items": [], "thesis": None}


class FakeThesis:
    def __init__(self, value: dict[str, Any] | None = None) -> None: self.value = value
    def list_thesis_versions(self, market: str, stock_code: str) -> list[dict[str, Any]]: return [self.value] if self.value else []


class FakeEvidence:
    def list_active_evidence_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]: return []


class FakeReview:
    def list_reviews_for_company(self, market: str, stock_code: str) -> list[dict[str, Any]]: return []


class FakeDisclosure:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None: self.rows, self.calls = rows or [], []
    def list_materials(self, stock_code: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        self.calls.append((stock_code, as_of)); return self.rows


def _pool(as_of: str | None) -> dict[str, Any]:
    return {"as_of": as_of, "members": [{"stock_code": "600519.SH", "lifecycle_status": "ACTIVE", "level1_name": "食品饮料",
        "level2_name": "白酒", "level3_code": "L3-1", "level3_name": "白酒", "leader_rank": 1, "leader_score": 90}]}


def _service(runtime: FakeRuntime | None = None, *, risk: FakeRisk | None = None,
             business: dict[str, Any] | None = None, thesis: dict[str, Any] | None = None,
             disclosure: list[dict[str, Any]] | None = None) -> ResearchSpecialistChatService:
    return ResearchSpecialistChatService(
        store=FakeStore(),
        runtime=runtime or FakeRuntime(),
        risk_service=risk or FakeRisk(),
        price_zone_service=FakeZones(),
        historical_valuation_service=FakeHistorical(),
        entry_service=FakeEntryExit(),
        exit_service=FakeEntryExit(),
        macro_service=FakeMacro(),
        tdx_service=FakeTdx(),
        financial_store=FakeFinancialStore(),
        financial_history_service=FakeFinancialHistory(),
        business_profile_service=FakeBusinessProfile(),
        business_store=FakeBusinessStore(business),
        overview_service=FakeOverview(),
        thesis_repository=FakeThesis(thesis),
        evidence_repository=FakeEvidence(),
        review_repository=FakeReview(),
        disclosure_store=FakeDisclosure(disclosure),
        leader_pool_reader=_pool,
        security_resolver=lambda _question, _entity: {"code": "600519.SH", "name": "贵州茅台"},
    )


@pytest.mark.parametrize(
    ("agent", "title", "model"),
    [
        ("risk_researcher", "风险研究员", "risk-model"),
        ("valuation_researcher", "估值研究员", "valuation-model"),
        ("macro_policy_researcher", "宏观政策研究员", "macro_policy-model"),
    ],
)
def test_role_intro_is_independent_and_reports_its_configured_model(
    agent: str, title: str, model: str,
) -> None:
    brief = _service().handle_question(agent=agent, question="你是什么模型，有什么功能")

    assert brief.title == title
    assert model in brief.answer
    assert brief.stock_code is None
    assert brief.research_as_of is None


def test_risk_and_valuation_are_grounded_in_their_own_local_context() -> None:
    runtime = FakeRuntime()
    service = _service(runtime)

    risk = service.handle_question(agent="risk_researcher", question="贵州茅台有什么风险")
    valuation = service.handle_question(agent="valuation_researcher", question="贵州茅台估值怎么样")

    assert risk.stock_code == valuation.stock_code == "600519.SH"
    assert runtime.calls[0]["role"] == "risk"
    assert {
        "risk_research", "financial_research", "financial_history", "business_profile", "business_research",
        "company_overview", "valuation_research", "thesis_research", "disclosure_materials", "industry_context",
    } <= set(runtime.calls[0]["payload"]["context"])
    assert runtime.calls[1]["role"] == "valuation"
    assert set(runtime.calls[1]["payload"]["context"]) == {"valuation_research"}


def test_risk_unknown_continues_with_all_read_only_company_capabilities() -> None:
    runtime, risk = FakeRuntime(), FakeRisk(unknown=True)
    brief = _service(runtime, risk=risk).handle_question(agent="risk_researcher", question="深度分析贵州茅台风险")
    context = runtime.calls[0]["payload"]["context"]
    assert brief.research_as_of == AS_OF and risk.calls == [("CN", "600519.SH", AS_OF)]
    assert context["risk_research"]["overall_risk"] == "UNKNOWN"
    assert context["financial_research"]["status"] == "READY"
    assert context["financial_history"]["period_count"] == 1
    assert context["business_profile"]["main_business"] == "白酒生产销售"
    assert context["business_research"]["status"] == "MISSING"
    assert context["thesis_research"]["status"] == "MISSING"
    assert context["disclosure_materials"]["status"] == "NOT_COLLECTED"
    assert context["industry_context"]["industry"]["leader_rank"] == 1


def test_risk_context_keeps_shared_as_of_and_saved_business_thesis_context() -> None:
    runtime = FakeRuntime()
    business = {"analysis_status": "COMPLETED", "data_as_of": "2026-08-24", "created_at": "2026-08-24T10:00:00+00:00",
                "snapshot": {"main_business": "白酒生产销售", "products": ["白酒"]}, "analysis": {"claims": []}}
    thesis = {"thesis_id": "t1", "title": "品牌与渠道", "core_thesis": "测试逻辑", "status": "UNCHANGED",
              "confidence": "HIGH", "version": 1, "updated_at": "2026-08-24T10:00:00+00:00",
              "created_at": "2026-08-24T10:00:00+00:00", "source_data_as_of": "2026-08-24"}
    _service(runtime, business=business, thesis=thesis).handle_question(agent="risk_researcher", question="深度分析贵州茅台风险")
    context = runtime.calls[0]["payload"]["context"]
    assert context["research_as_of"] == AS_OF
    assert context["business_research"]["status"] == "COMPLETED"
    assert context["thesis_research"]["status"] == "READY"
    assert context["thesis_research"]["thesis"]["title"] == "品牌与渠道"
    assert all(value.get("as_of", AS_OF) == AS_OF for value in (context["risk_research"], context["valuation_research"]["price_zones"]))


def test_risk_instruction_states_data_and_industry_boundaries() -> None:
    runtime = FakeRuntime()
    _service(runtime).handle_question(agent="risk_researcher", question="贵州茅台风险如何")
    instruction = runtime.calls[0]["instruction"]
    assert "系统未采集公告材料不等于公司没有披露" in instruction
    assert "深度低估不等于价值陷阱" in instruction
    assert "PPP回款" in instruction and "门店翻台率" in instruction
    assert "【已确认风险】" in instruction and "买入" in instruction
    # Endpoints may ignore response_format; the JSON contract must be in the
    # instruction or the whole answer is lost to the parser.
    assert "输出必须是且仅是一个 JSON 对象" in instruction
    assert '"answer"' in instruction


def test_macro_does_not_require_a_company() -> None:
    runtime = FakeRuntime()
    brief = _service(runtime).handle_question(
        agent="macro_policy_researcher",
        question="当前流动性环境怎么样",
    )

    assert brief.stock_code is None
    assert brief.research_as_of == AS_OF
    assert runtime.calls[0]["role"] == "macro_policy"
    assert set(runtime.calls[0]["payload"]["context"]) == {"macro_snapshot"}


def test_trading_language_from_model_is_rejected_and_falls_back_to_local_result() -> None:
    runtime = FakeRuntime("建议买入并加仓。")
    brief = _service(runtime).handle_question(agent="risk_researcher", question="贵州茅台风险如何")

    assert brief.status == "PARTIAL"
    assert brief.answer == "存在一项需要复核的风险。"
    assert "MODEL_EXPLANATION_UNAVAILABLE" in brief.data_gaps


def test_financial_history_coverage_separates_company_gaps_from_unintegrated_fields() -> None:
    full = ResearchSpecialistChatService._compact_financial_history({"items": [{
        "report_date": "2025-12-31", "announcement_date": "2026-04-20", "revenue": 100,
        "net_profit": 10, "operating_cash_flow": 12, "accounts_receivable": 8, "inventory": 6,
        "cash_and_equivalents": 20, "current_assets": 40, "current_liabilities": 30,
        "non_current_liabilities": 10, "interest_bearing_debt_ratio": 20, "debt_ratio": 40,
        "capex": 5, "gross_margin": 30, "roe": 10,
    }]})
    assert full["status"] == "READY"
    assert full["field_coverage"]["company_missing_fields"] == []
    assert full["message"] is None

    partial = ResearchSpecialistChatService._compact_financial_history({"items": [{
        "report_date": "2025-12-31", "announcement_date": "2026-04-20", "revenue": 100,
        "net_profit": 10, "operating_cash_flow": 12, "debt_ratio": 40, "roe": 10,
    }]})
    missing = partial["field_coverage"]["company_missing_fields"]
    assert "cash_and_equivalents" in missing and "inventory" in missing
    assert "货币资金" in (partial["message"] or "")
    assert "字段管道系统已接入" in (partial["message"] or "")
    assert "不是系统未接入" in (partial["message"] or "")


def test_risk_instruction_requires_company_gap_wording() -> None:
    runtime = FakeRuntime()
    _service(runtime).handle_question(agent="risk_researcher", question="贵州茅台风险如何")
    instruction = runtime.calls[0]["instruction"]
    assert "company_missing_fields" in instruction
    assert "不得写成'系统未接入'" in instruction

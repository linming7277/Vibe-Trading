from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import risk_research_routes
from src.api.risk_research_routes import register_risk_research_routes
from src.risk_research.service import RiskResearchService


SYMBOL = "000001.SZ"


def _snapshot(*, revenue: list[float] = [100.0, 80.0, 60.0], profit: list[float] = [20.0, 16.0, 10.0],
              cash: list[float] = [25.0, 18.0, 10.0], gross: list[float] = [40.0, 36.0, 32.0],
              roe: list[float] = [18.0, 12.0, 7.0], debt: list[float] = [40.0, 46.0, 52.0],
              receivable: list[float | None] = [20.0, 20.0, 20.0],
              inventory: list[float | None] = [20.0, 20.0, 20.0],
              cash_equivalents: list[float | None] = [30.0, 30.0, 30.0],
              current_assets: list[float | None] = [100.0, 100.0, 100.0],
              current_liabilities: list[float | None] = [50.0, 50.0, 50.0],
              non_current_liabilities: list[float | None] = [20.0, 20.0, 20.0],
              interest_debt: list[float | None] = [20.0, 20.0, 20.0],
              capex: list[float | None] = [10.0, 10.0, 10.0],
              status: str = "READY", as_of: str = "2026-08-19") -> dict:
    years = ["2023-12-31", "2024-12-31", "2025-12-31"]
    history = [{"report_date": year, "announcement_date": f"{int(year[:4]) + 1}-03-30", "revenue": revenue[i],
                "net_profit": profit[i], "operating_cash_flow": cash[i], "gross_margin": gross[i],
                "roe": roe[i], "debt_ratio": debt[i], "accounts_receivable": receivable[i],
                "inventory": inventory[i], "cash_and_equivalents": cash_equivalents[i],
                "current_assets": current_assets[i], "current_liabilities": current_liabilities[i],
                "non_current_liabilities": non_current_liabilities[i],
                "interest_bearing_debt_ratio": interest_debt[i], "capex": capex[i]}
               for i, year in enumerate(years)]
    return {"id": "financial-1", "source_hash": "hash-1", "stock_code": SYMBOL, "as_of": as_of,
            "feature_status": status, "forecast_status": "READY", "history": history,
            "feature": {"status": status}, "forecast": {"scenarios": {"BASE": {"forecast": [{"year": "2026E", "net_profit": 12.0}]}}}}


def _zones(valuation: str = "UNDERVALUED") -> dict:
    return {"stock_code": SYMBOL, "as_of": "2026-08-19", "current_price": 20.0,
            "valuation": {"status": valuation, "fair_value_low": 25.0, "fair_value_mid": 30.0, "fair_value_high": 35.0}}


class FakeFinancial:
    db_path = None
    def __init__(self, snapshots: list[dict] | None = None) -> None:
        self.snapshots = [_snapshot()] if snapshots is None else snapshots
        self.reads: list[tuple[str, str | None]] = []
    def latest(self, stock_code: str, as_of: str | None = None) -> dict | None:
        self.reads.append((stock_code, as_of)); return deepcopy(self.snapshots[0]) if self.snapshots else None
    def recent(self, stock_code: str, *, as_of: str | None = None, limit: int = 2) -> list[dict]:
        return deepcopy(self.snapshots[:limit])


class FakeBusiness:
    def __init__(self, value: dict | None = None) -> None: self.value = value
    def latest(self, stock_code: str, *, as_of: str | None = None) -> dict | None: return deepcopy(self.value)


class FakeThesis:
    def __init__(self, status: str | None = "UNCHANGED") -> None:
        self.value = None if status is None else {"thesis_id": "thesis-1", "status": status, "confidence": "HIGH", "created_at": "2026-08-01T00:00:00+00:00", "source_data_as_of": "2026-08-01"}
    def get_current_thesis(self, market: str, stock_code: str) -> dict | None: return deepcopy(self.value)
    def list_thesis_versions(self, market: str, stock_code: str) -> list[dict]: return [deepcopy(self.value)] if self.value else []


class FakeEvidence:
    def __init__(self, rows: list[dict] | None = None) -> None: self.rows = rows or []
    def list_evidence_for_thesis(self, thesis_id: str) -> list[dict]: return deepcopy(self.rows)


class FakeReview:
    def __init__(self, stale: bool = False) -> None: self.stale = stale
    def list_reviews_for_thesis(self, thesis_id: str) -> list[dict]: return [{"review_id": "review-1", "is_stale": self.stale}] if self.stale else []


class FakeDisclosure:
    def __init__(self, rows: list[dict] | None = None) -> None: self.rows = list(rows or [])
    def list_materials(self, _stock_code: str, *, as_of: str | None = None) -> list[dict]: return deepcopy(self.rows)
    def close(self) -> None: return None


class FakeZones:
    def __init__(self, value: dict) -> None: self.value, self.calls = value, []
    def get_price_zones(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        self.calls.append((market, stock_code, as_of)); return deepcopy(self.value)


def _pool(active: bool = True) -> dict:
    return {"members": [{"stock_code": SYMBOL, "lifecycle_status": "ACTIVE" if active else "OUT_OF_TOP2"}]}


def _challenge(index: int, *, confidence: str = "HIGH", business: bool = False) -> dict:
    return {"evidence_id": f"e{index}", "effect": "CHALLENGE", "confidence": confidence, "is_active": True,
            "created_at": "2026-08-10T00:00:00+00:00", "data_as_of": "2026-08-10",
            "evidence_type": "BUSINESS_CHANGE" if business else "FINANCIAL_CHANGE", "source_type": "BUSINESS_RESEARCH" if business else "PIT_FINANCIAL_HISTORY", "source_ref": f"source-{index}"}


def _service(*, snapshots: list[dict] | None = None, valuation: str = "UNDERVALUED", thesis: str | None = "UNCHANGED",
             evidence: list[dict] | None = None, stale: bool = False, active_leader: bool = True,
             disclosure: list[dict] | None = None, business: dict | None = None) -> RiskResearchService:
    return RiskResearchService(financial_store=FakeFinancial(snapshots), business_store=FakeBusiness(business), thesis_repository=FakeThesis(thesis),
        evidence_repository=FakeEvidence(evidence), review_repository=FakeReview(stale), disclosure_store=FakeDisclosure(disclosure),
        price_zone_service=FakeZones(_zones(valuation)), leader_pool_reader=lambda as_of: _pool(active_leader))  # type: ignore[arg-type]


def test_financial_rules_are_traceable_and_use_percentage_points_for_debt() -> None:
    result = _service().get_risk_research("CN", SYMBOL)
    items = {item["risk_type"]: item for item in result["risks"]}
    assert items["FINANCIAL_REVENUE_DECLINE"]["severity"] == "HIGH"
    assert items["FINANCIAL_DEBT_RATIO"]["severity"] == "HIGH"
    assert items["FINANCIAL_DEBT_RATIO"]["source_keys"]
    assert result["overall_risk"] == "HIGH"
    assert "买入" not in str(result) and "卖出" not in str(result)


def test_partial_financial_data_never_promotes_high_confirmed() -> None:
    result = _service(snapshots=[_snapshot(status="PARTIAL")]).get_risk_research("CN", SYMBOL)
    assert not any(item["severity"] == "HIGH" and item["risk_type"].startswith("FINANCIAL") for item in result["risks"])


def test_receivable_and_inventory_divergence_are_traceable() -> None:
    receivable = _snapshot(
        revenue=[100, 105, 110], profit=[20, 20, 20], cash=[30, 30, 30], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40],
        receivable=[100, 130, 170], inventory=[20, 20, 20],
    )
    inventory = _snapshot(
        revenue=[100, 105, 110], profit=[20, 20, 20], cash=[30, 30, 30], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40],
        receivable=[20, 20, 20], inventory=[100, 130, 170],
    )
    receivable_risks = {item["risk_type"]: item for item in _service(snapshots=[receivable]).get_risk_research("CN", SYMBOL)["risks"]}
    inventory_risks = {item["risk_type"]: item for item in _service(snapshots=[inventory]).get_risk_research("CN", SYMBOL)["risks"]}
    for kind, items, source in (
        ("FINANCIAL_RECEIVABLE", receivable_risks, "ACCOUNTS_RECEIVABLE"),
        ("FINANCIAL_INVENTORY", inventory_risks, "INVENTORY"),
    ):
        item = items[kind]
        assert item["severity"] == "HIGH" and any(source in key for key in item["source_keys"])
        assert item["metadata"]["announcement_date"] == "2026-03-30"
        assert item["metadata"]["derived_metrics"]["growth_gap_percentage_points"] >= 15


def test_liquidity_and_cash_coverage_are_one_root_cause_with_traceability() -> None:
    snapshot = _snapshot(
        revenue=[100, 100, 100], profit=[20, 20, 20], cash=[100, 100, 100], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40],
        current_assets=[200, 100, 40], current_liabilities=[100, 100, 100], cash_equivalents=[50, 15, 3],
    )
    result = _service(snapshots=[snapshot]).get_risk_research("CN", SYMBOL)
    items = {item["risk_type"]: item for item in result["risks"]}
    item = items["FINANCIAL_LIQUIDITY"]
    assert item["severity"] == "HIGH" and "FINANCIAL_CASH_COVERAGE" not in items
    assert item["metadata"]["derived_metrics"]["current_ratio_change_percent"] < 0
    assert item["metadata"]["derived_metrics"]["cash_coverage_change_percent"] < 0


def test_interest_debt_and_capex_pressure_require_cash_flow_context() -> None:
    debt_snapshot = _snapshot(
        revenue=[100, 100, 100], profit=[20, 20, 20], cash=[100, 70, 50], gross=[40, 40, 40], roe=[15, 15, 15], debt=[30, 33, 36],
        interest_debt=[20, 30, 40], non_current_liabilities=[20, 25, 30], capex=[10, 10, 10],
    )
    capex_snapshot = _snapshot(
        revenue=[100, 105, 110], profit=[20, 20, 20], cash=[100, 60, 40], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40],
        capex=[10, 20, 50],
    )
    debt_items = {item["risk_type"]: item for item in _service(snapshots=[debt_snapshot]).get_risk_research("CN", SYMBOL)["risks"]}
    capex_items = {item["risk_type"]: item for item in _service(snapshots=[capex_snapshot]).get_risk_research("CN", SYMBOL)["risks"]}
    assert debt_items["FINANCIAL_INTEREST_DEBT"]["severity"] == "HIGH"
    assert debt_items["FINANCIAL_INTEREST_DEBT"]["metadata"]["derived_metrics"]["interest_bearing_debt_ratio_change_percentage_points"] == 10
    assert capex_items["FINANCIAL_CAPEX_PRESSURE"]["severity"] == "HIGH"
    assert capex_items["FINANCIAL_CAPEX_PRESSURE"]["metadata"]["derived_metrics"]["capex_to_revenue"] > 0.26


def test_extended_field_gaps_are_unknown_and_partial_never_confirms_high() -> None:
    none = [None, None, None]
    missing = _snapshot(receivable=none, inventory=none, cash_equivalents=none, current_assets=none,
                        current_liabilities=none, non_current_liabilities=none, interest_debt=none, capex=none)
    result = _service(snapshots=[missing]).get_risk_research("CN", SYMBOL)
    assert result["data_quality"]["financial_extended"] == "MISSING"
    partial = _snapshot(status="PARTIAL", revenue=[100, 105, 110], cash=[100, 60, 40], receivable=[100, 130, 170],
                        interest_debt=[20, 30, 40], debt=[30, 33, 36], non_current_liabilities=[20, 25, 30], capex=[10, 20, 50])
    partial_result = _service(snapshots=[partial]).get_risk_research("CN", SYMBOL)
    assert not any(item["severity"] == "HIGH" and item["status"] == "CONFIRMED" for item in partial_result["risks"])


def test_value_trap_deduplicates_liquidity_root_cause() -> None:
    service = _service()
    financial = [
        {"risk_type": "FINANCIAL_LIQUIDITY", "severity": "MEDIUM", "source_keys": ["a"], "evidence_ids": []},
        {"risk_type": "FINANCIAL_CASH_COVERAGE", "severity": "MEDIUM", "source_keys": ["b"], "evidence_ids": []},
    ]
    level, _ = service._value_trap(leader={"stock_code": SYMBOL}, valuation_status="UNDERVALUED", financial=financial,
                                   business=[], thesis=[], financial_quality="READY", thesis_quality="READY")
    assert level == "UNKNOWN"


def test_thesis_challenge_review_stale_and_business_evidence_are_separate() -> None:
    stable = _snapshot(revenue=[100, 100, 100], profit=[20, 20, 20], cash=[20, 20, 20], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40])
    result = _service(snapshots=[stable], thesis="WEAKENING", evidence=[_challenge(1, business=True), _challenge(2), _challenge(3)], stale=True).get_risk_research("CN", SYMBOL)
    kinds = {item["risk_type"] for item in result["risks"]}
    assert {"THESIS_STATUS", "THESIS_CHALLENGE_EVIDENCE", "THESIS_REVIEW_STALE", "BUSINESS_CHALLENGE"} <= kinds
    assert any(item["risk_type"] == "BUSINESS_CHALLENGE" and item["evidence_ids"] for item in result["risks"])


@pytest.mark.parametrize(("valuation", "active", "expected"), [
    ("FAIR", True, "NOT_APPLICABLE"), ("UNDERVALUED", False, "NOT_APPLICABLE"),
    ("UNDERVALUED", True, "HIGH_TRAP_RISK"),
])
def test_value_trap_scope_and_levels(valuation: str, active: bool, expected: str) -> None:
    result = _service(valuation=valuation, active_leader=active).get_risk_research("CN", SYMBOL)
    assert result["value_trap_risk"] == expected


def test_value_trap_unknown_when_low_value_lacks_key_research() -> None:
    result = _service(snapshots=[], thesis=None).get_risk_research("CN", SYMBOL)
    assert result["value_trap_risk"] == "UNKNOWN" and result["overall_risk"] == "UNKNOWN"


def test_official_disclosure_coverage_is_visible_but_does_not_create_a_risk() -> None:
    result = _service(disclosure=[{
        "material_type": "CUSTOMER_CONCENTRATION", "status": "FOUND", "excerpts": [{"page": 8, "text": "前五名客户"}],
    }]).get_risk_research("CN", SYMBOL)
    assert result["data_quality"]["official_disclosure_sources"]["customer_concentration"] == "READY"
    assert not any(item["risk_type"] == "CUSTOMER_CONCENTRATION" for item in result["risks"])


def test_cited_business_claims_are_usable_before_human_thesis_confirmation() -> None:
    business = {"analysis": {"claims": [
        {"type": "FACT", "topic": "BUSINESS_CHANGE", "text": "生态环境治理业务收入下降，需持续跟踪。", "source_keys": ["DISCLOSURE_CURRENT_PRODUCT"]},
        {"type": "FACT", "topic": "BUSINESS_MODEL", "text": "客户集中度较高，前五名客户占比较高。", "source_keys": ["DISCLOSURE_CURRENT_CUSTOMER"]},
        {"type": "FACT", "topic": "PRODUCT", "text": "污水处理占营业收入超过一半。", "source_keys": ["DISCLOSURE_CURRENT_PRODUCT"]},
    ]}}
    stable = _snapshot(revenue=[100, 100, 100], profit=[20, 20, 20], cash=[20, 20, 20], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40])
    result = _service(snapshots=[stable, deepcopy(stable)], thesis=None, business=business).get_risk_research("CN", SYMBOL)
    kinds = {item["risk_type"] for item in result["risks"]}
    assert {"BUSINESS_OPERATION_CHANGE", "BUSINESS_CUSTOMER_CONCENTRATION"} <= kinds
    assert result["data_quality"]["business"] == "PARTIAL"
    assert result["data_quality"]["missing"] == ["MARKET_SHARE", "THESIS"]


def test_explicit_disclosure_risk_wording_creates_traceable_watch_not_keyword_risk() -> None:
    disclosure = [
        {"id": "m1", "announcement_id": "a1", "material_type": "RECEIVABLES_IMPAIRMENT", "status": "FOUND", "announcement_date": "2026-08-23",
         "excerpts": [{"page": 1, "text": "应收账款金额呈上升趋势，如持续累积将对现金流造成压力。长期借款持续增加。"}]},
        {"id": "m2", "announcement_id": "a1", "material_type": "DEBT_MATURITY", "status": "FOUND", "announcement_date": "2026-08-23",
         "excerpts": [{"page": 2, "text": "一年内到期的非流动负债详见附注。"}]},
    ]
    stable = _snapshot(revenue=[100, 100, 100], profit=[20, 20, 20], cash=[20, 20, 20], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40])
    result = _service(snapshots=[stable], thesis=None, disclosure=disclosure).get_risk_research("CN", SYMBOL)
    kinds = {item["risk_type"] for item in result["risks"]}
    assert {"DISCLOSURE_RECEIVABLES_COLLECTION", "DISCLOSURE_DEBT_MATURITY"} <= kinds
    assert all(item["source_keys"] for item in result["risks"] if item["risk_type"].startswith("DISCLOSURE_"))


def test_forecast_requires_comparable_persisted_snapshots_and_as_of_is_forwarded() -> None:
    older = _snapshot(revenue=[100, 100, 100], profit=[20, 20, 20], cash=[20, 20, 20], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40], as_of="2026-08-01")
    current = _snapshot(revenue=[100, 100, 100], profit=[20, 20, 20], cash=[20, 20, 20], gross=[40, 40, 40], roe=[15, 15, 15], debt=[40, 40, 40], as_of="2026-08-19")
    older["forecast"]["scenarios"]["BASE"]["forecast"][0]["net_profit"] = 20.0
    result = _service(snapshots=[current, older]).get_risk_research("CN", SYMBOL, as_of="2026-08-19")
    forecast = next(item for item in result["risks"] if item["risk_type"] == "FINANCIAL_FORECAST_DOWNGRADE")
    assert forecast["severity"] == "HIGH" and forecast["source_keys"] == ["FORECAST_BASE_NET_PROFIT_2026"]


def test_api_is_read_only_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    app = FastAPI(); register_risk_research_routes(app, require_auth=lambda: True)
    monkeypatch.setattr(risk_research_routes, "get_risk_research_service", lambda: service)
    response = TestClient(app).get(f"/api/value/companies/{SYMBOL}/risk-research?market=CN&as_of=2026-08-19")
    assert response.status_code == 200 and response.json()["stock_code"] == SYMBOL

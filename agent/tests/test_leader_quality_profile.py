from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import leader_quality_profile_routes
from src.api.leader_quality_profile_routes import register_leader_quality_profile_routes
from src.leader_quality_profile.service import FORMULA_VERSION, LeaderQualityProfileService
from src.level3_leaders.store import Level3LeaderStore


SYMBOLS = ("605108.SH", "600258.SH", "600138.SH")


def _financials(as_of: str):
    result = {}
    for index, symbol in enumerate(SYMBOLS):
        rows = []
        for year in range(2020, 2026):
            revenue = (100 - index * 25) * 100_000_000 + (year - 2020) * 10_000_000
            profit = (10 - index * 4) * 100_000_000 + (year - 2020) * 1_000_000
            rows.append({
                "symbol": symbol, "period_type": "annual", "report_date": f"{year}-12-31",
                "announcement_date": f"{year + 1}-04-25", "revenue": revenue,
                "net_profit": profit, "operating_cash_flow": profit * (1.1 - index * .1),
                "capex": revenue * (.08 + index * .02), "roe": 20 - index * 5,
                "gross_margin": 35 - index * 5, "net_margin": 12 - index * 3,
            })
        result[symbol] = rows
    return result


def _row(symbol: str, rank: int, score: float, index: int, as_of: str):
    return {
        "as_of": as_of,
        "level1_code": "L1", "level1_name": "消费", "level2_code": "L2", "level2_name": "服务",
        "level3_code": "881431.SH", "level3_name": "餐饮", "stock_code": symbol,
        "stock_name": ("同庆楼", "首旅酒店", "广州酒家")[index], "leader_rank": rank,
        "leader_score": score, "leader_formula_version": "value-leader-v2.0.0",
        "component_scores": {"industry_position": score, "profitability": score - 5, "growth_stability": score - 8,
                             "cash_flow": score - 10, "valuation": score - 15, "governance_risk": score - 12},
        "coverage": 1.0, "eligibility_status": "eligible", "eligibility_reasons": [], "metric_applicability_notes": [],
        "raw_features": {
            "market_cap": 300 - index * 70, "revenue": (100 - index * 25) * 100_000_000,
            "net_profit": (10 - index * 4) * 100_000_000, "roe": 20 - index * 5,
            "gross_margin": 35 - index * 5, "net_margin": 12 - index * 3,
            "revenue_cagr": 15 - index * 5, "profit_cagr": None if index == 2 else 12 - index * 5,
            "cash_conversion": 110 - index * 15, "ocf_margin": 11 - index * 2,
            "positive_ocf_years": 100 - index * 10, "ocf_trend": 8 - index,
            "debt_safety": -(45 + index * 5), "shareholder_stability": -index, "low_beta": -.8,
        },
        "provenance_key": f"{as_of}:{symbol}",
    }


def _service(tmp_path: Path):
    store = Level3LeaderStore(tmp_path / "research.db")
    for day in ("2026-08-14", "2026-08-17", "2026-08-19", "2026-08-21", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"):
        run = store.start_run(idempotency_key=day, as_of=day, catalog_as_of=day, formula_version="value-leader-v2.0.0")
        store.finish_run(run["id"], rows=[_row(symbol, index + 1, 90 - index * 8, index, day) for index, symbol in enumerate(SYMBOLS)], statistics={"industry_count": 1})
    return LeaderQualityProfileService(leader_store=store, financial_loader=_financials), store


def test_profile_uses_saved_l3_facts_and_short_window_warning(tmp_path):
    service, store = _service(tmp_path)
    try:
        profile = service.get_profile("CN", "605108", "2026-08-27")
        assert profile["formula_version"] == FORMULA_VERSION
        assert profile["leader_position"]["rank"] == 1
        assert profile["leader_position"]["valid_peer_count"] == 3
        assert profile["data_quality"]["small_peer_sample"] is True
        revenue = next(item for item in profile["peer_advantages"] if item["metric"] == "revenue")
        assert revenue["peer_percentile"] == 100
        assert revenue["data_quality"] == "SMALL_PEER_SAMPLE"
        capex = next(item for item in profile["peer_advantages"] if item["metric"] == "capex_to_revenue")
        assert capex["company_value"] == 8
        assert capex["status"] == "NOT_SCORED"
        assert profile["leader_stability"]["status"] == "SHORT_WINDOW_STABLE"
        assert profile["leader_stability"]["top1_count"] == 8
        assert "长期龙头稳定性" in profile["leader_stability"]["disclaimer"]
        assert profile["profitability_quality"]["cash_quality_status"] in {"STRONG", "ABOVE_AVERAGE", "NORMAL", "BELOW_AVERAGE"}
        assert profile["source_traceability"]["financial"]["announcement_date"] == "2026-04-25"
        assert profile["pricing_power_proxy"]["status"] in {"STRONG_PROXY", "MODERATE_PROXY", "WEAK_PROXY", "UNKNOWN"}
        assert "无市场份额数据" in profile["moat_data_gaps"]
        assert "WIDE_MOAT" not in str(profile)
        # The profile only reads persisted snapshots; it does not create a new run.
        assert len(store.completed_runs()) == 8
    finally:
        service.close()


def test_profile_reports_run_and_company_absence_without_rebuild(tmp_path):
    service, store = _service(tmp_path)
    try:
        unavailable = service.get_profile("CN", "605108", "2025-01-01")
        assert unavailable["leader_position"]["status"] == "RUN_NOT_AVAILABLE"
        absent = service.get_profile("CN", "000001")
        assert absent["leader_position"]["status"] == "NOT_IN_CURRENT_L3_RUN"
        assert len(store.completed_runs()) == 8
    finally:
        service.close()


def test_read_only_leader_quality_api(monkeypatch):
    class FakeService:
        def get_profile(self, market, stock_code, as_of=None):
            return {"market": market, "stock_code": stock_code, "as_of": as_of, "formula_version": FORMULA_VERSION}

    monkeypatch.setattr(leader_quality_profile_routes, "get_leader_quality_profile_service", lambda: FakeService())
    app = FastAPI()
    register_leader_quality_profile_routes(app, require_auth=lambda: True)
    response = TestClient(app).get("/api/value/companies/605108/leader-quality?market=CN&as_of=2026-08-27")
    assert response.status_code == 200
    assert response.json()["stock_code"] == "605108"
    assert response.json()["as_of"] == "2026-08-27"

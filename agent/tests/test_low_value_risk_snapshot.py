from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import low_value_leader_pool_routes
from src.api.low_value_leader_pool_routes import register_low_value_leader_pool_routes
from src.low_value_leader_pool.service import LowValueLeaderPoolService
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.low_value_risk_snapshot.service import LowValuePoolRiskSnapshotService
from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository


def _pool_item(code: str, *, active: bool = True, as_of: str = "2026-08-24") -> dict:
    return {"market": "CN", "stock_code": code, "company_name": code, "industry_code": "I1", "industry_name": "行业一",
            "leader_rank": 1, "leader_score": 80.0, "current_price": 10.0, "fair_value_low": 12.0,
            "fair_value_mid": 15.0, "fair_value_high": 18.0, "valuation_status": "UNDERVALUED",
            "historical_valuation_status": "CHEAP", "support_status": "AVAILABLE", "entry_level": "WATCH",
            "source_pool_id": "pool-1", "source_as_of": as_of, "enter_reason": "UNDERVALUED", "metadata": {}}


def _risk(overall: str, trap: str, *, risks: list[dict] | None = None) -> dict:
    return {"overall_risk": overall, "value_trap_risk": trap, "formula_version": "risk-research-v1.0.0",
            "data_quality": {"financial": "READY", "business": "PARTIAL", "thesis": "READY"},
            "risks": risks or []}


class FakeRiskResearch:
    def __init__(self, values: dict[str, dict | Exception]) -> None: self.values, self.calls = values, []
    def get_risk_research(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict:
        self.calls.append((market, stock_code, as_of))
        value = self.values[stock_code]
        if isinstance(value, Exception):
            raise value
        return value


def _setup(tmp_path: pytest.TempPathFactory, values: dict[str, dict | Exception]):
    pool = LowValueLeaderPoolRepository(tmp_path / "risk-pool.db")
    snapshots = LowValueRiskSnapshotRepository(tmp_path / "risk-pool.db")
    fake = FakeRiskResearch(values)
    return pool, snapshots, fake, LowValuePoolRiskSnapshotService(pool_repository=pool, repository=snapshots, risk_research_service=fake)


def test_active_pool_only_projects_risk_and_is_idempotent(tmp_path):
    pool, snapshots, fake, service = _setup(tmp_path, {
        "000001.SZ": _risk("HIGH", "HIGH_TRAP_RISK", risks=[{"risk_type": "FINANCIAL_PROFIT_DECLINE", "severity": "HIGH"}, {"risk_type": "FINANCIAL_CASH_FLOW", "severity": "MEDIUM"}]),
    })
    pool.create_entry(_pool_item("000001.SZ"))
    pool.create_entry(_pool_item("000002.SZ"))
    pool.mark_removed(pool.active_map()["000002.SZ"]["id"], reason="VALUATION_RECOVERED", source_pool_id="pool-2", source_as_of="2026-08-25")

    first = service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    second = service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    row = snapshots.get("CN", "000001.SZ", "2026-08-24")
    assert first == {"source_as_of": "2026-08-24", "active": 1, "processed": 1, "created": 1, "skipped": 0, "failed": 0, "errors": [], "status": "COMPLETED"}
    assert second["skipped"] == 1 and second["processed"] == 0
    assert fake.calls == [("CN", "000001.SZ", "2026-08-24")]
    assert row and row["overall_risk"] == "HIGH" and row["value_trap_risk"] == "HIGH_TRAP_RISK"
    assert row["top_risk_types"] == ["FINANCIAL_PROFIT_DECLINE", "FINANCIAL_CASH_FLOW"]
    assert pool.active_map().keys() == {"000001.SZ"}  # no membership change


@pytest.mark.parametrize("overall,trap", [("LOW", "LOW_TRAP_RISK"), ("MEDIUM", "MEDIUM_TRAP_RISK"), ("UNKNOWN", "UNKNOWN")])
def test_risk_states_are_a_direct_projection(tmp_path, overall: str, trap: str):
    pool, snapshots, _fake, service = _setup(tmp_path, {"000001.SZ": _risk(overall, trap)})
    pool.create_entry(_pool_item("000001.SZ"))
    service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    row = snapshots.get("CN", "000001.SZ", "2026-08-24")
    assert row and row["overall_risk"] == overall and row["value_trap_risk"] == trap


def test_single_company_failure_is_saved_as_unknown_without_stopping_batch(tmp_path):
    pool, snapshots, _fake, service = _setup(tmp_path, {
        "000001.SZ": _risk("LOW", "LOW_TRAP_RISK"), "000002.SZ": RuntimeError("source unavailable"),
    })
    pool.create_entry(_pool_item("000001.SZ"))
    pool.create_entry(_pool_item("000002.SZ"))
    result = service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    failed = snapshots.get("CN", "000002.SZ", "2026-08-24")
    assert result["created"] == 1 and result["failed"] == 1 and len(result["errors"]) == 1
    assert failed and failed["overall_risk"] == "UNKNOWN" and failed["error"]
    assert set(pool.active_map()) == {"000001.SZ", "000002.SZ"}


def test_error_snapshot_is_retried_and_coverage_requires_same_as_of(tmp_path):
    pool, snapshots, fake, service = _setup(tmp_path, {
        "000001.SZ": RuntimeError("temporary source failure"),
    })
    pool.create_entry(_pool_item("000001.SZ"))
    first = service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    assert first["status"] == "PARTIAL"
    assert service.coverage_for_active_pool(source_as_of="2026-08-24")["complete"] is False

    fake.values["000001.SZ"] = _risk("LOW", "LOW_TRAP_RISK")
    second = service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    assert second["status"] == "COMPLETED" and second["processed"] == 1
    assert service.coverage_for_active_pool(source_as_of="2026-08-24")["complete"] is True


def test_preparation_followup_refreshes_an_existing_same_date_projection(tmp_path):
    pool, snapshots, fake, service = _setup(tmp_path, {"000001.SZ": _risk("UNKNOWN", "UNKNOWN")})
    pool.create_entry(_pool_item("000001.SZ"))
    service.refresh_active_low_value_risk_snapshots(source_as_of="2026-08-24")
    fake.values["000001.SZ"] = _risk("MEDIUM", "LOW_TRAP_RISK", risks=[{"risk_type": "FINANCIAL_CASH_FLOW", "severity": "MEDIUM"}])
    result = service.refresh_company_snapshot(market="CN", stock_code="000001.SZ", source_as_of="2026-08-24")
    row = snapshots.get("CN", "000001.SZ", "2026-08-24")
    assert result["status"] == "READY"
    assert row and row["overall_risk"] == "MEDIUM" and row["top_risk_types"] == ["FINANCIAL_CASH_FLOW"]


def test_low_value_api_attaches_snapshot_and_missing_snapshot_is_unknown(tmp_path, monkeypatch):
    repository = LowValueLeaderPoolRepository(tmp_path / "risk-api.db")
    risks = LowValueRiskSnapshotRepository(tmp_path / "risk-api.db")
    repository.create_entry(_pool_item("000001.SZ"))
    repository.create_entry(_pool_item("000002.SZ"))
    risks.save(LowValuePoolRiskSnapshotService._project(_risk("HIGH", "HIGH_TRAP_RISK", risks=[{"risk_type": "VALUE_TRAP", "severity": "HIGH"}]), market="CN", stock_code="000001.SZ", source_as_of="2026-08-24"))
    service = LowValueLeaderPoolService(repository=repository, risk_snapshot_repository=risks, leader_service=object(), price_zone_service=object(), entry_research_service=object())
    monkeypatch.setattr(low_value_leader_pool_routes, "get_low_value_leader_pool_service", lambda: service)
    app = FastAPI()
    register_low_value_leader_pool_routes(app, lambda: True)
    data = TestClient(app).get("/api/value/low-value-leaders").json()["items"]
    by_code = {item["stock_code"]: item for item in data}
    assert by_code["000001.SZ"]["risk_overall"] == "HIGH"
    assert by_code["000002.SZ"]["risk_overall"] == "UNKNOWN" and by_code["000002.SZ"]["risk_as_of"] is None

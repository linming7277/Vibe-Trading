from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import fine_track_routes
from src.api.fine_track_routes import register_fine_track_routes
from src.level3_leaders.service import Level3IndustryLeaderService
from src.level3_leaders.store import Level3LeaderStore
from src.strategy_engines.value.leader_score_v2 import FORMULA_VERSION


def industry(code: str, name: str, members: int, level2: str = "L2"):
    return {
        "industry_code": code, "industry_name": name, "level": 3, "is_terminal": True,
        "level1_code": "L1", "level1_name": "一级", "level2_code": level2,
        "level2_name": f"二级{level2}", "member_count": members,
        "as_of": "2026-08-17T00:00:00+00:00",
    }


class FakeProfiles:
    def __init__(self):
        self._industries = [industry("I1", "科技设备", 3), industry("I2", "银行", 1), industry("I3", "空行业", 1, "L2B")]
        self.catalog = SimpleNamespace(memberships=lambda: {
            "I1": ["A.SH", "B.SH", "C.SH"], "I2": ["D.SH"], "I3": ["E.SH"],
        })

    def industries(self):
        return list(self._industries)


class FakeTdxStore:
    def list_records(self, dataset, limit=10_000):
        names = {symbol: symbol[0] for symbol in ("A.SH", "B.SH", "C.SH", "D.SH", "E.SH")}
        if dataset == "securities":
            return {"items": [{"key": symbol, "name": name} for symbol, name in names.items()]}
        return {"items": [{"key": symbol, "payload": {"name": name}} for symbol, name in names.items()]}


class FakeHistory:
    def read_symbols(self, symbols, *, as_of, count):
        rows = [{"data_as_of": f"2026-07-{day:02d}", "close": 10} for day in range(1, 31)]
        return {symbol: ([] if symbol == "E.SH" else list(rows)) for symbol in symbols}


class FakeValueLine:
    def __init__(self):
        self.seen_members = []

    def close(self):
        pass

    def _load_financials(self, as_of):
        return {symbol: [{"period_type": "annual"}] for symbol in ("A.SH", "B.SH", "C.SH", "D.SH")}

    def _leader_rows(self, sector_code, sector_name, members, as_of, financials, fundamentals, quotes, market_context):
        self.seen_members.append((sector_code, list(members)))
        return [{
            "symbol": symbol, "name": symbol[0], "score": 90 - index,
            "coverage": 1.0, "component_scores": {
                "industry_position": 90, "profitability": 80, "growth_stability": 70,
                "cash_flow": 60, "valuation": 50, "governance_risk": 40,
            }, "raw_features": {}, "provenance_key": f"{sector_code}:{symbol}",
        } for index, symbol in enumerate(reversed(members))]


def service(tmp_path: Path):
    value_line = FakeValueLine()
    instance = Level3IndustryLeaderService(
        store=Level3LeaderStore(tmp_path / "research.db"), profiles=FakeProfiles(),
        value_line=value_line, tdx_store=FakeTdxStore(), market_history=FakeHistory(),
    )
    return instance, value_line


def test_terminal_hierarchy_and_industry_internal_top2(tmp_path):
    subject, value_line = service(tmp_path)
    try:
        catalog = subject.industries()
        assert catalog["level3_total"] == 3
        assert subject.industry_tree()["level2_total"] == 2
        result = subject.build_level3_leaders("2026-08-14")
        assert result["status"] == "COMPLETED"
        first = subject.get_level3_leaders("I1")
        assert [row["stock_code"] for row in first["items"]] == ["C.SH", "B.SH"]
        assert [row["leader_rank"] for row in first["items"]] == [1, 2]
        assert first["total_ranked"] == 3
        assert value_line.seen_members == [("I1", ["A.SH", "B.SH", "C.SH"]), ("I2", ["D.SH"]), ("I3", [])]
        assert all(row["leader_formula_version"] == FORMULA_VERSION for row in first["items"])
    finally:
        subject.close()


def test_small_industries_are_not_backfilled_and_finance_warning_survives(tmp_path):
    subject, _ = service(tmp_path)
    try:
        subject.build_level3_leaders("2026-08-14")
        one = subject.get_level3_leaders("I2")
        assert len(one["items"]) == 1
        assert one["items"][0]["metric_applicability_notes"] == ["FINANCIAL_SECTOR_METRIC_CAUTION"]
        empty = subject.get_level3_leaders("I3")
        assert empty["items"] == []
        assert empty["company_count"] == 1
        assert empty["eligible_count"] == 0
        all_top = subject.get_all_level3_top_leaders(limit=2)
        assert set(all_top["items"]) == {"I1", "I2"}
        assert all(len(rows) <= 2 for rows in all_top["items"].values())
    finally:
        subject.close()


def test_build_is_idempotent_and_contains_no_fine_track_membership(tmp_path):
    subject, _ = service(tmp_path)
    try:
        first = subject.build_level3_leaders("2026-08-14")
        second = subject.build_level3_leaders("2026-08-14")
        assert first["id"] == second["id"]
        assert second["idempotent_reuse"] is True
        rows = subject.store.all_rows(first["id"])
        assert rows
        assert all("fine_track_id" not in row and "membership_type" not in row for row in rows)
    finally:
        subject.close()


def test_level3_tree_and_leader_api(monkeypatch):
    class ApiService:
        def industry_tree(self):
            return {"items": [], "level1_total": 30, "level2_total": 128, "level3_total": 345}

        def get_level3_leaders(self, code, *, as_of=None, limit=2):
            return {"industry": {"level3_code": code}, "items": [{"stock_code": "A.SH"}][:limit], "eligible_count": 1}

        def get_all_level3_top_leaders(self, *, as_of=None, limit=2):
            return {"as_of": as_of or "2026-08-14", "items": {"I1": [{"stock_code": "A.SH"}][:limit]}, "total": 1, "snapshot_status": "ready"}

        def build_level3_leaders(self, as_of, *, force=False):
            return {"status": "COMPLETED", "as_of": as_of, "force": force}

    monkeypatch.setattr(fine_track_routes, "get_level3_leader_service", lambda: ApiService())
    app = FastAPI()
    register_fine_track_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    assert client.get("/api/value/industry-tree").json()["level3_total"] == 345
    response = client.get("/api/value/industries/881321.SH/leaders?limit=2")
    assert response.status_code == 200
    assert response.json()["items"][0]["stock_code"] == "A.SH"
    all_response = client.get("/api/value/level3-leaders?limit=2")
    assert all_response.status_code == 200
    assert all_response.json()["items"]["I1"][0]["stock_code"] == "A.SH"

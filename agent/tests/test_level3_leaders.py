from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import value_l3_routes
from src.api.value_l3_routes import register_value_l3_routes
from src.level3_leaders.service import Level3IndustryLeaderService
from src.level3_leaders.store import Level3LeaderStore
from src.strategy_engines.value.leader_score_v3 import FORMULA_VERSION


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
        # 模拟 V3 两段式输出：rank 由 position_score 排序给出，score 是质量分。
        rows = [{
            "symbol": symbol, "name": symbol[0], "score": 90 - index, "position_score": 90 - index,
            "coverage": 1.0, "component_scores": {
                "industry_position": 90, "profitability": 80, "growth_stability": 70,
                "cash_flow": 60, "valuation": 50, "governance_risk": 40,
            }, "raw_features": {}, "provenance_key": f"{sector_code}:{symbol}",
        } for index, symbol in enumerate(reversed(members))]
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows


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
        assert one["quality"]["sample_warning"] == "唯一可评分公司，不代表已经验证为行业龙头。"
        assert one["items"][0]["explanation"]["selected"] is True
        assert one["items"][0]["explanation"]["eligible_count"] == 1
        empty = subject.get_level3_leaders("I3")
        assert empty["items"] == []
        assert empty["company_count"] == 1
        assert empty["eligible_count"] == 0
        assert empty["excluded_items"][0]["eligibility_reason_labels"] == [
            "行情缺失或超过5个交易日未更新",
            "缺少年度专业财务历史",
        ]
        all_top = subject.get_all_level3_top_leaders(limit=2)
        assert set(all_top["items"]) == {"I1", "I2"}
        assert all(len(rows) <= 2 for rows in all_top["items"].values())
    finally:
        subject.close()


def test_formula_contract_and_full_industry_explanation_are_consistent(tmp_path):
    subject, _ = service(tmp_path)
    try:
        subject.build_level3_leaders("2026-08-14")
        result = subject.get_level3_leaders("I1", limit=100)

        assert [row["stock_code"] for row in result["items"]] == ["C.SH", "B.SH", "A.SH"]
        assert result["quality"] == {
            "member_count": 3,
            "eligible_count": 3,
            "excluded_count": 0,
            "selected_count": 2,
            "sample_warning": "可评分公司少于5家，排名属于小样本结果。",
        }
        weights = {item["key"]: item["weight"] for item in result["formula"]["dimensions"]}
        assert weights == {
            "industry_position": 1.0,
            "profitability": .20 / .75,
            "growth_stability": .15 / .75,
            "cash_flow": .15 / .75,
            "valuation": .15 / .75,
            "governance_risk": .10 / .75,
        }
        stages = {item["key"]: item["stage"] for item in result["formula"]["dimensions"]}
        assert stages["industry_position"] == "position"
        assert all(stages[key] == "quality" for key in (
            "profitability", "growth_stability", "cash_flow", "valuation", "governance_risk",
        ))
        first = result["items"][0]
        assert first["explanation"]["rank"] == 1
        assert first["explanation"]["comparison_scope"] == "仅与科技设备行业内可评分公司比较"
        assert first["explanation"]["overall_reweighted"] is False
        assert first["explanation"]["missing_dimensions"] == []
        assert first["raw_metric_available"] == 0
        assert first["raw_metric_total"] == 20
        assert len(first["components"]) == 6
        # 第一阶段规模维度独立展示，第二阶段质量维度权重合计为 1。
        assert first["components"][0]["key"] == "industry_position"
        assert first["components"][0]["weight"] == 1.0
        assert round(sum(item["weight"] for item in first["components"][1:]), 9) == 1
        assert first["components"][1]["contribution"] == 21.3333
    finally:
        subject.close()


def test_build_is_idempotent_and_contains_no_legacy_membership(tmp_path):
    subject, _ = service(tmp_path)
    try:
        first = subject.build_level3_leaders("2026-08-14")
        second = subject.build_level3_leaders("2026-08-14")
        assert first["id"] == second["id"]
        assert second["idempotent_reuse"] is True
        rows = subject.store.all_rows(first["id"])
        assert rows
        assert all("legacy_track_id" not in row and "membership_type" not in row for row in rows)
    finally:
        subject.close()


def test_top_leaders_include_quality_unknown_leader(tmp_path):
    """第一阶段规模前2的龙头必须全部展示，质量分缺失不能从页面消失。"""

    class PartialValueLine(FakeValueLine):
        def _leader_rows(self, sector_code, sector_name, members, as_of, financials, fundamentals, quotes, market_context):
            rows = []
            for rank, symbol in enumerate(reversed(members), 1):
                rows.append({
                    "symbol": symbol, "name": symbol[0], "rank": rank,
                    # 规模第1的龙头质量分缺失（两段式：排名仍在，资格不给）
                    "score": None if rank == 1 else 90 - rank,
                    "position_score": 90 - rank,
                    "coverage": 0.6 if rank == 1 else 1.0,
                    "component_scores": {}, "raw_features": {},
                    "provenance_key": f"{sector_code}:{symbol}",
                })
            return rows

    value_line = PartialValueLine()
    subject = Level3IndustryLeaderService(
        store=Level3LeaderStore(tmp_path / "research.db"), profiles=FakeProfiles(),
        value_line=value_line, tdx_store=FakeTdxStore(), market_history=FakeHistory(),
    )
    try:
        subject.build_level3_leaders("2026-08-14")
        top = subject.get_all_level3_top_leaders(limit=2)
        codes = [row["stock_code"] for row in top["items"]["I1"]]
        assert codes == ["C.SH", "B.SH"]  # C 规模第1(质量未知) + B 质量分最高的入池龙头
        first = top["items"]["I1"][0]
        assert first["leader_score"] is None
        assert first["eligibility_status"] == "ineligible"
        assert first["explanation"]["summary"].endswith(
            "质量评分数据不足；行业龙头席位仍按规模保留，质量留给后续低估筛选。"
        )
        pool = subject.ensure_current_pool()
        pool_codes = {
            row["stock_code"]
            for row in pool["members"]
            if row.get("lifecycle_status") in {"NEW", "ACTIVE", "REENTERED"}
            and row.get("level3_code") == "I1"
        }
        assert "C.SH" in pool_codes  # 规模第1、质量不足，仍必须进入行业龙头池
        assert "B.SH" in pool_codes
    finally:
        subject.close()


def test_materialize_pool_keeps_quality_insufficient_size_leaders(tmp_path: Path) -> None:
    store = Level3LeaderStore(tmp_path / "research.db")
    try:
        run = store.start_run(
            idempotency_key="quality-gap", as_of="2026-09-07", catalog_as_of="2026-09-07",
            formula_version=FORMULA_VERSION,
        )
        store.finish_run(run["id"], rows=[
            {
                "as_of": "2026-09-07",
                "level1_code": "L1", "level1_name": "一级",
                "level2_code": "L2", "level2_name": "二级",
                "level3_code": "I1", "level3_name": "集成电路设计",
                "stock_code": "688825.SH", "stock_name": "长鑫科技",
                "leader_rank": 1, "leader_score": None,
                "leader_formula_version": FORMULA_VERSION,
                "component_scores": {}, "coverage": 0.6,
                "eligibility_status": "ineligible",
                "eligibility_reasons": ["QUALITY_DATA_INSUFFICIENT"],
                "metric_applicability_notes": [], "raw_features": {},
                "provenance_key": "I1:688825.SH",
            },
            {
                "as_of": "2026-09-07",
                "level1_code": "L1", "level1_name": "一级",
                "level2_code": "L2", "level2_name": "二级",
                "level3_code": "I1", "level3_name": "集成电路设计",
                "stock_code": "688041.SH", "stock_name": "海光信息",
                "leader_rank": 2, "leader_score": 59.5,
                "leader_formula_version": FORMULA_VERSION,
                "component_scores": {}, "coverage": 1.0,
                "eligibility_status": "eligible",
                "eligibility_reasons": [],
                "metric_applicability_notes": [], "raw_features": {},
                "provenance_key": "I1:688041.SH",
            },
            {
                "as_of": "2026-09-07",
                "level1_code": "L1", "level1_name": "一级",
                "level2_code": "L2", "level2_name": "二级",
                "level3_code": "I1", "level3_name": "集成电路设计",
                "stock_code": "603986.SH", "stock_name": "兆易创新",
                "leader_rank": 3, "leader_score": 64.0,
                "leader_formula_version": FORMULA_VERSION,
                "component_scores": {}, "coverage": 1.0,
                "eligibility_status": "eligible",
                "eligibility_reasons": [],
                "metric_applicability_notes": [], "raw_features": {},
                "provenance_key": "I1:603986.SH",
            },
        ], statistics={})
        pool, created = store.materialize_pool(run["id"])
        assert created is True
        active = [
            row for row in pool["members"]
            if row["lifecycle_status"] in {"NEW", "ACTIVE", "REENTERED"}
        ]
        assert sorted(row["stock_code"] for row in active) == ["688041.SH", "688825.SH"]
        assert next(row["eligibility_status"] for row in active if row["stock_code"] == "688825.SH") == "ineligible"
    finally:
        store.close()


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

    monkeypatch.setattr(value_l3_routes, "get_level3_leader_service", lambda: ApiService())
    app = FastAPI()
    register_value_l3_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    assert client.get("/api/value/industry-tree").json()["level3_total"] == 345
    response = client.get("/api/value/industries/881321.SH/leaders?limit=2")
    assert response.status_code == 200
    assert response.json()["items"][0]["stock_code"] == "A.SH"
    all_response = client.get("/api/value/level3-leaders?limit=2")
    assert all_response.status_code == 200
    assert all_response.json()["items"]["I1"][0]["stock_code"] == "A.SH"

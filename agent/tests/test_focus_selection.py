from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import focus_selection_routes
from src.api.focus_selection_routes import register_focus_selection_routes
from src.focus_selection.service import FocusSelectionService


AS_OF = "2026-08-27"


def pool_item(code: str, *, score: float, valuation: str = "DEEPLY_UNDERVALUED", entry: str = "WATCH") -> dict:
    return {
        "market": "CN", "stock_code": code, "company_name": f"公司{code}",
        "industry_code": f"L3-{code}", "industry_name": "测试三级行业", "leader_rank": 1,
        "leader_score": score, "valuation_status": valuation, "current_price": 10.0,
        "fair_value_mid": 20.0, "historical_valuation_status": "CHEAP",
        "support_status": "AVAILABLE", "entry_level": entry, "source_as_of": AS_OF,
        "metadata": {},
    }


def thesis(*, status: str = "FORMING", authority: str = "HUMAN_CONFIRMED") -> dict:
    return {
        "status": status, "authority_status": authority,
        "source_data_as_of": AS_OF, "created_at": f"{AS_OF}T10:00:00+00:00",
    }


def preparation(*, financial: str = "READY", profile: str = "READY") -> dict:
    return {"research_as_of": AS_OF, "financial_status": financial, "business_profile_status": profile}


def risk(*, overall: str = "LOW", trap: str = "LOW_TRAP_RISK") -> dict:
    return {"source_as_of": AS_OF, "overall_risk": overall, "value_trap_risk": trap}


class PoolRepository:
    def __init__(self, items: list[dict], snapshots: dict[str, list[dict]] | None = None) -> None:
        self.items = items
        self.snapshots = snapshots or {}
        self.calls: list[tuple[str, str | None]] = []

    def active(self, market: str) -> list[dict]:
        self.calls.append(("active", market))
        return deepcopy(self.items)

    def snapshots_for_as_of(self, source_as_of: str, market: str) -> list[dict]:
        self.calls.append(("snapshot", source_as_of))
        return deepcopy(self.snapshots.get(source_as_of, []))


class MappingRepository:
    def __init__(self, values: dict[str, dict]) -> None:
        self.values = values
        self.calls: list[tuple[str, str, str]] = []

    def get(self, market: str, stock_code: str, as_of: str) -> dict | None:
        self.calls.append((market, stock_code, as_of))
        return deepcopy(self.values.get(stock_code))


class ThesisRepository:
    def __init__(self, values: dict[str, dict]) -> None:
        self.values = values
        self.calls: list[tuple[str, str]] = []

    def get_current_thesis(self, market: str, stock_code: str) -> dict | None:
        self.calls.append((market, stock_code))
        return deepcopy(self.values.get(stock_code))


def build_service(items: list[dict], *, risks: dict[str, dict] | None = None,
                  preparations: dict[str, dict] | None = None, theses: dict[str, dict] | None = None,
                  snapshots: dict[str, list[dict]] | None = None):
    pool = PoolRepository(items, snapshots)
    risk_repo = MappingRepository(risks or {item["stock_code"]: risk() for item in items})
    prep_repo = MappingRepository(preparations or {item["stock_code"]: preparation() for item in items})
    thesis_repo = ThesisRepository(theses or {item["stock_code"]: thesis() for item in items})
    return FocusSelectionService(
        pool_repository=pool, risk_snapshot_repository=risk_repo,
        preparation_repository=prep_repo, thesis_repository=thesis_repo,
    ), pool, risk_repo, prep_repo, thesis_repo


def test_focus_selection_reuses_low_value_order_and_bounds_a_and_b():
    normal = [pool_item(f"000{i:03d}.SZ", score=100 - i) for i in range(1, 13)]
    high = pool_item("000900.SZ", score=150)
    unknown = pool_item("000901.SZ", score=149)
    trap = pool_item("000902.SZ", score=148)
    items = normal + [high, unknown, trap]
    risks = {item["stock_code"]: risk() for item in items}
    risks[high["stock_code"]] = risk(overall="HIGH")
    risks[unknown["stock_code"]] = risk(overall="UNKNOWN")
    risks[trap["stock_code"]] = risk(overall="MEDIUM", trap="HIGH_TRAP_RISK")
    service, pool, *_ = build_service(items, risks=risks)

    result = service.get_focus_selection()

    assert result["research_as_of"] == AS_OF
    assert result["A_count"] == 10
    assert result["B_count"] == 4  # two normal trailing companies + two soft demotions
    assert result["C_count"] == 1
    assert [item["stock_code"] for item in result["A"]] == [f"000{i:03d}.SZ" for i in range(1, 11)]
    assert {item["stock_code"] for item in result["B"]} == {"000011.SZ", "000012.SZ", "000901.SZ", "000902.SZ"}
    assert result["C"][0]["stock_code"] == "000900.SZ"
    assert "高等级风险" in str(result["C"][0]["primary_demotion_reason"])
    assert all("focus_score" not in item for tier in ("A", "B", "C") for item in result[tier])
    assert pool.calls == [("active", "CN")]


def test_unknown_and_high_trap_are_soft_but_profile_partial_and_ai_provisional_remain_visible_a_cautions():
    provisional = pool_item("605108.SH", score=100)
    unknown = pool_item("600210.SH", score=99)
    service, *_ = build_service(
        [provisional, unknown],
        risks={"605108.SH": risk(overall="MEDIUM", trap="MEDIUM_TRAP_RISK"), "600210.SH": risk(overall="UNKNOWN")},
        preparations={"605108.SH": preparation(profile="PARTIAL"), "600210.SH": preparation()},
        theses={"605108.SH": thesis(authority="AI_PROVISIONAL"), "600210.SH": thesis()},
    )

    result = service.get_focus_selection()

    assert [item["stock_code"] for item in result["A"]] == ["605108.SH"]
    assert "公司核心逻辑由 AI 初步形成，待人工复核" in result["A"][0]["focus_cautions"]
    assert result["A"][0]["business_profile_status"] == "PARTIAL"
    assert result["A"][0]["value_trap_risk"] == "MEDIUM_TRAP_RISK"
    assert [item["stock_code"] for item in result["B"]] == ["600210.SH"]
    assert "风险资料不足" in result["B"][0]["focus_cautions"][0]


def test_sparse_or_extreme_valuation_is_demoted_to_b_without_changing_pool_membership():
    item = pool_item("605108.SH", score=100)
    item["current_price"] = 13.23
    item["fair_value_mid"] = 316.07
    item["metadata"] = {
        "valuation_quality": {
            "method_count": 1,
            "min_peer_count": 3,
            "method_names": ["同三级行业 PB 可比"],
        },
    }
    service, *_ = build_service([item])

    result = service.get_focus_selection()

    assert result["A"] == []
    assert [row["stock_code"] for row in result["B"]] == ["605108.SH"]
    assert result["B"][0]["primary_demotion_reason"] == "合理价值依据需要先核验"
    assert result["B"][0]["valuation_quality"]["status"] == "REVIEW_REQUIRED"
    assert "合理价值仅由单一估值方法支撑，需要先核验" in result["B"][0]["focus_cautions"]


def test_extreme_midpoint_gap_is_demoted_even_when_multiple_methods_are_available():
    item = pool_item("000786.SZ", score=100)
    item["current_price"] = 5.0
    item["fair_value_mid"] = 30.0
    item["metadata"] = {
        "valuation_quality": {
            "method_count": 2,
            "min_peer_count": 12,
            "method_names": ["预测利润 + 同三级行业 PE 可比", "同三级行业 PB 可比"],
        },
    }
    service, *_ = build_service([item])

    result = service.get_focus_selection()

    assert result["A"] == []
    assert result["B"][0]["primary_demotion_reason"] == "合理价值依据需要先核验"
    assert "合理价值中枢与现价偏离过大，需要先核验估值输入" in result["B"][0]["focus_cautions"]


@pytest.mark.parametrize(
    ("code", "risk_data", "thesis_data", "entry", "prep_data", "expected"),
    [
        ("000544.SZ", risk(overall="HIGH"), thesis(), "WATCH", preparation(), "RISK_HIGH"),
        ("000545.SZ", risk(), thesis(status="FALSIFIED"), "WATCH", preparation(), "THESIS_FALSIFIED"),
        ("000546.SZ", risk(), thesis(), "BLOCKED", preparation(), "ENTRY_BLOCKED"),
        ("000547.SZ", risk(), thesis(), "WATCH", preparation(financial="PARTIAL"), "FINANCIAL_NOT_READY"),
    ],
)
def test_hard_conditions_always_go_to_c(code, risk_data, thesis_data, entry, prep_data, expected):
    item = pool_item(code, score=100, entry=entry)
    service, *_ = build_service([item], risks={code: risk_data}, preparations={code: prep_data}, theses={code: thesis_data})

    result = service.get_focus_selection()

    assert result["A"] == [] and result["B"] == []
    assert result["C"][0]["stock_code"] == code
    assert result["C"][0]["primary_demotion_reason"]
    assert expected in FocusSelectionService._hard_reason(risk=risk_data, preparation=prep_data, thesis=thesis_data, item=item)


def test_explicit_as_of_uses_matching_immutable_pool_snapshot():
    historical_as_of = "2026-08-26"
    historical = pool_item("000777.SZ", score=88)
    historical["source_as_of"] = historical_as_of
    current = pool_item("000888.SZ", score=99)
    service, pool, risk_repo, prep_repo, _ = build_service(
        [current],
        risks={"000777.SZ": {**risk(), "source_as_of": historical_as_of}},
        preparations={"000777.SZ": {**preparation(), "research_as_of": historical_as_of}},
        theses={"000777.SZ": {**thesis(), "source_data_as_of": historical_as_of, "created_at": f"{historical_as_of}T10:00:00+00:00"}},
        snapshots={historical_as_of: [historical]},
    )

    result = service.get_focus_selection(as_of=historical_as_of)

    assert result["research_as_of"] == historical_as_of
    assert [item["stock_code"] for item in result["A"]] == ["000777.SZ"]
    assert ("snapshot", historical_as_of) in pool.calls
    assert risk_repo.calls == [("CN", "000777.SZ", historical_as_of)]
    assert prep_repo.calls == [("CN", "000777.SZ", historical_as_of)]


def test_unavailable_historical_as_of_is_rejected_without_mutation():
    item = pool_item("000001.SZ", score=90)
    service, pool, *_ = build_service([item])

    with pytest.raises(ValueError, match="FOCUS_SELECTION_AS_OF_UNAVAILABLE"):
        service.get_focus_selection(as_of="2026-08-20")

    assert pool.calls == [("active", "CN"), ("snapshot", "2026-08-20")]


def test_focus_selection_api_is_read_only(monkeypatch):
    expected = {"research_as_of": AS_OF, "total_low_value": 1, "A_count": 1, "B_count": 0, "C_count": 0, "A": [], "B": [], "C": [], "read_only": True}

    class Service:
        def get_focus_selection(self, *, as_of=None):
            assert as_of == AS_OF
            return expected

    app = FastAPI()

    async def allow():
        return None

    monkeypatch.setattr(focus_selection_routes, "get_focus_selection_service", lambda: Service())
    register_focus_selection_routes(app, allow)
    response = TestClient(app).get(f"/api/value/focus-selection?as_of={AS_OF}")

    assert response.status_code == 200
    assert response.json() == expected


def test_preparation_gap_on_research_day_falls_back_to_latest_pit_snapshot() -> None:
    code = "605108.SH"
    pool_row = pool_item(code, score=90)
    earlier = {"research_as_of": "2026-08-26", "financial_status": "READY", "business_profile_status": "READY"}

    class GapPreparationRepository:
        def __init__(self) -> None:
            self.latest_calls: list[str] = []

        def get(self, _market: str, _stock_code: str, as_of: str) -> dict | None:
            assert as_of == AS_OF
            return None  # 同日准备仍在异步物化

        def latest_on_or_before(self, _market: str, _stock_code: str, on_or_before: str) -> dict | None:
            self.latest_calls.append(on_or_before)
            return deepcopy(earlier)

    prep_repo = GapPreparationRepository()
    service = FocusSelectionService(
        pool_repository=PoolRepository([pool_row]),
        risk_snapshot_repository=MappingRepository({code: risk()}),
        preparation_repository=prep_repo,
        thesis_repository=ThesisRepository({code: thesis()}),
    )

    result = service.get_focus_selection(as_of=AS_OF)

    assert prep_repo.latest_calls == [AS_OF]
    assert result["A_count"] == 1
    assert result["A"][0]["stock_code"] == code
    assert result["A"][0]["financial_status"] == "READY"
    assert result["A"][0]["source_dates"]["preparation"] == "2026-08-26"

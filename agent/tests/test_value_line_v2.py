from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.strategy_engines.macro_data import MacroDataService
from src.strategy_engines.policy_data import PolicyDataService
from src.strategy_engines.value.macro_sector_v2 import INDUSTRY_TO_GROUP, describe as macro_sector_profile
from src.strategy_engines.value.sector_score_v2 import calculate as sector_score
from src.strategy_engines.value_data_store import ValueDataStore
from src.strategy_engines.value_market_history import ValueMarketHistoryService
from src.strategy_engines.value_line import ValueLineService
from src.tdx_data.financial_history import FinancialHistoryService, _financial_rows, cagr, normalize_financial_row
from src.tdx_data.store import TdxDataStore


class DummyClient:
    def __init__(self, home: Path) -> None:
        self.home = home


def test_financial_row_mapping_uses_announcement_time_and_yuan() -> None:
    row = normalize_financial_row("600519.SH", {
        "tag_time": "20231231", "announce_time": "20240331", "FN230": "100000000.00",
        "FN232": "25000000", "FN234": "30000000", "FN40": "200000000",
        "FN63": "50000000", "FN72": "150000000", "FN202": "60", "FN281": "21.5",
    }, "package-1")
    assert row is not None
    assert row["report_date"] == "2023-12-31"
    assert row["announcement_date"] == "2024-03-31"
    assert row["revenue"] == 100_000_000
    assert row["gross_profit"] == 60_000_000
    assert row["period_type"] == "annual"


def test_financial_query_prevents_lookahead(tmp_path: Path) -> None:
    cache = TdxDataStore(tmp_path / "tdx.db")
    home = tmp_path / "tdx"
    cw = home / "vipdoc" / "cw"
    cw.mkdir(parents=True)
    (cw / "gpcw20231231.dat").write_bytes(b"x" * 2048)
    cache.upsert_records("financial_history", [
        {"key": "600519.SH:2023-12-31:2024-03-31", "category": "600519.SH", "payload": {"symbol": "600519.SH", "report_date": "2023-12-31", "announcement_date": "2024-03-31", "period_type": "annual"}},
        {"key": "600519.SH:2024-03-31:2024-04-29", "category": "600519.SH", "payload": {"symbol": "600519.SH", "report_date": "2024-03-31", "announcement_date": "2024-04-29", "period_type": "q1"}},
    ])
    service = FinancialHistoryService(cache, DummyClient(home))  # type: ignore[arg-type]
    assert service.query("600519.SH", as_of="2024-04-01")["total"] == 1
    assert service.query("600519.SH", as_of="2024-04-30")["total"] == 2
    assert service.query("600519.SH", as_of="2024-04-30", period_type="annual")["total"] == 1


def test_module_last_success_is_not_overwritten_by_running_state(tmp_path: Path) -> None:
    cache = TdxDataStore(tmp_path / "tdx.db")
    cache.ensure_modules(["financial_history"])
    cache.set_module_state("financial_history", status="ready", last_success_at="2026-08-12T09:00:00+00:00")
    cache.set_module_state("financial_history", status="running", progress=0, total=0)
    state = cache.module_states()[0]
    assert state["status"] == "running"
    assert state["last_success_at"] == "2026-08-12T09:00:00+00:00"


def test_value_service_recovers_jobs_interrupted_by_restart(tmp_path: Path) -> None:
    cache = TdxDataStore(tmp_path / "tdx.db")
    data_store = ValueDataStore(tmp_path / "research.db")
    cache.ensure_modules(["financial_history"])
    cache.set_module_state("financial_history", status="running")
    data_store.create_job("value-stale", ["financial_history"], "2026-08-13")
    data_store.update_job("value-stale", status="running", current_module="financial_history")
    service = ValueLineService(cache=cache, data_store=data_store)
    try:
        assert data_store.get_job("value-stale")["status"] == "failed"
        state = {row["module"]: row for row in cache.module_states()}["financial_history"]
        assert state["status"] == "failed"
        assert state["error"] == "service_restarted_before_completion"
    finally:
        service.close()


def test_membership_snapshot_reuses_cached_tdx_constituents(tmp_path: Path) -> None:
    cache = TdxDataStore(tmp_path / "tdx.db")
    data_store = ValueDataStore(tmp_path / "research.db")
    cache.upsert_records("sector_members", [
        {
            "key": "881121.SH:600000.SH", "category": "881121.SH", "name": "浦发银行",
            "payload": {
                "sector_code": "881121.SH", "sector_name": "银行",
                "code": "600000.SH", "name": "浦发银行",
            },
        },
        {
            "key": "880001.SH:600000.SH", "category": "880001.SH", "name": "浦发银行",
            "payload": {
                "sector_code": "880001.SH", "sector_name": "不应纳入价值赛道",
                "code": "600000.SH",
            },
        },
    ])
    service = ValueLineService(cache=cache, data_store=data_store)
    try:
        result = service.snapshot_memberships("2026-08-14")
        assert result == {
            "status": "ready", "industries": 1, "memberships": 1,
            "as_of": "2026-08-14", "source": "tdx_cache",
        }
        stored = data_store.memberships_as_of("2026-08-14")
        assert [(row["sector_code"], row["symbol"]) for row in stored["items"]] == [
            ("881121.SH", "600000.SH"),
        ]
    finally:
        service.close()


def test_cagr_guards_loss_to_profit_zero_and_short_history() -> None:
    ready = cagr([(str(year), value) for year, value in zip(range(2018, 2024), [100, 110, 121, 133.1, 146.41, 161.051])])
    assert ready["years"] == 5
    assert round(ready["value"], 4) == 10.0
    assert cagr([("2020", -10), ("2021", 1), ("2022", 2), ("2023", 3)])["status"] == "loss_to_profit"
    assert cagr([("2022", 1), ("2023", 2)])["status"] == "insufficient_history"


def test_financial_response_variants_do_not_abort_market_refresh() -> None:
    row = {"tag_time": "20231231", "announce_time": "20240331", "FN230": 1}
    assert _financial_rows([row, "--"]) == ([row], 1)
    assert _financial_rows(row) == ([row], 0)
    assert _financial_rows({"20231231": row, "bad": "--"}) == ([row], 1)
    assert _financial_rows("--") == ([], 1)


def test_value_service_lists_all_v2_leaders_when_sector_is_omitted(tmp_path: Path) -> None:
    cache = TdxDataStore(tmp_path / "tdx.db")
    data_store = ValueDataStore(tmp_path / "research.db")
    cache.upsert_records("value_leader_scores_v2", [
        {"key": "2026-08-13:881001.SH:000001.SZ", "category": "2026-08-13:881001.SH", "payload": {"data_as_of": "2026-08-13", "symbol": "000001.SZ", "score": 70.0}},
        {"key": "2026-08-13:881002.SH:600000.SH", "category": "2026-08-13:881002.SH", "payload": {"data_as_of": "2026-08-13", "symbol": "600000.SH", "score": 80.0}},
    ])
    service = ValueLineService(cache=cache, data_store=data_store)
    try:
        result = service.leaders(as_of="2026-08-13")
        assert result["formula_version"] == "value-leader-v2.0.0"
        assert [item["symbol"] for item in result["items"]] == ["600000.SH", "000001.SZ"]
    finally:
        service.close()


def test_sector_v2_requires_macro_and_six_dimensions() -> None:
    components = {
        "momentum": 60.0, "earnings_momentum": 60.0, "valuation": 60.0,
        "capital_flow_proxy": 60.0, "macro_fit": None, "policy_fit": None,
        "risk_quality": 60.0,
    }
    assert sector_score(components).status == "macro_pending"
    components["macro_fit"] = 60.0
    assert sector_score(components).status == "ready"
    components["risk_quality"] = None
    assert sector_score(components).status == "insufficient_data"


def test_macro_snapshot_is_pit_and_reproducible(tmp_path: Path) -> None:
    store = ValueDataStore(tmp_path / "research.db")
    series = [
        ("pmi_manufacturing", "growth", True), ("cpi_yoy", "inflation", True),
        ("m1_yoy", "liquidity", True), ("new_rmb_loans_yoy", "credit", True),
        ("lpr_5y", "financial_conditions", False),
    ]

    def provider():
        rows = []
        for series_id, axis, higher_good in series:
            for month in range(1, 7):
                rows.append({
                    "series_id": series_id, "axis": axis, "higher_good": higher_good,
                    "observation_date": f"2024-{month:02d}-01", "release_date": f"2024-{month:02d}-15",
                    "vintage_id": f"{series_id}-{month}", "value": float(month), "unit": "%",
                    "source": "official", "source_url": "https://example.test", "release_status": "official",
                    "fetched_at": "2024-07-01T00:00:00+00:00", "metadata": {},
                })
        return rows

    service = MacroDataService(store, provider=provider)
    first = service.refresh("2024-07-01")["snapshot"]
    second = service.refresh("2024-07-01")["snapshot"]
    third = service.refresh("2024-07-01")["snapshot"]
    assert first["coverage"] == 1.0
    assert first["axis_coverage"] == 1.0
    assert first["series_count"] == len(series)
    assert first["series_total"] == 19
    assert first["series_coverage"] == pytest.approx(len(series) / 19)
    assert "social_financing_increment" in first["missing_series"]
    assert first["provenance_key"] == second["provenance_key"] == third["provenance_key"]
    assert first["score"] == second["score"] == third["score"]
    assert first["coverage"] == second["coverage"] == third["coverage"]
    assert store.macro_series_as_of("2024-03-01")
    assert all(row["release_date"] <= "2024-03-01" for row in store.macro_series_as_of("2024-03-01"))


def test_macro_catalog_includes_exports_social_financing_and_market_risk() -> None:
    from src.strategy_engines.macro_data import MARKET_SERIES_SPECS, SERIES_SPECS

    series = {item[3] for item in SERIES_SPECS}
    assert {"exports_yoy", "social_financing_increment"} <= series
    assert {"csi_all_share_risk_appetite", "a_share_breadth_20d"} <= set(MARKET_SERIES_SPECS)


def test_domestic_official_http_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.strategy_engines.domestic_network as domestic_network

    captured: dict[str, object] = {}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(domestic_network.httpx, "Client", Client)
    direct = domestic_network.direct_domestic_http_client(headers={"X-Test": "1"})

    assert isinstance(direct, Client)
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is True
    assert captured["headers"] == {"X-Test": "1"}


def test_macro_sector_matrix_explicitly_covers_all_tdx_second_level_industries() -> None:
    assert len(INDUSTRY_TO_GROUP) == 128
    assert set(INDUSTRY_TO_GROUP.values()) == {
        "financial", "real_estate", "resources", "industrial", "consumer_discretionary",
        "consumer_staples", "technology", "media_services", "healthcare", "utilities",
        "transport", "diversified",
    }
    assert all(macro_sector_profile(name, {"growth": 50})["explicit"] for name in INDUSTRY_TO_GROUP)


def test_macro_sector_profile_exposes_direction_rank_inputs_and_drivers() -> None:
    profile = macro_sector_profile("证券", {
        "growth": 46.8, "inflation": 46.9, "liquidity": 55.8,
        "credit": 67.2, "financial_conditions": 60.7,
    })
    assert profile["group_name"] == "金融"
    assert profile["stance"] == "beneficiary"
    assert profile["score"] == pytest.approx(60.53)
    assert [item["axis"] for item in profile["drivers"]] == ["financial_conditions", "liquidity", "credit"]


def test_membership_history_does_not_fabricate_old_snapshot(tmp_path: Path) -> None:
    store = ValueDataStore(tmp_path / "research.db")
    store.replace_membership_snapshot("2026-08-13", [{
        "sector_code": "881101.SH", "sector_name": "样例行业", "symbol": "600000.SH", "source": "TDX",
    }])
    assert store.memberships_as_of("2026-08-12")["status"] == "membership_history_unavailable"
    assert store.memberships_as_of("2026-08-13")["status"] == "ready"


def test_policy_fit_is_null_without_valid_events(tmp_path: Path) -> None:
    store = ValueDataStore(tmp_path / "research.db")
    assert PolicyDataService(store).policy_fit("881101.SH", "2026-08-13")["score"] is None


def test_policy_model_outage_and_low_confidence_stay_pending(tmp_path: Path) -> None:
    store = ValueDataStore(tmp_path / "research.db")
    event = {"title": "产业支持政策", "content_text": "半导体产业支持政策"}
    candidates = [{"industry_code": "881121.SH", "industry_name": "半导体", "evidence": "半导体"}]

    unavailable = PolicyDataService(
        store,
        model_classifier=lambda _event, _candidates: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    assert unavailable._classify(event, candidates)[0]["confidence"] == 0.0

    low_confidence = PolicyDataService(
        store,
        model_classifier=lambda _event, _candidates: [{
            **candidates[0], "direction": 1, "strength": 2, "horizon_days": 90, "confidence": 0.64,
        }],
    )
    result = low_confidence._classify(event, candidates)
    assert result[0]["confidence"] < 0.65
    assert low_confidence.policy_fit("881121.SH", "2026-08-13")["score"] is None

    candidates = PolicyDataService._candidate_industries(
        "推动半导体产业高质量发展", [{"code": "881121.SH", "name": "半导体"}],
    )
    assert candidates[0]["sensitivity"] == 1.0


def test_policy_page_structure_change_returns_partial(tmp_path: Path) -> None:
    service = PolicyDataService(
        ValueDataStore(tmp_path / "research.db"),
        fetcher=lambda _url, _headers: ("<html><body>no links</body></html>", {}),
    )
    result = service.refresh([])
    assert result["status"] == "partial"
    assert all("page_structure_changed" in error for error in result["errors"])


def test_policy_classification_persists_industry_sensitivity(tmp_path: Path) -> None:
    store = ValueDataStore(tmp_path / "research.db")
    store.upsert_policy_events(
        [{
            "id": "policy-1", "document_number": "国发〔2026〕1号", "title": "测试政策",
            "normalized_url": "https://www.gov.cn/policy-1", "content_hash": "hash-1",
            "source": "国务院", "published_at": "2026-08-01", "fetched_at": "2026-08-02T00:00:00+00:00",
            "etag": "etag-1", "last_modified": "Fri, 01 Aug 2026 00:00:00 GMT",
            "status": "ready", "content_text": "测试政策正文",
        }],
        [{
            "id": "classification-1", "event_id": "policy-1", "industry_code": "881121.SH",
            "industry_name": "半导体", "direction": 1, "strength": 3, "sensitivity": .75,
            "horizon_days": 90, "evidence": "半导体", "confidence": .9,
            "classifier_version": "test", "status": "ready", "created_at": "2026-08-02T00:00:00+00:00",
        }],
    )
    classification = store.policies(status="ready")[0]["classifications"][0]
    assert classification["sensitivity"] == .75
    assert store.policy_request_headers("https://www.gov.cn/policy-1") == {
        "If-None-Match": "etag-1", "If-Modified-Since": "Fri, 01 Aug 2026 00:00:00 GMT",
    }
    service = PolicyDataService(store)
    early = service.policy_fit("881121.SH", "2026-08-13")["score"]
    late = service.policy_fit("881121.SH", "2027-08-13")["score"]
    assert early is not None and late is not None and early > late > 50


def test_tdx_history_normalizes_volume_and_amount_units() -> None:
    payload = {
        "Close": [{"index": "2026-08-13T00:00:00", "600519.SH": 1350}],
        "Volume": [{"index": "2026-08-13T00:00:00", "600519.SH": 2_000_000}],
        "Amount": [{"index": "2026-08-13T00:00:00", "600519.SH": 270_000}],
    }
    row = ValueMarketHistoryService._tdx_rows(payload, ["600519.SH"])[0]
    assert row["volume"] == 2_000_000
    assert row["amount"] == 2_700_000_000


def test_tdx_benchmark_alias_is_read_from_local_shenzhen_namespace() -> None:
    from src.strategy_engines.value_market_history import BENCHMARK, TDX_BENCHMARK_ALIAS

    assert BENCHMARK == "000985.SH"
    assert TDX_BENCHMARK_ALIAS == "000985.SZ"


def test_history_partition_preserves_row_source_and_failed_symbol(tmp_path: Path) -> None:
    from src.strategy_engines.history import HistoricalFeatureStore

    store = HistoricalFeatureStore(tmp_path / "history", tmp_path / "research.db")
    store.write_partition(
        market="CN", dataset="value_ohlcv", data_as_of="2026-08-12",
        frame=pd.DataFrame([
            {"symbol": "600000.SH", "close": 10.0, "source": "TongDaXin"},
            {"symbol": "600001.SH", "close": 20.0, "source": "AKShare/Eastmoney"},
        ]), provider="TongDaXin+AKShare",
    )
    service = ValueMarketHistoryService(store=store, client=DummyClient(tmp_path))  # type: ignore[arg-type]
    merged = service._retain_failed_symbols(
        "2026-08-12", pd.DataFrame([
            {"symbol": "600000.SH", "close": 11.0, "source": "TongDaXin"},
        ]),
    )
    assert set(merged["symbol"]) == {"600000.SH", "600001.SH"}
    assert merged.set_index("symbol").loc["600001.SH", "source"] == "AKShare/Eastmoney"

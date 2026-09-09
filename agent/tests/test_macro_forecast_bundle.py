"""预测输入包：特征、宏观选择、指纹、幂等、稳定性（任务卡1 §6-8；T12 及验收）。"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.macro_forecast.bars import ForecastBarStore, filter_completed_sessions, tdx_payload_rows
from src.macro_forecast.bundle import (
    DATA_MODE_DOMESTIC_LIMITED, DATA_MODE_FACTS_ONLY, DATA_MODE_FULL,
    ForecastBundleStore, StabilityGuard, build_bundle_payload, compute_entity_features,
    decide_data_mode, macro_group_coverage, select_macro_facts,
)
from src.macro_forecast.contracts import (
    CALENDAR_CONFIRMED, MIN_BENCHMARK_CLOSINGS, NEUTRAL_BAND, RELATIVE_BAND,
    build_timeline, canonical_json, fingerprint,
)

SH = ZoneInfo("Asia/Shanghai")
DAYS = ["20260525", "20260526", "20260527", "20260528", "20260529",
        "20260601", "20260602", "20260603", "20260604", "20260605"]


def _bars(code: str, closes: list[float], end_day: str = "20260605", *, amounts: list[float] | None = None) -> list[dict]:
    """生成以 end_day 结尾、按工作日向前构造的合成 K 线（只用于特征计算）。"""
    from datetime import date as _date, timedelta as _timedelta

    anchor = _date(int(end_day[:4]), int(end_day[4:6]), int(end_day[6:8]))
    days: list[str] = []
    cursor = anchor
    while len(days) < len(closes):
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor -= _timedelta(days=1)
    days.reverse()
    rows = []
    for day, close in zip(days, closes):
        rows.append({
            "code": code, "trade_date": day,
            "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
            "close": float(close), "volume": 1_000_000.0,
            "amount": (amounts[days.index(day)] if amounts else 10_000_000.0),
        })
    return rows


# ---------------------------------------------------------------------------
# 市场特征（§8.2）
# ---------------------------------------------------------------------------

def test_features_require_p_day_bar() -> None:
    bars = _bars("000300.SH", [100.0] * 22, end_day="20260605")
    features = compute_entity_features(bars, expected_last_date="20260604", min_closings=MIN_BENCHMARK_CLOSINGS)
    assert features["status"] == "EXCLUDED_NO_P_BAR"


def test_features_insufficient_history_excluded_not_zero_filled() -> None:
    bars = _bars("000300.SH", [100.0] * 10)
    features = compute_entity_features(bars, expected_last_date="20260605", min_closings=21)
    assert features["status"] == "EXCLUDED_INSUFFICIENT_HISTORY"
    assert features["closings"] == 10


def test_features_math_1d_5d_vol20() -> None:
    closes = [100.0 + i for i in range(25)]  # 25 个递增收盘
    bars = _bars("000300.SH", closes)
    features = compute_entity_features(bars, expected_last_date="20260605", min_closings=21)
    assert features["status"] == "READY"
    assert features["closings"] == 25
    assert features["ret_1d"] == pytest.approx(124.0 / 123.0 - 1, abs=1e-9)
    assert features["ret_5d"] == pytest.approx(124.0 / 119.0 - 1, abs=1e-9)
    assert features["vol_20d"] is not None and features["vol_20d"] > 0


def test_features_amount_ratio_and_missing_amount() -> None:
    closes = [100.0 + i for i in range(25)]
    amounts = [10_000_000.0] * 24 + [20_000_000.0]
    bars = _bars("000300.SH", closes, amounts=amounts)
    features = compute_entity_features(bars, expected_last_date="20260605", min_closings=21)
    assert features["amount_ratio_vs_20d_mean"] == pytest.approx(2.0, rel=1e-6)
    zero = _bars("000300.SH", closes, amounts=[0.0] * 25)
    assert compute_entity_features(zero, expected_last_date="20260605", min_closings=21)["amount_ratio_vs_20d_mean"] is None


# ---------------------------------------------------------------------------
# 宏观事实选择（§6.2）
# ---------------------------------------------------------------------------

def _series_rows() -> list[dict]:
    return [
        {"series_id": "cpi_yoy", "observation_date": "2026-04-01", "release_date": "2026-06-01",
         "value": 0.3, "unit": "%", "source": "统计局", "fetched_at": "2026-06-01T18:00:00+08:00", "vintage_id": "r1"},
        {"series_id": "cpi_yoy", "observation_date": "2026-05-01", "release_date": "2026-06-01",
         "value": 0.5, "unit": "%", "source": "统计局", "fetched_at": "2026-06-01T18:00:00+08:00", "vintage_id": "r2"},
        # 今日 11:33 才入库（晚于 C）→ 不可用
        {"series_id": "shibor_3m", "observation_date": "2026-06-01", "release_date": "2026-06-01",
         "value": 1.45, "unit": "%", "source": "同业拆借中心", "fetched_at": "2026-06-02T11:33:00+08:00", "vintage_id": "s1"},
        {"series_id": "shibor_3m", "observation_date": "2026-05-29", "release_date": "2026-05-29",
         "value": 1.44, "unit": "%", "source": "同业拆借中心", "fetched_at": "2026-06-01T18:00:00+08:00", "vintage_id": "s0"},
    ]


def test_macro_selection_drops_after_cutoff_rows() -> None:
    cutoff = datetime(2026, 6, 2, 8, 50, tzinfo=SH)  # T=0602 盘前；0602 11:33 的抓取尚未发生
    facts = {fact["series_id"]: fact for fact in select_macro_facts(_series_rows(), cutoff=cutoff)}
    assert facts["cpi_yoy"]["observation_date"] == "2026-05-01"
    assert facts["cpi_yoy"]["comparison_value"] == 0.3
    assert facts["cpi_yoy"]["direction"] == "UP"
    # shibor 只剩 05-29 那条（06-01 那条抓取晚于 C）
    assert facts["shibor_3m"]["observation_date"] == "2026-05-29"


def test_macro_selection_pit_status_marked() -> None:
    cutoff = datetime(2026, 6, 2, 8, 50, tzinfo=SH)
    facts = {fact["series_id"]: fact for fact in select_macro_facts(_series_rows(), cutoff=cutoff)}
    assert facts["cpi_yoy"]["pit_status"] in {"CONSERVATIVE_UPPER_BOUND", "FORWARD_OBSERVED"}
    assert facts["cpi_yoy"]["revision_id"] == "r2"


def test_macro_group_coverage_gate() -> None:
    catalog = [
        {"series_id": "cpi_yoy", "group": "prices"},
        {"series_id": "m2_yoy", "group": "credit_liquidity"},
        {"series_id": "gdp_yoy", "group": "growth"},
    ]
    facts = [{"series_id": "cpi_yoy", "value": 0.5}, {"series_id": "m2_yoy", "value": 8.0}]
    coverage = macro_group_coverage(facts, catalog)
    assert coverage["direction_input_ok"] is True
    assert coverage["direction_groups_covered"] == ["credit_liquidity", "prices"]
    single = macro_group_coverage([{"series_id": "cpi_yoy", "value": 0.5}], catalog)
    assert single["direction_input_ok"] is False


def test_macro_null_value_not_counted() -> None:
    coverage = macro_group_coverage([{"series_id": "cpi_yoy", "value": None}], [
        {"series_id": "cpi_yoy", "group": "prices"}])
    assert coverage["direction_input_ok"] is False


# ---------------------------------------------------------------------------
# 数据模式与阈值冻结
# ---------------------------------------------------------------------------

def test_decide_data_mode_domestic_limited_and_facts_only() -> None:
    coverage_ok = {"direction_input_ok": True}
    mode, gaps = decide_data_mode(market={"benchmark_ready": True}, macro_coverage=coverage_ok,
                                  cross_market_available=False)
    assert mode == DATA_MODE_DOMESTIC_LIMITED and "OVERSEAS_EQUITY_INDEX_UNAVAILABLE" in gaps
    mode, gaps = decide_data_mode(market={"benchmark_ready": False}, macro_coverage=coverage_ok,
                                  cross_market_available=False)
    assert mode == DATA_MODE_FACTS_ONLY
    mode, gaps = decide_data_mode(market={"benchmark_ready": True}, macro_coverage=coverage_ok,
                                  cross_market_available=True)
    assert mode == DATA_MODE_FULL and gaps == []


def test_bundle_freezes_evaluation_thresholds_and_versions() -> None:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    payload = build_bundle_payload(
        timeline=timeline,
        bars_map={"000300.SH": _bars("000300.SH", [100.0 + i for i in range(25)])},
        series_rows=_series_rows(),
        industry_rows=[{"code": "881016.SH", "name_zh": "煤炭开采", "matrix_linked": True}],
        benchmark_code="000300.SH",
        macro_catalog=[{"series_id": "cpi_yoy", "group": "prices"},
                       {"series_id": "shibor_3m", "group": "credit_liquidity"}],
        registry_version="test-registry-v1", bars_as_of="20260605",
    )
    assert payload["evaluation"]["neutral_band"] == NEUTRAL_BAND == 0.003
    assert payload["evaluation"]["relative_band"] == RELATIVE_BAND == 0.002
    assert payload["transmission_matrix_status"] == "HYPOTHESIS"
    assert payload["input_bundle_version"] == "macro-forecast-input-v1.1.0"
    assert payload["target"]["calendar_status"] == CALENDAR_CONFIRMED
    assert payload["data_mode"] == DATA_MODE_DOMESTIC_LIMITED


def test_bundle_industry_excess_vs_benchmark() -> None:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    benchmark = _bars("000300.SH", [100.0 + i for i in range(25)])
    industry = _bars("881016.SH", [50.0 + i * 0.2 for i in range(25)])
    payload = build_bundle_payload(
        timeline=timeline,
        bars_map={"000300.SH": benchmark, "881016.SH": industry},
        series_rows=_series_rows(),
        industry_rows=[{"code": "881016.SH", "name_zh": "煤炭开采", "matrix_linked": True}],
        benchmark_code="000300.SH",
        macro_catalog=[{"series_id": "cpi_yoy", "group": "prices"}],
        registry_version="t", bars_as_of="20260605",
    )
    entry = payload["industries"]["entries"][0]
    assert entry["status"] == "READY"
    assert entry["excess_ret_1d"] == pytest.approx(entry["ret_1d"] - payload["market"]["benchmark"]["ret_1d"], abs=1e-9)


def test_bundle_p_close_pending_gap() -> None:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    stale_bars = _bars("000300.SH", [100.0 + i for i in range(25)])[:-1]  # 最后一根在 0604
    payload = build_bundle_payload(
        timeline=timeline, bars_map={"000300.SH": stale_bars}, series_rows=_series_rows(),
        industry_rows=[], benchmark_code="000300.SH",
        macro_catalog=[{"series_id": "cpi_yoy", "group": "prices"}],
        registry_version="t", bars_as_of="20260604", p_close_pending=True,
    )
    assert "P_DAY_CLOSE_PENDING" in payload["gaps"]
    assert payload["market"]["benchmark"]["status"] == "EXCLUDED_NO_P_BAR"
    assert payload["data_mode"] == DATA_MODE_FACTS_ONLY


# ---------------------------------------------------------------------------
# canonical JSON / 指纹 / 幂等（§6.7）
# ---------------------------------------------------------------------------

def test_fingerprint_stable_across_key_order() -> None:
    a = {"b": 1, "a": [1, 2, {"z": None, "y": 2.5}]}
    b = {"a": [1, 2, {"y": 2.5, "z": None}], "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_changes_on_target_and_content() -> None:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    base = dict(timeline=timeline, bars_map={}, series_rows=[], industry_rows=[],
                benchmark_code="000300.SH", macro_catalog=[], registry_version="t", bars_as_of="20260605")
    first = build_bundle_payload(**base)
    same = build_bundle_payload(**copy.deepcopy(base))
    assert first["fingerprint"] == same["fingerprint"]
    changed_content = build_bundle_payload(**{**base, "series_rows": [
        {"series_id": "cpi_yoy", "observation_date": "2026-05-01", "release_date": "2026-06-01",
         "value": 0.9, "unit": "%", "source": "统计局", "fetched_at": "2026-06-01T18:00:00+08:00", "vintage_id": "r2"}]})
    assert changed_content["fingerprint"] != first["fingerprint"]
    other_target = build_bundle_payload(**{**base, "timeline": build_timeline("20260609", DAYS + ["20260608", "20260609"])})
    assert other_target["fingerprint"] != first["fingerprint"]


def test_canonical_json_rejects_nan_and_nulls_missing() -> None:
    assert json.loads(canonical_json({"x": float("nan")})) == {"x": None}
    assert json.loads(canonical_json({"x": -0.0})) == {"x": 0.0}


@pytest.fixture()
def bundle_store(tmp_path: Path) -> ForecastBundleStore:
    return ForecastBundleStore(tmp_path / "test.db")


def _sample_payload(target="2026-06-08") -> dict:
    timeline = build_timeline("20260608", DAYS + ["20260608"])
    return build_bundle_payload(
        timeline=timeline, bars_map={}, series_rows=[], industry_rows=[],
        benchmark_code="000300.SH", macro_catalog=[], registry_version="t", bars_as_of="20260605",
    )


def test_bundle_store_idempotent_and_immutable(bundle_store: ForecastBundleStore) -> None:
    payload = _sample_payload()
    first = bundle_store.save(payload)
    assert first["status"] == "SAVED" and first["written"] == 1
    again = bundle_store.save(payload)
    assert again["status"] == "ALREADY_SAVED" and again["bundle_id"] == first["bundle_id"]
    loaded = bundle_store.load(first["bundle_id"])
    assert loaded["fingerprint"] == payload["fingerprint"]
    # 内容变化 → 新 bundle，不覆盖
    payload2 = copy.deepcopy(payload)
    payload2["gaps"] = sorted(set(payload2["gaps"]) | {"NEW_GAP"})
    payload2["fingerprint"] = fingerprint({k: v for k, v in payload2.items() if k != "fingerprint"})
    second = bundle_store.save(payload2)
    assert second["bundle_id"] != first["bundle_id"]
    assert bundle_store.load(first["bundle_id"])["gaps"] == payload["gaps"]


def test_bundle_store_latest_for_target(bundle_store: ForecastBundleStore) -> None:
    first = bundle_store.save(_sample_payload())
    assert bundle_store.latest_for_target("20260608")["bundle_id"] == first["bundle_id"]
    assert bundle_store.latest_for_target("20260609") is None


def test_t12_stability_guard_detects_concurrent_write(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "guard.db")
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    guard = StabilityGuard({"db": conn})
    assert guard.stable()[0] is True
    # data_version 只在其他连接提交时递增（SQLite 语义）→ 模拟并发写方
    other = sqlite3.connect(tmp_path / "guard.db")
    with other:
        other.execute("INSERT INTO t VALUES(1)")
    other.close()
    stable, versions = guard.stable()
    assert stable is False
    assert versions["final"]["db"] > versions["initial"]["db"]
    conn.close()


# ---------------------------------------------------------------------------
# K 线采集与 PIT 留档
# ---------------------------------------------------------------------------

def test_filter_completed_sessions_drops_today_unclosed() -> None:
    now = datetime(2026, 6, 5, 11, 41, tzinfo=SH)
    rows = [
        {"code": "X", "trade_date": "20260604", "close": 1.0},
        {"code": "X", "trade_date": "20260605", "close": 1.1},
        {"code": "X", "trade_date": "20260608", "close": 1.2},  # 未来日
    ]
    kept = filter_completed_sessions(rows, now=now)
    assert [row["trade_date"] for row in kept] == ["20260604"]


def test_filter_completed_sessions_keeps_today_after_close() -> None:
    now = datetime(2026, 6, 5, 15, 30, tzinfo=SH)
    rows = [{"code": "X", "trade_date": "20260605", "close": 1.1}]
    assert filter_completed_sessions(rows, now=now)[0]["trade_date"] == "20260605"


def test_tdx_payload_rows_amount_unit_conversion() -> None:
    payload = {"Close": [{"index": "2026-06-04T00:00:00", "000300.SH": 4500.0}],
               "Volume": [{"index": "2026-06-04T00:00:00", "000300.SH": 123.0}],
               "Amount": [{"index": "2026-06-04T00:00:00", "000300.SH": 7.5}]}
    rows = tdx_payload_rows(payload, ["000300.SH"])
    assert rows[0]["close"] == 4500.0
    assert rows[0]["amount"] == 75_000.0  # 万元 → 元


def test_bar_store_first_observed_pit_marks(tmp_path: Path) -> None:
    store = ForecastBarStore(tmp_path / "bars.db")
    try:
        written = store.write_bars(
            [{"code": "000300.SH", "trade_date": "20260604", "close": 4500.0, "volume": 1.0, "amount": 2.0},
             {"code": "000300.SH", "trade_date": "20260605", "close": 4510.0}],
            fetched_at=datetime(2026, 6, 5, 16, 30, tzinfo=SH),
        )
        assert written["inserted"] == 2
        rows = store.read_bars(["000300.SH"], end_date="20260605")
        by_day = {row["trade_date"]: row for row in rows["000300.SH"]}
        # 0605 当日 16:30 采集 → STRICT_PIT_ELIGIBLE；0604 历史 → FORWARD_OBSERVED
        assert by_day["20260605"]["pit_status"] == "STRICT_PIT_ELIGIBLE"
        assert by_day["20260604"]["pit_status"] == "FORWARD_OBSERVED"
        # 二次写入不改写既有行
        rewritten = store.write_bars([{"code": "000300.SH", "trade_date": "20260604", "close": 9999.0}],
                                     fetched_at=datetime(2026, 6, 8, 9, 0, tzinfo=SH))
        assert rewritten["inserted"] == 0
        assert store.read_bars(["000300.SH"], end_date="20260604")["000300.SH"][-1]["close"] == 4500.0
    finally:
        store.close()


def test_service_prepare_with_injected_inputs(tmp_path: Path) -> None:
    from src.macro_forecast.service import prepare_input_bundle

    result = prepare_input_bundle(
        target_date="20260608", trading_days=DAYS + ["20260608"],
        bars_map={"000300.SH": _bars("000300.SH", [100.0 + i for i in range(25)])},
        series_rows=_series_rows(),
        industry_rows=[{"code": "881016.SH", "name_zh": "煤炭开采", "matrix_linked": True}],
        save=False, research_db=tmp_path / "test.db",
    )
    assert result["status"] == "BUILT"
    assert result["calendar_status"] == CALENDAR_CONFIRMED
    assert result["data_mode"] in {DATA_MODE_DOMESTIC_LIMITED, DATA_MODE_FULL, DATA_MODE_FACTS_ONLY}
    payload = result["payload"]
    assert payload["target"]["target_date"] == "2026-06-08"
    assert payload["market"]["benchmark"]["status"] == "READY"


def test_service_calendar_unavailable_when_no_future(tmp_path: Path) -> None:
    from src.macro_forecast.service import prepare_input_bundle

    result = prepare_input_bundle(
        target_date=None, trading_days=DAYS, save=False, research_db=tmp_path / "test.db",
    )
    assert result["status"] == "CALENDAR_UNAVAILABLE"

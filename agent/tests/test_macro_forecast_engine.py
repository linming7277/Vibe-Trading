"""联合预测引擎 V1：32 项契约测试（任务卡2 §51）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.macro_forecast.engine import ForecastEngine
from src.macro_forecast.features import (
    ELIGIBLE, EXCLUDED_INSUFFICIENT_HISTORY, build_industry_features, select_candidates,
)
from src.macro_forecast.forecast_service import (
    load_macro_context, run_macro_forecast,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.prompt import SYSTEM_PROMPT
from src.macro_forecast.render import render_narrative
from src.macro_forecast.validate import ForecastValidator

SH = ZoneInfo("Asia/Shanghai")
P_DAY = "20260605"
TARGET = "20260608"

MACRO_CONTEXT = {
    "snapshot_as_of": "2026-06-05", "regime": "中性",
    "axes": {"growth": 51.5, "inflation": 46.8, "liquidity": 54.0, "credit": 26.6, "financial_conditions": 64.6},
    "events": [{"research_as_of": "2026-06-05", "event_type": "MACRO_AXIS_CHANGED", "axis_key": "growth",
                "from_value": "资料不足", "to_value": "中性", "text": "经济增长由「资料不足」变为「中性」"}],
    "pit_caveat": "测试口径",
}

INDUSTRY_ROWS = [
    {"code": "881016.SH", "name_zh": "煤炭开采", "matrix_linked": True},
    {"code": "881306.SH", "name_zh": "半导体", "matrix_linked": True},
    {"code": "881479.SH", "name_zh": "电子化学品", "matrix_linked": False},
    {"code": "881203.SH", "name_zh": "电子商务", "matrix_linked": True},
]


def _weekday_days(end_day: str, count: int) -> list[str]:
    anchor = date(int(end_day[:4]), int(end_day[4:6]), int(end_day[6:8]))
    days: list[str] = []
    cursor = anchor
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _bars(code: str, closes: list[float], end_day: str = P_DAY) -> list[dict]:
    days = _weekday_days(end_day, len(closes))
    return [
        {"code": code, "trade_date": day, "open": close * 0.99, "high": close * 1.01,
         "low": close * 0.98, "close": float(close), "volume": 1e6, "amount": 1e7}
        for day, close in zip(days, closes)
    ]


def _make_bars_map() -> dict[str, list[dict]]:
    return {
        "000300.SH": _bars("000300.SH", [100 + i * 0.05 for i in range(260)]),
        "881016.SH": _bars("881016.SH", [50 + i * 0.08 for i in range(260)]),   # 强于基准
        "881306.SH": _bars("881306.SH", [80 - i * 0.04 for i in range(260)]),   # 弱于基准
        "881479.SH": _bars("881479.SH", [30 + i * 0.02 for i in range(50)]),    # 历史不足
        "881203.SH": _bars("881203.SH", [40 + i * 0.02 for i in range(260)]),
    }


def _make_bundle(calendar_status: str = "CALENDAR_CONFIRMED") -> dict:
    from src.macro_forecast.bundle import build_bundle_payload
    from src.macro_forecast.contracts import build_timeline

    days = _weekday_days(P_DAY, 20) + [TARGET]
    timeline = build_timeline(TARGET, days)
    if calendar_status == "CALENDAR_UNVERIFIED_NEXT_SESSION":
        timeline = build_timeline(TARGET, _weekday_days(P_DAY, 20))  # 日历不含 T
    bars_map = _make_bars_map()
    return build_bundle_payload(
        timeline=timeline, bars_map=bars_map,
        series_rows=[
            {"series_id": "cpi_yoy", "observation_date": "2026-05-01", "release_date": "2026-06-01",
             "value": 0.5, "unit": "%", "source": "统计局", "fetched_at": "2026-06-01T18:00:00+08:00",
             "vintage_id": "r2"},
            {"series_id": "m2_yoy", "observation_date": "2026-05-01", "release_date": "2026-06-02",
             "value": 8.2, "unit": "%", "source": "央行", "fetched_at": "2026-06-02T18:00:00+08:00",
             "vintage_id": "m1"},
        ],
        industry_rows=[row for row in INDUSTRY_ROWS if row["code"] != "881479.SH"],
        benchmark_code="000300.SH",
        macro_catalog=[{"series_id": "cpi_yoy", "group": "prices"},
                       {"series_id": "m2_yoy", "group": "credit_liquidity"}],
        registry_version="test-registry", bars_as_of=P_DAY,
    )


class FakeModel:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else '{"abstain": true, "abstain_reason": "输入异常"}'
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(content=item)


def _valid_output(direction: str = "STRONGER") -> str:
    return json.dumps({
        "market": {
            "direction": direction,
            "summary": "境内宏观背景中性，量能回落，判断震荡偏强的概率结构暂不成立" if direction == "RANGE_BOUND" else "近期相对强弱与宏观背景支持偏强判断",
            "evidence_keys": ["MKT_BENCH_RET_1D", "MKT_BENCH_RET_5D"],
            "counter_evidence_keys": ["MKT_BENCH_VOL_20D"],
            "invalidation_conditions": ["若盘前出现新的重大政策冲击或外盘大幅波动，本判断失效"],
        },
        "industries": {
            "relative_strong": [{
                "industry_id": "tdx:881016.SH", "reason": "5日相对沪深300 走强且趋势一致",
                "evidence_keys": ["IND_881016_SH_RELATIVE_5D"]}],
            "relative_weak": [{
                "industry_id": "tdx:881306.SH", "reason": "5日相对沪深300 走弱",
                "evidence_keys": ["IND_881306_SH_RELATIVE_5D"]}],
        },
        "overall_cautions": ["概念板块预测尚未启用"],
        "abstain": False, "abstain_reason": None,
    }, ensure_ascii=False)


@pytest.fixture()
def store(tmp_path: Path) -> ForecastStore:
    return ForecastStore(tmp_path / "forecast.db")


@pytest.fixture()
def engine(store: ForecastStore) -> ForecastEngine:
    return ForecastEngine(store, model_factory=lambda **kwargs: FakeModel([_valid_output()]))


def _prep(engine: ForecastEngine, bundle: dict | None = None):
    return engine.prepare(
        bundle=bundle or _make_bundle(), bars_map=_make_bars_map(),
        industry_rows=INDUSTRY_ROWS, macro_context=MACRO_CONTEXT, bundle_id="test-bundle",
    )


# ---------------------------------------------------------------- 1-3 方向
def test_01_valid_market_direction(engine: ForecastEngine) -> None:
    prep = _prep(engine)
    result = engine.run(prep=prep, bundle=prep and _make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "test-model"})
    assert result["status"] == "SHADOW"
    assert result["row"]["market_direction"] == "STRONGER"
    assert result["logical_calls"] == 1 and result["actual_requests"] == 1


def test_02_abstain_accepted(store: ForecastStore) -> None:
    output = json.dumps({
        "market": {"direction": "ABSTAIN"},
        "industries": {"relative_strong": [], "relative_weak": []},
        "overall_cautions": [],
        "abstain": True, "abstain_reason": "行业信号高度分裂，相对强弱证据冲突",
    }, ensure_ascii=False)
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([output]))
    prep = _prep(engine)
    result = engine.run(prep=prep, bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "ABSTAINED"
    assert result["row"]["market_direction"] == "ABSTAIN"


def test_03_invalid_direction_rejected(engine: ForecastEngine) -> None:
    bad = _valid_output().replace('"STRONGER"', '"UP_BIG"')
    engine.model_factory = lambda **kw: FakeModel([bad])
    prep = _prep(engine)
    result = engine.run(prep=prep, bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


# ------------------------------------------------------------ 4-7 行业约束
def test_04_unknown_industry_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["industries"]["relative_strong"][0]["industry_id"] = "tdx:880506.SH"  # 概念板块
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_05_insufficient_history_industry_excluded_from_candidates() -> None:
    features = build_industry_features(bars_map=_make_bars_map(), industry_rows=INDUSTRY_ROWS,
                                       previous_date=P_DAY, macro_axes=MACRO_CONTEXT["axes"])
    row = next(item for item in features if item["code"] == "881479.SH")
    assert row["history_status"] == EXCLUDED_INSUFFICIENT_HISTORY
    candidates = select_candidates(features)
    ids = {item["industry_id"] for item in candidates["strong"] + candidates["weak"]}
    assert "tdx:881479.SH" not in ids


def test_06_strong_weak_duplicate_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["industries"]["relative_weak"][0]["industry_id"] = "tdx:881016.SH"
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_07_more_than_three_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["industries"]["relative_strong"] = [
        {"industry_id": "tdx:881016.SH", "reason": "r", "evidence_keys": ["IND_881016_SH_RELATIVE_5D"]},
        {"industry_id": "tdx:881203.SH", "reason": "r", "evidence_keys": ["IND_881203_SH_RELATIVE_5D"]},
        {"industry_id": "tdx:881306.SH", "reason": "r", "evidence_keys": ["IND_881306_SH_RELATIVE_5D"]},
        {"industry_id": "tdx:881479.SH", "reason": "r", "evidence_keys": ["IND_881479_SH_RELATIVE_5D"]},
    ]
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


# ------------------------------------------------------------ 8-12 证据与泄漏
def test_08_evidence_missing_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["market"]["evidence_keys"] = []
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_09_invalid_evidence_key_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["market"]["evidence_keys"] = ["MKT_BENCH_RET_1D", "NOT_A_REAL_KEY"]
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_10_probability_leakage_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["market"]["summary"] = "上涨概率约 70%，建议重视"
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_11_target_point_leakage_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["market"]["invalidation_conditions"] = ["若跌破 3200 点则判断失效"]
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_12_trading_language_rejected(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["industries"]["relative_strong"][0]["reason"] = "趋势一致，建议买入"
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


# ------------------------------------------------------------ 13-17 模型预算
def test_13_transient_error_retries_once(store: ForecastStore) -> None:
    fake = FakeModel([TimeoutError("request timed out"), _valid_output()])
    engine = ForecastEngine(store, model_factory=lambda **kw: fake)
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "SHADOW"
    assert result["actual_requests"] == 2 and result["retry_count"] == 1


def test_14_second_transient_fail_model_failed(store: ForecastStore) -> None:
    fake = FakeModel([TimeoutError("timeout"), ConnectionError("connection reset")])
    engine = ForecastEngine(store, model_factory=lambda **kw: fake)
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "MODEL_FAILED"
    assert result["actual_requests"] == 2 and result["retry_count"] == 1


def test_15_validation_fail_no_retry(engine: ForecastEngine) -> None:
    fake = FakeModel(["不是 JSON"])
    engine.model_factory = lambda **kw: fake
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"
    assert result["actual_requests"] == 1  # 内容失败 0 次模型重试


def test_16_hard_deadline_stops_retry(store: ForecastStore) -> None:
    fake = FakeModel([TimeoutError("timeout"), _valid_output()])
    ticks = iter([0.0, 0.0, 85.0, 85.0, 85.0, 85.0, 85.0, 85.0])
    engine = ForecastEngine(store, model_factory=lambda **kw: fake, clock=lambda: next(ticks, 86.0))
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "MODEL_FAILED"
    assert result["error_class"] == "HARD_DEADLINE"


def test_17_model_fail_not_converted_to_range(store: ForecastStore) -> None:
    fake = FakeModel([RuntimeError("provider 500 internal")])
    engine = ForecastEngine(store, model_factory=lambda **kw: fake)
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "MODEL_FAILED"
    assert result["row"]["market_direction"] is None
    narrative = result["row"]["narrative_md"]
    assert "未生成" in narrative


# ------------------------------------------------------------ 18-22 留档与并发
def test_18_same_fingerprint_reuse(engine: ForecastEngine) -> None:
    prep = _prep(engine)
    first = engine.run(prep=prep, bundle=_make_bundle(), run_mode="SHADOW",
                       model_config={"provider": "openai", "model": "m"})
    assert first["status"] == "SHADOW" and first["logical_calls"] == 1
    second = engine.run(prep=prep, bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert second["status"] == "REUSED" and second["logical_calls"] == 0


def test_19_official_immutable_unique_index(store: ForecastStore) -> None:
    from src.macro_forecast.engine import FORECAST_FORMULA_VERSION

    record = {
        "target_trade_date": "2026-06-08", "run_mode": "OFFICIAL", "status": "OFFICIAL",
        "input_bundle_id": "b1", "input_fingerprint": "fp-1",
        "forecast_formula_version": FORECAST_FORMULA_VERSION, "prompt_version": "p",
        "renderer_version": "r", "candidate_rules_version": "c",
        "structured_payload_json": "{}", "logical_calls": 1, "actual_requests": 1, "retry_count": 0,
    }
    assert store.save(record)["status"] == "SAVED"
    record2 = {**record, "input_bundle_id": "b2", "input_fingerprint": "fp-2", "status": "OFFICIAL"}
    with pytest.raises(sqlite3.IntegrityError):
        store.save(record2)


def test_20_concurrent_single_flight(store: ForecastStore) -> None:
    first = store.acquire_lease("forecast:SHADOW:t:fp", owner="A")
    assert first["acquired"] is True
    second = store.acquire_lease("forecast:SHADOW:t:fp", owner="B")
    assert second["acquired"] is False and second["owner"] == "A"
    store.release_lease("forecast:SHADOW:t:fp", owner="A")
    third = store.acquire_lease("forecast:SHADOW:t:fp", owner="B")
    assert third["acquired"] is True


def test_21_draft_not_counted_official(store: ForecastStore) -> None:
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([_valid_output()]))
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="DRAFT",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "DRAFT"
    assert store.official_for_target("2026-06-08") is None


def test_22_calendar_unverified_prevents_official(tmp_path: Path) -> None:
    bundle = _make_bundle(calendar_status="CALENDAR_UNVERIFIED_NEXT_SESSION")
    result = run_macro_forecast(target_date=TARGET, mode="official",
                                research_db=tmp_path / "t.db", bundle=bundle)
    assert result["status"] == "BLOCKED_CALENDAR_UNVERIFIED"


# ------------------------------------------------------------ 23-28 输出边界
def test_23_theme_forecast_null(engine: ForecastEngine) -> None:
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    payload = json.loads(result["row"]["structured_payload_json"])
    assert payload["theme_forecast"] is None


def test_24_25_read_path_zero_llm_zero_network(tmp_path: Path) -> None:
    from src.macro_forecast.forecast_service import get_latest_macro_forecast

    store = ForecastStore(tmp_path / "t.db")
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([_valid_output()]))
    engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
               model_config={"provider": "openai", "model": "m"})
    store.close()
    latest = get_latest_macro_forecast(research_db=tmp_path / "t.db")
    assert latest is not None and latest["status"] == "SHADOW"
    assert "structured_payload" in latest  # 纯本地 SQLite 读取，无模型/网络路径


def test_26_no_value_line_writes(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path / "t.db")
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([_valid_output()]))
    engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
               model_config={"provider": "openai", "model": "m"})
    store.close()
    connection = sqlite3.connect(tmp_path / "t.db")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    written = tables - {"forecast_run_leases", "forecast_provider_health", "sqlite_sequence", "forecast_semantic_validations", "macro_forecast_outcomes"}
    # 宏观域写集：预测留档 + 实验日 pair 记录（Value Line 表仍被禁止）
    assert written == {"macro_market_forecasts", "macro_forecast_experiment_days"}


def test_27_no_strategy_event_writes(tmp_path: Path) -> None:
    store = ForecastStore(tmp_path / "t.db")
    with store._conn:
        store._conn.execute("CREATE TABLE IF NOT EXISTS macro_events (id TEXT)")
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([_valid_output()]))
    engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
               model_config={"provider": "openai", "model": "m"})
    assert store._conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0
    store.close()


def test_28_renderer_chinese_and_enum_free(engine: ForecastEngine) -> None:
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    narrative = result["row"]["narrative_md"]
    assert "偏强" in narrative and "相对看强行业" in narrative
    for token in ("STRONGER", "RANGE_BOUND", "ABSTAIN", "DOMESTIC_LIMITED", "SHADOW"):
        assert token not in narrative
    assert "概念/主题板块预测尚未启用" in narrative


# ------------------------------------------------------------ 29-32 行业数量与候选
def test_29_zero_industries_valid(store: ForecastStore) -> None:
    output = json.dumps({
        "market": {"direction": "RANGE_BOUND", "summary": "量能与宏观信号均不支持方向判断",
                   "evidence_keys": ["MKT_BENCH_RET_1D", "MKT_BENCH_VOL_20D"],
                   "counter_evidence_keys": ["MKT_BENCH_RET_5D"],
                   "invalidation_conditions": ["若盘前出现新的重大政策冲击，本判断失效"]},
        "industries": {"relative_strong": [], "relative_weak": []},
        "overall_cautions": [], "abstain": False, "abstain_reason": None,
    }, ensure_ascii=False)
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([output]))
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "SHADOW"
    payload = json.loads(result["row"]["structured_payload_json"])
    assert payload["validation"]["industry_entries"] == []


def test_30_one_strong_zero_weak_valid(store: ForecastStore) -> None:
    output = json.loads(_valid_output())
    output["industries"]["relative_weak"] = []
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel([json.dumps(output, ensure_ascii=False)]))
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "SHADOW"
    payload = json.loads(result["row"]["structured_payload_json"])
    entries = payload["validation"]["industry_entries"]
    assert len(entries) == 1 and entries[0]["side"] == "RELATIVE_STRONG"


def test_31_candidate_screening_deterministic() -> None:
    features = build_industry_features(bars_map=_make_bars_map(), industry_rows=INDUSTRY_ROWS,
                                       previous_date=P_DAY, macro_axes=None)
    first = select_candidates(features)
    second = select_candidates(build_industry_features(bars_map=_make_bars_map(), industry_rows=INDUSTRY_ROWS,
                                                       previous_date=P_DAY, macro_axes=None))
    assert first == second


def test_32_candidate_screening_versioned_and_directional() -> None:
    from src.macro_forecast.features import CANDIDATE_RULES_VERSION

    features = build_industry_features(bars_map=_make_bars_map(), industry_rows=INDUSTRY_ROWS,
                                       previous_date=P_DAY, macro_axes=None)
    result = select_candidates(features)
    assert result["candidate_rules_version"] == CANDIDATE_RULES_VERSION
    # 881016 强于基准应进 strong 头部；881306 弱于基准应进 weak 头部
    assert result["strong"][0]["industry_id"] == "tdx:881016.SH"
    assert result["weak"][0]["industry_id"] == "tdx:881306.SH"
    assert all(row["history_status"] == ELIGIBLE for row in result["strong"] + result["weak"])
    assert len(result["strong"]) <= 20 and len(result["weak"]) <= 20


# ------------------------------------------------------------ 附加：宏观引用约束 + 无反向证据声明
def test_macro_reason_requires_macro_key(engine: ForecastEngine) -> None:
    bad = json.loads(_valid_output())
    bad["industries"]["relative_strong"][0]["reason"] = "受益于宏观流动性改善"
    bad["industries"]["relative_strong"][0]["evidence_keys"] = ["IND_881016_SH_RELATIVE_5D"]
    engine.model_factory = lambda **kw: FakeModel([json.dumps(bad, ensure_ascii=False)])
    result = engine.run(prep=_prep(engine), bundle=_make_bundle(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "INVALID_OUTPUT"


def test_no_counter_claim_rejected_when_bundle_has_contradiction() -> None:
    bad = json.loads(_valid_output("WEAKER"))
    bad["market"]["counter_evidence_keys"] = ["NO_MATERIAL_COUNTER_EVIDENCE_IN_BUNDLE"]
    validator = ForecastValidator(
        evidence_catalog={"MKT_BENCH_RET_1D": "x"}, candidate_ids=set(),
        industry_names={}, market_features={"ret_1d": 0.01, "ret_5d": 0.02})
    report = validator.validate(bad)
    assert report["valid"] is False
    assert any("NO_MATERIAL" in issue for issue in report["issues"])

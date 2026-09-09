"""输入压缩与运行稳定性 V1：18 项契约测试（§22）。"""

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
    ELIGIBLE, build_industry_features, select_candidates,
)
from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.prompt import (
    SYSTEM_PROMPT, build_compact_context, build_user_payload, estimate_tokens,
    prompt_hash,
)
from src.macro_forecast.validate import ForecastValidator

SH = ZoneInfo("Asia/Shanghai")
P_DAY = "20260605"

MACRO = {
    "snapshot_as_of": "2026-06-05", "regime": "中性",
    "axes": {"growth": 51.5, "inflation": 46.8, "liquidity": 54.0, "credit": 26.6, "financial_conditions": 64.6},
    "events": [{"research_as_of": "2026-06-05", "event_type": "MACRO_AXIS_CHANGED", "axis_key": "growth",
                "from_value": "资料不足", "to_value": "中性", "text": "经济增长由「资料不足」变为「中性」"}],
    "pit_caveat": "测试",
}

INDUSTRY_ROWS = [
    {"code": f"8810{index:02d}.SH", "name_zh": f"测试行业{index}", "matrix_linked": True} for index in range(1, 21)
]


def _weekday_days(end_day: str, count: int) -> list[str]:
    anchor = date(int(end_day[:4]), int(end_day[4:6]), int(end_day[6:8]))
    days, cursor = [], anchor
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _bars(code: str, closes: list[float], end_day: str = P_DAY) -> list[dict]:
    days = _weekday_days(end_day, len(closes))
    return [{"code": code, "trade_date": day, "open": c * 0.99, "high": c * 1.01, "low": c * 0.98,
             "close": float(c), "volume": 1e6, "amount": 1e7} for day, c in zip(days, closes)]


def _bars_map() -> dict[str, list[dict]]:
    bars_map = {"000300.SH": _bars("000300.SH", [100 + i * 0.05 for i in range(260)])}
    for index, row in enumerate(INDUSTRY_ROWS):
        drift = 0.08 if index < 6 else (-0.04 if index >= 14 else 0.02)
        bars_map[row["code"]] = _bars(row["code"], [50 + i * drift for i in range(260)])
    return bars_map


def _features() -> list[dict]:
    return build_industry_features(bars_map=_bars_map(), industry_rows=INDUSTRY_ROWS,
                                   previous_date=P_DAY, macro_axes=MACRO["axes"])


@pytest.fixture()
def store(tmp_path: Path) -> ForecastStore:
    return ForecastStore(tmp_path / "c.db")


def _prep(engine: ForecastEngine):
    bundle = {
        "fingerprint": "mfi_test", "data_mode": "DOMESTIC_LIMITED",
        "target": {"target_date": "2026-06-08", "previous_date": "2026-06-05",
                   "cutoff_at": "2026-06-08T08:50:00+08:00", "calendar_status": "CALENDAR_UNVERIFIED_NEXT_SESSION"},
        "market": {"benchmark": {"status": "READY", "close": 112.5, "ret_1d": 0.002, "ret_5d": -0.011,
                                 "vol_20d": 0.0098, "amount_ratio_vs_20d_mean": 0.9}},
        "macro_facts": [{"series_id": "a_share_breadth_20d", "value": 0.61}],
        "macro_coverage": {"direction_input_ok": True},
        "gaps": ["OVERSEAS_EQUITY_INDEX_UNAVAILABLE"],
    }
    return engine.prepare(bundle=bundle, bars_map=_bars_map(), industry_rows=INDUSTRY_ROWS,
                          macro_context=MACRO, bundle_id="test-bundle")


# 1. 压缩后 prompt ≤8k tokens
def test_01_compressed_prompt_within_budget() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None  # prepare 不触网不落库
    prep = _prep(engine)
    assert prep["prompt_size"]["input_token_estimate"] <= 8000
    assert prep["prompt_size"]["total_chars"] <= 20000


# 2/3. 候选 ≥8 strong / ≥8 weak
def test_02_03_candidates_at_least_eight_per_side() -> None:
    result = select_candidates(_features())
    assert len(result["strong"]) >= 8
    assert len(result["weak"]) >= 8
    assert len(result["strong"]) <= 12 and len(result["weak"]) <= 12


# 4. evidence mapping 保留（别名值均落在完整目录内）
def test_04_alias_map_points_into_full_catalog() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    catalog = set(prep["evidence_catalog"])
    alias_values = set(prep["alias_map"].values())
    assert alias_values, "应有别名映射"
    assert alias_values <= catalog, f"越界别名: {sorted(alias_values - catalog)[:5]}"


# 5. 短键可逆（每个 prompt 短键唯一解析回一个完整键）
def test_05_short_key_reversibility() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    alias = prep["alias_map"]
    ev_keys = set(prep["context"]["ev"])
    assert ev_keys <= set(alias), "ev 中每个短键都有映射"
    assert len(alias) == len(set(alias.values())) or len(set(alias.values())) <= len(c := set(prep["evidence_catalog"]))
    # 双向唯一：不同短键不指向同一完整键（除允许的 None）
    targets = list(alias.values())
    assert len(targets) == len(set(targets)), "短键→完整键必须一一对应"


# 6. 未知键仍被拒（别名解析后校验）
def test_06_unknown_key_rejected_with_alias() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    validator = ForecastValidator(
        evidence_catalog=prep["evidence_catalog"], candidate_ids=set(),
        industry_names={}, alias_map=prep["alias_map"], candidate_alias=prep["candidate_alias"],
    )
    output = {
        "market": {"direction": "STRONGER", "summary": "ok",
                   "evidence_keys": ["M1", "ZZZ9"], "counter_evidence_keys": ["M2"],
                   "invalidation_conditions": ["若盘前出现新的重大政策冲击，本判断失效"]},
        "industries": {"relative_strong": [], "relative_weak": []},
        "abstain": False, "abstain_reason": None,
    }
    report = validator.validate(output)
    assert report["valid"] is False
    assert any("未知" in issue for issue in report["issues"])


# 7. prompt 无历史 K 线
def test_07_prompt_has_no_historical_bars() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    payload = prep["payload_text"]
    for token in ("volume", "amount", "open", "high", "low", "trade_date"):
        assert token not in payload, f"prompt 不应含 K 线字段 {token}"


# 8. prompt 无宏观原始序列史
def test_08_prompt_has_no_raw_macro_history() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    payload = prep["payload_text"]
    for token in ("fetched_at", "vintage_id", "observation_date", "release_date"):
        assert token not in payload


# 9. prompt 无未支持的海外数据
def test_09_prompt_has_no_overseas_values() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    ctx = prep["context"]
    assert ctx["mode"] == "DOMESTIC_LIMITED"
    for key in ("hsi", "spx", "dji", "ixic", "usdcny", "overnight"):
        flat = json.dumps(ctx, ensure_ascii=False).lower()
        assert key not in flat or key in ("gaps",), f"不应出现海外数值字段 {key}"


# 10. validator 契约不变（旧式完整键输出仍按原规则通过/拒绝）
def test_10_validator_contract_unchanged() -> None:
    catalog = {"MKT_BENCH_RET_1D": "x", "MKT_BENCH_RET_5D": "x", "MKT_BENCH_VOL_20D": "x"}
    validator = ForecastValidator(evidence_catalog=catalog, candidate_ids=set(), industry_names={})
    good = {
        "market": {"direction": "WEAKER", "summary": "ok",
                   "evidence_keys": ["MKT_BENCH_RET_1D", "MKT_BENCH_RET_5D"],
                   "counter_evidence_keys": ["MKT_BENCH_VOL_20D"],
                   "invalidation_conditions": ["若盘前出现新的重大政策冲击，本判断失效"]},
        "industries": {"relative_strong": [], "relative_weak": []},
        "abstain": False, "abstain_reason": None,
    }
    assert validator.validate(json.loads(json.dumps(good)))["valid"] is True
    bad = json.loads(json.dumps(good))
    bad["market"]["direction"] = "UP"
    assert validator.validate(bad)["valid"] is False


# 11. 无 Theme
def test_11_theme_not_implemented(store: ForecastStore) -> None:
    class FakeModel:
        def chat(self, messages, **kw):
            return SimpleNamespace(content=json.dumps({
                "market": {"direction": "RANGE_BOUND", "summary": "信号中性",
                           "evidence_keys": ["M1", "M4"], "counter_evidence_keys": ["M2"],
                           "invalidation_conditions": ["若盘前出现新的重大政策冲击，本判断失效"]},
                "industries": {"relative_strong": [], "relative_weak": []},
                "overall_cautions": [], "abstain": False, "abstain_reason": None,
            }, ensure_ascii=False))

    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel())
    result = engine.run(prep=_prep(engine), bundle=_prep_bare(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    payload = json.loads(result["row"]["structured_payload_json"])
    assert payload["theme_forecast"] is None


def _prep_bare() -> dict:
    return {
        "fingerprint": "mfi_test", "data_mode": "DOMESTIC_LIMITED",
        "target": {"target_date": "2026-06-08", "previous_date": "2026-06-05",
                   "cutoff_at": "2026-06-08T08:50:00+08:00", "calendar_status": "CALENDAR_UNVERIFIED_NEXT_SESSION"},
        "market": {"benchmark": {"status": "READY", "ret_1d": 0.002, "ret_5d": -0.011, "vol_20d": 0.0098}},
        "macro_coverage": {"direction_input_ok": True}, "gaps": [],
    }


# 12. 无价值线写入
def test_12_no_value_line_writes(tmp_path: Path) -> None:
    class FakeModel:
        def chat(self, messages, **kw):
            raise TimeoutError("timeout")

    store = ForecastStore(tmp_path / "c.db")
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel())
    engine.run(prep=_prep(engine), bundle=_prep_bare(), run_mode="SHADOW",
               model_config={"provider": "openai", "model": "m"})
    store.close()
    connection = sqlite3.connect(tmp_path / "c.db")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    assert tables - {"forecast_run_leases", "forecast_provider_health", "sqlite_sequence",
                     "forecast_semantic_validations", "macro_forecast_outcomes",
                     "macro_forecast_experiment_days"} == {"macro_market_forecasts"}


# 13. 无 Strategy Event 写入
def test_13_no_strategy_event_writes(tmp_path: Path) -> None:
    class FakeModel:
        def chat(self, messages, **kw):
            raise TimeoutError("timeout")

    store = ForecastStore(tmp_path / "c.db")
    with store._conn:
        store._conn.execute("CREATE TABLE IF NOT EXISTS macro_events (id TEXT)")
    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel())
    engine.run(prep=_prep(engine), bundle=_prep_bare(), run_mode="SHADOW",
               model_config={"provider": "openai", "model": "m"})
    assert store._conn.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0] == 0
    store.close()


# 14. 压缩确定性
def test_14_compression_deterministic() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    first, second = _prep(engine), _prep(engine)
    assert first["payload_text"] == second["payload_text"]


# 15. 同 bundle 同 prompt hash
def test_15_same_bundle_same_prompt_hash() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    assert _prep(engine)["prompt_hash"] == _prep(engine)["prompt_hash"]
    assert prompt_hash("abc") == prompt_hash("abc") and prompt_hash("abc") != prompt_hash("abd")


# 16. 候选缩减确定性
def test_16_candidate_reduction_deterministic() -> None:
    features = _features()
    first = select_candidates(features, limit=12)
    second = select_candidates(build_industry_features(bars_map=_bars_map(), industry_rows=INDUSTRY_ROWS,
                                                       previous_date=P_DAY, macro_axes=None), limit=12)
    assert [row["industry_id"] for row in first["strong"]] == [row["industry_id"] for row in second["strong"]]
    assert [row["industry_id"] for row in first["weak"]] == [row["industry_id"] for row in second["weak"]]
    # 嵌套缩减：limit 小的池是 limit 大池的前缀（同一确定性排序）
    big = select_candidates(features, limit=20)
    assert [row["industry_id"] for row in first["strong"]] == [row["industry_id"] for row in big["strong"][:12]]


# 17. 输出 schema 校验（短 id + 短键输出通过并被解析为完整行业身份）
def test_17_short_id_output_validates_and_resolves() -> None:
    engine = ForecastEngine(ForecastStore.__new__(ForecastStore))
    engine.store = None
    prep = _prep(engine)
    strong1 = prep["candidates"]["strong"][0]
    validator = ForecastValidator(
        evidence_catalog=prep["evidence_catalog"], candidate_ids=set(),
        industry_names={}, alias_map=prep["alias_map"], candidate_alias=prep["candidate_alias"],
    )
    output = {
        "market": {"direction": "STRONGER", "summary": "结构改善",
                   "evidence_keys": ["M2", "M4"], "counter_evidence_keys": ["M1"],
                   "invalidation_conditions": ["若盘前出现新的重大政策冲击，本判断失效"]},
        "industries": {"relative_strong": [{"id": "S1", "reason": "5日相对走强且趋势一致",
                                            "evidence_keys": ["S1_REL5"]}],
                       "relative_weak": []},
        "overall_cautions": [], "abstain": False, "abstain_reason": None,
    }
    report = validator.validate(output)
    assert report["valid"] is True, report["issues"]
    entry = report["industry_entries"][0]
    assert entry["industry_id"] == strong1["industry_id"]
    assert entry["display_name"] == strong1["name"]
    assert entry["evidence_keys"] == [f"IND_{strong1['industry_id'].replace('tdx:', '').replace('.', '_')}_RELATIVE_5D"]


# 18. 模型失败仍是失败（不转震荡）
def test_18_model_failed_remains_failed(store: ForecastStore) -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **kw):
            self.calls += 1
            raise TimeoutError("request timed out")

    engine = ForecastEngine(store, model_factory=lambda **kw: FakeModel())
    result = engine.run(prep=_prep(engine), bundle=_prep_bare(), run_mode="SHADOW",
                        model_config={"provider": "openai", "model": "m"})
    assert result["status"] == "MODEL_FAILED"
    assert result["row"]["market_direction"] is None
    assert "未生成" in result["row"]["narrative_md"]

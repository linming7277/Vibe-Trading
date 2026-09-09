"""收盘复盘与结果评价 V1 测试（§57 fixtures + §58 边界 + §59 PIT + 复用/不变量）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.macro_forecast.forecast_store import ForecastStore
from src.macro_forecast.outcome import (
    BENCHMARK_SYMBOL, MARKET_BAND, OUTCOME_FORMULA_VERSION, CONTRACT_FROZEN_AT,
    classify_market_return, evaluate_forecast_outcome, evaluate_industry_prediction,
    evaluate_market_prediction, persist_outcome,
)
from src.macro_forecast.performance import (
    get_macro_forecast_performance, market_baselines_for_outcome, sample_size_warning,
)
from src.macro_forecast.review_render import render_review

FORECAST_ID = "mmf_test_0001"
T_DAY, P_DAY = "20260908", "20260907"


def _setup_db(tmp_path: Path, *, forecast_status="SHADOW", direction="STRONGER",
              abstain=False, industries=None, benchmark_t_close=4560.0,
              include_t_bars=True, benchmark_p_close=4500.0) -> Path:
    """构造一条预测留档 + 输入包 + P/T K 线的临时库。"""
    industries = industries if industries is not None else [
        {"side": "RELATIVE_STRONG", "industry_id": "tdx:881106.SH", "display_name": "种植业",
         "reason": "r", "evidence_keys": ["IND_881106_SH_RELATIVE_5D"], "reason_basis": "PRICE_MOMENTUM"},
        {"side": "RELATIVE_WEAK", "industry_id": "tdx:881002.SH", "display_name": "煤炭开采",
         "reason": "r", "evidence_keys": ["IND_881002_SH_RELATIVE_5D"], "reason_basis": "PRICE_MOMENTUM"},
    ]
    db = tmp_path / "o.db"
    from src.macro_forecast.bars import ForecastBarStore
    from src.macro_forecast.bundle import ForecastBundleStore

    ForecastBundleStore(db).close()  # 初始化 forecast_input_bundles 表
    ForecastBarStore(db).close()     # 初始化 forecast_index_bars 表
    store = ForecastStore(db)
    payload = {
        "input": {"target_trade_date": "2026-09-08", "alias_map": {}, "evidence_catalog": {}},
        "model_output": {"abstain": abstain,
                         "market": {"direction": direction, "summary": "s",
                                    "evidence_keys": ["M1"], "counter_evidence_keys": ["M2"],
                                    "invalidation_conditions": ["cutoff后政策冲击"]}},
        "validation": {"valid": True, "industry_entries": industries},
        "theme_forecast": None,
    }
    with store._conn:
        store._conn.execute(
            """INSERT INTO macro_market_forecasts(
                id, target_trade_date, run_mode, status, input_bundle_id, input_fingerprint,
                forecast_formula_version, prompt_version, renderer_version, candidate_rules_version,
                model_provider, model_name, market_direction, structured_payload_json,
                logical_calls, actual_requests, retry_count, created_at, published_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (FORECAST_ID, "2026-09-08", "SHADOW", forecast_status, "b1", "mfi_testfingerprint01",
             "macro-market-industry-forecast-v1.1.0", "p", "r", "c", "openai", "m",
             None if abstain else direction, json.dumps(payload, ensure_ascii=False),
             1, 1, 0, "2026-09-07T05:00:00+00:00", None),  # published_at
        )
        store._conn.execute(
            """INSERT INTO forecast_input_bundles(
                bundle_id, target_date, fingerprint, data_mode, payload_json, created_at)
            VALUES(?,?,?,?,?,?)""",
            ("mfib_test", "2026-09-08", "mfi_testfingerprint01", "DOMESTIC_LIMITED",
             json.dumps({"target": {"target_date": "2026-09-08", "previous_date": "2026-09-07"},
                         "fingerprint": "mfi_testfingerprint01"}, ensure_ascii=False),
             "2026-09-07T04:00:00+00:00"),
        )
        bars = [(BENCHMARK_SYMBOL, P_DAY, benchmark_p_close), (BENCHMARK_SYMBOL, T_DAY, benchmark_t_close),
                ("881106.SH", P_DAY, 100.0), ("881002.SH", P_DAY, 100.0)]
        if include_t_bars:
            bars += [("881106.SH", T_DAY, 102.0), ("881002.SH", T_DAY, 99.0)]
        for code, day, close in bars:
            store._conn.execute(
                "INSERT INTO forecast_index_bars(code, trade_date, close, source, first_observed_date, pit_status, fetched_at) VALUES(?,?,?,?,?,?,?)",
                (code, day, close, "TongDaXin", "20260907", "FORWARD_OBSERVED", "t"),
            )
    store.close()
    return db


def _evaluate(db: Path):
    return evaluate_forecast_outcome(FORECAST_ID, research_db=db)


# ------------------------------------------------------------- §58 阈值边界
def test_threshold_boundaries_exact() -> None:
    assert classify_market_return(0.005) == "震荡"          # +0.500000% → 震荡
    assert classify_market_return(0.00500001) == "偏强"      # > +0.500000% → 偏强
    assert classify_market_return(-0.005) == "震荡"         # -0.500000% → 震荡
    assert classify_market_return(-0.00500001) == "偏弱"
    assert classify_market_return(0.0) == "震荡"
    assert MARKET_BAND == 0.005


# ------------------------------------------------------------- 1/2 偏强命中/未命中
def test_fixture_01_stronger_hit(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="STRONGER", benchmark_t_close=4560.0)  # +1.33% → 偏强
    result = _evaluate(db)
    assert result["status"] == "EVALUATED"
    assert result["outcome"]["actual_market_class"] == "偏强"
    assert result["outcome"]["market_evaluation"] == "HIT"


def test_fixture_02_stronger_miss(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="STRONGER", benchmark_t_close=4500.0)  # 0% → 震荡
    result = _evaluate(db)
    assert result["outcome"]["market_evaluation"] == "MISS"


# ------------------------------------------------------------- 3/4 震荡
def test_fixture_03_range_hit(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="RANGE_BOUND", benchmark_t_close=4510.0)  # +0.22%
    assert _evaluate(db)["outcome"]["market_evaluation"] == "HIT"


def test_fixture_04_range_miss(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="RANGE_BOUND", benchmark_t_close=4590.0)  # +2%
    assert _evaluate(db)["outcome"]["market_evaluation"] == "MISS"


# ------------------------------------------------------------- 5/6/7/8 状态
def test_fixture_05_weaker_hit(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="WEAKER", benchmark_t_close=4410.0)  # -2%
    assert _evaluate(db)["outcome"]["market_evaluation"] == "HIT"


def test_fixture_06_abstained(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, abstain=True, direction="ABSTAIN")
    result = _evaluate(db)
    assert result["outcome"]["market_evaluation"] == "ABSTAINED"


def test_fixture_07_model_failed_not_evaluable(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, forecast_status="MODEL_FAILED")
    result = _evaluate(db)
    assert result["status"] == "NOT_EVALUABLE"
    assert result["outcome"]["market_evaluation"] == "NOT_EVALUABLE"


def test_fixture_08_invalid_output_not_evaluable(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, forecast_status="INVALID_OUTPUT")
    assert _evaluate(db)["outcome"]["market_evaluation"] == "NOT_EVALUABLE"


# ------------------------------------------------------------- 9-13 行业
def test_fixture_09_10_strong_hit_miss(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)  # benchmark +1.33%；种植业 +2% → RR=+0.67% 强 HIT；煤炭 -1% → RR=-1.67% 弱 HIT
    result = _evaluate(db)
    results = {item["display_name"]: item for item in result["outcome"]["industry_results"]}
    assert results["种植业"]["evaluation"] == "HIT"
    assert results["煤炭开采"]["evaluation"] == "HIT"


def test_fixture_11_12_weak_hit_miss(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, benchmark_t_close=4410.0)  # benchmark -2%；种植 +2 → RR=+4 强 HIT；煤炭 -1 → RR=+1 → 弱 MISS
    result = _evaluate(db)
    results = {item["display_name"]: item for item in result["outcome"]["industry_results"]}
    assert results["煤炭开采"]["evaluation"] == "MISS"


def test_fixture_13_industry_down_but_relative_strong_hit(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, benchmark_t_close=4400.0)  # benchmark -2.22%
    store = ForecastStore(db)
    with store._conn:
        store._conn.execute(
            "UPDATE forecast_index_bars SET close=99.0 WHERE code='881106.SH' AND trade_date=?", (T_DAY,))
    store.close()
    result = _evaluate(db)  # 种植业 -1%，基准 -2.22% → RR=+1.22% → 强侧 HIT
    results = {item["display_name"]: item for item in result["outcome"]["industry_results"]}
    assert results["种植业"]["evaluation"] == "HIT"
    assert results["种植业"]["absolute_note"]


# ------------------------------------------------------------- 14/15 行情门
def test_fixture_14_t_bars_missing_pending(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, include_t_bars=False)
    result = _evaluate(db)
    assert result["status"] == "PENDING"
    assert result["outcome"]["market_evaluation"] == "PENDING"


def test_fixture_15_benchmark_missing_not_evaluable(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    store = ForecastStore(db)
    with store._conn:
        store._conn.execute(
            "DELETE FROM forecast_index_bars WHERE code=? AND trade_date=?", (BENCHMARK_SYMBOL, P_DAY))
    store.close()
    result = _evaluate(db)
    assert result["status"] == "NOT_EVALUABLE"
    assert "P 日收盘缺失" in result["outcome"].get("not_evaluable_reason", "")


# ------------------------------------------------------------- 16 复用不可覆盖
def test_fixture_16_duplicate_review_reuse(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    first = _evaluate(db)
    saved = persist_outcome(first, research_db=db)
    assert saved["status"] == "SAVED"
    again = _evaluate(db)
    assert again["status"] == "REUSED"
    # 直接再存也被 UNIQUE 拒绝
    store = ForecastStore(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with store._conn:
                store._conn.execute(
                    "INSERT INTO macro_forecast_outcomes(id, forecast_id, target_trade_date, run_mode, benchmark_symbol, market_evaluation, outcome_formula_version, created_at) VALUES('dup',?,?,?,?,?,?,?)",
                    (FORECAST_ID, "2026-09-08", "SHADOW", BENCHMARK_SYMBOL, "HIT", OUTCOME_FORMULA_VERSION, "t"),
                )
    finally:
        store.close()


# ------------------------------------------------------------- §59 PIT
def test_pit_p_price_from_p_and_source_locked(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    result = _evaluate(db)
    refs = result["outcome"]["actual_source_refs"]
    assert refs["benchmark"]["p"]["bar_ref"] == f"forecast_index_bars[{BENCHMARK_SYMBOL},{P_DAY}]"
    assert refs["input_fingerprint"] == "mfi_testfingerprint01"


def test_pit_no_later_bar_silently_changes_outcome(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    first = _evaluate(db)
    persist_outcome(first, research_db=db)
    store = ForecastStore(db)
    with store._conn:
        store._conn.execute(
            "UPDATE forecast_index_bars SET close=9999 WHERE code=? AND trade_date=?", (BENCHMARK_SYMBOL, T_DAY))
    store.close()
    again = _evaluate(db)
    assert again["status"] == "REUSED"  # 原评价不覆盖；修订评价属未来 DATA_REVISION_REVIEW


def test_pit_evaluate_before_t_close_pending_not_leaked(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, include_t_bars=False)
    result = _evaluate(db)
    assert result["status"] == "PENDING"
    assert result["outcome"].get("actual_close") is None
    assert result["outcome"].get("actual_return") is None
    assert result["outcome"]["market_evaluation"] == "PENDING"


# ------------------------------------------------------------- 等值/渲染/基准/性能
def test_industry_equal_case_is_miss() -> None:
    assert evaluate_industry_prediction("RELATIVE_STRONG", 0.0) == "MISS"
    assert evaluate_industry_prediction("RELATIVE_WEAK", 0.0) == "MISS"
    assert evaluate_industry_prediction("RELATIVE_STRONG", 5e-11) == "MISS"  # <1e-10 视为 0
    assert evaluate_industry_prediction("RELATIVE_STRONG", 1e-9) == "HIT"


def test_review_renderer_chinese_and_no_english_enums(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    result = _evaluate(db)
    store = ForecastStore(db)
    try:
        row = dict(store._conn.execute(
            "SELECT * FROM macro_market_forecasts WHERE id=?", (FORECAST_ID,)).fetchone())
    finally:
        store.close()
    narrative = render_review(result["outcome"], row)
    assert "命中" in narrative and "相对看强行业" in narrative and "实际涨跌" in narrative
    for token in ("HIT", "MISS", "PENDING", "NOT_EVALUABLE", "RANGE_BOUND"):
        assert token not in narrative
    assert "不解释对错原因" in narrative


def test_market_baselines_use_p_day_only(tmp_path: Path) -> None:
    db = _setup_db(tmp_path, direction="STRONGER", benchmark_t_close=4560.0,
                   benchmark_p_close=4500.0)
    result = _evaluate(db)
    outcome = result["outcome"]
    # P=09-07 收盘 4500，其前一日需要存在才有 PREVIOUS_DAY 方向
    store = ForecastStore(db)
    with store._conn:
        store._conn.execute(
            "INSERT INTO forecast_index_bars(code, trade_date, close, source, first_observed_date, pit_status, fetched_at) VALUES(?,?,?,?,?,?,?)",
            (BENCHMARK_SYMBOL, "20260904", 4520.0, "TongDaXin", "20260904", "FORWARD_OBSERVED", "t"),
        )
    store.close()
    baselines = market_baselines_for_outcome(outcome, db)
    assert baselines["BASELINE_ALWAYS_RANGE"] == "MISS"  # 实际偏强
    # P 日收益 = 4500/4520-1 ≈ -0.44% → 震荡 → 实际偏强 → MISS
    assert baselines["BASELINE_PREVIOUS_DAY_DIRECTION"] == "MISS"
    assert baselines["previous_day_direction"] == "震荡"


def test_performance_aggregation_and_reliability(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    persist_outcome(_evaluate(db), research_db=db)
    report = get_macro_forecast_performance(run_mode="SHADOW", research_db=db)
    metrics = report["prediction_metrics"]
    assert metrics["shadow_forecasts"] == 1
    assert metrics["market_evaluable"] == 1 and metrics["market_hits"] == 1
    assert metrics["market_hit_rate"] == 1.0
    assert metrics["industry_evaluable"] == 2 and metrics["industry_hits"] == 2
    reliability = report["reliability_metrics"]
    assert reliability["generation_success_rate"] == 1.0
    assert report["sample_size"] == 1
    assert "暂不具有统计意义" in report["sample_warning"] or "样本极少" in report["sample_warning"]
    assert report["forward_only"] is True


def test_sample_size_wording() -> None:
    assert "样本极少" in sample_size_warning(5)
    assert "初步观察" in sample_size_warning(30)
    assert "有一定参考价值" in sample_size_warning(70)
    assert "较稳定统计" in sample_size_warning(150)


def test_contract_frozen_before_target_day() -> None:
    # 契约冻结时间必须早于 T=2026-09-08 的任何信息
    assert CONTRACT_FROZEN_AT < "2026-09-08"


def test_no_value_line_or_strategy_event_writes(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    persist_outcome(_evaluate(db), research_db=db)
    get_macro_forecast_performance(run_mode="SHADOW", research_db=db)
    conn = sqlite3.connect(db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    forecast_tables = {"macro_market_forecasts", "forecast_index_bars", "forecast_input_bundles",
                       "forecast_run_leases", "forecast_provider_health", "sqlite_sequence",
                       "forecast_semantic_validations", "macro_forecast_outcomes",
                       "macro_forecast_experiment_days"}
    assert tables - forecast_tables == set()

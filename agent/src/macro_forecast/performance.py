"""历史表现动态聚合与基准比较（§33-§40/§45-§52，0 LLM / 0 网络）。

V1 不建多套物化汇总表：查询时从 forecast + outcome 动态计算。
基准（ALWAYS_RANGE / PREVIOUS_DAY_DIRECTION / MOMENTUM）只用预测时可见输入
（P 日及以前），不用 T 日信息。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.macro_forecast.outcome import (
    BENCHMARK_SYMBOL, MARKET_BAND, MARKET_RANGE, MARKET_STRONGER, MARKET_WEAKER,
    OUTCOME_FORMULA_VERSION, classify_market_return,
)

MARKET_BASELINE_VERSION = "market-baseline-v1.0.0"
INDUSTRY_MOMENTUM_BASELINE_VERSION = "industry-momentum-baseline-v1.0.0"


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def sample_size_warning(n: int) -> str:
    if n < 20:
        return f"样本极少（{n} 个），仅记录，不评价有效性"
    if n < 50:
        return f"样本较少（{n} 个），初步观察"
    if n < 100:
        return f"样本中等（{n} 个），有一定参考价值"
    return f"样本 {n} 个，开始可讨论较稳定统计（仍非显著性证明）"


def _p_day_return(db_path: Path, previous_date: str) -> float | None:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT trade_date, close FROM forecast_index_bars WHERE code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 2",
            (BENCHMARK_SYMBOL, previous_date),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 2:
        return None
    later, earlier = rows[0][1], rows[1][1]
    if not earlier:
        return None
    return later / earlier - 1


def market_baselines_for_outcome(outcome: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """§36：两条市场基准，仅用 P 日及以前信息。

    ALWAYS_RANGE：恒预测震荡。
    PREVIOUS_DAY_DIRECTION：P 日涨跌按 ±0.50% 阈值映射为次日方向。
    """
    previous_date = str(outcome.get("previous_trade_date") or "").replace("-", "")
    actual_class = outcome.get("actual_market_class")
    p_return = _p_day_return(db_path, previous_date) if previous_date else None

    always_range = "HIT" if actual_class == MARKET_RANGE else "MISS"
    if p_return is None or actual_class is None:
        prev_direction = None
        prev_result = "NOT_EVALUABLE"
    else:
        prev_direction = (MARKET_STRONGER if p_return > MARKET_BAND
                          else MARKET_WEAKER if p_return < -MARKET_BAND else MARKET_RANGE)
        prev_result = "HIT" if prev_direction == actual_class else "MISS"
    return {
        "market_baseline_version": MARKET_BASELINE_VERSION,
        "BASELINE_ALWAYS_RANGE": always_range,
        "BASELINE_PREVIOUS_DAY_DIRECTION": prev_result,
        "previous_day_return": round(p_return, 12) if p_return is not None else None,
        "previous_day_direction": prev_direction,
    }


def get_macro_forecast_performance(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    run_mode: str | None = None,
    research_db: Any = None,
) -> dict[str, Any]:
    """动态聚合预测表现 + 系统可用率 + 基准（0 LLM / 0 网络）。"""
    from src.macro_forecast.forecast_store import ForecastStore

    db_path = Path(research_db or (get_runtime_root() / "research.db"))
    store = ForecastStore(db_path)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        outcomes = store.list_outcomes(run_mode=run_mode, start_date=start_date, end_date=end_date)
        query = "SELECT status FROM macro_market_forecasts WHERE 1=1"
        params: list[Any] = []
        if run_mode:
            query += " AND run_mode=?"
            params.append(run_mode)
        if start_date:
            query += " AND target_trade_date>=?"
            params.append(start_date)
        if end_date:
            query += " AND target_trade_date<=?"
            params.append(end_date)
        statuses = [row["status"] for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()
        store.close()

    market_evaluable = [o for o in outcomes if o.get("market_evaluation") in ("HIT", "MISS")]
    market_hits = sum(1 for o in market_evaluable if o["market_evaluation"] == "HIT")
    market_abstained = sum(1 for o in outcomes if o.get("market_evaluation") == "ABSTAINED")
    industry_evaluable = industry_hits = 0
    strong_predictions = strong_hits = weak_predictions = weak_hits = 0
    for o in outcomes:
        for item in o.get("industry_results") or []:
            evaluation = item.get("evaluation")
            if evaluation not in ("HIT", "MISS"):
                continue
            industry_evaluable += 1
            industry_hits += 1 if evaluation == "HIT" else 0
            if item.get("side") == "RELATIVE_STRONG":
                strong_predictions += 1
                strong_hits += 1 if evaluation == "HIT" else 0
            else:
                weak_predictions += 1
                weak_hits += 1 if evaluation == "HIT" else 0

    total_forecasts = len(statuses)
    model_failures = sum(1 for s in statuses if s == "MODEL_FAILED")
    invalid_outputs = sum(1 for s in statuses if s == "INVALID_OUTPUT")
    valid_generated = sum(1 for s in statuses if s in ("SHADOW", "OFFICIAL", "DRAFT", "ABSTAINED"))
    eligible_days = market_abstained + len(market_evaluable)

    baselines = [market_baselines_for_outcome(o, db_path) for o in market_evaluable]
    baseline_summary = {
        "evaluable_days": len(baselines),
        "ALWAYS_RANGE_hits": sum(1 for b in baselines if b["BASELINE_ALWAYS_RANGE"] == "HIT"),
        "PREVIOUS_DAY_DIRECTION_hits": sum(1 for b in baselines if b["BASELINE_PREVIOUS_DAY_DIRECTION"] == "HIT"),
        "PREVIOUS_DAY_DIRECTION_not_evaluable": sum(1 for b in baselines if b["BASELINE_PREVIOUS_DAY_DIRECTION"] == "NOT_EVALUABLE"),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"start_date": start_date, "end_date": end_date, "run_mode": run_mode},
        "performance_class": "SHADOW_PERFORMANCE" if run_mode == "SHADOW" else (
            "OFFICIAL_PERFORMANCE" if run_mode == "OFFICIAL" else "ALL"),
        "prediction_metrics": {
            "total_forecasts": total_forecasts,
            "official_forecasts": sum(1 for s in statuses if s == "OFFICIAL"),
            "shadow_forecasts": sum(1 for s in statuses if s == "SHADOW"),
            "market_evaluable": len(market_evaluable),
            "market_hits": market_hits,
            "market_misses": len(market_evaluable) - market_hits,
            "market_hit_rate": _rate(market_hits, len(market_evaluable)),
            "market_abstained": market_abstained,
            "market_model_failed": model_failures,
            "market_invalid_output": invalid_outputs,
            "industry_predictions": strong_predictions + weak_predictions,
            "industry_evaluable": industry_evaluable,
            "industry_hits": industry_hits,
            "industry_misses": industry_evaluable - industry_hits,
            "industry_hit_rate": _rate(industry_hits, industry_evaluable),
            "strong_predictions": strong_predictions, "strong_hits": strong_hits,
            "strong_hit_rate": _rate(strong_hits, strong_predictions),
            "weak_predictions": weak_predictions, "weak_hits": weak_hits,
            "weak_hit_rate": _rate(weak_hits, weak_predictions),
        },
        "reliability_metrics": {
            "forecast_runs_expected": total_forecasts,
            "valid_forecasts_generated": valid_generated,
            "model_failures": model_failures,
            "invalid_outputs": invalid_outputs,
            "generation_success_rate": _rate(valid_generated, total_forecasts),
            "abstain_rate": _rate(market_abstained, eligible_days) if eligible_days else None,
        },
        "market_baselines": baseline_summary,
        "market_baseline_version": MARKET_BASELINE_VERSION,
        "sample_size": len(market_evaluable),
        "sample_warning": sample_size_warning(len(market_evaluable)),
        "outcome_formula_version": OUTCOME_FORMULA_VERSION,
        "forward_only": True,  # §41：只从 forward recorded forecast 起算，无历史回测混入
    }

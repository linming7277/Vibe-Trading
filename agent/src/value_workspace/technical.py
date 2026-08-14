"""Deterministic company-level technical features from the PIT market warehouse."""

from __future__ import annotations

import math
import statistics
from typing import Any


TECHNICAL_FORMULA_VERSION = "value-company-technical-v1.0.0"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _change(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return None
    return (values[-1] / values[-periods - 1] - 1) * 100


def _average(values: list[float], periods: int) -> float | None:
    return sum(values[-periods:]) / periods if len(values) >= periods else None


def calculate_technical(
    rows: list[dict[str, Any]], benchmark_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate auditable trend/risk metrics; no directional trade signal is fabricated."""
    normalized = []
    for raw in rows:
        close = _number(raw.get("close"))
        volume = _number(raw.get("volume"))
        if close is None or close <= 0 or volume is None or volume <= 0:
            continue
        normalized.append({
            "date": str(raw.get("data_as_of") or raw.get("trade_date") or raw.get("date") or "")[:10],
            "open": _number(raw.get("open")), "high": _number(raw.get("high")),
            "low": _number(raw.get("low")), "close": close, "volume": volume,
            "amount": _number(raw.get("amount")), "source": str(raw.get("source") or ""),
        })
    normalized.sort(key=lambda item: item["date"])
    if len(normalized) < 20:
        return {
            "status": "unavailable", "coverage": min(len(normalized) / 120, 1), "bar_count": len(normalized),
            "formula_version": TECHNICAL_FORMULA_VERSION, "data_as_of": normalized[-1]["date"] if normalized else None,
            "metrics": {}, "facts": [], "risks": [], "missing_fields": ["至少20个有效交易日日线"],
            "sources": sorted({item["source"] for item in normalized if item["source"]}),
        }

    closes = [item["close"] for item in normalized]
    volumes = [item["volume"] for item in normalized]
    amounts = [item["amount"] for item in normalized if item["amount"] is not None]
    ma20, ma60, ma120 = _average(closes, 20), _average(closes, 60), _average(closes, 120)
    latest = normalized[-1]
    return_20 = _change(closes, 20)

    benchmark_return_20 = None
    if benchmark_rows:
        benchmark = []
        for item in benchmark_rows:
            close = _number(item.get("close"))
            volume = _number(item.get("volume"))
            if close is not None and close > 0 and (volume is None or volume > 0):
                benchmark.append((str(item.get("data_as_of") or item.get("trade_date") or item.get("date") or "")[:10], close))
        benchmark.sort()
        benchmark_return_20 = _change([item[1] for item in benchmark], 20)

    daily_returns = [(closes[index] / closes[index - 1] - 1) for index in range(max(1, len(closes) - 20), len(closes))]
    volatility_20 = statistics.pstdev(daily_returns) * math.sqrt(252) * 100 if len(daily_returns) >= 10 else None
    drawdown_values = closes[-120:]
    peak, max_drawdown = drawdown_values[0], 0.0
    for value in drawdown_values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, (value / peak - 1) * 100)

    true_ranges = []
    for index in range(max(1, len(normalized) - 14), len(normalized)):
        current, previous_close = normalized[index], normalized[index - 1]["close"]
        if current["high"] is None or current["low"] is None:
            continue
        true_ranges.append(max(current["high"] - current["low"], abs(current["high"] - previous_close), abs(current["low"] - previous_close)))
    atr_14_pct = (sum(true_ranges) / len(true_ranges) / latest["close"] * 100) if true_ranges else None

    changes = [closes[index] - closes[index - 1] for index in range(max(1, len(closes) - 14), len(closes))]
    gains = sum(max(value, 0) for value in changes) / len(changes) if changes else 0
    losses = sum(max(-value, 0) for value in changes) / len(changes) if changes else 0
    rsi_14 = 100 if losses == 0 and gains > 0 else 0 if gains == losses == 0 else 100 - 100 / (1 + gains / losses) if losses else None

    volume_base = _average(volumes[:-1], 20) if len(volumes) >= 21 else None
    amount_base = _average(amounts[:-1], 20) if len(amounts) >= 21 else None
    volume_ratio = latest["volume"] / volume_base if volume_base and volume_base > 0 else None
    amount_ratio = latest["amount"] / amount_base if latest["amount"] is not None and amount_base and amount_base > 0 else None
    recent_lows = [item["low"] for item in normalized[-20:] if item["low"] is not None]
    recent_highs = [item["high"] for item in normalized[-20:] if item["high"] is not None]
    support_20 = min(recent_lows) if recent_lows else None
    resistance_20 = max(recent_highs) if recent_highs else None

    if ma120 is not None and latest["close"] > ma20 > ma60 > ma120:
        trend = "多头排列"
    elif ma120 is not None and latest["close"] < ma20 < ma60 < ma120:
        trend = "空头排列"
    elif ma60 is not None and latest["close"] >= ma20 >= ma60:
        trend = "中期偏强"
    elif ma60 is not None and latest["close"] <= ma20 <= ma60:
        trend = "中期偏弱"
    else:
        trend = "区间震荡"

    metrics = {
        "latest_close": latest["close"], "return_5d": _change(closes, 5), "return_20d": return_20,
        "return_60d": _change(closes, 60), "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "price_vs_ma20": (latest["close"] / ma20 - 1) * 100 if ma20 else None,
        "relative_strength_20d": return_20 - benchmark_return_20 if return_20 is not None and benchmark_return_20 is not None else None,
        "volume_ratio_20d": volume_ratio, "amount_ratio_20d": amount_ratio,
        "volatility_20d": volatility_20, "max_drawdown_120d": max_drawdown,
        "atr_14_pct": atr_14_pct, "rsi_14": rsi_14, "support_20d": support_20, "resistance_20d": resistance_20,
    }
    metrics = {key: _round(value) for key, value in metrics.items()}
    available = sum(value is not None for value in metrics.values())
    coverage = available / len(metrics)
    facts = [f"趋势状态：{trend}"]
    if metrics["relative_strength_20d"] is not None:
        facts.append(f"近20日相对中证全指强弱 {metrics['relative_strength_20d']:+.2f} 个百分点")
    if metrics["volume_ratio_20d"] is not None:
        facts.append(f"当日量比20日均量 {metrics['volume_ratio_20d']:.2f} 倍")
    risks = []
    if trend in {"空头排列", "中期偏弱"}:
        risks.append(f"技术趋势为{trend}")
    if metrics["max_drawdown_120d"] is not None and metrics["max_drawdown_120d"] <= -20:
        risks.append(f"近120日最大回撤 {metrics['max_drawdown_120d']:.2f}%")
    if metrics["volatility_20d"] is not None and metrics["volatility_20d"] >= 40:
        risks.append(f"近20日年化波动率 {metrics['volatility_20d']:.2f}%")
    missing = []
    if len(normalized) < 120:
        missing.append("120日完整历史")
    if metrics["relative_strength_20d"] is None:
        missing.append("中证全指相对强弱")
    return {
        "status": "ready" if coverage >= .8 and len(normalized) >= 120 else "partial",
        "coverage": round(coverage, 4), "bar_count": len(normalized), "formula_version": TECHNICAL_FORMULA_VERSION,
        "data_as_of": latest["date"], "trend": trend, "metrics": metrics, "facts": facts, "risks": risks,
        "missing_fields": missing, "sources": sorted({item["source"] for item in normalized if item["source"]}),
    }

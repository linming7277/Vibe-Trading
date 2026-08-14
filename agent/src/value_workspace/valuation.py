"""Deterministic, versioned company valuation snapshots for the value line."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Any


VALUATION_FORMULA_VERSION = "value-company-valuation-v1.0.0"
DEFAULT_ENTRY_MARGIN = 15.0
OVERVALUED_MARGIN = -20.0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _percentile(values: list[float], current: float | None) -> float | None:
    if current is None or len(values) < 20:
        return None
    ordered = sorted(values)
    return sum(value <= current for value in ordered) / len(ordered) * 100


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def calculate_valuation(
    *, universe_id: str, symbol: str, data_as_of: str, payload: dict[str, Any],
    peers: list[dict[str, Any]], history: list[dict[str, Any]],
) -> dict[str, Any]:
    quote = dict(payload.get("quote") or {})
    fundamental = dict(payload.get("fundamental") or {})
    financial = dict(payload.get("financial_latest") or {})
    price = _number(quote.get("price"))
    pe = _number(fundamental.get("pe_ttm"))
    pb = _number(fundamental.get("pb_mrq"))
    dividend = _number(fundamental.get("dividend_yield"))
    peer_pe = sorted(value for item in peers if (value := _number(item.get("pe_ttm"))) is not None and value > 0)
    peer_pb = sorted(value for item in peers if (value := _number(item.get("pb_mrq"))) is not None and value > 0)
    peer_pe_median = statistics.median(peer_pe) if peer_pe else None
    peer_pb_median = statistics.median(peer_pb) if peer_pb else None
    historical_pe = [value for item in history if (value := _number(item.get("pe_ttm"))) is not None and value > 0]
    historical_pb = [value for item in history if (value := _number(item.get("pb_mrq"))) is not None and value > 0]
    pe_percentile = _percentile(historical_pe, pe)
    pb_percentile = _percentile(historical_pb, pb)

    implied_values = []
    if price and pe and pe > 0 and peer_pe_median and peer_pe_median > 0:
        implied_values.append(("PE", price * peer_pe_median / pe))
    if price and pb and pb > 0 and peer_pb_median and peer_pb_median > 0:
        implied_values.append(("PB", price * peer_pb_median / pb))
    fair_values = [value for _, value in implied_values if value > 0]
    if len(fair_values) >= 2:
        fair_low, fair_high = min(fair_values), max(fair_values)
    elif fair_values:
        fair_low, fair_high = fair_values[0] * .90, fair_values[0] * 1.10
    else:
        fair_low = fair_high = None
    fair_mid = (fair_low + fair_high) / 2 if fair_low is not None and fair_high is not None else None
    safety_margin = (fair_mid / price - 1) * 100 if price and fair_mid else None
    watch_low = fair_low * .70 if fair_low else None
    watch_high = fair_low * .85 if fair_low else None

    dcf_inputs = {
        "operating_cash_flow": _number(financial.get("operating_cash_flow")),
        "capex": _number(financial.get("capex")),
        "total_shares": _number(financial.get("total_shares")),
        "forecast_cash_flows": None,
        "discount_rate": None,
        "terminal_growth": None,
    }
    dcf_available = all(dcf_inputs.get(key) is not None for key in ("operating_cash_flow", "capex", "total_shares", "forecast_cash_flows", "discount_rate", "terminal_growth"))
    dcf_coverage = sum(value is not None for value in dcf_inputs.values()) / len(dcf_inputs)
    dcf = {
        "status": "ready" if dcf_available else "unavailable",
        "input_coverage": round(dcf_coverage, 4), "inputs": dcf_inputs,
        "value_low": None, "value_high": None,
        "reason": "DCF 输入完整" if dcf_available else "缺少可验证的预测现金流、折现率或永续增长率，未猜测补值",
    }
    comparable = {
        "status": "ready" if implied_values else "unavailable",
        "peer_count_pe": len(peer_pe), "peer_count_pb": len(peer_pb),
        "methods": [{"method": method, "implied_value": _round(value)} for method, value in implied_values],
        "historical_observations_pe": len(historical_pe), "historical_observations_pb": len(historical_pb),
    }
    required = {
        "current_price": price, "pe_ttm": pe, "pb_mrq": pb, "dividend_yield": dividend,
        "peer_pe_median": peer_pe_median, "peer_pb_median": peer_pb_median,
        "pe_percentile": pe_percentile, "pb_percentile": pb_percentile,
        "fair_value": fair_mid, "safety_margin": safety_margin,
    }
    coverage = sum(value is not None for value in required.values()) / len(required)
    missing = [key for key, value in required.items() if value is None]
    status = "ready" if price is not None and fair_mid is not None and coverage >= .60 else "partial" if price is not None and (pe is not None or pb is not None) else "unavailable"
    source_input = {
        "price": price, "pe": pe, "pb": pb, "dividend": dividend,
        "peer_pe": peer_pe, "peer_pb": peer_pb, "history_pe": historical_pe,
        "history_pb": historical_pb, "financial": dcf_inputs,
        "formula_version": VALUATION_FORMULA_VERSION, "data_as_of": data_as_of,
    }
    return {
        "universe_id": universe_id, "symbol": symbol, "status": status,
        "data_as_of": data_as_of, "coverage": round(coverage, 4),
        "source_hash": _fingerprint(source_input),
        "current_price": _round(price), "pe_ttm": _round(pe), "pb_mrq": _round(pb),
        "dividend_yield": _round(dividend), "peer_pe_median": _round(peer_pe_median),
        "peer_pb_median": _round(peer_pb_median), "pe_percentile": _round(pe_percentile),
        "pb_percentile": _round(pb_percentile), "safety_margin": _round(safety_margin),
        "fair_value_low": _round(fair_low), "fair_value_high": _round(fair_high),
        "watch_price_low": _round(watch_low), "watch_price_high": _round(watch_high),
        "comparable": comparable, "dcf": dcf, "missing_fields": missing,
        "sources": ["TongDaXin quote/fundamental", "TongDaXin 881xxx peers", "Value valuation history"],
        "formula_version": VALUATION_FORMULA_VERSION,
    }


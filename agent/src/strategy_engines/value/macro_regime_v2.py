"""Five-axis China macro state machine used by Value V2."""

from __future__ import annotations

FORMULA_VERSION = "value-macro-v2.0.0"
AXES = ("growth", "inflation", "liquidity", "credit", "financial_conditions")


def _state(score: float | None, *, positive: str, negative: str) -> str:
    if score is None:
        return "数据不足"
    if score >= 60:
        return positive
    if score <= 40:
        return negative
    return "中性"


def calculate(features: dict[str, float | None]) -> dict[str, object]:
    available = {key: float(features[key]) for key in AXES if features.get(key) is not None}
    coverage = len(available) / len(AXES)
    score = round(sum(available.values()) / len(available), 4) if available else None
    states = {
        "growth": _state(features.get("growth"), positive="扩张", negative="恶化"),
        "inflation": _state(features.get("inflation"), positive="上升", negative="下降"),
        "liquidity": _state(features.get("liquidity"), positive="宽松", negative="收紧"),
        "credit": _state(features.get("credit"), positive="改善", negative="恶化"),
        "financial_conditions": _state(features.get("financial_conditions"), positive="改善", negative="收紧"),
    }
    growth, inflation, liquidity = features.get("growth"), features.get("inflation"), features.get("liquidity")
    if coverage < .60 or growth is None or liquidity is None:
        regime, status = "数据不足", "insufficient_data"
    elif growth >= 60 and liquidity >= 60 and (inflation is None or inflation < 60):
        regime, status = "复苏宽松", "ready"
    elif growth >= 60 and (inflation is None or inflation < 70):
        regime, status = "扩张", "ready"
    elif growth <= 40 and inflation is not None and inflation >= 60:
        regime, status = "滞胀", "ready"
    elif growth <= 40 and liquidity <= 40:
        regime, status = "收缩", "ready"
    else:
        regime, status = "中性", "ready"
    return {
        "formula_version": FORMULA_VERSION, "regime": regime, "score": score,
        "coverage": round(coverage, 4), "status": status, "states": states,
    }

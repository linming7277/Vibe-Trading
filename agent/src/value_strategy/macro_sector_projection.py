"""Read-only macro environment projection for /value, /macro, and Daily Brief.

Environment-only per macro-line V1 contract: Regime + five axes + missing axes.
No policy sector lists, no L3 industry rankings, no trading signals.
"""

from __future__ import annotations

from typing import Any

FORMULA_VERSION = "macro-sector-projection-v1.0.0"

_AXIS_LABELS = {
    "growth": "经济增长",
    "inflation": "通胀",
    "liquidity": "流动性",
    "credit": "信用",
    "financial_conditions": "金融条件",
}

_REGIME_LABELS = {
    "复苏宽松": "经济在改善、资金面偏松，对研究偏友好",
    "扩张": "经济在扩张，可正常推进研究",
    "滞胀": "经济走弱且通胀偏高，需要更谨慎",
    "收缩": "经济和资金面都偏紧，先保证研究质量",
    "中性": "经济和资金面没有明显方向",
    "数据不足": "宏观资料还不够完整，先不据此调整研究节奏",
}


def _axis_state(score: Any) -> str:
    if score is None:
        return "资料不足"
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "资料不足"
    if value >= 60:
        return "偏暖"
    if value <= 40:
        return "偏冷"
    return "中性"


def get_macro_sector_projection(as_of: str | None = None) -> dict[str, Any]:
    """Environment-only projection: Regime + five axes. Phase 0 slimmed."""
    from src.strategy_engines.value_line import ValueLineService

    engine = ValueLineService()
    # P1 fix: the cached snapshot can be days stale; compute fresh from the
    # local macro_series table (zero network) so series that landed after the
    # last snapshot's as_of are no longer invisible.
    from src.strategy_engines.macro_data import MacroDataService
    from src.strategy_engines.value_data_store import ValueDataStore

    if as_of is None:
        from datetime import date as _date
        as_of = _date.today().isoformat()
    try:
        service = MacroDataService(store=ValueDataStore())
        macro = service.build_snapshot(as_of)
    except Exception:  # noqa: BLE001 — fall back to cached snapshot
        macro = engine.macro(as_of=as_of)
    if macro is None:
        return {
            "as_of": as_of,
            "available": False,
            "reason": "尚无宏观快照",
            "formula_version": FORMULA_VERSION,
        }

    regime = str(macro.get("regime") or "数据不足")
    states = dict(macro.get("states") or {})
    axes = dict(macro.get("axes") or {})

    axis_rows = []
    for key, label in _AXIS_LABELS.items():
        axis_rows.append({
            "key": key,
            "label": label,
            "score": axes.get(key),
            "state": _axis_state(axes.get(key)),
            "direction": str(states.get(key) or "资料不足"),
        })

    return {
        "as_of": str(macro.get("as_of") or as_of or ""),
        "available": True,
        "macro": {
            "regime": regime,
            "regime_label": _REGIME_LABELS.get(regime, regime),
            "score": macro.get("score"),
            "coverage": macro.get("coverage"),
            "axes": axis_rows,
            "status": macro.get("status"),
            "missing_series": list(macro.get("missing_series") or []),
        },
        "data_quality": _macro_data_quality(as_of),
        "formula_version": FORMULA_VERSION,
    }


def _macro_data_quality(as_of: str) -> dict[str, Any]:
    try:
        from src.macro_line.freshness import check_macro_source_freshness

        return check_macro_source_freshness(as_of=as_of)
    except Exception:
        return {"status": "UNKNOWN", "reason": "宏观资料预检不可用"}


_service_cache: dict[str, Any] | None = None


def get_macro_sector_projection_service() -> Any:
    global _service_cache
    if _service_cache is None:
        _service_cache = {"get": get_macro_sector_projection}
    return _service_cache

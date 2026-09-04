"""Macro refresh orchestration: run after EOD success, diff, record events.

Fail-soft by design: macro refresh failure never affects EOD status.
"""

from __future__ import annotations

import logging
from typing import Any


from .events import MacroEventStore, event_to_chinese

logger = logging.getLogger(__name__)

MACRO_LINE_FORMULA_VERSION = "macro-line-refresh-v1.0.0"

# Optional network ingest when key credit/FX series are absent (M3-02, fail-soft).
_OPTIONAL_INGEST_SERIES = frozenset({"social_financing_increment", "usd_cny"})


def _maybe_ingest_missing_series(as_of: str) -> dict[str, Any]:
    """Try one network refresh when optional series are missing locally."""
    from .freshness import check_macro_source_freshness

    preflight = check_macro_source_freshness(as_of=as_of)
    missing = set(preflight.get("missing_series") or []) & _OPTIONAL_INGEST_SERIES
    if not missing:
        return {"ingested": False, "reason": "optional series present"}
    try:
        from src.strategy_engines.macro_data import MacroDataService
        from src.strategy_engines.value_data_store import ValueDataStore

        result = MacroDataService(store=ValueDataStore()).refresh(as_of)
        return {
            "ingested": True,
            "reason": "network refresh attempted",
            "missing_before": sorted(missing),
            "status": result.get("status"),
            "series_rows": result.get("series_rows"),
        }
    except Exception as exc:
        logger.warning("macro series ingest failed (fail-soft): %s", exc, exc_info=True)
        return {"ingested": False, "reason": f"{type(exc).__name__}: {exc}", "missing_before": sorted(missing)}


def refresh_macro_line(as_of: str) -> dict[str, Any]:
    """Build a fresh snapshot, diff vs previous, record events. One call.

    This is the post-EOD hook (plan §6).  By default reads local macro_series;
    if optional credit/FX series are missing, attempts one fail-soft network
    ingest before building the snapshot.
    """
    store = MacroEventStore()
    try:
        from .freshness import check_macro_source_freshness
        from src.strategy_engines.macro_data import MacroDataService
        from src.strategy_engines.value_data_store import ValueDataStore

        ingest = _maybe_ingest_missing_series(as_of)
        data_service = MacroDataService(store=ValueDataStore())
        snapshot = data_service.build_snapshot(as_of)
        if not snapshot:
            return {"status": "NO_DATA", "as_of": as_of, "events": [], "ingest": ingest}

        # Persist the snapshot so /macro and future diffs see it.
        value_store = ValueDataStore()
        try:
            value_store.save_macro_snapshot(snapshot)
        finally:
            value_store.close()

        events = store.diff_and_record(snapshot)
        freshness = check_macro_source_freshness(as_of=as_of)
        return {
            "status": "OK",
            "as_of": as_of,
            "regime": snapshot.get("regime"),
            "coverage": snapshot.get("coverage"),
            "series_count": snapshot.get("series_count"),
            "series_total": snapshot.get("series_total"),
            "events": [event_to_chinese(e) for e in events],
            "event_count": len(events),
            "ingest": ingest,
            "freshness": freshness,
        }
    except Exception as exc:
        logger.warning("macro line refresh failed (fail-soft): %s", exc, exc_info=True)
        return {"status": "FAILED", "as_of": as_of, "error": f"{type(exc).__name__}: {exc}", "events": []}
    finally:
        store.close()


def get_macro_line_summary(as_of: str | None = None) -> dict[str, Any]:
    """Read the latest snapshot + undigested events for Daily Brief / Hermes.

    Zero network, zero LLM. If no snapshot exists, returns available=False.
    """
    store = MacroEventStore()
    try:
        from src.strategy_engines.macro_data import MacroDataService
        from src.strategy_engines.value_data_store import ValueDataStore

        if as_of is None:
            from datetime import date
            as_of = date.today().isoformat()

        data_service = MacroDataService(store=ValueDataStore())
        snapshot = data_service.build_snapshot(as_of)
        if not snapshot:
            return {"available": False, "text": "宏观环境资料暂不可用。", "changed": False, "changes": []}

        events = store.events_for_summary(as_of)
        change_texts = [event_to_chinese(e) for e in events]
        regime = str(snapshot.get("regime") or "资料不足")
        _AXIS_CN = {"growth": "经济增长", "inflation": "通胀", "liquidity": "流动性", "credit": "信用", "financial_conditions": "金融条件"}
        states = dict(snapshot.get("states") or {})
        axes_summary = "；".join(
            f"{_AXIS_CN.get(key, key)} {value}" for key, value in states.items() if str(value) != "数据不足"
        ) or "各轴资料不足"

        if change_texts:
            text = f"当前宏观环境：{regime}。{axes_summary}。{'; '.join(change_texts)}。"
        else:
            text = f"当前宏观环境：{regime}。{axes_summary}。环境无变化。"

        return {
            "available": True,
            "text": text,
            "regime": regime,
            "regime_label": _regime_label(regime),
            "changed": bool(change_texts),
            "changes": change_texts,
            "as_of": str(snapshot.get("as_of") or as_of),
        }
    except Exception:
        return {"available": False, "text": "宏观环境资料暂不可用。", "changed": False, "changes": []}
    finally:
        store.close()


def _regime_label(regime: str) -> str:
    return {
        "复苏宽松": "经济在改善、资金面偏松，对研究偏友好",
        "扩张": "经济在扩张，可正常推进研究",
        "滞胀": "经济走弱且通胀偏高，需要更谨慎",
        "收缩": "经济和资金面都偏紧，先保证研究质量",
        "中性": "经济和资金面没有明显方向",
        "数据不足": "宏观资料还不够完整，先不据此调整研究节奏",
    }.get(regime, regime)

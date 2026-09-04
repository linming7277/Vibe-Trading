"""Macro series preflight — local DB only (SPEC M3-04)."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.strategy_engines.macro_data import SERIES_SPECS
from src.strategy_engines.value.macro_regime_v2 import AXES
from src.strategy_engines.value_data_store import ValueDataStore

_EXPECTED_SERIES = sorted({item[3] for item in SERIES_SPECS} | {"csi_all_share_risk_appetite", "a_share_breadth_20d"})
_SERIES_LABELS = {item[3]: item[0] for item in SERIES_SPECS} | {
    "csi_all_share_risk_appetite": "全A风险偏好",
    "a_share_breadth_20d": "全市场20日上涨占比",
    "social_financing_increment": "社融增量",
    "usd_cny": "美元/人民币",
}


def check_macro_source_freshness(*, as_of: str | None = None) -> dict[str, Any]:
    """Preflight macro_series coverage for the given as-of date.

    Returns READY / PARTIAL / STALE / UNKNOWN.  Never raises; safe for UI
    and EOD observability.  Does not trigger network fetches.
    """
    if as_of is None:
        as_of = date.today().isoformat()
    try:
        store = ValueDataStore()
        try:
            rows = store.macro_series_as_of(as_of)
            snapshot = store.get_macro_snapshot(as_of)
        finally:
            store.close()
    except Exception as exc:
        return {
            "status": "UNKNOWN",
            "as_of": as_of,
            "reason": f"{type(exc).__name__}: {exc}",
            "series_count": 0,
            "series_total": len(_EXPECTED_SERIES),
            "missing_series": list(_EXPECTED_SERIES),
            "axes_available": 0,
            "axes_total": len(AXES),
        }

    present = {
        str(row.get("series_id") or "")
        for row in rows
        if row.get("series_id") and row.get("value") is not None
    }
    missing = sorted(set(_EXPECTED_SERIES) - present)
    series_count = len(present & set(_EXPECTED_SERIES))
    series_total = len(_EXPECTED_SERIES)
    coverage = series_count / series_total if series_total else 0.0

    axes_available = 0
    if snapshot:
        axes = dict(snapshot.get("axes") or {})
        axes_available = sum(1 for axis in AXES if axes.get(axis) is not None)

    snapshot_as_of = str((snapshot or {}).get("as_of") or "")[:10]
    snapshot_status = str((snapshot or {}).get("status") or "")

    if not rows:
        status, reason = "UNKNOWN", "macro_series 尚无本地数据"
    elif coverage >= 0.95 and axes_available >= 3:
        status, reason = "READY", "宏观序列覆盖充分"
    elif coverage >= 0.75 and axes_available >= 3:
        status, reason = "PARTIAL", f"缺 {len(missing)} 条序列：{', '.join(_SERIES_LABELS.get(s, s) for s in missing[:4])}"
    elif axes_available >= 3:
        status, reason = "PARTIAL", f"五轴可算但仅覆盖 {series_count}/{series_total} 条序列"
    else:
        status, reason = "STALE", "可用轴不足三个，整体判断应保守"

    if snapshot_status == "insufficient_data" and status == "READY":
        status, reason = "PARTIAL", "快照状态为资料不足"

    return {
        "status": status,
        "as_of": as_of,
        "reason": reason,
        "series_count": series_count,
        "series_total": series_total,
        "series_coverage": round(coverage, 4),
        "missing_series": missing,
        "missing_series_labels": [_SERIES_LABELS.get(item, item) for item in missing],
        "axes_available": axes_available,
        "axes_total": len(AXES),
        "snapshot_as_of": snapshot_as_of or None,
        "snapshot_status": snapshot_status or None,
    }

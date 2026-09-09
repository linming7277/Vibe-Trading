"""Cross-market context projection for the forecast input bundle (M1-B §十六/§十七)。

只做简单确定性派生（level / 1d / 5d / 20d / 曲线差 / MoM / YoY），
不做综合分、风险分或任何模型。全部输入经 §6.2 可见性筛选：
按北京时间 cutoff 只取已发布且已抓取的观测，绝不倒灌。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.macro_forecast.contracts import visibility_decision
from src.macro_data.freshness import evaluate_freshness
from src.macro_data.identity import (
    ALL_IDENTITIES,
    COPPER_WORLD_MONTHLY,
    USD_CNY_OFFICIAL_MID,
    US_TREASURY_10Y_FRED,
    US_TREASURY_2Y_FRED,
    WTI_SPOT,
    TREASURY_US10Y,
    TREASURY_US2Y,
)

_STATUS_OK = "OK"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_UNAVAILABLE = "UNAVAILABLE"

# 主备映射（§十一）：Treasury 官方为主，FRED 为备，不做平均。
_FALLBACK_FOR: dict[str, str] = {
    TREASURY_US2Y.series_id: US_TREASURY_2Y_FRED.series_id,
    TREASURY_US10Y.series_id: US_TREASURY_10Y_FRED.series_id,
}


def visible_window(rows: list[dict[str, Any]], *, cutoff: datetime) -> list[dict[str, Any]]:
    """一个序列的 PIT 可见窗口（按 observation_date 升序，剔除空值行）。

    发布精度来自 metadata：DATETIME_SCHEDULED → 精确发布时刻（STRICT），
    其余 → 日期级保守边界（发布日末）。抓取时间晚于 cutoff 的行
    （含历史回补行在历史重放中）一律不可见——禁止倒灌。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        metadata = dict(row.get("metadata") or {})
        scheduled = str(metadata.get("published_at_precision") or "") == "DATETIME_SCHEDULED"
        visibility = visibility_decision(
            cutoff=cutoff,
            release_time_precision="DATETIME" if scheduled else "DATE",
            source_released_at=metadata.get("published_at") if scheduled else row.get("release_date"),
            first_seen_at=row.get("fetched_at"),
            content_hash=row.get("vintage_id"),
        )
        if not visibility.usable:
            continue
        out.append({
            "observation_date": str(row["observation_date"])[:10],
            "value": float(value),
            "source": str(row.get("source") or ""),
            "pit_status": visibility.pit_status,
            "capture_pit_status": str(metadata.get("pit_status") or ""),
            "fetched_at": str(row.get("fetched_at") or ""),
        })
    out.sort(key=lambda item: item["observation_date"])
    return out


def _diff_changes(window: list[dict[str, Any]], lags: tuple[int, ...]) -> dict[str, float | None]:
    """水平量差分（利率曲线：单位 pp，绝对差而非百分比）。"""
    latest = window[-1]
    changes: dict[str, float | None] = {}
    for lag in lags:
        index = len(window) - 1 - lag
        changes[f"chg_{lag}d"] = (
            round(latest["value"] - window[index]["value"], 6) if index >= 0 else None
        )
    return changes


def _pct_changes(window: list[dict[str, Any]], lags: tuple[int, ...]) -> dict[str, float | None]:
    """百分比变化（汇率/油价；基数为非正或缺失时 None，不做兜底填充）。"""
    latest = window[-1]
    changes: dict[str, float | None] = {}
    for lag in lags:
        index = len(window) - 1 - lag
        if index < 0 or window[index]["value"] <= 0:
            changes[f"chg_{lag}d_pct"] = None
        else:
            changes[f"chg_{lag}d_pct"] = round(
                latest["value"] / window[index]["value"] - 1.0, 6,
            )
    return changes


def _monthly_changes(window: list[dict[str, Any]]) -> dict[str, float | None]:
    latest = window[-1]
    def _pct(base: dict[str, Any] | None) -> float | None:
        if base is None or base["value"] <= 0:
            return None
        return round(latest["value"] / base["value"] - 1.0, 6)
    return {"mom_pct": _pct(window[-2] if len(window) >= 2 else None),
            "yoy_pct": _pct(window[-13] if len(window) >= 13 else None)}


def _entry(window: list[dict[str, Any]], identity_key: str, *, now: datetime,
           changes: dict[str, float | None], actual_source_used: str | None = None,
           fallback_used: bool = False, fallback_reason: str | None = None) -> dict[str, Any]:
    identity = ALL_IDENTITIES[identity_key]
    latest = window[-1]
    return {
        "value": latest["value"],
        "observed_at": latest["observation_date"],
        "unit": identity.unit,
        "frequency": identity.frequency,
        "source_id": identity.source_id,
        "source_tier": identity.source_tier,
        "actual_source_used": actual_source_used or identity.source_id,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "pit_status": latest["pit_status"],
        "freshness": evaluate_freshness(
            identity_key, date.fromisoformat(latest["observation_date"]),
            now_date=now.date(),
        ),
        "changes": changes,
    }


def _missing_entry(identity_key: str) -> dict[str, Any]:
    identity = ALL_IDENTITIES[identity_key]
    return {
        "value": None, "observed_at": None, "unit": identity.unit,
        "frequency": identity.frequency, "source_id": identity.source_id,
        "source_tier": identity.source_tier,
        "actual_source_used": identity.source_id, "fallback_used": False,
        "fallback_reason": None, "pit_status": None, "freshness": "MISSING",
        "changes": {},
    }


def _resolved_window(series_rows: dict[str, list[dict[str, Any]]],
                     primary_key: str, *, cutoff: datetime,
                     now: datetime) -> tuple[list[dict[str, Any]], str, bool, str | None]:
    """主备解析：主源可见且新鲜则用主源；否则允许 FRED fallback。"""
    from src.macro_data.freshness import evaluate_freshness

    primary = visible_window(series_rows.get(primary_key) or [], cutoff=cutoff)
    if primary:
        latest_day = date.fromisoformat(primary[-1]["observation_date"])
        if evaluate_freshness(primary_key, latest_day, now_date=now.date()) == "READY":
            return primary, primary_key, False, None
        fallback_reason = "PRIMARY_STALE"
    else:
        fallback_reason = "PRIMARY_MISSING"
    fallback_key = _FALLBACK_FOR.get(primary_key)
    if fallback_key:
        fallback = visible_window(series_rows.get(fallback_key) or [], cutoff=cutoff)
        if fallback:
            return fallback, fallback_key, True, fallback_reason
    return primary, primary_key, False, (fallback_reason if primary else None)


def build_cross_market_context(series_rows: list[dict[str, Any]], *, cutoff: datetime,
                               now: datetime | None = None) -> dict[str, Any]:
    """bundle 新增字段 cross_market_context（additive，§十六）。"""
    now = now or datetime.now().astimezone()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in series_rows:
        series_id = str(row.get("series_id") or "")
        if series_id in ALL_IDENTITIES:
            grouped.setdefault(series_id, []).append(row)

    us2y_window, us2y_source, us2y_fb, us2y_reason = _resolved_window(
        grouped, TREASURY_US2Y.series_id, cutoff=cutoff, now=now)
    us10y_window, us10y_source, us10y_fb, us10y_reason = _resolved_window(
        grouped, TREASURY_US10Y.series_id, cutoff=cutoff, now=now)

    treasury_2y = (
        _entry(us2y_window, us2y_source, now=now, changes=_diff_changes(us2y_window, (1, 5)),
               actual_source_used=ALL_IDENTITIES[us2y_source].source_id,
               fallback_used=us2y_fb, fallback_reason=us2y_reason)
        if us2y_window else _missing_entry(TREASURY_US2Y.series_id)
    )
    treasury_10y = (
        _entry(us10y_window, us10y_source, now=now, changes=_diff_changes(us10y_window, (1, 5)),
               actual_source_used=ALL_IDENTITIES[us10y_source].source_id,
               fallback_used=us10y_fb, fallback_reason=us10y_reason)
        if us10y_window else _missing_entry(TREASURY_US10Y.series_id)
    )

    # 曲线 10Y-2Y：两条腿各自解析主备后取最新共同观测日。
    by_date_2y = {item["observation_date"]: item["value"] for item in us2y_window}
    by_date_10y = {item["observation_date"]: item["value"] for item in us10y_window}
    common = sorted(set(by_date_2y) & set(by_date_10y))
    if common:
        history = [by_date_10y[d] - by_date_2y[d] for d in common]
        curve_day = common[-1]
        curve_entry = {
            "value": round(history[-1], 6),
            "observed_at": curve_day,
            "unit": "percentage_points",
            "frequency": "DAILY",
            "source_id": "derived",
            "source_tier": "DERIVED",
            "actual_source_used": f"{ALL_IDENTITIES[us10y_source].source_id}+{ALL_IDENTITIES[us2y_source].source_id}",
            "fallback_used": us2y_fb or us10y_fb,
            "fallback_reason": us2y_reason or us10y_reason,
            "pit_status": "DERIVED_FROM_VISIBLE",
            "freshness": evaluate_freshness(
                TREASURY_US2Y.series_id, date.fromisoformat(curve_day), now_date=now.date(),
            ),
            "changes": {
                "chg_1d": round(history[-1] - history[-2], 6) if len(history) >= 2 else None,
                "chg_5d": round(history[-1] - history[-6], 6) if len(history) >= 6 else None,
            },
        }
    else:
        curve_entry = _missing_entry(TREASURY_US2Y.series_id)
        curve_entry.update({"unit": "percentage_points", "source_id": "derived",
                            "source_tier": "DERIVED"})

    mid_window = visible_window(grouped.get(USD_CNY_OFFICIAL_MID.series_id) or [], cutoff=cutoff)
    usd_cny = (
        _entry(mid_window, USD_CNY_OFFICIAL_MID.series_id, now=now,
               changes=_pct_changes(mid_window, (1, 5)))
        if mid_window else _missing_entry(USD_CNY_OFFICIAL_MID.series_id)
    )
    wti_window = visible_window(grouped.get(WTI_SPOT.series_id) or [], cutoff=cutoff)
    wti = (
        _entry(wti_window, WTI_SPOT.series_id, now=now,
               changes=_pct_changes(wti_window, (1, 5, 20)))
        if wti_window else _missing_entry(WTI_SPOT.series_id)
    )
    copper_window = visible_window(grouped.get(COPPER_WORLD_MONTHLY.series_id) or [], cutoff=cutoff)
    copper = (
        _entry(copper_window, COPPER_WORLD_MONTHLY.series_id, now=now,
               changes=_monthly_changes(copper_window))
        if copper_window else _missing_entry(COPPER_WORLD_MONTHLY.series_id)
    )

    entries = [treasury_2y, treasury_10y, curve_entry, usd_cny, wti, copper]
    ready = sum(1 for item in entries if item["freshness"] == "READY")
    if ready == 0:
        status = _STATUS_UNAVAILABLE
    elif ready == len(entries):
        status = _STATUS_OK
    else:
        status = _STATUS_PARTIAL
    return {
        "status": status,
        "entries_ready": ready,
        "entries_total": len(entries),
        "us_rates": {
            "treasury_2y": treasury_2y,
            "treasury_10y": treasury_10y,
            "curve_10y_2y": curve_entry,
        },
        "fx": {"usd_cny_official_mid": usd_cny},
        "commodities": {"wti_spot": wti, "copper_monthly": copper},
    }


def fx_official_mid_ready(context: dict[str, Any] | None) -> bool:
    """GAP_FX 消解判定：官方中间价可见且新鲜。"""
    if not context:
        return False
    fx = ((context.get("fx") or {}).get("usd_cny_official_mid") or {})
    return fx.get("freshness") == "READY" and fx.get("value") is not None

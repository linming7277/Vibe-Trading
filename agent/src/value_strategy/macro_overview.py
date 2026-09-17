"""Boss-facing macro overview: environment, key series with trends, forecast.

Read-only composition of existing local stores — no new data sources:

* environment projection (the same one the /macro page renders), plus the
  previous snapshot's axis scores so the page can show what *changed*;
* cross-market key series and domestic macro readings with latest value,
  change versus the previous observation, and a downsampled sparkline;
* the latest SHADOW market forecast (direction + boss-rendered narrative).

Series staleness is surfaced directly (READY / STALE / MISSING) so data lag is
visible on the page instead of ageing silently behind READY gates.  Labels come
from the frozen forecast registry where available; cross-market series carry
page-level Chinese labels.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

# 跨市场序列卡（展示顺序即页面顺序）；只用主序列，FRED 交叉核对通道不上页。
OVERVIEW_SERIES: tuple[str, ...] = (
    "us_treasury_10y",
    "us_treasury_2y",
    "wti_spot",
    "usd_cny_official_mid",
    "shibor_overnight",
    "shibor_3m",
    "copper_world_monthly",
)

# 国内宏观读数（月度为主 + 市场内部两列）。
DOMESTIC_SERIES: tuple[str, ...] = (
    "cpi_yoy",
    "ppi_yoy",
    "pmi_manufacturing",
    "m1_yoy",
    "m2_yoy",
    "social_financing_increment",
    "exports_yoy",
    "lpr_1y",
    "lpr_5y",
    "gdp_yoy",
    "a_share_breadth_20d",
    "csi_all_share_risk_appetite",
)

# 跨市场序列不在 MACRO_SERIES_CATALOG（那是宏观预测的序列目录），
# 页面中文名在此维护；目录内已有 name_zh 的序列一律以目录为准。
_SERIES_LABELS: dict[str, str] = {
    "us_treasury_10y": "美债10年期收益率",
    "us_treasury_2y": "美债2年期收益率",
    "wti_spot": "WTI 原油现货",
    "usd_cny_official_mid": "人民币中间价",
    "copper_world_monthly": "全球铜价（月度）",
    "shibor_overnight": "银行间隔夜拆借利率（SHIBOR）",
    "shibor_3m": "三个月拆借利率（SHIBOR）",
}

_SPARKLINE_POINTS = 40
_FALLBACK_MAX_LAG_DAYS = 3


def _catalog_meta() -> dict[str, dict[str, str]]:
    from src.macro_forecast.registry import MACRO_SERIES_CATALOG

    return {str(item.get("series_id")): item for item in MACRO_SERIES_CATALOG}


def _freshness_status(series_id: str, obs_day: date, *, now_date: date) -> str:
    from src.macro_data.freshness import evaluate_freshness

    try:
        return evaluate_freshness(series_id, obs_day, now_date=now_date)
    except ValueError:
        # 序列未注册新鲜度规则（如 shibor）：按日历日兜底，只标注不挡接口。
        return "READY" if (now_date - obs_day).days <= _FALLBACK_MAX_LAG_DAYS else "STALE"


def _series_entry(series_id: str, rows: list[dict[str, Any]], *, now_date: date) -> dict[str, Any]:
    from src.macro_data.identity import ALL_IDENTITIES

    catalog = _catalog_meta()
    identity = ALL_IDENTITIES.get(series_id)
    meta = catalog.get(series_id) or {}
    entry: dict[str, Any] = {
        "series_id": series_id,
        "label": _SERIES_LABELS.get(series_id) or str(meta.get("name_zh") or series_id),
        "unit": str(meta.get("unit") or (identity.unit if identity else "")),
        "frequency": str(meta.get("frequency") or (identity.frequency if identity else "")),
        "value": None,
        "observation_date": None,
        "captured_at": None,
        "status": "MISSING",
        "lag_days": None,
        "prev_value": None,
        "prev_observation_date": None,
        "change": None,
        "sparkline": [],
    }
    if not rows:
        return entry
    ordered = sorted(rows, key=lambda item: str(item["observation_date"]))
    latest = ordered[-1]
    entry["value"] = latest.get("value")
    entry["observation_date"] = str(latest["observation_date"])
    entry["captured_at"] = str(latest["fetched_at"])
    obs_day = date.fromisoformat(entry["observation_date"][:10])
    entry["status"] = _freshness_status(series_id, obs_day, now_date=now_date)
    entry["lag_days"] = (now_date - obs_day).days
    if len(ordered) >= 2 and latest.get("value") is not None:
        prev = ordered[-2]
        entry["prev_value"] = prev.get("value")
        entry["prev_observation_date"] = str(prev["observation_date"])
        if prev.get("value") is not None:
            entry["change"] = round(float(latest["value"]) - float(prev["value"]), 6)
    points = ordered[-_SPARKLINE_POINTS:]
    if len(points) >= 2:
        entry["sparkline"] = [
            {"date": str(row["observation_date"]), "value": row.get("value")}
            for row in points if row.get("value") is not None
        ]
    return entry


def _read_history(db_path: Path, series_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    from src.macro_data.store import CrossMarketStore

    if not series_ids:
        return {}
    store = CrossMarketStore(db_path)
    try:
        rows = store.read_rows(series_ids)
    finally:
        store.close()
    by_series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_series.setdefault(str(row["series_id"]), []).append(row)
    return by_series


def _snapshot_axes_trend(db_path: Path) -> dict[str, dict[str, Any]]:
    """最近两份宏观快照的轴分值，供页面显示「较前值」变化。缺前值时为空。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT as_of, axes_json, states_json FROM macro_snapshots "
            "ORDER BY as_of DESC, created_at DESC LIMIT 2"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {}

    def _axes(row: sqlite3.Row) -> dict[str, Any]:
        import json

        try:
            axes = json.loads(row["axes_json"] or "{}")
            states = json.loads(row["states_json"] or "{}")
        except (TypeError, ValueError):
            return {}
        return {
            key: {"score": value, "state": str(states.get(key) or "")}
            for key, value in (axes or {}).items() if isinstance(value, (int, float))
        }

    previous = _axes(rows[-1]) if len(rows) >= 2 else {}
    return {key: {**value, "prev": previous.get(key)} for key, value in _axes(rows[0]).items()}


def _latest_forecast(db_path: Path) -> dict[str, Any] | None:
    from src.macro_forecast.engine import DIRECTION_CN

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT target_trade_date, run_mode, status, market_direction, narrative_md, "
            "published_at, created_at FROM macro_market_forecasts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    direction = str(row["market_direction"] or "") or None
    return {
        "target_trade_date": str(row["target_trade_date"] or ""),
        "run_mode": str(row["run_mode"] or ""),
        "status": str(row["status"] or ""),
        "direction": direction,
        "direction_cn": DIRECTION_CN.get(direction) if direction else None,
        "narrative_md": str(row["narrative_md"] or ""),
        "published_at": str(row["published_at"] or "") or None,
        "created_at": str(row["created_at"] or ""),
    }


def get_macro_overview() -> dict[str, Any]:
    from src.value_strategy.macro_sector_projection import get_macro_sector_projection

    db_path = get_runtime_root() / "research.db"
    projection = get_macro_sector_projection()
    history = _read_history(db_path, [*OVERVIEW_SERIES, *DOMESTIC_SERIES])
    now = datetime.now().astimezone()
    now_date = now.date()

    def _entries(series_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        return [_series_entry(series_id, history.get(series_id) or [], now_date=now_date) for series_id in series_ids]

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "series": _entries(OVERVIEW_SERIES),
        "domestic_series": _entries(DOMESTIC_SERIES),
        "axes_trend": _snapshot_axes_trend(db_path),
        "forecast": _latest_forecast(db_path),
        "projection": projection,
    }

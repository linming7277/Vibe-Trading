"""冻结输入包的编排：日历 → 稳定性守卫 → 读取 → 组装 → 幂等保存。

只做数据读取与确定性组装；不调用模型、不发网络请求（TDX 读取除外，且
只读）。P 日收盘未就绪时如实生成带缺口的前向输入包，不造数。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from src.macro_forecast.bars import ForecastBarStore
from src.macro_forecast.bundle import (
    ForecastBundleStore, StabilityGuard, build_bundle_payload,
)
from src.macro_forecast.contracts import (
    CALENDAR_UNAVAILABLE, CALENDAR_UNVERIFIED_NEXT_SESSION, build_timeline,
)
from src.macro_forecast.registry import (
    MACRO_SERIES_CATALOG, PRIMARY_BENCHMARK, REGISTRY_VERSION, InstrumentRegistryStore,
)

logger = logging.getLogger(__name__)

_LOCAL_CALENDAR_DATASET = "trading_dates"


def load_local_trading_days() -> list[str]:
    from src.tdx_data.store import TdxDataStore

    store = TdxDataStore()
    try:
        rows = store.list_records(_LOCAL_CALENDAR_DATASET, limit=5000)["items"]
    finally:
        store.close()
    return [str(row.get("key") or "") for row in rows]


def load_trading_days(*, tdx_client: Any = None, today: str | None = None) -> list[str, ...]:
    """本地日历优先；传入客户端时合并 TDX 实时日历（覆盖当日与近期）。"""
    days = [str(item or "").replace("-", "")[:8] for item in load_local_trading_days()]
    days = [item for item in days if len(item) == 8 and item.isdigit()]
    if tdx_client is not None:
        anchor = (today or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
        try:
            from datetime import timedelta

            start = (datetime.strptime(anchor, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d")
            end = (datetime.strptime(anchor, "%Y%m%d") + timedelta(days=15)).strftime("%Y%m%d")
            live = tdx_client.call("get_trading_dates", market="SH", start_time=start, end_time=end) or []
            days.extend(str(item).replace("-", "")[:8] for item in live)
        except Exception as exc:  # 日历读取失败 → 保守降级，不阻塞事实层
            logger.warning("TDX 实时日历读取失败（降级用本地日历）: %s", exc)
    return sorted(set(days))


def load_macro_series_rows(db_path: Any) -> list[dict[str, Any]]:
    import sqlite3

    from pathlib import Path as _Path

    uri = f"file:{_Path(db_path).as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT series_id, observation_date, release_date, value, unit, source, fetched_at, vintage_id, metadata_json FROM macro_series"
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            raw_metadata = item.pop("metadata_json") or "{}"
            try:
                item["metadata"] = json.loads(raw_metadata)
            except (TypeError, ValueError):
                item["metadata"] = {}
            items.append(item)
        return items
    finally:
        connection.close()


def load_industry_rows(registry: InstrumentRegistryStore, *, registry_version: str = REGISTRY_VERSION) -> list[dict[str, Any]]:
    from src.strategy_engines.value.macro_sector_v2 import INDUSTRY_TO_GROUP

    rows = registry.industry_targets(registry_version=registry_version)
    for row in rows:
        name = str(row.get("name_zh") or "")
        row["matrix_linked"] = name in INDUSTRY_TO_GROUP
        row["matrix_group"] = INDUSTRY_TO_GROUP.get(name)
    return rows


def prepare_input_bundle(
    *,
    target_date: str | None = None,
    trading_days: list[str] | None = None,
    bars_map: dict[str, list[dict[str, Any]]] | None = None,
    series_rows: list[dict[str, Any]] | None = None,
    industry_rows: list[dict[str, Any]] | None = None,
    registry_version: str = REGISTRY_VERSION,
    benchmark_code: str = PRIMARY_BENCHMARK,
    bar_read_count: int = 520,
    save: bool = True,
    research_db: Any = None,
) -> dict[str, Any]:
    """组装（并默认幂等保存）一份输入包。测试可注入全部输入（save=False）。

    ``research_db`` 缺省指向生产 research.db；测试应显式传临时库路径。
    """
    days = trading_days if trading_days is not None else load_trading_days()
    if target_date is None:
        from src.macro_forecast.contracts import next_trading_day

        today = datetime.now().strftime("%Y%m%d")
        target_date = next_trading_day(today, days) or ""
        if not target_date:
            return {"status": "CALENDAR_UNAVAILABLE", "message": "日历未覆盖当前日之后的交易日，无法确定 T"}
    timeline = build_timeline(target_date, days)
    if timeline.calendar_status == CALENDAR_UNAVAILABLE:
        return {"status": "CALENDAR_UNAVAILABLE", "message": "本地交易日历为空"}

    if research_db is None:
        from src.config.paths import get_runtime_root

        research_db = get_runtime_root() / "research.db"
    bundle_store = ForecastBundleStore(research_db)
    bar_store = ForecastBarStore(research_db)
    registry_store = InstrumentRegistryStore(research_db)
    try:
        guard = StabilityGuard({
            "research_bundles": bundle_store._conn,
            "research_bars": bar_store._conn,
            "research_registry": registry_store._conn,
        })
        for attempt in range(2):  # §6.7：并发变化重取一次
            industries = industry_rows if industry_rows is not None else load_industry_rows(registry_store, registry_version=registry_version)
            if bars_map is None:
                codes = [benchmark_code] + [row["code"] for row in industries]
                bars_map = bar_store.read_bars(codes, end_date=timeline.previous_date or target_date, count=bar_read_count)
            if series_rows is None:
                series_rows = load_macro_series_rows(research_db)
            stable, versions = guard.stable()
            if stable or attempt == 1:
                break
            bars_map = None
            series_rows = None
        if not stable:
            return {"status": "UNSTABLE_SOURCES", "message": "读取期间源持续变化，按契约放弃本次保存", "versions": versions}

        bars_as_of = max(
            (str(row.get("trade_date")) for rows in bars_map.values() for row in rows),
            default="",
        )
        expected_p = timeline.previous_date
        p_close_pending = bool(expected_p) and bars_as_of != expected_p
        # M1-B：官方跨市场上下文（additive 字段；PIT 按同一 cutoff 裁剪）。
        from src.macro_data.features import build_cross_market_context

        cross_market_context = build_cross_market_context(
            series_rows, cutoff=timeline.cutoff_at)
        payload = build_bundle_payload(
            timeline=timeline,
            bars_map=bars_map,
            series_rows=series_rows,
            industry_rows=industries,
            benchmark_code=benchmark_code,
            macro_catalog=[dict(entry) for entry in MACRO_SERIES_CATALOG],
            registry_version=registry_version,
            bars_as_of=bars_as_of or expected_p,
            p_close_pending=p_close_pending,
            cross_market_available=False,
            cross_market_context=cross_market_context,
        )
        result: dict[str, Any] = {
            "status": "BUILT",
            "payload": payload,
            "calendar_status": timeline.calendar_status,
            "data_mode": payload["data_mode"],
            "gaps": payload["gaps"],
            "p_close_pending": p_close_pending,
        }
        if save:
            saved = bundle_store.save(payload)
            result["save"] = saved
        if timeline.calendar_status == CALENDAR_UNVERIFIED_NEXT_SESSION:
            result["warning"] = "目标日未获日历确认，正式计分预测前须复核（§4.3）"
        return result
    finally:
        bundle_store.close()
        bar_store.close()
        registry_store.close()



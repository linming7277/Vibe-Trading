"""EOD 内的前向预测输入准备步骤（M1-B 日报事故修复，2026-09-08）。

背景：forecast_index_bars 当日刷新与次日 input bundle 构建原本是
EOD 之外的隐式人工步骤——2026-09-08 因两步缺席，日报的市场复盘/
预测复盘/前瞻三段全灭。本模块把两步固化进 EOD 流水线
（STRATEGY_EVENT_DELIVERY 之后、MACRO_LINE/日报之前）。

约束：fail-soft——任一步失败只降级该阶段状态，绝不阻塞 EOD；
TDX 只读；bundle 走既有 prepare_input_bundle（幂等保存）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def refresh_forecast_bars(*, end_date: str | None = None) -> dict[str, Any]:
    """刷新基准/背景指数与行业目标的 forecast_index_bars 至最新交易日。"""
    from src.macro_forecast.bars import ForecastBarStore, collect_history
    from src.macro_forecast.registry import (
        InstrumentRegistryStore, PRIMARY_BENCHMARK, REFERENCE_INDEXES,
    )
    from src.macro_forecast.service import load_industry_rows
    from src.tdx_data.client import TdxClient

    client = TdxClient()
    store = ForecastBarStore()
    registry = InstrumentRegistryStore()
    try:
        industry_rows = load_industry_rows(registry)
        codes = ([PRIMARY_BENCHMARK] + [code for code, _ in REFERENCE_INDEXES]
                 + [row["code"] for row in industry_rows])
        end = end_date or datetime.now().strftime("%Y%m%d")
        rows = collect_history(client, codes, end_date=end, count=10)
        written = store.write_bars(rows)
        return {"status": "READY", "codes": len(codes), **written}
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("forecast bars refresh failed: %s", exc)
        return {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:160]}
    finally:
        client.close()
        store.close()
        registry.close()


def resolve_next_target(*, today: str | None = None) -> str:
    """TDX 实时日历解析下一交易日（离线日历滞后时的权威路径）。"""
    from src.macro_forecast.contracts import next_trading_day
    from src.macro_forecast.forecast_service import load_trading_days
    from src.tdx_data.client import TdxClient

    anchor = (today or datetime.now().strftime("%Y%m%d")).replace("-", "")
    client = TdxClient()
    try:
        days = load_trading_days(tdx_client=client, today=anchor)
    finally:
        client.close()
    return next_trading_day(anchor, days) or ""


def build_next_bundle(*, target_date: str | None = None) -> dict[str, Any]:
    """构建（幂等保存）下一交易日输入包；世界未就绪时如实返回。"""
    from src.macro_forecast.forecast_service import load_trading_days
    from src.macro_forecast.service import prepare_input_bundle
    from src.tdx_data.client import TdxClient

    anchor = (target_date or datetime.now().strftime("%Y%m%d")).replace("-", "")
    client = TdxClient()
    try:
        trading_days = load_trading_days(tdx_client=client, today=anchor)
    finally:
        client.close()
    result = prepare_input_bundle(
        target_date=anchor or None, trading_days=trading_days, save=True,
    )
    status = str(result.get("status") or "")
    saved = result.get("save") or {}
    payload = result.get("payload") or {}
    return {
        "status": status if status in {"BUILT", "CALENDAR_UNAVAILABLE"} else f"FAILED_{status}",
        "target_date": (payload.get("target") or {}).get("target_date"),
        "data_mode": payload.get("data_mode"),
        "bundle_id": saved.get("bundle_id"),
        "gaps": result.get("gaps"),
    }


def prepare_forecast_inputs_step(*, today: str | None = None) -> dict[str, Any]:
    """EOD 组合步骤：刷新 bars → 解析目标日 → 构建 bundle。

    返回 bars_status / bundle_status / target_date，供 MACRO_NEXT_FORECAST
    阶段显式传 target（不再依赖离线日历的自动解析）。
    """
    bars = refresh_forecast_bars()
    target_date = ""
    bundle_status = "SKIPPED"
    bundle: dict[str, Any] = {}
    try:
        target_date = resolve_next_target(today=today)
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("next target resolution failed: %s", exc)
        bundle_status = "FAILED_TARGET"
    if target_date:
        bundle = build_next_bundle(target_date=target_date)
        bundle_status = str(bundle.get("status") or "FAILED")
    return {
        "bars_status": str(bars.get("status") or "FAILED"),
        "bundle_status": bundle_status,
        "target_date": target_date,
        "bars": bars,
        "bundle": bundle,
    }

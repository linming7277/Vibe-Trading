"""Bounded cross-market refresh orchestration (M1-B §六/§十一/§二十一~§二十五)。

- 每源独立隔离：一个源失败不阻塞其它源（READY / PARTIAL / FAILED）。
- PIT 诚实标注：批量历史回补行 = HISTORICAL_BACKFILL；日常前向抓取 =
  FORWARD_CAPTURED。同主键首次捕获优先，不翻写证据属性。
- 网络有界：Treasury 每年一个文件、ChinaMoney 一个区间调用、FRED 每序列
  一次调用；重复 refresh 幂等（0 duplicate）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.macro_data import chinamoney, fred_source, treasury
from src.macro_data.identity import (
    ALL_IDENTITIES,
    COPPER_WORLD_MONTHLY,
    PIT_FORWARD_CAPTURED,
    PIT_HISTORICAL_BACKFILL,
    TREASURY_US10Y,
    TREASURY_US2Y,
    USD_CNY_OFFICIAL_MID,
    US_TREASURY_10Y_FRED,
    US_TREASURY_2Y_FRED,
    WTI_SPOT,
    source_hash,
    vintage_id,
)
from src.macro_data.store import CrossMarketStore

logger = logging.getLogger(__name__)

_TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
    "/daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)


def shift_business_days(day: date, lag: int) -> date:
    """官方发布滞后（按工作日平移；lag=0 即同日）。"""
    cursor = day
    remaining = lag
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def _add_months_to_15(day: date, months: int) -> date:
    """月频序列发布日：观测月向后平移 N 个月的 15 日（IMF 月度发布节奏）。"""
    total = day.year * 12 + (day.month - 1) + months
    return date(total // 12, total % 12 + 1, 15)


def row_pit_status(observation_date: str, captured_at: str) -> str:
    """按行诚实标注（§六/§十四）：观测日=抓取日的前向捕获；更早的批量取回
    一律 HISTORICAL_BACKFILL，绝不把当前抓取时间伪装成历史前向捕获。"""
    capture_day = captured_at[:10]
    return PIT_FORWARD_CAPTURED if str(observation_date)[:10] >= capture_day else PIT_HISTORICAL_BACKFILL


def _row(series_id: str, observation_date: str, release_date: str, value: float | None,
         *, pit_status: str, captured_at: str, source_url: str,
         published_at: str | None = None, published_at_precision: str | None = None) -> dict[str, Any]:
    identity = ALL_IDENTITIES[series_id]
    metadata: dict[str, Any] = {
        "source_id": identity.source_id,
        "source_tier": identity.source_tier,
        "series_native_id": identity.series_native_id,
        "definition_tag": identity.definition_tag,
        "frequency": identity.frequency,
        "timezone": identity.timezone,
        "unit": identity.unit,
        "observed_at": observation_date,
        "published_at": published_at or f"{release_date}T00:00:00+00:00",
        "published_at_precision": published_at_precision or identity.published_at_precision,
        "captured_at": captured_at,
        "captured_via": identity.captured_via,
        "pit_status": pit_status,
        "source_hash": source_hash(series_id, observation_date, value, identity.series_native_id),
    }
    if identity.extras:
        metadata["publish_schedule"] = identity.extras.get("publish_schedule")
    return {
        "series_id": series_id,
        "observation_date": observation_date,
        "release_date": release_date,
        "vintage_id": vintage_id(series_id, observation_date, value, identity.series_native_id),
        "value": value,
        "source_url": source_url,
        "release_status": "RELEASED",
        "fetched_at": captured_at,
        "metadata": metadata,
    }


def refresh_treasury(store: CrossMarketStore, *, start_year: int, end_year: int,
                     timeout: float = 30.0) -> dict[str, Any]:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    url = _TREASURY_URL.format(year="{year}")
    days = treasury.fetch_history(start_year, end_year, timeout=timeout)
    rows: list[dict[str, Any]] = []
    for day in days:
        release = shift_business_days(date.fromisoformat(day.observation_date), 0)
        for identity, value in ((TREASURY_US2Y, day.us2y), (TREASURY_US10Y, day.us10y)):
            if value is None:
                continue  # 空值不入库更不写 0
            rows.append(_row(
                identity.series_id, day.observation_date, release.isoformat(), value,
                pit_status=row_pit_status(day.observation_date, captured_at),
                captured_at=captured_at,
                source_url=url.format(year=day.observation_date[:4]),
            ))
    inserted = store.upsert_rows(rows)
    return {"source": "treasury.gov", "status": "READY", "fetched_days": len(days),
            "rows": len(rows), "inserted": inserted, "error": None}


def refresh_chinamoney(store: CrossMarketStore, *, start_date: str, end_date: str,
                       timeout: float = 25.0) -> dict[str, Any]:
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    days = chinamoney.fetch_mid_range(start_date, end_date, timeout=timeout)
    rows: list[dict[str, Any]] = []
    for day in days:
        if day.mid is None:
            continue
        published_at, precision = chinamoney.published_at_iso(day.observation_date)
        rows.append(_row(
            USD_CNY_OFFICIAL_MID.series_id, day.observation_date,
            day.observation_date, day.mid,
            pit_status=row_pit_status(day.observation_date, captured_at),
            captured_at=captured_at,
            source_url=chinamoney.build_query(start_date, end_date),
            published_at=published_at, published_at_precision=precision,
        ))
    inserted = store.upsert_rows(rows)
    return {"source": "chinamoney.ccpr", "status": "READY", "fetched_days": len(days),
            "rows": len(rows), "inserted": inserted, "error": None}


def refresh_fred_series(store: CrossMarketStore, *, series_keys: list[str],
                        start_date: str, end_date: str) -> dict[str, Any]:
    """FRED 通道：key 缺失 → 整通道 BLOCKED（§二）；每序列独立隔离。"""
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if fred_source.key_status() == fred_source.KEY_STATUS_MISSING:
        return {"source": "fred", "status": "BLOCKED", "fetched_days": 0, "rows": 0,
                "inserted": 0, "error": "FRED_KEY_STATUS=MISSING"}
    per_series: dict[str, Any] = {}
    total_rows = 0
    total_inserted = 0
    failures = 0
    for key in series_keys:
        identity = ALL_IDENTITIES[key]
        try:
            observations = fred_source.fetch_series(key, start_date=start_date, end_date=end_date)
        except fred_source.FredKeyMissing as exc:
            per_series[key] = {"status": "BLOCKED", "error": str(exc)}
            failures += 1
            continue
        except Exception as exc:  # noqa: BLE001 - 单序列隔离
            per_series[key] = {"status": "FAILED", "error": str(exc)}
            failures += 1
            continue
        rows: list[dict[str, Any]] = []
        for item in observations:
            if item.value is None:
                continue
            obs_day = date.fromisoformat(item.observation_date)
            if identity.frequency == "MONTHLY":
                release = _add_months_to_15(obs_day, identity.release_month_end_shift)
            else:
                release = shift_business_days(obs_day, identity.release_lag_business_days)
            rows.append(_row(
                key, item.observation_date, release.isoformat(), item.value,
                pit_status=row_pit_status(item.observation_date, captured_at),
                captured_at=captured_at,
                source_url="https://api.stlouisfed.org/fred/series/observations",
            ))
        inserted = store.upsert_rows(rows)
        total_rows += len(rows)
        total_inserted += inserted
        per_series[key] = {"status": "READY", "rows": len(rows), "inserted": inserted}
    status = "READY" if failures == 0 else ("BLOCKED" if failures == len(series_keys) else "PARTIAL")
    return {"source": "fred", "status": status, "fetched_days": 0,
            "rows": total_rows, "inserted": total_inserted,
            "per_series": per_series, "error": None if failures == 0 else f"{failures} series failed/blocked"}


def treasury_fred_crosscheck(treasury_rows: list[dict[str, Any]],
                             fred_rows: list[dict[str, Any]], *,
                             min_common_days: int = 30) -> dict[str, Any]:
    """官方主源 vs FRED fallback 一致性（§十）：只报告差异，不做平均。"""
    def _series(rows: list[dict[str, Any]], series_id: str) -> dict[str, float]:
        return {
            str(row["observation_date"])[:10]: float(row["value"])
            for row in rows
            if str(row.get("series_id")) == series_id and row.get("value") is not None
        }

    legs = {}
    for leg, primary_id, fred_id in (
        ("us2y", TREASURY_US2Y.series_id, US_TREASURY_2Y_FRED.series_id),
        ("us10y", TREASURY_US10Y.series_id, US_TREASURY_10Y_FRED.series_id),
    ):
        primary = _series(treasury_rows, primary_id)
        fallback = _series(fred_rows, fred_id)
        common = sorted(set(primary) & set(fallback))
        diffs = [abs(primary[d] - fallback[d]) for d in common]
        legs[leg] = {
            "matched_days": len(common),
            "mean_abs_diff": round(sum(diffs) / len(diffs), 6) if diffs else None,
            "max_abs_diff": round(max(diffs), 6) if diffs else None,
            "sufficient": len(common) >= min_common_days,
            "min_common_days": min_common_days,
        }
    return {"policy": "Treasury 为主、FRED 为 fallback，不做平均", "legs": legs}


def run_refresh(db_path: Path | str | None = None, *, mode: str = "backfill",
                backfill_years: int = 5, copper_years: int = 10,
                treasury_enabled: bool = True, chinamoney_enabled: bool = True,
                fred_enabled: bool = True,
                timeout: float = 30.0) -> dict[str, Any]:
    """一次有界 refresh。mode=backfill 批量历史；mode=forward 日常小窗口。"""
    from src.config.paths import get_runtime_root

    if mode not in {"backfill", "forward"}:
        raise ValueError(f"未知 refresh mode: {mode!r}")
    pit_status = PIT_HISTORICAL_BACKFILL if mode == "backfill" else PIT_FORWARD_CAPTURED
    now = datetime.now().astimezone()
    if db_path is None:
        db_path = get_runtime_root() / "research.db"
    store = CrossMarketStore(db_path)
    results: list[dict[str, Any]] = []
    try:
        today = now.date()
        if treasury_enabled:
            start_year = today.year - backfill_years + 1 if mode == "backfill" else today.year
            try:
                results.append(refresh_treasury(
                    store, start_year=start_year, end_year=today.year,
                    timeout=timeout,
                ))
            except Exception as exc:  # noqa: BLE001 - 源隔离
                logger.warning("treasury refresh failed: %s", exc)
                results.append({"source": "treasury.gov", "status": "FAILED",
                                "rows": 0, "inserted": 0, "error": str(exc)})
        if chinamoney_enabled:
            if mode == "backfill":
                # CcprHisNew 为"最新页"端点（仅返回最新 15 条，长区间拒绝）：
                # 官方历史深回补不可得，按审计与任务书只取可稳定窗口并
                # 从今日起 forward capture。
                start = (today - timedelta(days=30)).isoformat()
            else:
                start = (today - timedelta(days=14)).isoformat()
            try:
                results.append(refresh_chinamoney(
                    store, start_date=start, end_date=today.isoformat(),
                    timeout=timeout,
                ))
            except chinamoney.ChinaMoneyTlsBlocked as exc:
                logger.warning("chinamoney tls blocked: %s", exc)
                results.append({"source": "chinamoney.ccpr", "status": "TLS_BLOCKED",
                                "rows": 0, "inserted": 0, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - 源隔离
                logger.warning("chinamoney refresh failed: %s", exc)
                results.append({"source": "chinamoney.ccpr", "status": "FAILED",
                                "rows": 0, "inserted": 0, "error": str(exc)})
        if fred_enabled:
            if mode == "backfill":
                start = (today - timedelta(days=365 * backfill_years)).isoformat()
                copper_start = (today - timedelta(days=365 * copper_years)).isoformat()
            else:
                start = (today - timedelta(days=14)).isoformat()
                copper_start = (today - timedelta(days=120)).isoformat()
            daily_keys = [US_TREASURY_2Y_FRED.series_id, US_TREASURY_10Y_FRED.series_id,
                          WTI_SPOT.series_id]
            try:
                results.append(refresh_fred_series(
                    store, series_keys=daily_keys, start_date=start,
                    end_date=today.isoformat(),
                ))
                results.append(refresh_fred_series(
                    store, series_keys=[COPPER_WORLD_MONTHLY.series_id],
                    start_date=copper_start, end_date=today.isoformat(),
                ))
            except Exception as exc:  # noqa: BLE001 - 源隔离
                logger.warning("fred refresh failed: %s", exc)
                results.append({"source": "fred", "status": "FAILED",
                                "rows": 0, "inserted": 0, "error": str(exc)})

        statuses = [str(item.get("status")) for item in results]
        if not statuses:
            overall = "READY"
        elif all(item in {"READY"} for item in statuses):
            overall = "READY"
        elif all(item in {"FAILED"} for item in statuses):
            overall = "FAILED"
        else:
            overall = "PARTIAL"

        summary: dict[str, Any] = {
            "mode": mode,
            "pit_status": pit_status,
            "ran_at": now.isoformat(timespec="seconds"),
            "overall": overall,
            "sources": results,
        }
        # Treasury/FRED 交叉核验：仅当两条通道都有数据（§十）。
        if treasury_enabled and fred_enabled and statuses and "BLOCKED" not in statuses:
            treasury_rows = store.read_rows([TREASURY_US2Y.series_id, TREASURY_US10Y.series_id])
            fred_rows = store.read_rows(
                [US_TREASURY_2Y_FRED.series_id, US_TREASURY_10Y_FRED.series_id])
            if treasury_rows and fred_rows:
                summary["treasury_fred_crosscheck"] = treasury_fred_crosscheck(
                    treasury_rows, fred_rows)
        return summary
    finally:
        store.close()

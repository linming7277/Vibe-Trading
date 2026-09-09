"""Per-series freshness rules (M1-B §十五)。

按各自频率与官方发布节奏判断 READY / STALE / MISSING：
- 美债/WTI 日频：按美国交易日（周一~周五）计滞后，WTI 与 FRED 通道
  预留 T+1 发布滞后；中间价按中国工作日。
- 铜月频：两个月内有过更新即 READY，绝不因 10 天未更新误判 STALE。
"""

from __future__ import annotations

from datetime import date, timedelta

from src.macro_data.identity import (
    ALL_IDENTITIES,
    FREQ_MONTHLY,
)

# 日频序列允许的最大交易日滞后（含节假日缓冲与官方发布滞后）。
_DAILY_MAX_BUSINESS_LAG = {
    "us_treasury_2y": 4,
    "us_treasury_10y": 4,
    "us_treasury_2y_fred": 6,
    "us_treasury_10y_fred": 6,
    "wti_spot": 6,
    "usd_cny_official_mid": 4,
}
# 月频序列允许的最大月度周期滞后。
_MONTHLY_MAX_LAG_MONTHS = {
    "copper_world_monthly": 2,
}


def business_days_between(earlier: date, later: date) -> int:
    """晚于 earlier 的周一~周五天数（不含 earlier，含 later）。"""
    if later <= earlier:
        return 0
    count = 0
    cursor = earlier
    while cursor < later:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def evaluate_freshness(series_id: str, latest_observation: date | None, *,
                       now_date: date) -> str:
    identity = ALL_IDENTITIES.get(series_id)
    if identity is None:
        raise ValueError(f"未知序列 {series_id!r}")
    if latest_observation is None:
        return "MISSING"
    if identity.frequency == FREQ_MONTHLY:
        max_months = _MONTHLY_MAX_LAG_MONTHS.get(series_id, 2)
        return "READY" if months_between(latest_observation, now_date) <= max_months else "STALE"
    max_lag = _DAILY_MAX_BUSINESS_LAG.get(series_id)
    if max_lag is None:
        raise ValueError(f"序列 {series_id!r} 缺少新鲜度规则")
    lag = business_days_between(latest_observation, now_date)
    return "READY" if lag <= max_lag else "STALE"

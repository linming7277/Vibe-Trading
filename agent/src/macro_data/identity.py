"""Cross-market official source identities (M1-B, audit V1 §四).

每条跨市场序列的来源身份在本模块一次性冻结；写库行的
``metadata_json`` 由此生成，保证 series_id 与 source identity
永不混淆（尤其 USD/CNY 官方中间价与市场价）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

SOURCE_TIER_OFFICIAL_GOVT = "OFFICIAL_GOVT"
SOURCE_TIER_OFFICIAL_BENCHMARK = "OFFICIAL_BENCHMARK"
SOURCE_TIER_OFFICIAL_PUBLIC = "OFFICIAL_PUBLIC"

FREQ_DAILY = "DAILY"
FREQ_MONTHLY = "MONTHLY"

PIT_FORWARD_CAPTURED = "FORWARD_CAPTURED"
PIT_HISTORICAL_BACKFILL = "HISTORICAL_BACKFILL"

# 审计 V1 定案的禁用 FRED 原生序列：DCOILWTI 已退役、LBMA 贵金属系列
# 已于 2022-01-31 被 FRED 移除。任何路径再次引用都必须显式报错。
BANNED_FRED_SERIES: frozenset[str] = frozenset({
    "DCOILWTI",
    "GOLDAMGBD228NLBM",
    "GOLDPMGBD228NLBM",
})


@dataclass(frozen=True)
class SeriesIdentity:
    """一条正式跨市场序列的不可变来源身份。"""

    series_id: str
    source_id: str
    source_tier: str
    series_native_id: str
    definition_tag: str
    frequency: str
    timezone: str
    unit: str
    captured_via: str
    role: str = "PRIMARY"  # PRIMARY / FALLBACK
    # 发布语义：DATE_ONLY=仅日期级官方发布；DATETIME_SCHEDULED=官方固定时刻表
    # （如中间价 09:15 北京时间）；发布滞后天数用于 release_date 推导。
    published_at_precision: str = "DATE_ONLY"
    release_lag_business_days: int = 0
    release_month_end_shift: int = 0  # 月频序列：发布日落在他月末第 N 月的 15 日
    extras: dict[str, str] = field(default_factory=dict)


TREASURY_US2Y = SeriesIdentity(
    series_id="us_treasury_2y",
    source_id="treasury.gov.yield_curve",
    source_tier=SOURCE_TIER_OFFICIAL_GOVT,
    series_native_id="2 Yr",
    definition_tag="us_treasury_par_yield_2y",
    frequency=FREQ_DAILY,
    timezone="America/New_York",
    unit="percent",
    captured_via="KEYLESS_OFFICIAL_CSV",
    release_lag_business_days=0,
)
TREASURY_US10Y = SeriesIdentity(
    series_id="us_treasury_10y",
    source_id="treasury.gov.yield_curve",
    source_tier=SOURCE_TIER_OFFICIAL_GOVT,
    series_native_id="10 Yr",
    definition_tag="us_treasury_par_yield_10y",
    frequency=FREQ_DAILY,
    timezone="America/New_York",
    unit="percent",
    captured_via="KEYLESS_OFFICIAL_CSV",
    release_lag_business_days=0,
)
USD_CNY_OFFICIAL_MID = SeriesIdentity(
    series_id="usd_cny_official_mid",
    source_id="chinamoney.usdcny_mid",
    source_tier=SOURCE_TIER_OFFICIAL_BENCHMARK,
    series_native_id="USD/CNY",
    definition_tag="cny_official_mid_rate",
    frequency=FREQ_DAILY,
    timezone="Asia/Shanghai",
    unit="CNY_per_USD",
    captured_via="PUBLIC_JSON",
    published_at_precision="DATETIME_SCHEDULED",
    extras={"publish_schedule": "每交易日 09:15:00 北京时间（CFETS 官方固定时刻）"},
)
US_TREASURY_2Y_FRED = SeriesIdentity(
    series_id="us_treasury_2y_fred",
    source_id="fred.dgs2",
    source_tier=SOURCE_TIER_OFFICIAL_PUBLIC,
    series_native_id="DGS2",
    definition_tag="us_treasury_par_yield_2y",
    frequency=FREQ_DAILY,
    timezone="America/New_York",
    unit="percent",
    captured_via="API_KEY_FRED",
    role="FALLBACK",
    release_lag_business_days=1,
)
US_TREASURY_10Y_FRED = SeriesIdentity(
    series_id="us_treasury_10y_fred",
    source_id="fred.dgs10",
    source_tier=SOURCE_TIER_OFFICIAL_PUBLIC,
    series_native_id="DGS10",
    definition_tag="us_treasury_par_yield_10y",
    frequency=FREQ_DAILY,
    timezone="America/New_York",
    unit="percent",
    captured_via="API_KEY_FRED",
    role="FALLBACK",
    release_lag_business_days=1,
)
WTI_SPOT = SeriesIdentity(
    series_id="wti_spot",
    source_id="fred.dcoilwtico",
    source_tier=SOURCE_TIER_OFFICIAL_PUBLIC,
    series_native_id="DCOILWTICO",
    definition_tag="wti_spot_cushing_fob",
    frequency=FREQ_DAILY,
    timezone="America/New_York",
    unit="USD_per_barrel",
    captured_via="API_KEY_FRED",
    release_lag_business_days=1,
)
COPPER_WORLD_MONTHLY = SeriesIdentity(
    series_id="copper_world_monthly",
    source_id="fred.pcoppusdm",
    source_tier=SOURCE_TIER_OFFICIAL_PUBLIC,
    series_native_id="PCOPPUSDM",
    definition_tag="imf_world_copper_monthly_avg",
    frequency=FREQ_MONTHLY,
    timezone="America/New_York",
    unit="USD_per_metric_ton",
    captured_via="API_KEY_FRED",
    release_month_end_shift=1,
)

ALL_IDENTITIES: dict[str, SeriesIdentity] = {
    identity.series_id: identity
    for identity in (
        TREASURY_US2Y, TREASURY_US10Y, USD_CNY_OFFICIAL_MID,
        US_TREASURY_2Y_FRED, US_TREASURY_10Y_FRED, WTI_SPOT, COPPER_WORLD_MONTHLY,
    )
}

MACRO_SERIES_IDS: frozenset[str] = frozenset(ALL_IDENTITIES)


def source_hash(series_id: str, observation_date: str, value: float | None,
                series_native_id: str) -> str:
    """内容寻址 vintage：同内容重抓 → 同哈希 → 幂等；值修订 → 新 vintage 行。"""
    payload = "|".join((
        series_id, observation_date,
        "null" if value is None else repr(round(float(value), 10)),
        series_native_id,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def vintage_id(series_id: str, observation_date: str, value: float | None,
               series_native_id: str) -> str:
    return source_hash(series_id, observation_date, value, series_native_id)[:12]

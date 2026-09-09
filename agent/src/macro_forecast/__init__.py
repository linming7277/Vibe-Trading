"""宏观与次日行情前瞻：输入与时间契约层（任务卡1）。

只建设数据基础与契约，不含预测模型调用、发送与定时任务。
"""

from src.macro_forecast.contracts import (
    CALENDAR_CONFIRMED, CALENDAR_NOT_TRADING_DAY, CALENDAR_UNAVAILABLE,
    CALENDAR_UNVERIFIED_NEXT_SESSION, CONSERVATIVE_UPPER_BOUND, FORWARD_OBSERVED,
    NEUTRAL_BAND, RELATIVE_BAND, STRICT_PIT, UNVERIFIED, ForecastTimeline,
    build_timeline, canonical_json, fingerprint, is_trading_day, next_trading_day,
    previous_trading_day, resolve_reference, visibility_decision,
)
from src.macro_forecast.registry import (
    PRIMARY_BENCHMARK, REGISTRY_VERSION, InstrumentRegistryStore, build_registry_rows,
    classify_board,
)

__all__ = [
    "CALENDAR_CONFIRMED", "CALENDAR_NOT_TRADING_DAY", "CALENDAR_UNAVAILABLE",
    "CALENDAR_UNVERIFIED_NEXT_SESSION", "CONSERVATIVE_UPPER_BOUND", "FORWARD_OBSERVED",
    "NEUTRAL_BAND", "RELATIVE_BAND", "STRICT_PIT", "UNVERIFIED", "ForecastTimeline",
    "build_timeline", "canonical_json", "fingerprint", "is_trading_day", "next_trading_day",
    "previous_trading_day", "resolve_reference", "visibility_decision",
    "PRIMARY_BENCHMARK", "REGISTRY_VERSION", "InstrumentRegistryStore",
    "build_registry_rows", "classify_board",
]

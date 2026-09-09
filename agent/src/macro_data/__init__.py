"""Cross-market free official macro data (M1-B).

审计 V1 定案的 P0 免费官方通道接入：US Treasury 2Y/10Y（官方 CSV 主源 +
FRED fallback）、USD/CNY 官方中间价（中国货币网）、WTI（FRED DCOILWTICO）、
铜（FRED PCOPPUSDM，月频）。复用既有 macro_series 表与 FredMacroTool，
零表结构变更、零付费依赖。
"""

from src.macro_data.features import build_cross_market_context, fx_official_mid_ready
from src.macro_data.freshness import evaluate_freshness
from src.macro_data.identity import ALL_IDENTITIES, BANNED_FRED_SERIES
from src.macro_data.refresh import run_refresh, treasury_fred_crosscheck
from src.macro_data.store import CrossMarketStore

__all__ = [
    "ALL_IDENTITIES",
    "BANNED_FRED_SERIES",
    "CrossMarketStore",
    "build_cross_market_context",
    "evaluate_freshness",
    "fx_official_mid_ready",
    "run_refresh",
    "treasury_fred_crosscheck",
]

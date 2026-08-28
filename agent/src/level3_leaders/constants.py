"""Stable product constants for the Value Line L3 main path."""

VALUE_LINE_LEADER_LIMIT = 2
MIN_LISTED_TRADING_DAYS = 20
MAX_MARKET_DATA_STALENESS_TRADING_DAYS = 5

TDX_TERMINAL_INDUSTRY_SOURCE = {
    "source_id": "tdx-research-industry-terminal-v2",
    "raw_source_name": "通达信研究行业三级（TQ 分组 18）",
    "product_display_name": "通达信末级行业（三级口径）",
    "industry_level": "TDX_RESEARCH_TERMINAL",
    "raw_industry_level": "TDX_RESEARCH_LEVEL_3_OR_LEVEL_2_LEAF",
    "code_pattern": "881xxx.SH",
    "hierarchy": {"level1_group": "16", "level2_group": "17", "terminal_group": "18"},
}

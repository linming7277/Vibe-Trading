"""Fiscal-year flow aggregation fixes the vendor single-quarter flow basis.

TDX professional finance returns revenue / net profit / operating cash flow
as single-quarter values on every row, including the 12-31 annual row, and
emits 0.00 flow values for report rows it never populated (2025-2026 IPOs).
These tests pin the fiscal-year aggregation and the leader feature basis.
"""

from __future__ import annotations

from src.strategy_engines.value_line import ValueLineService
from src.tdx_data.financial_history import (
    fiscal_year_flows,
    normalize_financial_row,
    trailing_twelve_month_flows,
)


def _q(report_date: str, announced: str, *, revenue: float | None, net_profit: float | None,
       ocf: float | None, roe: float | None = 10.0) -> dict:
    return {
        "symbol": "A.SH", "report_date": report_date, "announcement_date": announced,
        "period_type": {"03-31": "q1", "06-30": "semiannual", "09-30": "q3", "12-31": "annual"}[report_date[5:]],
        "revenue": revenue, "net_profit": net_profit, "operating_cash_flow": ocf,
        "roe": roe, "gross_margin": 40.0, "net_margin": 20.0, "revenue_yoy": 10.0,
        "net_profit_yoy": 10.0, "debt_ratio": 30.0, "shareholders": 50_000.0,
    }


def test_complete_fiscal_year_sums_four_single_quarters() -> None:
    rows = [
        _q("2024-03-31", "2024-04-26", revenue=7.4e8, net_profit=2.2e8, ocf=3.5e8),
        _q("2024-06-30", "2024-08-27", revenue=9.3e8, net_profit=3.7e8, ocf=4.6e8),
        _q("2024-09-30", "2024-10-31", revenue=9.1e8, net_profit=3.8e8, ocf=4.4e8),
        _q("2024-12-31", "2025-04-11", revenue=10.7e8, net_profit=4.3e8, ocf=4.3e8),
    ]
    result = fiscal_year_flows(rows)
    assert len(result) == 1
    assert result[0]["fiscal_year"] == "2024"
    assert result[0]["report_date"] == "2024-12-31"
    assert result[0]["announcement_date"] == "2025-04-11"
    assert result[0]["revenue"] == 36.5e8
    assert result[0]["net_profit"] == 14.0e8
    assert result[0]["operating_cash_flow"] == 16.8e8


def test_incomplete_fiscal_year_is_never_partially_summed() -> None:
    rows = [
        _q("2024-03-31", "2024-04-26", revenue=7.4e8, net_profit=2.2e8, ocf=3.5e8),
        _q("2024-06-30", "2024-08-27", revenue=9.3e8, net_profit=3.7e8, ocf=4.6e8),
        _q("2024-12-31", "2025-04-11", revenue=10.7e8, net_profit=4.3e8, ocf=4.3e8),
    ]
    assert fiscal_year_flows(rows) == []


def test_vendor_zero_flows_are_missing_not_summed() -> None:
    rows = [
        _q("2025-06-30", "2025-12-30", revenue=92.4e8, net_profit=-7.7e8, ocf=40.5e8),
        _q("2025-12-31", "2026-07-09", revenue=0.0, net_profit=0.0, ocf=0.0),
        _q("2026-03-31", "2026-07-09", revenue=508.0e8, net_profit=247.6e8, ocf=425.7e8),
    ]
    result = fiscal_year_flows(rows)
    assert result == []


def test_revised_announcement_wins_and_partial_field_years_keep_other_fields() -> None:
    rows = [
        _q("2024-03-31", "2024-04-26", revenue=7.0e8, net_profit=2.0e8, ocf=3.0e8),
        _q("2024-06-30", "2024-08-27", revenue=9.0e8, net_profit=3.0e8, ocf=4.0e8),
        _q("2024-09-30", "2024-10-31", revenue=9.0e8, net_profit=3.0e8, ocf=4.0e8),
        _q("2024-12-31", "2025-04-11", revenue=10.0e8, net_profit=4.0e8, ocf=4.0e8),
        # 同一期更正公告（营收缺失字段场景：0 视为缺）
        _q("2024-12-31", "2025-06-01", revenue=11.0e8, net_profit=4.2e8, ocf=4.1e8),
    ]
    result = fiscal_year_flows(rows)
    assert result[0]["revenue"] == 36.0e8  # 7+9+9+11，最新公告生效
    assert result[0]["net_profit"] == 12.2e8
    # OCF 某季为零（供应商未填充）→ 该字段缺失，营收/净利不受影响
    rows[1] = _q("2024-06-30", "2024-08-27", revenue=9.0e8, net_profit=3.0e8, ocf=0.0)
    result = fiscal_year_flows(rows)
    assert result[0]["operating_cash_flow"] is None
    assert result[0]["revenue"] == 36.0e8


def test_trailing_twelve_month_flows_needs_four_consecutive_quarters() -> None:
    # 年中上市公司：Q1'25 未披露，无完整财年，但 25H1→26Q1 四个连续报告期可组 TTM
    rows = [
        _q("2025-06-30", "2025-12-30", revenue=90.0e8, net_profit=9.0e8, ocf=8.0e8),
        _q("2025-09-30", "2025-10-30", revenue=87.0e8, net_profit=8.0e8, ocf=7.0e8),
        _q("2025-12-31", "2026-07-09", revenue=91.0e8, net_profit=10.0e8, ocf=9.0e8),
        _q("2026-03-31", "2026-07-09", revenue=99.0e8, net_profit=11.0e8, ocf=10.0e8),
    ]
    assert fiscal_year_flows(rows) == []
    ttm = trailing_twelve_month_flows(rows)
    assert ttm is not None
    assert ttm["revenue"] == 367.0e8
    assert ttm["net_profit"] == 38.0e8
    assert ttm["operating_cash_flow"] == 34.0e8
    assert ttm["last_report_date"] == "2026-03-31"
    # 断档（缺 Q1'25 与 Q4'25 之间不连续）时不可组 TTM
    assert trailing_twelve_month_flows(rows[:2]) is None
    # 只有三个报告期（长鑫科技现状）时不可组 TTM
    assert trailing_twelve_month_flows(rows[1:]) is None


def test_normalize_financial_row_maps_vendor_zero_flows_to_none() -> None:
    raw = {
        "tag_time": "20251231", "announce_time": "20260709",
        "FN230": 0.0, "FN232": 0.0, "FN234": 0.0, "FN197": 3.3, "FN281": 3.83,
        "FN202": 40.99, "FN40": 336_784_916_480.0, "FN72": 154_099_941_376.0,
    }
    normalized = normalize_financial_row("688825.SH", raw, "test")
    assert normalized["revenue"] is None
    assert normalized["net_profit"] is None
    assert normalized["operating_cash_flow"] is None
    assert normalized["gross_profit"] is None
    assert normalized["roe"] == 3.83
    assert normalized["gross_margin"] == 40.99


def _quarterly_year(symbol: str, year: int, revenue: float, net_profit: float, ocf: float) -> list[dict]:
    rows = []
    for suffix, announced in (("03-31", f"{year}-04-26"), ("06-30", f"{year}-08-27"),
                              ("09-30", f"{year}-10-31"), ("12-31", f"{year + 1}-04-11")):
        rows.append(_q(f"{year}-{suffix}", announced, revenue=revenue, net_profit=net_profit, ocf=ocf))
    for row in rows:
        row["symbol"] = symbol
    return rows


def _leader_rows_for(financials: dict, fundamentals: dict) -> list[dict]:
    return ValueLineService._leader_rows(
        None, "881322.SH", "集成电路设计", sorted(financials), "2026-09-04",
        financials, fundamentals, {},
        {"market_data_as_of": "2026-09-04", "market_data_status": "COMPLETE"},
    )


def test_leader_features_use_fiscal_year_totals_not_q4_only() -> None:
    financials = {
        # FY2024: 营收 40亿 / 净利 10亿；FY2025: 营收 54.5亿 / 净利 22.3亿
        "A.SH": _quarterly_year("A.SH", 2024, 10.0e8, 2.5e8, 3.0e8)
        + _quarterly_year("A.SH", 2025, 13.625e8, 5.575e8, 6.0e8),
    }
    fundamentals = {"A.SH": {"market_cap_100m": 2501.0, "pe_ttm": 101.0, "pb_mrq": 11.6,
                            "dividend_yield": 0.27, "beta": 1.1, "name": "澜起科技"}}
    rows = _leader_rows_for(financials, fundamentals)
    raw = rows[0]["raw_features"]
    # 年度营收 = 四季合计 54.5亿，而不是 12-31 行的 Q4 单季 13.625亿
    assert raw["revenue"] == 54.5e8
    assert raw["net_profit"] == 22.3e8
    assert raw["ocf_margin"] == 24.0e8 / 54.5e8 * 100
    assert raw["cash_conversion"] == 24.0e8 / 22.3e8 * 100
    assert raw["positive_ocf_years"] == 100.0


def test_new_ipo_without_complete_year_scores_on_missing_flows() -> None:
    financials = {
        "A.SH": _quarterly_year("A.SH", 2024, 10.0e8, 2.5e8, 3.0e8)
        + _quarterly_year("A.SH", 2025, 13.625e8, 5.575e8, 6.0e8),
        # 次新股：季报有真数据，年报行全 0（供应商未填充）
        "B.SH": [
            _q("2025-06-30", "2025-12-30", revenue=92.4e8, net_profit=-7.7e8, ocf=40.5e8),
            _q("2025-12-31", "2026-07-09", revenue=0.0, net_profit=0.0, ocf=0.0, roe=3.83),
            _q("2026-03-31", "2026-07-09", revenue=508.0e8, net_profit=247.6e8, ocf=425.7e8),
        ],
        # 年中上市：Q1'25 未披露 → 无完整财年，但 25H1→26Q1 四个连续报告期 → TTM 口径
        "C.SH": [
            _q("2024-12-31", "2025-12-30", revenue=0.0, net_profit=0.0, ocf=0.0),
            _q("2025-06-30", "2025-12-30", revenue=90.0e8, net_profit=9.0e8, ocf=8.0e8),
            _q("2025-09-30", "2025-10-30", revenue=87.0e8, net_profit=8.0e8, ocf=7.0e8),
            _q("2025-12-31", "2026-04-28", revenue=91.0e8, net_profit=10.0e8, ocf=9.0e8, roe=11.04),
            _q("2026-03-31", "2026-04-28", revenue=99.0e8, net_profit=11.0e8, ocf=10.0e8),
        ],
    }
    fundamentals = {
        "A.SH": {"market_cap_100m": 2501.0, "pe_ttm": 101.0, "pb_mrq": 11.6, "dividend_yield": 0.27, "beta": 1.1, "name": "澜起科技"},
        "B.SH": {"market_cap_100m": 2630.0, "pe_ttm": 141.0, "pb_mrq": 6.0, "dividend_yield": 0.0, "beta": 1.4, "name": "长鑫科技"},
        "C.SH": {"market_cap_100m": 80.0, "pe_ttm": 21.0, "pb_mrq": 2.0, "dividend_yield": 0.5, "beta": 1.2, "name": "年中上市"},
    }
    rows = {row["symbol"]: row for row in _leader_rows_for(financials, fundamentals)}
    new_ipo = rows["B.SH"]
    # 规模类指标缺失：不拿假 0 或单季值冒充年度口径
    assert new_ipo["raw_features"]["revenue"] is None
    assert new_ipo["raw_features"]["net_profit"] is None
    assert new_ipo["raw_features"]["positive_ocf_years"] is None
    # 比率类指标仍来自年报行（供应商已给出真实比率）
    assert new_ipo["raw_features"]["roe"] == 3.83
    assert new_ipo["coverage"] < rows["A.SH"]["coverage"]
    assert new_ipo["growth_status"]["flow_basis"] == "insufficient"
    # 年中上市公司用四个连续报告期 TTM 作为规模口径
    mid_year = rows["C.SH"]
    assert mid_year["raw_features"]["revenue"] == 367.0e8
    assert mid_year["raw_features"]["net_profit"] == 38.0e8
    assert mid_year["growth_status"]["flow_basis"] == "ttm"
    assert mid_year["coverage"] > new_ipo["coverage"]
    # 有完整财年的公司不受 TTM 回退影响
    assert rows["A.SH"]["growth_status"]["flow_basis"] == "fiscal_year"


def test_two_stage_ranking_by_size_keeps_quality_unknown_leaders_visible() -> None:
    """V3 两段式：排名=规模分位（市值/营收/净利），质量分缺失不挤出排名。"""
    financials = {
        # 质量好、规模中等
        "A.SH": _quarterly_year("A.SH", 2024, 10.0e8, 2.5e8, 3.0e8)
        + _quarterly_year("A.SH", 2025, 13.625e8, 5.575e8, 6.0e8),
        # 营收比 A 大、市值小
        "D.SH": _quarterly_year("D.SH", 2024, 50.0e8, 4.0e8, 4.0e8)
        + _quarterly_year("D.SH", 2025, 91.75e8, 9.5e8, 8.0e8),
        # 长鑫型：市值最大但只有三个报告期，无任何 12 个月口径
        "B.SH": [
            _q("2025-06-30", "2025-12-30", revenue=92.4e8, net_profit=-7.7e8, ocf=40.5e8),
            _q("2025-12-31", "2026-07-09", revenue=0.0, net_profit=0.0, ocf=0.0, roe=3.83),
            _q("2026-03-31", "2026-07-09", revenue=508.0e8, net_profit=247.6e8, ocf=425.7e8),
        ],
    }
    fundamentals = {
        "A.SH": {"market_cap_100m": 2501.0, "pe_ttm": 101.0, "pb_mrq": 11.6, "dividend_yield": 0.27, "beta": 1.1, "name": "澜起科技"},
        "D.SH": {"market_cap_100m": 900.0, "pe_ttm": 40.0, "pb_mrq": 4.0, "dividend_yield": 1.0, "beta": 1.0, "name": "同业"},
        "B.SH": {"market_cap_100m": 2630.0, "pe_ttm": 141.0, "pb_mrq": 6.0, "dividend_yield": 0.0, "beta": 1.4, "name": "长鑫科技"},
    }
    rows = {row["symbol"]: row for row in _leader_rows_for(financials, fundamentals)}
    # 市值最大的 B 即使质量分缺失，仍按规模拿到第 1 名（第一阶段事实层）。
    assert rows["B.SH"]["rank"] == 1
    assert rows["B.SH"]["position_score"] is not None
    assert rows["B.SH"]["score"] is None
    assert rows["A.SH"]["rank"] > 1
    assert rows["A.SH"]["score"] is not None

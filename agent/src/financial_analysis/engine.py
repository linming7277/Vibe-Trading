"""Pure-Python financial features and reproducible three-scenario forecasts."""

from __future__ import annotations

import math
import statistics
from typing import Any

FINANCIAL_FEATURE_VERSION = "value-financial-feature-v1.0.0"
FORECAST_VERSION = "value-financial-forecast-v1.0.0"
TREND_STATES = {"IMPROVING", "STABLE", "WEAKENING", "VOLATILE", "INSUFFICIENT_DATA"}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest PIT-visible announcement for each report period."""
    periods: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        report_date = str(row.get("report_date") or "")
        announcement_date = str(row.get("announcement_date") or "")
        if not report_date or not announcement_date:
            continue
        previous = periods.get(report_date)
        if previous is None or announcement_date >= str(previous.get("announcement_date") or ""):
            periods[report_date] = row
    return sorted(periods.values(), key=lambda row: (str(row["report_date"]), str(row["announcement_date"])))


def _annualize(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert TDX single-period flow values into full-year annual values.

    The local professional-finance package exposes FN230/FN232/FN234 as
    Q1/Q2/Q3/Q4 single-period flows even when the fourth row is tagged 12-31.
    Balance-sheet fields and ROE remain point-in-time fields from the Q4 row.
    """
    by_year: dict[int, list[dict[str, Any]]] = {}
    for row in periods:
        by_year.setdefault(int(str(row["report_date"])[:4]), []).append(row)
    annual: list[dict[str, Any]] = []
    flow_fields = ("revenue", "net_profit", "operating_cash_flow", "capex", "gross_profit")
    expected = {"q1", "semiannual", "q3", "annual"}
    for year, year_rows in sorted(by_year.items()):
        year_rows.sort(key=lambda row: str(row["report_date"]))
        q4 = next((row for row in year_rows if row.get("period_type") == "annual"), None)
        if not q4:
            continue
        if q4.get("flow_basis") == "annual":
            item = {**q4, "annualization_status": "SOURCE_ANNUAL"}
        elif expected.issubset({str(row.get("period_type")) for row in year_rows}):
            item = dict(q4)
            for field in flow_fields:
                values = [_number(row.get(field)) for row in year_rows if row.get("period_type") in expected]
                item[field] = sum(values) if len(values) == 4 and all(value is not None for value in values) else None
            revenue, profit = _number(item.get("revenue")), _number(item.get("net_profit"))
            gross_profit, ocf = _number(item.get("gross_profit")), _number(item.get("operating_cash_flow"))
            item["gross_margin"] = gross_profit / revenue * 100 if gross_profit is not None and (revenue or 0) != 0 else None
            item["net_margin"] = profit / revenue * 100 if profit is not None and (revenue or 0) != 0 else None
            item["cash_conversion"] = ocf / profit * 100 if ocf is not None and (profit or 0) != 0 else None
            item["flow_basis"] = "annualized_from_single_periods"
            item["annualization_status"] = "SUM_FOUR_SINGLE_PERIODS"
        else:
            item = {**q4, "annualization_status": "PARTIAL_RAW_Q4"}
        item["report_date"] = f"{year}-12-31"
        annual.append(item)
    for previous, current in zip(annual, annual[1:]):
        for field, yoy_field in (("revenue", "revenue_yoy"), ("net_profit", "net_profit_yoy")):
            before, after = _number(previous.get(field)), _number(current.get(field))
            current[yoy_field] = ((after / before) - 1) * 100 if before is not None and after is not None and before != 0 else None
    return annual


def _points(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    return [{
        "report_date": row["report_date"], "announcement_date": row["announcement_date"],
        "period_type": row.get("period_type"), "value": _round(_number(row.get(field))),
    } for row in rows]


def _cagr(rows: list[dict[str, Any]], field: str, years: int) -> dict[str, Any]:
    annual = {int(str(row["report_date"])[:4]): _number(row.get(field)) for row in rows if row.get("period_type") == "annual"}
    if not annual:
        return {"value": None, "years": years, "status": "INSUFFICIENT_HISTORY"}
    end_year = max(annual)
    start_year = end_year - years
    start, end = annual.get(start_year), annual.get(end_year)
    if start is None or end is None:
        return {"value": None, "years": years, "status": "INSUFFICIENT_HISTORY"}
    if start <= 0 or end <= 0:
        return {"value": None, "years": years, "status": "NON_POSITIVE_ENDPOINT"}
    return {"value": _round(((end / start) ** (1 / years) - 1) * 100), "years": years, "status": "READY"}


def _trend(values: list[float | None], *, reverse: bool = False, percentage_points: bool = False) -> str:
    usable = [value for value in values if value is not None]
    if len(usable) < 3:
        return "INSUFFICIENT_DATA"
    recent = usable[-4:]
    deltas = [recent[index] - recent[index - 1] for index in range(1, len(recent))]
    if len(deltas) >= 2 and max(deltas) > 0 > min(deltas):
        spread = max(recent) - min(recent)
        scale = 5.0 if percentage_points else max(abs(statistics.fmean(recent)), 1.0)
        if spread / scale > 0.35:
            return "VOLATILE"
    delta = recent[-1] - recent[0]
    threshold = 1.0 if percentage_points else max(abs(recent[0]) * 0.05, 1.0)
    if abs(delta) <= threshold:
        return "STABLE"
    improving = delta > 0
    if reverse:
        improving = not improving
    return "IMPROVING" if improving else "WEAKENING"


def _combined_trend(states: list[str]) -> str:
    usable = [state for state in states if state != "INSUFFICIENT_DATA"]
    if not usable:
        return "INSUFFICIENT_DATA"
    if "VOLATILE" in usable:
        return "VOLATILE"
    score = sum({"IMPROVING": 1, "STABLE": 0, "WEAKENING": -1}[state] for state in usable)
    if score > 0:
        return "IMPROVING"
    if score < 0:
        return "WEAKENING"
    return "STABLE"


class FinancialFeatureEngine:
    version = FINANCIAL_FEATURE_VERSION

    def build(self, *, stock_code: str, stock_name: str, as_of: str,
              rows: list[dict[str, Any]], financial_sector: bool = False) -> dict[str, Any]:
        all_periods = _dedupe([row for row in rows if str(row.get("announcement_date") or "9999-12-31") <= as_of])
        annual_all = _annualize(all_periods)
        annual = annual_all[-6:]
        latest = all_periods[-1] if all_periods else None
        display = annual[-5:]
        if latest and latest.get("period_type") != "annual":
            display = [*display, latest]

        required = ["revenue", "net_profit", "operating_cash_flow", "equity", "assets", "roe", "debt_ratio"]
        if not financial_sector:
            required.append("gross_margin")
        denominator = max(1, len(display) * len(required))
        present = sum(_number(row.get(field)) is not None for row in display for field in required)
        missing = [field for field in required if not any(_number(row.get(field)) is not None for row in display)]
        if len(annual_all) >= 5:
            status = "READY"
        elif len(annual_all) >= 2:
            status = "PARTIAL"
        else:
            status = "INSUFFICIENT_DATA"

        growth = {
            "revenue": _points(display, "revenue"), "revenue_yoy": _points(display, "revenue_yoy"),
            "net_profit": _points(display, "net_profit"), "net_profit_yoy": _points(display, "net_profit_yoy"),
            "revenue_cagr_3y": _cagr(annual_all, "revenue", 3),
            "revenue_cagr_5y": _cagr(annual_all, "revenue", 5),
            "profit_cagr_3y": _cagr(annual_all, "net_profit", 3),
            "profit_cagr_5y": _cagr(annual_all, "net_profit", 5),
        }
        profitability = {
            "roe": _points(display, "roe"),
            "gross_margin": {"status": "NOT_APPLICABLE", "items": []} if financial_sector else {"status": "AVAILABLE", "items": _points(display, "gross_margin")},
            "net_margin": _points(display, "net_margin"),
        }
        cash_flow = {
            "operating_cash_flow": _points(display, "operating_cash_flow"),
            "cash_conversion": _points(display, "cash_conversion"),
            "ocf_to_revenue": [{**point, "value": _round(
                (_number(row.get("operating_cash_flow")) / _number(row.get("revenue")) * 100)
                if _number(row.get("operating_cash_flow")) is not None and (_number(row.get("revenue")) or 0) != 0 else None
            )} for point, row in zip(_points(display, "operating_cash_flow"), display)],
        }
        balance_sheet = {
            "total_assets": _points(display, "assets"), "equity": _points(display, "equity"),
            "debt_ratio": _points(display, "debt_ratio"),
        }
        capital_expenditure = {
            "capex": _points(display, "capex"),
            "capex_to_revenue": [{**point, "value": _round(
                (_number(row.get("capex")) / _number(row.get("revenue")) * 100)
                if _number(row.get("capex")) is not None and (_number(row.get("revenue")) or 0) != 0 else None
            )} for point, row in zip(_points(display, "capex"), display)],
        }

        growth_trend = _combined_trend([
            _trend([_number(row.get("revenue")) for row in annual_all]),
            _trend([_number(row.get("net_profit")) for row in annual_all]),
        ])
        margin_fields = ["roe", "net_margin"] if financial_sector else ["roe", "gross_margin", "net_margin"]
        profitability_trend = _combined_trend([
            _trend([_number(row.get(field)) for row in annual_all], percentage_points=True) for field in margin_fields
        ])
        cash_flow_trend = _combined_trend([
            _trend([_number(row.get("operating_cash_flow")) for row in annual_all]),
            _trend([_number(row.get("cash_conversion")) for row in annual_all], percentage_points=True),
        ])
        balance_sheet_trend = _combined_trend([
            _trend([_number(row.get("equity")) for row in annual_all]),
            _trend([_number(row.get("debt_ratio")) for row in annual_all], reverse=True, percentage_points=True),
        ])

        latest_changes: list[dict[str, Any]] = []
        if len(all_periods) >= 2:
            current = all_periods[-1]
            if current.get("period_type") == "annual" and len(annual_all) >= 2:
                previous, current = annual_all[-2], annual_all[-1]
            else:
                previous_year = int(str(current["report_date"])[:4]) - 1
                previous = next((row for row in reversed(all_periods)
                                 if int(str(row["report_date"])[:4]) == previous_year
                                 and row.get("period_type") == current.get("period_type")), None)
            if previous is None:
                previous = {}
            for field in ("revenue", "net_profit", "operating_cash_flow", "roe", "debt_ratio"):
                before, after = _number(previous.get(field)), _number(current.get(field))
                if before is None or after is None:
                    continue
                change = ((after / before) - 1) * 100 if before != 0 else None
                latest_changes.append({
                    "metric": field, "previous": _round(before), "current": _round(after),
                    "change_percent": _round(change), "report_date": current["report_date"],
                    "fact": True,
                })

        cautions = ["FINANCIAL_SECTOR_METRIC_CAUTION"] if financial_sector else []
        return {
            "stock_code": stock_code, "stock_name": stock_name, "as_of": as_of,
            "feature_version": self.version, "status": status,
            "historical_periods": display, "annual_periods_for_calculation": annual,
            "growth": growth, "profitability": profitability, "cash_flow": cash_flow,
            "balance_sheet": balance_sheet, "capital_expenditure": capital_expenditure,
            "trends": {
                "growth_trend": growth_trend, "profitability_trend": profitability_trend,
                "cash_flow_trend": cash_flow_trend, "balance_sheet_trend": balance_sheet_trend,
            },
            "latest_changes": latest_changes,
            "data_quality": {
                "coverage": _round(present / denominator), "missing_fields": missing,
                "annual_period_count": len(annual_all), "latest_report_date": latest.get("report_date") if latest else None,
                "latest_announcement_date": latest.get("announcement_date") if latest else None,
                "cautions": cautions,
            },
        }


class FinancialForecastEngine:
    version = FORECAST_VERSION

    @staticmethod
    def _recent_growth(annual: list[dict[str, Any]]) -> list[float]:
        values: list[float] = []
        for previous, current in zip(annual[-3:-1], annual[-2:]):
            left, right = _number(previous.get("revenue")), _number(current.get("revenue"))
            if left is not None and right is not None and left > 0:
                values.append((right / left - 1) * 100)
        return values

    def build(self, feature: dict[str, Any], *, financial_sector: bool = False) -> dict[str, Any]:
        annual = list(feature.get("annual_periods_for_calculation") or [])
        base = {
            "forecast_version": self.version,
            "historical_cutoff": feature.get("as_of"),
            "feature_version": feature.get("feature_version"),
            "disclaimer": "情景推演，不代表确定预测。",
        }
        if financial_sector:
            return {**base, "status": "LIMITED", "scenarios": {}, "assumption_notes": [
                "FINANCIAL_SECTOR_METRIC_CAUTION",
                "传统企业收入、利润率、现金流和负债率模型不宜直接用于金融公司。",
            ]}
        if len(annual) < 4:
            return {**base, "status": "INSUFFICIENT_DATA", "scenarios": {}, "assumption_notes": ["至少需要 4 个完整年度用于最小情景推演。"]}
        latest = annual[-1]
        revenue, profit = _number(latest.get("revenue")), _number(latest.get("net_profit"))
        if revenue is None or revenue <= 0:
            return {**base, "status": "INSUFFICIENT_DATA", "scenarios": {}, "assumption_notes": ["缺少正的最新年度营收基数。"]}
        growth_inputs = self._recent_growth(annual)
        for key in ("revenue_cagr_3y", "revenue_cagr_5y"):
            item = dict(feature.get("growth", {}).get(key) or {})
            if item.get("status") == "READY" and _number(item.get("value")) is not None:
                growth_inputs.append(float(item["value"]))
        if not growth_inputs:
            return {**base, "status": "INSUFFICIENT_DATA", "scenarios": {}, "assumption_notes": ["营收长期与近期增长假设均不可用。"]}
        base_growth = _clip(statistics.median(growth_inputs), -10.0, 30.0)
        margins = [
            _number(row.get("net_margin")) if _number(row.get("net_margin")) is not None
            else ((_number(row.get("net_profit")) / _number(row.get("revenue")) * 100)
                  if (_number(row.get("revenue")) or 0) != 0 and _number(row.get("net_profit")) is not None else None)
            for row in annual[-3:]
        ]
        usable_margins = [value for value in margins if value is not None]
        profit_limited = profit is None or profit <= 0 or not usable_margins or min(usable_margins) <= 0
        base_margin = _clip(statistics.median(usable_margins), -20.0, 50.0) if usable_margins else None
        start_year = int(str(latest["report_date"])[:4]) + 1
        definitions = {
            "BEAR": {"label": "谨慎", "growth_delta": -8.0, "margin_delta": (-1.5, -2.0, -2.5)},
            "BASE": {"label": "基准", "growth_delta": 0.0, "margin_delta": (0.0, 0.0, 0.0)},
            "BULL": {"label": "乐观", "growth_delta": 6.0, "margin_delta": (1.0, 1.5, 2.0)},
        }
        scenarios: dict[str, Any] = {}
        for scenario, definition in definitions.items():
            current_revenue = revenue
            growth_assumptions: list[float] = []
            margin_assumptions: list[float | None] = []
            forecast: list[dict[str, Any]] = []
            for index in range(3):
                faded_base = base_growth * (1 - index * 0.1)
                growth = _clip(faded_base + float(definition["growth_delta"]), -20.0, 35.0)
                margin = _clip(base_margin + definition["margin_delta"][index], -20.0, 50.0) if base_margin is not None else None
                current_revenue *= 1 + growth / 100
                forecast.append({
                    "year": f"{start_year + index}E", "revenue": _round(current_revenue, 2),
                    "net_profit": None if profit_limited or margin is None else _round(current_revenue * margin / 100, 2),
                })
                growth_assumptions.append(_round(growth))
                margin_assumptions.append(_round(margin))
            scenarios[scenario] = {
                "scenario": scenario, "label": definition["label"],
                "revenue_growth_assumptions": growth_assumptions,
                "margin_assumptions": margin_assumptions,
                "forecast": forecast,
                "assumption_notes": [
                    "营收增速由历史 3Y/5Y CAGR 与最近年度增速中位数形成，并执行上下限约束。",
                    "净利润由情景营收乘以最近三年净利率中位数及有限幅度调整计算。",
                ],
            }
        status = "LIMITED" if profit_limited else ("READY" if feature.get("status") == "READY" else "PARTIAL")
        notes = [
            f"Base 初始营收增速 {base_growth:.2f}%，约束区间 [-10%, 30%]。",
            "Bear/Base/Bull 年营收增速统一约束在 [-20%, 35%]。",
            "净利率统一约束在 [-20%, 50%]。",
        ]
        if profit_limited:
            notes.append("历史净利润为负、非正或净利率不可用，未来净利润不生成精确数值。")
        return {**base, "status": status, "scenarios": scenarios, "assumption_notes": notes}

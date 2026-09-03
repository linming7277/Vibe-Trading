"""Normalized Earnings Reference V1 — read-only auxiliary for CIO valuation.

A deterministic "what could earnings look like if margins returned to a
historical, non-cherry-picked midpoint" reference.  NOT a forecast, NOT a
valuation method, does NOT touch ValuePriceZone / Low Value Pool / Focus.

All calculations are pure percentile arithmetic over completed annual periods;
the LLM never picks years, margins, or multiples.
"""

from __future__ import annotations

from typing import Any

NORMALIZED_EARNINGS_FORMULA_VERSION = "normalized-earnings-ref-v1"

MIN_SAMPLE_YEARS = 5
UPPER_MARGIN_CAP_MULTIPLIER = 3.0
# Industries where net-margin normalization is not meaningful.
_EXCLUDED_SECTOR_KEYWORDS = ("银行", "保险", "证券")


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Deterministic percentile: nearest-rank on sorted values."""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


class NormalizedEarningsReferenceService:
    """Compute a three-tier normalized-earnings reference for one company."""

    def reference(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        from src.financial_analysis.service import get_financial_analysis_service

        code = stock_code.upper()
        financial = get_financial_analysis_service().get_saved_resolved_analysis(code, as_of=as_of)
        annual = [dict(r) for r in list(financial.get("history") or [])
                  if str(r.get("period_type") or "") == "annual" and r.get("net_margin") is not None]

        # --- applicability gates (task §3) ---
        if len(annual) < MIN_SAMPLE_YEARS:
            return self._insufficient(code, as_of, f"完整年度不足{MIN_SAMPLE_YEARS}个（当前{len(annual)}个）")

        identity = dict(financial.get("identity") or {})
        sector = " ".join(str(identity.get(k) or "") for k in ("level1_name", "level2_name", "level3_name"))
        if any(keyword in sector for keyword in _EXCLUDED_SECTOR_KEYWORDS):
            return self._insufficient(code, as_of, "银行/保险/证券行业不适合净利率正常化")

        margins_raw = [float(r["net_margin"]) for r in annual]
        positive = [m for m in margins_raw if m > 0]
        if not positive:
            return self._insufficient(code, as_of, "历史全部亏损，无盈利中枢可正常化")

        # Consecutive multi-year losses with no recovery → unreliable midpoint
        loss_streak = 0
        max_loss_streak = 0
        for m in margins_raw:
            loss_streak = loss_streak + 1 if m < 0 else 0
            max_loss_streak = max(max_loss_streak, loss_streak)
        if max_loss_streak >= 3:
            return self._insufficient(code, as_of, f"连续{max_loss_streak}年亏损，无法形成可靠正常化参考")

        # --- percentile computation (all years preserved: no deletion) ---
        margins_sorted = sorted(margins_raw)
        p25 = _percentile(margins_sorted, 0.25)
        p50 = _percentile(margins_sorted, 0.50)
        raw_p75 = _percentile(margins_sorted, 0.75)
        upper_cap = p50 * UPPER_MARGIN_CAP_MULTIPLIER
        effective_upper = min(raw_p75, upper_cap)

        # --- GM Consistency Ceiling (task §7: aligned with Cycle Profit Scenario) ---
        # Applicability is determined from the RAW capped upper (P75 vs P50×3),
        # BEFORE the GM ceiling is applied — otherwise the GM constraint would
        # compress the range below the CYCLICAL threshold and hide the very
        # companies that need it most.
        from src.cycle_profit_scenario.service import _gross_margin_ceiling

        gm_result = _gross_margin_ceiling(annual)
        gm_ceiling = float(gm_result.get("ceiling_margin") or 0) if gm_result.get("status") == "READY" else None
        raw_upper = effective_upper  # pre-GM-ceiling, used for applicability
        if gm_ceiling is not None and (raw_upper - p25) > 5.0:
            effective_upper = min(effective_upper, gm_ceiling)

        # --- reference revenue: latest completed annual, NOT forecast ---
        latest_annual = annual[-1]
        reference_revenue = float(latest_annual.get("revenue") or 0)

        conservative_profit = reference_revenue * p25 / 100
        base_profit = reference_revenue * p50 / 100
        upper_profit = reference_revenue * effective_upper / 100

        # --- current market cap from identity ---
        market_cap = None
        try:
            market_cap = float(dict(identity.get("market_valuation") or {}).get("market_cap"))
        except (TypeError, ValueError):
            market_cap = None

        def _ref_pe(profit_yuan: float) -> float | None:
            """PE = market_cap(亿) / profit(亿).  None if profit <= 0."""
            profit_yi = profit_yuan / 1e8
            if profit_yi <= 0 or market_cap is None or market_cap <= 0:
                return None
            return round(market_cap / profit_yi, 1)

        # --- latest quarter anchor (NOT in the distribution) ---
        quarterly = [dict(r) for r in list(financial.get("history") or [])
                     if str(r.get("period_type") or "") != "annual" and r.get("net_margin") is not None]
        latest_quarter = None
        if quarterly:
            q = quarterly[-1]
            latest_quarter = {
                "report_date": str(q.get("report_date") or "")[:10],
                "net_margin": float(q.get("net_margin") or 0),
                "gross_margin": float(q.get("gross_margin") or 0) if q.get("gross_margin") is not None else None,
                "operating_cash_flow": float(q.get("operating_cash_flow") or 0),
                "position_vs_base": "高于" if float(q.get("net_margin") or 0) > p50 else
                                    ("低于" if float(q.get("net_margin") or 0) < p50 else "持平"),
            }

        # --- quality cautions (display-only, never adjusts profit) ---
        cautions: list[str] = []
        ocf = [float(r.get("operating_cash_flow") or 0) for r in annual]
        if ocf and latest_annual.get("capex"):
            capex_latest = float(latest_annual["capex"])
            if capex_latest > (ocf[-1] * 1.5 if ocf[-1] > 0 else capex_latest):
                cautions.append("公司仍处于较高资本投入阶段，需同时观察经营现金流与资本开支。")
        if len(ocf) >= 3:
            positive_profits = [float(r.get("net_profit") or 0) for r in annual]
            ratios = [o / p for o, p in zip(ocf, positive_profits) if p > 0 and o is not None]
            if ratios and (max(ratios) - min(ratios)) > 2.0:
                cautions.append("历史利润转化为现金的稳定性较弱，正常化盈利参考需谨慎使用。")
        if not cautions:
            cautions.append("正常化盈利只回答利润能力参考，不代表自由现金回报。")

        # --- stable-company low value added ---
        # Use the RAW (pre-GM-ceiling) range for applicability.
        raw_margin_range = raw_upper - p25
        applicability = "CYCLICAL_RELEVANT" if raw_margin_range > 5.0 else "LOW_VALUE_ADDED"

        # --- position judgment (task §11: honest, not narrative-forced) ---
        current_margin = margins_raw[-1]
        if abs(current_margin - p50) <= 1.0:
            position = "接近历史中位正常化水平"
        elif current_margin < p50:
            position = "低于历史中位正常化水平"
        else:
            position = "高于历史中位正常化水平"

        return {
            "status": "READY",
            "applicability": applicability,
            "stock_code": code, "research_as_of": as_of,
            "historical_years": [str(r.get("report_date") or "")[:4] for r in annual],
            "historical_net_margins": margins_raw,
            "sample_count": len(annual),
            "p25_margin": round(p25, 2), "p50_margin": round(p50, 2),
            "raw_p75_margin": round(raw_p75, 2),
            "upper_margin_cap": round(upper_cap, 2),
            "effective_upper_margin": round(effective_upper, 2),
            "upper_cap_applied": raw_p75 > upper_cap,
            "reference_revenue": reference_revenue,
            "reference_revenue_year": str(latest_annual.get("report_date") or "")[:4],
            "conservative_margin": round(p25, 2), "base_margin": round(p50, 2),
            "effective_upper": round(effective_upper, 2),
            "conservative_profit": round(conservative_profit, 2),
            "base_profit": round(base_profit, 2),
            "upper_profit": round(upper_profit, 2),
            "current_market_cap": market_cap,
            "conservative_reference_pe": _ref_pe(conservative_profit),
            "base_reference_pe": _ref_pe(base_profit),
            "upper_reference_pe": _ref_pe(upper_profit),
            "latest_quarter_anchor": latest_quarter,
            "current_margin_position": position,
            "quality_cautions": cautions,
            "data_gaps": ([] if market_cap else ["market_cap_missing"])
                        + ["deducted_net_profit_unavailable"],
            "formula_version": NORMALIZED_EARNINGS_FORMULA_VERSION,
        }

    @staticmethod
    def _insufficient(code: str, as_of: str | None, reason: str) -> dict[str, Any]:
        return {
            "status": "INSUFFICIENT", "applicability": "NOT_APPLICABLE",
            "stock_code": code, "research_as_of": as_of,
            "insufficient_reason": reason,
            "boss_message": "当前历史盈利结构不足以形成可靠的正常化盈利参考。",
            "formula_version": NORMALIZED_EARNINGS_FORMULA_VERSION,
        }


_service: NormalizedEarningsReferenceService | None = None


def get_normalized_earnings_reference_service() -> NormalizedEarningsReferenceService:
    global _service
    if _service is None:
        _service = NormalizedEarningsReferenceService()
    return _service

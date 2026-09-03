"""Cycle Profit Scenario V1 — three-tier future profit for cyclical-recovery companies.

Independent read-only service.  Does NOT touch the existing Financial Forecast
engine's `profit_limited` rule — that engine continues to own Revenue Scenarios.
This service consumes Revenue Forecast + Normalized Earnings + a per-company
Gross-Margin Consistency Ceiling to produce Bear/Base/Bull net profit tiers.

All arithmetic is deterministic; the LLM never picks margins or numbers.
"""

from __future__ import annotations

from typing import Any

from src.normalized_earnings.service import NORMALIZED_EARNINGS_FORMULA_VERSION

CYCLE_PROFIT_SCENARIO_VERSION = "cycle-profit-scenario-v1.0.0"
GM_CONSISTENCY_VERSION = "gross-margin-consistency-v1.0.0"


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def _gross_margin_ceiling(annual: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-company GM Consistency Ceiling (task §6).

    Deterministic band method: split the company's own GM history into
    low/mid/high bands; find which band the current GM anchor sits in; the
    ceiling is the max NM observed in that band.  No cross-company thresholds.
    """
    if len(annual) < 3:
        return {"status": "INSUFFICIENT", "reason": f"年度样本不足（{len(annual)}<3）"}

    gms = [float(r.get("gross_margin") or 0) for r in annual if r.get("gross_margin") is not None]
    if not gms:
        return {"status": "INSUFFICIENT", "reason": "无毛利率数据"}

    current_gm = float(annual[-1].get("gross_margin") or 0)
    gm_sorted = sorted(gms)
    p33 = _percentile(gm_sorted, 1 / 3)
    p67 = _percentile(gm_sorted, 2 / 3)

    # Classify the current GM into the company's own historical band.
    band = "LOW" if current_gm <= p33 else ("HIGH" if current_gm >= p67 else "MID")

    # Collect NMs from years in the same band.
    band_nms: list[float] = []
    band_years: list[str] = []
    for r in annual:
        gm = float(r.get("gross_margin") or 0)
        year_band = "LOW" if gm <= p33 else ("HIGH" if gm >= p67 else "MID")
        if year_band == band and r.get("net_margin") is not None:
            band_nms.append(float(r["net_margin"]))
            band_years.append(str(r.get("report_date") or "")[:4])

    if len(band_nms) < 2:
        return {
            "status": "INSUFFICIENT",
            "reason": f"当前毛利率区间（{band}）有效样本不足（{len(band_nms)}<2）",
            "band": band, "current_gm": round(current_gm, 2),
            "band_p33": round(p33, 2), "band_p67": round(p67, 2),
        }

    ceiling = max(band_nms)
    return {
        "status": "READY",
        "band": band,
        "current_gm": round(current_gm, 2),
        "band_p33": round(p33, 2), "band_p67": round(p67, 2),
        "ceiling_margin": round(ceiling, 2),
        "source_years": band_years,
        "source_nms": [round(n, 2) for n in band_nms],
        "formula_version": GM_CONSISTENCY_VERSION,
    }


class CycleProfitScenarioService:
    """Generate three profit scenarios for CYCLICAL_RELEVANT companies only."""

    def scenario(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        from src.financial_analysis.service import get_financial_analysis_service
        from src.normalized_earnings import get_normalized_earnings_reference_service

        code = stock_code.upper()

        # --- normalized earnings (also the applicability gate) ---
        norm = get_normalized_earnings_reference_service().reference(market, code, as_of=as_of)
        if norm.get("status") != "READY":
            return self._not_applicable(code, as_of, f"正常化盈利{norm.get('status')}：{norm.get('insufficient_reason', '')}")
        if norm.get("applicability") != "CYCLICAL_RELEVANT":
            return self._not_applicable(code, as_of, f"适用性={norm.get('applicability')}，不需要周期利润情景")

        # --- revenue forecast (existing engine, unmodified) ---
        financial = get_financial_analysis_service().get_saved_resolved_analysis(code, as_of=as_of)
        forecast = dict(financial.get("forecast") or {})
        scenarios = dict(forecast.get("scenarios") or {})
        base_scenario = dict(scenarios.get("BASE") or {})
        bear_scenario = dict(scenarios.get("BEAR") or {})
        base_rows = [dict(r) for r in list(base_scenario.get("forecast") or [])]
        bear_rows = [dict(r) for r in list(bear_scenario.get("forecast") or [])]
        if not base_rows or not bear_rows:
            return self._not_applicable(code, as_of, "收入情景缺失")

        # Use 2028 (last forecast year) — V1 only outputs the terminal year.
        base_revenue = float(base_rows[-1].get("revenue") or 0)
        bear_revenue = float(bear_rows[-1].get("revenue") or 0)
        terminal_year = str(base_rows[-1].get("year") or "")

        # --- historical annual data for GM guardrail ---
        annual = [dict(r) for r in list(financial.get("history") or [])
                  if str(r.get("period_type") or "") == "annual" and r.get("net_margin") is not None]
        if len(annual) < 5:
            return self._not_applicable(code, as_of, f"完整年度不足（{len(annual)}<5）")

        # --- GM Consistency Ceiling (per-company, deterministic) ---
        gm_result = _gross_margin_ceiling(annual)
        gm_ceiling = float(gm_result.get("ceiling_margin") or 0) if gm_result.get("status") == "READY" else None

        # --- margins ---
        bear_margin = float(norm["p25_margin"])
        base_margin = float(norm["p50_margin"])

        # Upper margin: normalized upper (P75 capped at P50*3) → further capped by GM ceiling.
        normalized_upper = float(norm["effective_upper_margin"])
        if gm_ceiling is not None:
            bull_margin = min(normalized_upper, gm_ceiling)
            gm_guardrail_applied = gm_ceiling < normalized_upper
        else:
            # GM ceiling insufficient → conservative fallback: use the current-year NM
            # as the safety ceiling (company-specific, not a hardcoded constant).
            current_nm = float(annual[-1].get("net_margin") or 0)
            bull_margin = min(normalized_upper, current_nm) if current_nm > 0 else base_margin
            gm_guardrail_applied = False

        # --- Double-Optimism Guardrail: bull uses BASE revenue, not BULL revenue ---
        bull_revenue = base_revenue  # Guardrail 1

        # --- profit ---
        bear_profit = bear_revenue * bear_margin / 100
        base_profit = base_revenue * base_margin / 100
        bull_profit = bull_revenue * bull_margin / 100

        # --- implied PE ---
        market_cap = norm.get("current_market_cap")

        def _implied_pe(profit: float) -> float | None:
            profit_yi = profit / 1e8
            if profit_yi <= 0 or market_cap is None or market_cap <= 0:
                return None
            return round(market_cap / profit_yi, 1)

        # --- latest quarter anchor ---
        quarterly = [dict(r) for r in list(financial.get("history") or [])
                     if str(r.get("period_type") or "") != "annual" and r.get("net_margin") is not None]
        latest_quarter = None
        if quarterly:
            q = quarterly[-1]
            latest_quarter = {
                "report_date": str(q.get("report_date") or "")[:10],
                "net_margin": round(float(q.get("net_margin") or 0), 2),
                "gross_margin": round(float(q.get("gross_margin") or 0), 2) if q.get("gross_margin") is not None else None,
                "net_profit": float(q.get("net_profit") or 0),
                "position_vs_p50": "高于" if float(q.get("net_margin") or 0) > base_margin else
                                   ("低于" if float(q.get("net_margin") or 0) < base_margin else "持平"),
            }

        # --- current earnings anchor ---
        current_earnings = {
            "year": str(annual[-1].get("report_date") or "")[:4],
            "net_profit": float(annual[-1].get("net_profit") or 0),
            "net_margin": round(float(annual[-1].get("net_margin") or 0), 2),
        }

        # --- quality cautions ---
        cautions: list[str] = []
        ocf = float(annual[-1].get("operating_cash_flow") or 0)
        capex = float(annual[-1].get("capex") or 0)
        if capex > ocf * 1.5:
            cautions.append("利润改善不代表自由现金回报同步改善，仍需结合经营现金流与资本开支观察。")
        ocf_ratios = []
        for r in annual:
            np_val = float(r.get("net_profit") or 0)
            ocf_val = float(r.get("operating_cash_flow") or 0)
            if np_val > 0 and ocf_val is not None:
                ocf_ratios.append(ocf_val / np_val)
        if ocf_ratios and (max(ocf_ratios) - min(ocf_ratios)) > 2.0:
            cautions.append("历史利润转化为现金的稳定性较弱，情景利润需谨慎使用。")

        # --- bull ≈ base explanation (task §9) ---
        bull_base_gap_pct = abs(bull_profit - base_profit) / max(abs(base_profit), 1) * 100 if base_profit else 0
        explanation = ""
        if bull_base_gap_pct < 15:
            explanation = (
                "当前毛利率仍处历史低位，因此即使采用较积极的盈利假设，利润率上沿仍受到历史毛利环境约束。"
                "当前乐观情景的主要上行空间来自收入增长，而不是假设利润率快速回到上一轮周期高点。")

        return {
            "status": "READY" if gm_result.get("status") == "READY" else "PARTIAL",
            "applicability": "CYCLICAL_RELEVANT",
            "stock_code": code, "research_as_of": as_of,
            "terminal_year": terminal_year,
            "forecast_status": forecast.get("status"),
            "bear_revenue": bear_revenue, "base_revenue": base_revenue, "bull_revenue": bull_revenue,
            "bear_revenue_source": "BEAR", "base_revenue_source": "BASE",
            "bull_revenue_source": "BASE",  # double-optimism guardrail
            "bear_margin": round(bear_margin, 2), "base_margin": round(base_margin, 2),
            "bull_margin": round(bull_margin, 2),
            "bear_profit": round(bear_profit, 2), "base_profit": round(base_profit, 2),
            "bull_profit": round(bull_profit, 2),
            "bear_implied_pe": _implied_pe(bear_profit),
            "base_implied_pe": _implied_pe(base_profit),
            "bull_implied_pe": _implied_pe(bull_profit),
            "gross_margin_guardrail": gm_result,
            "gm_guardrail_applied": gm_guardrail_applied,
            "double_optimism_guardrail": {"applied": True, "description": "乐观情景使用基准收入而非乐观收入"},
            "normalized_upper_margin": normalized_upper,
            "current_earnings_anchor": current_earnings,
            "latest_quarter_anchor": latest_quarter,
            "bull_base_gap_pct": round(bull_base_gap_pct, 1),
            "bull_explanation": explanation,
            "quality_cautions": cautions,
            "data_gaps": list(norm.get("data_gaps") or []),
            "formula_version": CYCLE_PROFIT_SCENARIO_VERSION,
            "gm_formula_version": GM_CONSISTENCY_VERSION,
            "normalized_earnings_version": NORMALIZED_EARNINGS_FORMULA_VERSION,
        }

    @staticmethod
    def _not_applicable(code: str, as_of: str | None, reason: str) -> dict[str, Any]:
        return {
            "status": "NOT_APPLICABLE",
            "applicability": "NOT_APPLICABLE",
            "stock_code": code, "research_as_of": as_of,
            "not_applicable_reason": reason,
            "formula_version": CYCLE_PROFIT_SCENARIO_VERSION,
        }


_service: CycleProfitScenarioService | None = None


def get_cycle_profit_scenario_service() -> CycleProfitScenarioService:
    global _service
    if _service is None:
        _service = CycleProfitScenarioService()
    return _service

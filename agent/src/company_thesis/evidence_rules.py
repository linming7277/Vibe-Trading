"""Explicit, deterministic rules for Company Thesis Evidence extraction V1.

These are deliberately data rules rather than investment recommendations.  A rule
only describes a measurable change in an already persisted snapshot and whether
that change supports, challenges, or is neutral to the current Company Thesis.
"""

from __future__ import annotations

from dataclasses import dataclass


EXTRACTOR_VERSION = "value-thesis-evidence-extractor-v1.0.0"


@dataclass(frozen=True)
class ChangeRule:
    rule_id: str
    metric_name: str
    evidence_type: str
    positive_threshold: float
    negative_threshold: float
    label: str
    improvement_direction: int = 1


# `latest_changes.change_percent` and research YoY deltas are percentage points
# in the currently persisted V1 payloads.  The thresholds are intentionally
# conservative to avoid manufacturing evidence out of normal reporting noise.
FINANCIAL_CHANGE_RULES = (
    ChangeRule("financial.revenue.material_change", "revenue", "FINANCIAL", 15.0, -15.0, "营业收入"),
    ChangeRule("financial.net_profit.material_change", "net_profit", "FINANCIAL", 15.0, -15.0, "净利润"),
    ChangeRule("financial.operating_cash_flow.material_change", "operating_cash_flow", "FINANCIAL", 20.0, -20.0, "经营现金流"),
    ChangeRule("financial.roe.material_change", "roe", "FINANCIAL", 10.0, -10.0, "净资产收益率"),
    ChangeRule("financial.debt_ratio.material_change", "debt_ratio", "FINANCIAL", 10.0, -10.0, "资产负债率", -1),
)

# These fields are read only from `financial_latest` / `financial_previous` in
# the structured research snapshot, never from its free-text research payload.
RESEARCH_CHANGE_RULES = (
    ChangeRule("research.revenue_yoy.change", "revenue_yoy", "BUSINESS", 15.0, -15.0, "营业收入同比"),
    ChangeRule("research.net_profit_yoy.change", "net_profit_yoy", "BUSINESS", 15.0, -15.0, "净利润同比"),
    ChangeRule("research.roe.change", "roe", "BUSINESS", 5.0, -5.0, "净资产收益率"),
    ChangeRule("research.debt_ratio.change", "debt_ratio", "RISK", 5.0, -5.0, "资产负债率", -1),
)

# A percentile movement is evidence of a valuation *position* change only.  It
# is always NEUTRAL in V1: no buy/sell/target-price language or semantics.
VALUATION_PERCENTILE_CHANGE = 10.0

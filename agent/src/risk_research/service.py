"""Deterministic, read-only company risk research.

This module is deliberately a projection over persisted PIT inputs.  It never
prepares financial data, runs business analysis, refreshes a review, mutates a
Thesis, or changes any Value Line pool.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from src.business_research.store import BusinessResearchStore
from src.disclosure_materials.store import DisclosureMaterialStore
from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.company_thesis.review_store import CompanyThesisReviewRepository
from src.company_thesis.store import CompanyThesisRepository
from src.financial_analysis.service import FinancialAnalysisService
from src.financial_analysis.store import FinancialAnalysisStore
from src.level3_leaders import get_level3_leader_service
from src.research_workspace.store import normalize_market, normalize_symbol
from src.value_price_zones import ValuePriceZoneService, get_value_price_zone_service


FORMULA_VERSION = "risk-research-v2.0.0"
ACTIVE_LEADER_STATES = {"ACTIVE", "NEW", "REENTERED"}
UNDERVALUED = {"UNDERVALUED", "DEEPLY_UNDERVALUED"}
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# These thresholds are calibrated from the 2026-08-26 current L3 pool before
# this rule set was introduced.  Among 662 companies with comparable inputs,
# the 10th percentiles were 0.915 for current ratio and 0.186 for cash
# coverage; the corresponding one-period changes were -22.0% and -41.8%.
# They are deliberately rounded to conservative, reproducible rule values,
# rather than imported from a generic textbook liquidity rule.
CURRENT_RATIO_LOW = 0.90
CURRENT_RATIO_DECLINE_PCT = -20.0
CASH_COVERAGE_LOW = 0.18
CASH_COVERAGE_DECLINE_PCT = -40.0

# Current L3 distribution checks on the same date: the 90th percentile
# changes were +9.6ppt for interest-bearing debt ratio, +4.8ppt for debt
# ratio, and +91.5% for capex.  These are only deterministic screening
# thresholds, never a credit-rating or trading recommendation.
INTEREST_DEBT_INCREASE_PPT = 8.0
DEBT_RATIO_INCREASE_PPT = 2.0
NON_CURRENT_LIABILITY_GROWTH_PCT = 20.0
CAPEX_GROWTH_PCT = 50.0
CAPEX_TO_OCF_STRETCH = 1.80
CAPEX_TO_REVENUE_STRETCH = 0.26


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _target_date(as_of: str | None) -> str | None:
    if not as_of:
        return None
    target = str(as_of)[:10]
    date.fromisoformat(target)
    return target


def _available(row: dict[str, Any] | None, as_of: str | None, *, data_key: str = "data_as_of") -> bool:
    if not row:
        return False
    if not as_of:
        return True
    created = str(row.get("created_at") or "")[:10]
    data_as_of = str(row.get(data_key) or row.get("source_data_as_of") or "")[:10]
    return bool(created and created <= as_of and (not data_as_of or data_as_of <= as_of))


class RiskResearchService:
    def __init__(
        self, *, financial_store: FinancialAnalysisStore | None = None,
        business_store: BusinessResearchStore | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        evidence_repository: CompanyThesisEvidenceRepository | None = None,
        review_repository: CompanyThesisReviewRepository | None = None,
        disclosure_store: DisclosureMaterialStore | None = None,
        price_zone_service: ValuePriceZoneService | None = None,
        leader_pool_reader: Callable[[str | None], dict[str, Any] | None] | None = None,
    ) -> None:
        self.price_zone_service = price_zone_service or get_value_price_zone_service()
        self.financial_store = financial_store or FinancialAnalysisStore()
        db_path = getattr(self.financial_store, "db_path", None)
        self.business_store = business_store or BusinessResearchStore(db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(db_path)
        self.evidence_repository = evidence_repository or CompanyThesisEvidenceRepository(db_path)
        self.review_repository = review_repository or CompanyThesisReviewRepository(db_path)
        self.disclosure_store = disclosure_store or DisclosureMaterialStore(db_path)
        self.leader_pool_reader = leader_pool_reader or self._read_leader_pool
        self._owned = {
            "financial": financial_store is None, "business": business_store is None,
            "thesis": thesis_repository is None, "evidence": evidence_repository is None,
            "review": review_repository is None,
            "disclosure": disclosure_store is None,
        }

    def close(self) -> None:
        for name, value in (("financial", self.financial_store), ("business", self.business_store),
                            ("thesis", self.thesis_repository), ("evidence", self.evidence_repository),
                            ("review", self.review_repository), ("disclosure", self.disclosure_store)):
            if self._owned[name] and hasattr(value, "close"):
                value.close()

    @staticmethod
    def _read_leader_pool(as_of: str | None) -> dict[str, Any] | None:
        """Read an already materialized L3 pool; never call ensure_current_pool()."""
        service = get_level3_leader_service()
        candidates = service.store.list_pools(limit=200)
        if as_of:
            candidates = [pool for pool in candidates if str(pool.get("as_of") or "")[:10] <= as_of]
        if not candidates:
            return None
        return service.get_pool(str(candidates[0]["id"]), include_inactive=True)

    @staticmethod
    def _period_label(row: dict[str, Any]) -> str:
        report_date = str(row.get("report_date") or "")[:10]
        if report_date.endswith("-12-31"):
            return report_date[:4]
        if report_date.endswith("-03-31"):
            return f"{report_date[:4]}Q1"
        if report_date.endswith("-06-30"):
            return f"{report_date[:4]}H1"
        if report_date.endswith("-09-30"):
            return f"{report_date[:4]}Q3"
        return report_date or "UNKNOWN"

    @staticmethod
    def _same_period_history(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [dict(row) for row in snapshot.get("history") or [] if isinstance(row, dict)]
        # The Financial Feature Engine already filters on announcement_date and
        # produces comparable annual rows.  We only compare rows of one type.
        # A newest Q1 alone must not discard five comparable annual reports.
        latest = rows[-1] if rows else None
        if not latest:
            return []
        latest_month_day = str(latest.get("report_date") or "")[5:10]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get("report_date") or "")[5:10], []).append(row)
        if len(groups.get(latest_month_day, [])) >= 2:
            return groups[latest_month_day]
        return max(groups.values(), key=len)

    @staticmethod
    def _source_keys(snapshot: dict[str, Any], rows: list[dict[str, Any]], metric: str) -> list[str]:
        try:
            manifest = FinancialAnalysisService._evidence_manifest(snapshot)
        except (KeyError, TypeError):
            manifest = {}
        labels = {
            "revenue": "REVENUE", "net_profit": "NET_PROFIT", "operating_cash_flow": "OCF",
            "gross_margin": "GROSS_MARGIN", "roe": "ROE", "debt_ratio": "DEBT_RATIO",
            "accounts_receivable": "ACCOUNTS_RECEIVABLE", "inventory": "INVENTORY",
            "cash_and_equivalents": "CASH_AND_EQUIVALENTS", "current_assets": "CURRENT_ASSETS",
            "current_liabilities": "CURRENT_LIABILITIES",
            "non_current_liabilities": "NON_CURRENT_LIABILITIES",
            "interest_bearing_debt_ratio": "INTEREST_BEARING_DEBT_RATIO", "capex": "CAPEX",
        }
        label = labels.get(metric)
        if not label:
            return []
        keys = [f"FIN_{label}_{RiskResearchService._period_label(row)}" for row in rows]
        return [key for key in keys if not manifest or key in manifest]

    @staticmethod
    def _risk(*, risk_type: str, severity: str, status: str, text: str, why: str,
              sources: list[str] | None = None, evidence_ids: list[str] | None = None,
              watch: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        sources, evidence_ids = list(dict.fromkeys(sources or [])), list(dict.fromkeys(evidence_ids or []))
        if status in {"CONFIRMED", "WATCH"} and not (sources or evidence_ids):
            severity, status = "LOW", "UNKNOWN"
        return {
            "risk_type": risk_type, "severity": severity, "status": status, "text": text,
            "why_it_matters": why, "source_keys": sources, "evidence_ids": evidence_ids,
            "watch_item": watch, "metadata": dict(metadata or {}),
        }

    @staticmethod
    def _financial_traceability(*, current: dict[str, Any], previous: dict[str, Any] | None,
                                current_values: dict[str, float | None],
                                previous_values: dict[str, float | None] | None,
                                derived_metrics: dict[str, float | None]) -> dict[str, Any]:
        """Attach the PIT facts and formula inputs behind a financial risk.

        This is intentionally raw deterministic data rather than a model
        explanation so the owner-facing "view evidence" path can reconstruct
        the observation without looking at today's financial snapshot.
        """
        return {
            "formula_version": FORMULA_VERSION,
            "report_date": current.get("report_date"),
            "announcement_date": current.get("announcement_date"),
            "data_as_of": current.get("data_as_of") or current.get("announcement_date"),
            "previous_report_date": (previous or {}).get("report_date"),
            "previous_announcement_date": (previous or {}).get("announcement_date"),
            "current_values": current_values,
            "previous_values": previous_values or {},
            "derived_metrics": derived_metrics,
        }

    @staticmethod
    def _quality(snapshot: dict[str, Any] | None) -> str:
        if not snapshot:
            return "MISSING"
        value = str(snapshot.get("feature_status") or (snapshot.get("feature") or {}).get("status") or "PARTIAL").upper()
        return "READY" if value == "READY" else "PARTIAL" if value in {"PARTIAL", "LIMITED"} else "MISSING"

    def _financial_risks(self, snapshot: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str], str, str]:
        if not snapshot:
            return [], ["FINANCIAL_HISTORY", "REVENUE", "NET_PROFIT", "OPERATING_CASH_FLOW", "ROE", "DEBT_RATIO"], "MISSING", "MISSING"
        quality = self._quality(snapshot)
        rows = self._same_period_history(snapshot)
        if len(rows) < 2:
            return [], ["COMPARABLE_FINANCIAL_PERIODS"], quality, "UNKNOWN"
        risks: list[dict[str, Any]] = []
        missing: list[str] = []
        prior, latest = rows[-2], rows[-1]

        def change(metric: str, left: dict[str, Any] = prior, right: dict[str, Any] = latest) -> float | None:
            old, new = _number(left.get(metric)), _number(right.get(metric))
            if old is None or new is None or old == 0:
                return None
            return (new - old) / abs(old) * 100

        def value_pairs(metric: str, *, percentage_points: bool = False) -> list[dict[str, Any]]:
            pairs: list[dict[str, Any]] = []
            for left, right in zip(rows, rows[1:]):
                old, new = _number(left.get(metric)), _number(right.get(metric))
                if old is None or new is None or (not percentage_points and old == 0):
                    continue
                pairs.append({
                    "previous": left, "current": right, "previous_value": old, "current_value": new,
                    "change": new - old if percentage_points else (new - old) / abs(old) * 100,
                })
            return pairs

        def ratio_pairs(numerator: str, denominator: str) -> list[dict[str, Any]]:
            pairs: list[dict[str, Any]] = []
            for left, right in zip(rows, rows[1:]):
                old_num, old_den = _number(left.get(numerator)), _number(left.get(denominator))
                new_num, new_den = _number(right.get(numerator)), _number(right.get(denominator))
                if old_num is None or old_den is None or new_num is None or new_den is None or old_den <= 0 or new_den <= 0:
                    continue
                old_ratio, new_ratio = old_num / old_den, new_num / new_den
                pairs.append({
                    "previous": left, "current": right, "previous_value": old_ratio, "current_value": new_ratio,
                    "change": (new_ratio - old_ratio) / abs(old_ratio) * 100 if old_ratio else None,
                    "previous_numerator": old_num, "previous_denominator": old_den,
                    "current_numerator": new_num, "current_denominator": new_den,
                })
            return pairs

        def source_rows(pair: dict[str, Any], *metrics: str) -> list[str]:
            return list(dict.fromkeys([
                key for metric in metrics
                for key in self._source_keys(snapshot, [pair["previous"], pair["current"]], metric)
            ]))

        def trend_risk(metric: str, kind: str, threshold: float, title: str, why: str, watch: str) -> None:
            changes = value_pairs(metric)
            if not changes:
                missing.append(metric.upper())
                return
            current = changes[-1]
            hits = sum(float(item["change"]) <= threshold for item in changes[-2:])
            if float(current["change"]) <= threshold:
                high = hits >= 2 and quality == "READY"
                risks.append(self._risk(
                    risk_type=kind, severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                    text=f"{title}较上一可比报告期下降约 {abs(float(current['change'])):.1f}%。", why=why,
                    sources=source_rows(current, metric), watch=watch,
                    metadata=self._financial_traceability(
                        current=current["current"], previous=current["previous"],
                        current_values={metric: current["current_value"]}, previous_values={metric: current["previous_value"]},
                        derived_metrics={f"{metric}_change_percent": current["change"]},
                    ),
                ))

        trend_risk("revenue", "FINANCIAL_REVENUE_DECLINE", -15, "营业收入", "收入连续走弱可能反映核心业务承压。", "观察下一份同口径报告的收入变化")
        trend_risk("net_profit", "FINANCIAL_PROFIT_DECLINE", -15, "净利润", "利润走弱会削弱公司内生投入和抗风险能力。", "观察下一份同口径报告的净利润变化")
        # Gross margin and ROE use percentage-point differences, never the feature's relative percentage change.
        for metric, kind, title, one_threshold, two_threshold, why, watch in (
            ("gross_margin", "FINANCIAL_MARGIN_DECLINE", "毛利率", -3, -5, "毛利率下降可能压缩盈利质量。", "观察产品结构和毛利率是否继续下降"),
            ("roe", "FINANCIAL_ROE_DECLINE", "ROE", -5, -10, "ROE下降表示使用股东资金创造利润的效率在走弱。", "观察下一份报告的ROE和盈利能力"),
        ):
            pairs = value_pairs(metric, percentage_points=True)
            if not pairs:
                missing.append(metric.upper())
                continue
            current = pairs[-1]
            cumulative = None
            if len(pairs) >= 2:
                before = float(pairs[-2]["previous_value"])
                cumulative = float(current["current_value"]) - before
            if float(current["change"]) <= one_threshold:
                high = cumulative is not None and cumulative <= two_threshold and quality == "READY"
                risks.append(self._risk(
                    risk_type=kind, severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                    text=f"{title}较上一可比报告期下降约 {abs(float(current['change'])):.1f} 个百分点。", why=why,
                    sources=source_rows(current, metric), watch=watch,
                    metadata=self._financial_traceability(
                        current=current["current"], previous=current["previous"],
                        current_values={metric: current["current_value"]}, previous_values={metric: current["previous_value"]},
                        derived_metrics={f"{metric}_change_percentage_points": current["change"], f"{metric}_two_period_change_percentage_points": cumulative},
                    ),
                ))

        trend_risk("operating_cash_flow", "FINANCIAL_CASH_FLOW", -20, "经营现金流", "真正收到的经营现金若持续变少，利润质量和后续投入能力需要复核。", "观察经营现金流和现金转换情况")
        profit_change, cash_change = change("net_profit"), change("operating_cash_flow")
        if profit_change is None or cash_change is None:
            missing.append("PROFIT_CASH_CONVERSION")
        elif profit_change >= 15 and cash_change <= -20:
            risks.append(self._risk(
                risk_type="FINANCIAL_PROFIT_CASH_DIVERGENCE", severity="MEDIUM", status="WATCH",
                text=f"净利润增长约 {profit_change:.1f}%，但经营现金流下降约 {abs(cash_change):.1f}%。",
                why="利润增长没有同步转化为经营现金，需要继续确认利润质量。",
                sources=self._source_keys(snapshot, [prior, latest], "net_profit") + self._source_keys(snapshot, [prior, latest], "operating_cash_flow"),
                watch="观察下一份同口径报告的利润与经营现金流是否同步",
                metadata=self._financial_traceability(
                    current=latest, previous=prior,
                    current_values={"net_profit": _number(latest.get("net_profit")), "operating_cash_flow": _number(latest.get("operating_cash_flow"))},
                    previous_values={"net_profit": _number(prior.get("net_profit")), "operating_cash_flow": _number(prior.get("operating_cash_flow"))},
                    derived_metrics={"net_profit_change_percent": profit_change, "operating_cash_flow_change_percent": cash_change},
                ),
            ))
        debt_pairs = value_pairs("debt_ratio", percentage_points=True)
        if not debt_pairs:
            missing.append("DEBT_RATIO")
        else:
            current = debt_pairs[-1]
            cumulative = float(current["current_value"]) - float(debt_pairs[-2]["previous_value"]) if len(debt_pairs) >= 2 else None
            if float(current["change"]) >= 5:
                high = cumulative is not None and cumulative >= 10 and quality == "READY"
                risks.append(self._risk(
                    risk_type="FINANCIAL_DEBT_RATIO", severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                    text=f"资产负债率较上一可比报告期上升约 {float(current['change']):.1f} 个百分点。",
                    why="负债压力上升可能压缩经营调整空间。", sources=source_rows(current, "debt_ratio"),
                    watch="观察负债率、经营现金流和偿债压力",
                    metadata=self._financial_traceability(
                        current=current["current"], previous=current["previous"],
                        current_values={"debt_ratio": current["current_value"]}, previous_values={"debt_ratio": current["previous_value"]},
                        derived_metrics={"debt_ratio_change_percentage_points": current["change"], "debt_ratio_two_period_change_percentage_points": cumulative},
                    ),
                ))

        # Working-capital risk: compare same-period receivable/inventory growth
        # with revenue growth.  A balance increase alone is not an adverse fact.
        def working_capital_risk(metric: str, risk_type: str, title: str, missing_key: str, why: str, watch: str) -> None:
            subject = {str(item["current"].get("report_date")): item for item in value_pairs(metric)}
            revenue = {str(item["current"].get("report_date")): item for item in value_pairs("revenue")}
            comparisons = [
                {"subject": subject[key], "revenue": revenue[key]}
                for key in subject.keys() & revenue.keys()
            ]
            comparisons.sort(key=lambda item: str(item["subject"]["current"].get("report_date") or ""))
            if not comparisons:
                missing.append(missing_key)
                return
            current = comparisons[-1]
            subject_change, revenue_change = float(current["subject"]["change"]), float(current["revenue"]["change"])
            divergence = subject_change >= 20 and subject_change - revenue_change >= 15
            if not divergence:
                return
            persistent = len(comparisons) >= 2 and all(
                float(item["subject"]["change"]) >= 20 and float(item["subject"]["change"]) - float(item["revenue"]["change"]) >= 15
                for item in comparisons[-2:]
            )
            revenue_declining = revenue_change < 0
            high = quality == "READY" and (persistent or revenue_declining)
            subject_pair = current["subject"]
            risks.append(self._risk(
                risk_type=risk_type, severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                text=f"{title}增长约 {subject_change:.1f}%，明显快于收入增长约 {revenue_change:.1f}%，需要关注{'收入回款质量' if metric == 'accounts_receivable' else '库存消化和经营效率'}。",
                why=why, sources=source_rows(subject_pair, metric) + source_rows(current["revenue"], "revenue"), watch=watch,
                metadata=self._financial_traceability(
                    current=subject_pair["current"], previous=subject_pair["previous"],
                    current_values={metric: subject_pair["current_value"], "revenue": current["revenue"]["current_value"]},
                    previous_values={metric: subject_pair["previous_value"], "revenue": current["revenue"]["previous_value"]},
                    derived_metrics={f"{metric}_growth_percent": subject_change, "revenue_growth_percent": revenue_change, "growth_gap_percentage_points": subject_change - revenue_change},
                ),
            ))

        working_capital_risk("accounts_receivable", "FINANCIAL_RECEIVABLE", "应收账款", "ACCOUNTS_RECEIVABLE_COMPARISON", "应收增长持续快于收入时，经营现金回收和信用风险需要进一步核对。", "观察应收账款、经营现金流和后续减值情况")
        working_capital_risk("inventory", "FINANCIAL_INVENTORY", "存货", "INVENTORY_COMPARISON", "存货增长持续快于收入时，库存消化与经营效率需要进一步核对。", "观察存货、收入、毛利率和后续周转情况")

        current_ratio_pairs = ratio_pairs("current_assets", "current_liabilities")
        cash_coverage_pairs = ratio_pairs("cash_and_equivalents", "current_liabilities")
        if not current_ratio_pairs:
            missing.append("CURRENT_RATIO")
        if not cash_coverage_pairs:
            missing.append("CASH_COVERAGE")
        liquidity_pair = current_ratio_pairs[-1] if current_ratio_pairs else None
        cash_pair = cash_coverage_pairs[-1] if cash_coverage_pairs else None
        liquidity_watch = bool(liquidity_pair and float(liquidity_pair["current_value"]) <= CURRENT_RATIO_LOW and (
            liquidity_pair["change"] is None or float(liquidity_pair["change"]) <= CURRENT_RATIO_DECLINE_PCT
        ))
        cash_watch = bool(cash_pair and float(cash_pair["current_value"]) <= CASH_COVERAGE_LOW and (
            cash_pair["change"] is None or float(cash_pair["change"]) <= CASH_COVERAGE_DECLINE_PCT
        ))
        if liquidity_watch or cash_watch:
            persistent_liquidity = len(current_ratio_pairs) >= 2 and all(
                float(item["current_value"]) <= CURRENT_RATIO_LOW and item["change"] is not None and float(item["change"]) <= CURRENT_RATIO_DECLINE_PCT
                for item in current_ratio_pairs[-2:]
            )
            persistent_cash = len(cash_coverage_pairs) >= 2 and all(
                float(item["current_value"]) <= CASH_COVERAGE_LOW and item["change"] is not None and float(item["change"]) <= CASH_COVERAGE_DECLINE_PCT
                for item in cash_coverage_pairs[-2:]
            )
            high = quality == "READY" and (persistent_liquidity or persistent_cash)
            pair = liquidity_pair or cash_pair
            assert pair is not None
            details: list[str] = []
            if liquidity_watch and liquidity_pair:
                details.append(f"流动比率为 {float(liquidity_pair['current_value']):.2f}")
            if cash_watch and cash_pair:
                details.append(f"现金覆盖为 {float(cash_pair['current_value']):.2f}")
            kind = "FINANCIAL_LIQUIDITY" if liquidity_watch else "FINANCIAL_CASH_COVERAGE"
            sources = source_rows(pair, "current_assets", "current_liabilities") if liquidity_pair else []
            if cash_pair:
                sources += source_rows(cash_pair, "cash_and_equivalents", "current_liabilities")
            current_values = {
                "current_ratio": float(liquidity_pair["current_value"]) if liquidity_pair else None,
                "cash_coverage": float(cash_pair["current_value"]) if cash_pair else None,
                "current_assets": _number(pair["current"].get("current_assets")),
                "current_liabilities": _number(pair["current"].get("current_liabilities")),
                "cash_and_equivalents": _number(pair["current"].get("cash_and_equivalents")),
            }
            previous_values = {
                "current_ratio": float(liquidity_pair["previous_value"]) if liquidity_pair else None,
                "cash_coverage": float(cash_pair["previous_value"]) if cash_pair else None,
                "current_assets": _number(pair["previous"].get("current_assets")),
                "current_liabilities": _number(pair["previous"].get("current_liabilities")),
                "cash_and_equivalents": _number(pair["previous"].get("cash_and_equivalents")),
            }
            risks.append(self._risk(
                risk_type=kind, severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                text=f"{'、'.join(details)}且较上一可比期走弱，需要关注短期流动性安排。",
                why="流动资产和现金对流动负债的覆盖下降时，经营波动或融资安排变化更容易传导为流动性压力。",
                sources=list(dict.fromkeys(sources)), watch="观察流动资产、现金、流动负债和经营现金流是否改善",
                metadata=self._financial_traceability(
                    current=pair["current"], previous=pair["previous"], current_values=current_values, previous_values=previous_values,
                    derived_metrics={
                        "current_ratio_change_percent": liquidity_pair["change"] if liquidity_pair else None,
                        "cash_coverage_change_percent": cash_pair["change"] if cash_pair else None,
                        "liquidity_distribution_threshold": CURRENT_RATIO_LOW,
                        "cash_coverage_distribution_threshold": CASH_COVERAGE_LOW,
                    },
                ),
            ))

        interest_pairs = value_pairs("interest_bearing_debt_ratio", percentage_points=True)
        if not interest_pairs:
            missing.append("INTEREST_BEARING_DEBT_RATIO")
        else:
            current = interest_pairs[-1]
            interest_change = float(current["change"])
            debt_ratio_change = None
            debt_pair_by_date = {str(item["current"].get("report_date")): item for item in debt_pairs}
            matching_debt = debt_pair_by_date.get(str(current["current"].get("report_date")))
            if matching_debt:
                debt_ratio_change = float(matching_debt["change"])
            long_debt_change = change("non_current_liabilities", current["previous"], current["current"])
            ocf_change = change("operating_cash_flow", current["previous"], current["current"])
            if interest_change >= INTEREST_DEBT_INCREASE_PPT:
                balance_sheet_pressure = bool(
                    (debt_ratio_change is not None and debt_ratio_change >= DEBT_RATIO_INCREASE_PPT)
                    or (long_debt_change is not None and long_debt_change >= NON_CURRENT_LIABILITY_GROWTH_PCT)
                )
                high = quality == "READY" and balance_sheet_pressure and ocf_change is not None and ocf_change <= -20
                extra = "，且资产负债或长期负债同步上升、经营现金流下降" if high else ""
                risks.append(self._risk(
                    risk_type="FINANCIAL_INTEREST_DEBT", severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                    text=f"带息债务率较上一可比报告期上升约 {interest_change:.1f} 个百分点{extra}，需要关注债务压力与现金创造能力。",
                    why="带息债务上升本身不等于风险，但若现金创造走弱，融资与偿债压力可能增加。",
                    sources=source_rows(current, "interest_bearing_debt_ratio", "debt_ratio", "non_current_liabilities", "operating_cash_flow"),
                    watch="观察带息债务率、资产负债率、长期负债和经营现金流的后续变化",
                    metadata=self._financial_traceability(
                        current=current["current"], previous=current["previous"],
                        current_values={"interest_bearing_debt_ratio": current["current_value"], "debt_ratio": _number(current["current"].get("debt_ratio")), "non_current_liabilities": _number(current["current"].get("non_current_liabilities")), "operating_cash_flow": _number(current["current"].get("operating_cash_flow"))},
                        previous_values={"interest_bearing_debt_ratio": current["previous_value"], "debt_ratio": _number(current["previous"].get("debt_ratio")), "non_current_liabilities": _number(current["previous"].get("non_current_liabilities")), "operating_cash_flow": _number(current["previous"].get("operating_cash_flow"))},
                        derived_metrics={"interest_bearing_debt_ratio_change_percentage_points": interest_change, "debt_ratio_change_percentage_points": debt_ratio_change, "non_current_liabilities_change_percent": long_debt_change, "operating_cash_flow_change_percent": ocf_change},
                    ),
                ))

        capex_pairs = value_pairs("capex")
        if not capex_pairs:
            missing.append("CAPEX_PRESSURE")
        else:
            current = capex_pairs[-1]
            capex_change = float(current["change"])
            ocf_change = change("operating_cash_flow", current["previous"], current["current"])
            current_capex, current_ocf, current_revenue = (
                float(current["current_value"]), _number(current["current"].get("operating_cash_flow")), _number(current["current"].get("revenue")),
            )
            capex_to_ocf = current_capex / current_ocf if current_ocf is not None and current_ocf > 0 else None
            capex_to_revenue = current_capex / current_revenue if current_revenue is not None and current_revenue > 0 else None
            stretched = bool((capex_to_ocf is not None and capex_to_ocf >= CAPEX_TO_OCF_STRETCH) or (
                capex_to_revenue is not None and capex_to_revenue >= CAPEX_TO_REVENUE_STRETCH
            ))
            if (capex_change >= CAPEX_GROWTH_PCT or stretched) and ocf_change is not None and ocf_change <= -20:
                persistent = len(capex_pairs) >= 2 and all(
                    float(item["change"]) >= CAPEX_GROWTH_PCT and change("operating_cash_flow", item["previous"], item["current"]) is not None
                    and float(change("operating_cash_flow", item["previous"], item["current"]) or 0) <= -20
                    for item in capex_pairs[-2:]
                )
                high = quality == "READY" and persistent and stretched
                risks.append(self._risk(
                    risk_type="FINANCIAL_CAPEX_PRESSURE", severity="HIGH" if high else "MEDIUM", status="CONFIRMED" if high else "WATCH",
                    text="资本投入增加，但经营现金流承压，需要关注扩张资金压力。",
                    why="资本投入增长本身不是风险；当投入强度较高且经营现金流下降时，融资和现金调度压力需要复核。",
                    sources=source_rows(current, "capex", "operating_cash_flow", "revenue"),
                    watch="观察资本开支、经营现金流、收入增长和融资安排是否匹配",
                    metadata=self._financial_traceability(
                        current=current["current"], previous=current["previous"],
                        current_values={"capex": current_capex, "operating_cash_flow": current_ocf, "revenue": current_revenue},
                        previous_values={"capex": current["previous_value"], "operating_cash_flow": _number(current["previous"].get("operating_cash_flow")), "revenue": _number(current["previous"].get("revenue"))},
                        derived_metrics={"capex_change_percent": capex_change, "operating_cash_flow_change_percent": ocf_change, "capex_to_operating_cash_flow": capex_to_ocf, "capex_to_revenue": capex_to_revenue},
                    ),
                ))

        extended_fields = (
            "accounts_receivable", "inventory", "cash_and_equivalents", "current_assets", "current_liabilities",
            "non_current_liabilities", "interest_bearing_debt_ratio", "capex",
        )
        present = sum(_number(latest.get(field)) is not None for field in extended_fields)
        extended_quality = "READY" if present == len(extended_fields) else "PARTIAL" if present else "MISSING"
        return risks, list(dict.fromkeys(missing)), quality, extended_quality

    def _forecast_risk(self, snapshots: list[dict[str, Any]], quality: str) -> tuple[dict[str, Any] | None, bool]:
        if len(snapshots) < 2:
            return None, False
        new, old = snapshots[0], snapshots[1]
        if str(new.get("forecast_status") or "") not in {"READY", "PARTIAL"} or str(old.get("forecast_status") or "") not in {"READY", "PARTIAL"}:
            return None, False
        def profits(item: dict[str, Any]) -> dict[str, float]:
            scenarios = ((item.get("forecast") or {}).get("scenarios") or {}).get("BASE") or {}
            return {str(row.get("year")): value for row in scenarios.get("forecast") or []
                    if (value := _number(row.get("net_profit"))) is not None}
        current, previous = profits(new), profits(old)
        shared = sorted(set(current) & set(previous))
        if not shared:
            return None, False
        year = shared[0]
        if previous[year] == 0:
            return None, False
        delta = (current[year] - previous[year]) / abs(previous[year]) * 100
        if delta > -10:
            return None, True
        severity = "HIGH" if delta <= -20 and quality == "READY" else "MEDIUM"
        return self._risk(risk_type="FINANCIAL_FORECAST_DOWNGRADE", severity=severity, status="CONFIRMED" if severity == "HIGH" else "WATCH",
            text=f"系统情景中 {year} 的基础利润预测较前一份可比快照下降约 {abs(delta):.1f}%。",
            why="未来盈利假设变弱时，需要重新核对增长和利润率基础。",
            sources=[f"FORECAST_BASE_NET_PROFIT_{year.replace('E', '')}"], watch="观察后续可比预测快照是否继续下调"), True

    @staticmethod
    def _select_thesis(repo: Any, market: str, symbol: str, target: str | None) -> dict[str, Any] | None:
        versions = repo.list_thesis_versions(market, symbol) if target else []
        if target:
            return next((row for row in versions if _available(row, target, data_key="source_data_as_of")), None)
        return repo.get_current_thesis(market, symbol)

    @staticmethod
    def _active_evidence(rows: list[dict[str, Any]], target: str | None) -> list[dict[str, Any]]:
        return [row for row in rows if row.get("is_active") and _available(row, target)]

    def _thesis_risks(self, thesis: dict[str, Any] | None, target: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, bool]:
        if not thesis:
            return [], [], "MISSING", False
        evidence = self._active_evidence(self.evidence_repository.list_evidence_for_thesis(str(thesis["thesis_id"])), target)
        challenges = [row for row in evidence if str(row.get("effect")) == "CHALLENGE"]
        status = str(thesis.get("status") or "")
        risks: list[dict[str, Any]] = []
        if status in {"FALSIFIED", "WEAKENING"}:
            risks.append(self._risk(risk_type="THESIS_STATUS", severity="HIGH", status="CONFIRMED",
                text="公司核心逻辑已被标记为核心逻辑已失效。" if status == "FALSIFIED" else "公司核心逻辑已被标记为逻辑正在减弱。",
                why="核心逻辑发生变化时，需要重新核对基本面与估值假设。", sources=[f"THESIS:{thesis['thesis_id']}"], watch="查看公司核心逻辑及其失效条件"))
        if challenges:
            high_evidence = any(str(row.get("confidence") or "") == "HIGH" for row in challenges)
            high = len(challenges) >= 3 and high_evidence
            severity = "HIGH" if high else "MEDIUM"
            risks.append(self._risk(risk_type="THESIS_CHALLENGE_EVIDENCE", severity=severity, status="CONFIRMED" if high else "WATCH",
                text=f"当前公司核心逻辑有 {len(challenges)} 条挑战证据需要复核。",
                why="这些证据与当前公司核心逻辑相冲突，可能改变对经营前景的理解。",
                evidence_ids=[str(row.get("evidence_id")) for row in challenges], watch="逐条核对挑战证据的最新性和影响"))
        review_stale = False
        if not target:
            reviews = self.review_repository.list_reviews_for_thesis(str(thesis["thesis_id"]))
            review = reviews[0] if reviews else None
            review_stale = bool(review and review.get("is_stale"))
            if review_stale:
                risks.append(self._risk(risk_type="THESIS_REVIEW_STALE", severity="MEDIUM", status="WATCH",
                    text="已有新的研究证据，当前研究复核已过期。", why="这不代表负面事实，但说明现有结论尚未结合最新证据重新检查。",
                    evidence_ids=[str(review.get("review_id"))], watch="由用户决定是否重新生成研究复核"))
        return risks, challenges, "READY", review_stale

    def _business_risks(self, stock_code: str, target: str | None,
                        challenges: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], str]:
        """Read completed, cited Business Research without requiring a Thesis.

        A formal Thesis is deliberately human-confirmed, but a missing Thesis
        must not hide already verified operating facts from a risk report.  The
        rule only promotes explicit, cited business wording; it never infers a
        business risk from a plain keyword hit in an annual report.
        """
        saved = self.business_store.latest(stock_code, as_of=target)
        claims = ((saved or {}).get("analysis") or {}).get("claims") or []
        cited = []
        for item in claims:
            if not isinstance(item, dict) or str(item.get("type") or "").upper() not in {"FACT", "INFERENCE"}:
                continue
            text = str(item.get("text") or "").strip()
            keys = item.get("source_keys") if isinstance(item.get("source_keys"), list) else []
            if text and keys:
                cited.append({"topic": str(item.get("topic") or ""), "text": text, "source_keys": [str(key) for key in keys]})
        risks: list[dict[str, Any]] = []
        ready = {
            "BUSINESS_CHANGE": any(item["topic"] == "BUSINESS_CHANGE" for item in cited),
            "CUSTOMER_CONCENTRATION": any("客户集中度" in item["text"] or "前五名客户" in item["text"] for item in cited),
            "PRODUCT_REVENUE_SHARE": any("占营业收入" in item["text"] or "收入占比" in item["text"] for item in cited),
            # Current official reports do not reliably provide an auditable
            # market-share time series.  Keep this visible as a true gap.
            "MARKET_SHARE": False,
        }
        negative = ("下降", "下滑", "收缩", "减少", "承压", "不利")
        for item in cited:
            if item["topic"] == "BUSINESS_CHANGE" and any(word in item["text"] for word in negative):
                risks.append(self._risk(
                    risk_type="BUSINESS_OPERATION_CHANGE", severity="MEDIUM", status="WATCH",
                    text="已验证经营资料显示，部分业务或经营结构出现需要持续跟踪的变化。",
                    why="核心业务结构或项目阶段变化可能影响后续收入、利润率和回款节奏。",
                    sources=item["source_keys"], watch="核对下一期同口径业务收入、毛利率和项目运营情况",
                ))
                break
        for item in cited:
            if ("客户集中度较高" in item["text"] or "前五名客户" in item["text"] and "占" in item["text"]):
                risks.append(self._risk(
                    risk_type="BUSINESS_CUSTOMER_CONCENTRATION", severity="MEDIUM", status="WATCH",
                    text="已验证经营资料提示客户集中度较高，需要持续关注主要客户的回款与需求变化。",
                    why="客户集中时，个别重要客户的付款或需求变化可能放大经营波动。",
                    sources=item["source_keys"], watch="观察主要客户占比、应收账款和回款进展",
                ))
                break
        # Existing Thesis-linked challenge evidence remains an additional
        # source; it is not required for the base operating-risk projection.
        business_challenges = [row for row in challenges if str(row.get("evidence_type") or "").startswith("BUSINESS")
                               or str(row.get("source_type") or "").startswith("BUSINESS")]
        if business_challenges:
            ids = [str(row.get("evidence_id")) for row in business_challenges]
            sources = [str(row.get("source_ref")) for row in business_challenges if row.get("source_ref")]
            risks.append(self._risk(risk_type="BUSINESS_CHALLENGE", severity="MEDIUM", status="WATCH",
                text=f"已有 {len(business_challenges)} 条可追溯的经营挑战资料需要复核。",
                why="经营方向或核心业务变化会影响未来收入和利润假设。", sources=sources, evidence_ids=ids,
                watch="核对后续经营资料是否确认或缓解这些变化"))
        missing = [name for name, available in ready.items() if not available]
        quality = "READY" if not missing else "PARTIAL" if cited else "MISSING"
        return risks, missing, quality

    @staticmethod
    def _disclosure_source(row: dict[str, Any]) -> str:
        return f"CNINFO:{row.get('announcement_id') or row.get('document_id') or row.get('id')}:{row.get('material_type')}"

    def _disclosure_risks(self, stock_code: str, target: str | None) -> tuple[list[dict[str, Any]], dict[str, str], str | None]:
        """Turn only explicit disclosure risk wording into traceable watch items."""
        rows = self.disclosure_store.list_materials(stock_code, as_of=target)
        found = [row for row in rows if str(row.get("status") or "") == "FOUND" and row.get("excerpts")]
        latest_by_type: dict[str, dict[str, Any]] = {}
        for row in found:
            material_type = str(row.get("material_type") or "")
            latest_by_type.setdefault(material_type, row)
        source_dates = [str(row.get("announcement_date") or "")[:10] for row in found if row.get("announcement_date")]
        risks: list[dict[str, Any]] = []
        all_text = "\n".join(
            str(excerpt.get("text") or "") for row in found for excerpt in (row.get("excerpts") or [])
            if isinstance(excerpt, dict)
        )
        receivable = next((row for row in found if str(row.get("material_type")) == "RECEIVABLES_IMPAIRMENT"), None)
        if receivable and ("应收账款金额呈上升趋势" in all_text or "应收账款持续累积" in all_text):
            risks.append(self._risk(
                risk_type="DISCLOSURE_RECEIVABLES_COLLECTION", severity="MEDIUM", status="WATCH",
                text="定期报告披露应收账款呈上升或持续累积趋势，需要重点跟踪回款和减值变化。",
                why="应收款累积可能占用经营现金，并增加后续信用减值的不确定性。",
                sources=[self._disclosure_source(receivable)], watch="观察应收账款账龄、坏账准备和经营现金流是否改善",
            ))
        debt = next((row for row in found if str(row.get("material_type")) == "DEBT_MATURITY"), None)
        debt_source = debt or receivable
        if debt_source and ("长期借款持续增加" in all_text or "一年内到期的非流动负债" in all_text and "持续增加" in all_text):
            risks.append(self._risk(
                risk_type="DISCLOSURE_DEBT_MATURITY", severity="MEDIUM", status="WATCH",
                text="定期报告提及长期借款增加或短期到期负债安排，需要持续核对偿债与回款匹配情况。",
                why="债务期限安排和经营回款错配时，可能增加流动性压力。",
                sources=[self._disclosure_source(debt_source)], watch="观察一年内到期负债、长期借款、经营现金流和融资安排",
            ))
        coverage = self._disclosure_coverage(stock_code, target)
        return risks, coverage, max(source_dates) if source_dates else None

    def _disclosure_coverage(self, stock_code: str, target: str | None) -> dict[str, str]:
        """Expose official-report source availability without treating it as risk.

        A disclosure mention is evidence to read, not a deterministic adverse
        event.  Therefore this is returned as data quality only; Risk Research
        keeps its existing source/evidence rules for actual risk records.
        """
        found = {
            str(row.get("material_type") or "")
            for row in self.disclosure_store.list_materials(stock_code, as_of=target)
            if str(row.get("status") or "") == "FOUND" and row.get("excerpts")
        }
        categories = {
            "accounts_receivable_ageing": "ACCOUNTS_RECEIVABLE_AGEING",
            "receivables_impairment": "RECEIVABLES_IMPAIRMENT",
            "customer_concentration": "CUSTOMER_CONCENTRATION",
            "product_structure": "BUSINESS_PRODUCT_STRUCTURE",
            "ppp_collection": "PPP_COLLECTION",
            "debt_maturity": "DEBT_MATURITY",
            "guarantees_contingencies": "GUARANTEES_CONTINGENCIES",
        }
        return {name: "READY" if material_type in found else "MISSING" for name, material_type in categories.items()}

    @staticmethod
    def _leader_member(pool: dict[str, Any] | None, symbol: str) -> dict[str, Any] | None:
        for row in (pool or {}).get("members") or []:
            if str(row.get("stock_code") or "").upper() == symbol.upper() and str(row.get("lifecycle_status") or "") in ACTIVE_LEADER_STATES:
                return row
        return None

    def _value_trap(self, *, leader: dict[str, Any] | None, valuation_status: str,
                    financial: list[dict[str, Any]], business: list[dict[str, Any]], thesis: list[dict[str, Any]],
                    financial_quality: str, thesis_quality: str) -> tuple[str, dict[str, Any] | None]:
        if not leader or valuation_status not in UNDERVALUED:
            return "NOT_APPLICABLE", None
        signals: set[str] = set()
        # Several rules can describe one financial root cause.  They remain
        # separate, traceable risks for the owner, but must not inflate the
        # low-value trap conclusion merely because they share the same cause.
        trap_signal_group = {
            "FINANCIAL_PROFIT_CASH_DIVERGENCE": "CASH_QUALITY",
            "FINANCIAL_CASH_FLOW": "CASH_QUALITY",
            "FINANCIAL_LIQUIDITY": "LIQUIDITY",
            "FINANCIAL_CASH_COVERAGE": "LIQUIDITY",
            "FINANCIAL_DEBT_RATIO": "DEBT_PRESSURE",
            "FINANCIAL_INTEREST_DEBT": "DEBT_PRESSURE",
        }
        for item in financial:
            if item["severity"] in {"HIGH", "MEDIUM"}:
                signals.add(trap_signal_group.get(item["risk_type"], item["risk_type"]))
        if business:
            signals.add("BUSINESS")
        thesis_status = next((item for item in thesis if item["risk_type"] == "THESIS_STATUS"), None)
        if thesis_status and "失效" in thesis_status["text"]:
            level = "HIGH_TRAP_RISK"
        elif any(item["severity"] == "HIGH" for item in financial + business + thesis) and len(signals) >= 2:
            level = "HIGH_TRAP_RISK"
        elif len(signals) >= 3:
            level = "HIGH_TRAP_RISK"
        elif len(signals) >= 2 or (thesis_status and len(signals) >= 1):
            level = "MEDIUM_TRAP_RISK"
        elif financial_quality == "READY" and thesis_quality == "READY" and not signals:
            level = "LOW_TRAP_RISK"
        else:
            level = "UNKNOWN"
        # A partial financial snapshot may still surface useful WATCH items,
        # but it cannot turn their aggregate into a confirmed high trap risk.
        # A separately confirmed Thesis failure remains governed by its own
        # explicit branch above.
        if level == "HIGH_TRAP_RISK" and financial_quality != "READY" and not (thesis_status and "失效" in thesis_status["text"]):
            level = "MEDIUM_TRAP_RISK"
        if level == "LOW_TRAP_RISK":
            return level, None
        severity = "HIGH" if level == "HIGH_TRAP_RISK" else "MEDIUM" if level == "MEDIUM_TRAP_RISK" else "LOW"
        status = "CONFIRMED" if level == "HIGH_TRAP_RISK" else "WATCH" if level == "MEDIUM_TRAP_RISK" else "UNKNOWN"
        text = {"HIGH_TRAP_RISK": "公司虽然处于低估区域，但多项基本面或核心逻辑风险同时出现，需要重点复核低估原因。",
                "MEDIUM_TRAP_RISK": "公司处于低估区域，且已有基本面或经营风险信号，需要优先核对低估原因。",
                "UNKNOWN": "公司处于低估区域，但关键财务或公司核心逻辑资料不足，暂不能判断低估原因。"}[level]
        sources = [key for item in financial + business + thesis for key in item.get("source_keys") or []]
        evidence = [key for item in financial + business + thesis for key in item.get("evidence_ids") or []]
        return level, self._risk(risk_type="VALUE_TRAP", severity=severity, status=status, text=text,
            why="低估可能来自价值机会，也可能反映基本面变化；需要区分两者。", sources=sources, evidence_ids=evidence,
            watch="核对利润、现金流、经营变化和公司核心逻辑是否同步改善")

    def get_risk_research(self, market: str, stock_code: str, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        target = _target_date(as_of)
        snapshot = self.financial_store.latest(symbol, as_of=target)
        financial, financial_missing, financial_quality, extended_financial_quality = self._financial_risks(snapshot)
        forecast, forecast_comparable = self._forecast_risk(self.financial_store.recent(symbol, as_of=target, limit=2), financial_quality)
        if forecast:
            financial.append(forecast)
        elif not forecast_comparable:
            financial_missing.append("FORECAST_REVISION_COMPARISON")
        thesis = self._select_thesis(self.thesis_repository, normalized_market, symbol, target)
        thesis_risks, challenges, thesis_quality, review_stale = self._thesis_risks(thesis, target)
        business, business_missing, business_quality = self._business_risks(symbol, target, challenges)
        disclosure, disclosure_coverage, disclosure_as_of = self._disclosure_risks(symbol, target)
        zones = self.price_zone_service.get_price_zones(normalized_market, symbol, as_of=target)
        valuation_status = str((zones.get("valuation") or {}).get("status") or "INSUFFICIENT_DATA")
        pool = self.leader_pool_reader(target)
        leader = self._leader_member(pool, symbol)
        trap_level, trap_risk = self._value_trap(leader=leader, valuation_status=valuation_status, financial=financial + disclosure,
                                                  business=business, thesis=thesis_risks, financial_quality=financial_quality,
                                                  thesis_quality=thesis_quality)
        # Keep the owner-facing list short without allowing numerous financial
        # sub-rules to hide an operating or Thesis challenge.
        financial_priority = {
            "FINANCIAL_PROFIT_CASH_DIVERGENCE": 0,
            "FINANCIAL_RECEIVABLE": 1, "FINANCIAL_INVENTORY": 2,
            "FINANCIAL_LIQUIDITY": 3, "FINANCIAL_CASH_COVERAGE": 3,
            "FINANCIAL_INTEREST_DEBT": 4, "FINANCIAL_CAPEX_PRESSURE": 5,
            "FINANCIAL_CASH_FLOW": 6, "FINANCIAL_DEBT_RATIO": 7,
            "FINANCIAL_PROFIT_DECLINE": 8, "FINANCIAL_REVENUE_DECLINE": 9,
            "FINANCIAL_FORECAST_DOWNGRADE": 10, "FINANCIAL_MARGIN_DECLINE": 11,
            "FINANCIAL_ROE_DECLINE": 12,
        }
        financial = sorted(financial, key=lambda item: financial_priority.get(item["risk_type"], 99))
        risks = financial[:6] + disclosure + business + thesis_risks + ([trap_risk] if trap_risk else [])
        # A deterministic service only exposes risks with evidence/source keys.  Unknowns are reflected in data_quality.
        risks = [risk for risk in risks if risk["severity"] != "LOW"]
        risks.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9), item["risk_type"]))
        risks = risks[:8]
        confirmed = [item for item in risks if item["status"] in {"CONFIRMED", "WATCH"}]
        missing = list(dict.fromkeys(financial_missing + business_missing + ([] if thesis else ["THESIS"])))
        overall = "HIGH" if any(item["severity"] == "HIGH" for item in confirmed) else "MEDIUM" if confirmed else ("UNKNOWN" if missing else "LOW")
        status = "READY" if not missing else "PARTIAL" if (snapshot or thesis or leader) else "UNKNOWN"
        summary = ("当前没有发现已确认的重点风险，但仍需结合后续财务和经营资料持续复核。" if not confirmed else
                   f"当前发现 {len(confirmed)} 项需要复核的风险，其中 {sum(item['severity'] == 'HIGH' for item in confirmed)} 项为重点复核。")
        return {
            "stock_code": symbol, "market": normalized_market, "as_of": zones.get("as_of") or target,
            "status": status, "overall_risk": overall, "summary": summary, "value_trap_risk": trap_level,
            "is_current_l3_leader": bool(leader), "valuation_status": valuation_status,
            "data_quality": {"financial": financial_quality, "financial_extended": extended_financial_quality,
                             "business": business_quality, "forecast": "READY" if forecast_comparable else "PARTIAL",
                             "thesis": thesis_quality, "review_stale": review_stale if not target else None,
                             "missing": missing, "official_disclosure_sources": disclosure_coverage,
                             "source_as_of": {"financial": snapshot.get("as_of") if snapshot else None,
                                               "business": ((saved := self.business_store.latest(symbol, as_of=target)) or {}).get("data_as_of"),
                                               "official_disclosures": disclosure_as_of,
                                               "valuation_price": zones.get("as_of")}},
            "risks": risks, "formula_version": FORMULA_VERSION,
        }


_service: RiskResearchService | None = None


def get_risk_research_service() -> RiskResearchService:
    global _service
    if _service is None:
        _service = RiskResearchService()
    return _service

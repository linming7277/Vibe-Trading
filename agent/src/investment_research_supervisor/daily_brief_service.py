"""Deterministic, read-only daily brief for the Investment Research Supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from src.business_research.store import BusinessResearchStore
from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.company_thesis.review_store import CompanyThesisReviewRepository
from src.company_thesis.store import CompanyThesisRepository
from src.financial_analysis.store import FinancialAnalysisStore
from src.entry_research import get_entry_research_service
from src.focus_selection import FocusSelectionService
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository

from .daily_brief_bitable_service import LOW_VALUE_LEADER_BITABLE_URL
from .daily_brief_store import InvestmentResearchDailyBriefRepository


FORMULA_VERSION = "daily-brief-v20"
_TRADING_TERMS = ("买入", "卖出", "推荐", "止盈", "止损", "仓位", "加仓", "减仓")
_FINANCIAL_METRIC_LABELS = {
    "revenue": "营业收入",
    "net_profit": "净利润",
    "operating_cash_flow": "经营现金流",
    "roe": "净资产收益率",
    "debt_ratio": "资产负债率",
}
_VALUATION_LABELS = {
    "UNDERVALUED": "低估",
    "DEEPLY_UNDERVALUED": "深度低估",
    "FAIR": "合理估值",
    "OVERVALUED": "估值偏高",
    "DEEPLY_OVERVALUED": "估值偏高",
}


def _company_url(stock_code: str, web_base_url: str) -> str | None:
    parsed = urlparse(web_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = (
        f"/company/CN/{quote(stock_code, safe='')}?from=%2Fvalue%2Ffocus"
        "&from_label=%E6%8A%95%E7%A0%94%E4%B8%BB%E7%AE%A1%E6%AF%8F%E6%97%A5%E7%AE%80%E6%8A%A5&tab=overview"
    )
    return urljoin(web_base_url.rstrip("/") + "/", path.lstrip("/"))


def _risk_label(snapshot: dict[str, Any] | None) -> str:
    if not snapshot or snapshot.get("error") or str(snapshot.get("overall_risk") or "") == "UNKNOWN":
        return "资料不足"
    if int(snapshot.get("high_risk_count") or 0) > 0:
        return "有明显风险需要重点核验"
    if int(snapshot.get("medium_risk_count") or 0) > 0 or int(snapshot.get("material_risk_count") or 0) > 0:
        return "有风险需要复核"
    return "暂无明显风险"


def _risk_tier(snapshot: dict[str, Any] | None) -> int:
    if not snapshot or snapshot.get("error") or str(snapshot.get("overall_risk") or "") == "UNKNOWN":
        return -1
    if int(snapshot.get("high_risk_count") or 0) > 0:
        return 2
    if int(snapshot.get("medium_risk_count") or 0) > 0 or int(snapshot.get("material_risk_count") or 0) > 0:
        return 1
    return 0


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _format_financial_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        if abs(value) >= 100_000_000:
            return f"{value / 100_000_000:.2f}亿元"
        return f"{value:.2f}"
    return str(value if value is not None else "—")


def _specific_counterpoint(snapshot: dict[str, Any] | None) -> str | None:
    summary = str((snapshot or {}).get("risk_summary") or "").strip()
    if not summary or summary.startswith("发现 ") or "需要继续观察" in summary:
        return None
    return summary


@dataclass(frozen=True)
class DailyBriefBuildResult:
    status: str
    brief: dict[str, Any]
    reused: bool = False


class InvestmentResearchDailyBriefService:
    """Build one date-bounded brief without invoking research preparation."""

    def __init__(
        self,
        *,
        repository: InvestmentResearchDailyBriefRepository | None = None,
        pool_repository: LowValueLeaderPoolRepository | None = None,
        risk_repository: LowValueRiskSnapshotRepository | None = None,
        financial_store: FinancialAnalysisStore | None = None,
        business_store: BusinessResearchStore | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        evidence_repository: CompanyThesisEvidenceRepository | None = None,
        review_repository: CompanyThesisReviewRepository | None = None,
        entry_research_service: Any | None = None,
        focus_selection_service: Any | None = None,
        web_base_url: str = "",
    ) -> None:
        self.repository = repository or InvestmentResearchDailyBriefRepository()
        db_path = self.repository.db_path
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository(db_path)
        self.risk_repository = risk_repository or LowValueRiskSnapshotRepository(db_path)
        self.financial_store = financial_store or FinancialAnalysisStore(db_path)
        self.business_store = business_store or BusinessResearchStore(db_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(db_path)
        self.evidence_repository = evidence_repository or CompanyThesisEvidenceRepository(db_path)
        self.review_repository = review_repository or CompanyThesisReviewRepository(db_path)
        self.entry_research_service = entry_research_service or get_entry_research_service()
        # 重点观察名单必须与「机会与风险」页一致：先经风险与资料条件筛选，
        # 再取 A 级重点研究，而不是低估池里深度低估的直接切片。
        self.focus_selection_service = focus_selection_service or FocusSelectionService(
            pool_repository=self.pool_repository,
            risk_snapshot_repository=self.risk_repository,
            thesis_repository=self.thesis_repository,
        )
        self.web_base_url = web_base_url

    def build(self, *, research_as_of: str) -> DailyBriefBuildResult:
        existing = self.repository.get_completed(research_as_of)
        if existing and existing.get("formula_version") == FORMULA_VERSION:
            return DailyBriefBuildResult("READY", existing, reused=True)
        try:
            payload = self._build_payload(research_as_of)
            saved = self.repository.save_ready(payload)
            self._record_freshness_manifest(research_as_of, payload)
            return DailyBriefBuildResult("READY", saved)
        except Exception as exc:
            failed = self.repository.record_failure(
                research_as_of, f"{type(exc).__name__}: {exc}", formula_version=FORMULA_VERSION,
            )
            return DailyBriefBuildResult("FAILED", failed)

    def _record_freshness_manifest(self, research_as_of: str, payload: dict[str, Any]) -> None:
        """Record the brief's input fingerprint so same-day upstream fixes are visible (plan §20.3).

        Best effort: manifest recording must never fail the brief build.
        """
        try:
            import hashlib
            import json

            from src.research_freshness.manifests import ResearchManifestStore

            fingerprint = hashlib.sha256(json.dumps({
                "formula_version": FORMULA_VERSION,
                "low_value_active_count": payload.get("low_value_active_count"),
                "priority_companies": payload.get("priority_companies"),
                "risk_summary": payload.get("risk_summary"),
                "thesis_changes": payload.get("thesis_changes"),
                "financial_changes": payload.get("financial_changes"),
            }, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            ResearchManifestStore().record(
                research_type="daily_brief", market="CN", stock_code="",
                research_as_of=research_as_of, input_fingerprint=fingerprint,
                formula_version=str(FORMULA_VERSION),
            )
        except Exception:  # noqa: BLE001
            return

    def get_completed(self, research_as_of: str | None = None) -> dict[str, Any] | None:
        return self.repository.get_completed(research_as_of)

    def _build_payload(self, research_as_of: str) -> dict[str, Any]:
        pool, price_gaps = self._price_ready_pool(
            self.pool_repository.snapshots_for_as_of(research_as_of),
            research_as_of,
        )
        events = self.pool_repository.events(event_date=research_as_of, limit=500)
        entered = {
            str(item.get("stock_code") or "").upper(): item
            for item in events if item.get("event_type") == "ENTER_LOW_VALUE"
        }
        exited = [item for item in events if item.get("event_type") == "EXIT_LOW_VALUE"]
        risks = {
            str(item.get("stock_code") or "").upper(): self.risk_repository.get(
                "CN", str(item.get("stock_code") or ""), research_as_of,
            )
            for item in pool
        }
        thesis_changes = self._thesis_changes(pool, research_as_of)
        thesis_by_code = {str(item["stock_code"]): item for item in thesis_changes}
        low_value_leader_table = self._low_value_leader_table(pool)
        previous_brief = self.repository.previous_completed(research_as_of)
        deeply_undervalued = self._deeply_undervalued_companies(pool, research_as_of)
        focus_a, focus_gaps, focus_available = self._focus_a_tier(research_as_of)
        priority, priority_gaps = self._priority_companies(pool, entered, risks, thesis_by_code)
        risk_summary, risk_details, risk_gaps = self._risk_summary(pool, risks)
        financial_changes, financial_gaps = self._financial_changes(pool, research_as_of)
        business_changes, business_gaps = self._business_changes(pool, research_as_of)
        data_gaps = _dedupe(price_gaps + focus_gaps + priority_gaps + risk_gaps + financial_gaps + business_gaps)
        executive_situations = self._executive_situations(
            risks=risks,
            thesis_changes=thesis_changes,
            low_value_leader_table=low_value_leader_table,
            previous_brief=previous_brief,
        )
        executive_watchlist = self._executive_watchlist(
            focus_available=focus_available,
            focus_a=focus_a,
            deeply_undervalued=deeply_undervalued,
            pool=pool,
            risks=risks,
            thesis_changes=thesis_changes,
            financial_changes=financial_changes,
            business_changes=business_changes,
            research_as_of=research_as_of,
        )
        watchlist_basis = "FOCUS_A" if focus_available else "DEEP_FALLBACK"
        rendered = self._render_executive(
            research_as_of=research_as_of,
            situations=executive_situations,
            watchlist=executive_watchlist,
            watchlist_basis=watchlist_basis,
        )
        if any(term in rendered for term in _TRADING_TERMS):
            raise ValueError("daily brief contains prohibited trading language")
        return {
            "research_as_of": research_as_of,
            "low_value_active_count": len(pool),
            "enter_count": len(entered),
            "exit_count": len(exited),
            "priority_companies": priority,
            "risk_summary": {**risk_summary, "important_companies": risk_details},
            "thesis_changes": thesis_changes,
            "financial_changes": [*financial_changes, *business_changes],
            "data_gaps": data_gaps,
            "brief_payload": {
                "text": rendered,
                "research_as_of": research_as_of,
                "executive_situations": executive_situations,
                "executive_watchlist": executive_watchlist,
                "executive_watchlist_basis": watchlist_basis,
                "low_value_leader_table": low_value_leader_table,
                "low_value_leader_bitable_url": LOW_VALUE_LEADER_BITABLE_URL,
                "deeply_undervalued_count": len(deeply_undervalued),
                "deeply_undervalued_companies": deeply_undervalued,
                "research_appendix": {
                    "priority_companies": priority,
                    "risk_summary": {**risk_summary, "important_companies": risk_details},
                    "thesis_changes": thesis_changes,
                    "financial_changes": financial_changes,
                    "business_changes": business_changes,
                    "data_gaps": data_gaps,
                },
            },
            "formula_version": FORMULA_VERSION,
        }

    @staticmethod
    def _price_ready_pool(
        pool: list[dict[str, Any]],
        research_as_of: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Reject old prices that were previously relabelled as today's data.

        ``price`` was added to the Value Price Zones data-quality contract in
        v1.1.  A legacy snapshot is accepted only when its cached daily bar
        explicitly reaches the report date; otherwise it is a data gap, not a
        valid low-value observation.
        """
        ready: list[dict[str, Any]] = []
        gaps: list[str] = []
        for item in pool:
            code = str(item.get("stock_code") or "")
            metadata = dict(item.get("metadata") or {})
            quality = dict(metadata.get("data_quality") or {})
            price = dict(quality.get("price") or {})
            price_as_of = str(price.get("as_of") or metadata.get("price_as_of") or "")[:10]
            if price:
                is_ready = str(price.get("status") or "") == "READY" and price_as_of == research_as_of
            else:
                # Old snapshots do not expose a dedicated price date.  Do not
                # infer freshness from their requested as-of date; rely only
                # on the actual final daily-bar date.
                daily = dict(quality.get("daily_history") or {})
                is_ready = str(daily.get("last_date") or "")[:10] == research_as_of
            if is_ready:
                ready.append(item)
            else:
                gaps.append(f"{code or '未知证券'}：行情未更新至 {research_as_of}，未纳入正式低估判断")
        return ready, gaps

    def _thesis_changes(self, pool: list[dict[str, Any]], research_as_of: str) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for item in pool:
            code = str(item.get("stock_code") or "").upper()
            thesis = self.thesis_repository.get_current_thesis("CN", code)
            if not thesis or str(thesis.get("source_data_as_of") or "") != research_as_of:
                continue
            versions = self.thesis_repository.list_thesis_versions("CN", code)
            previous_status = str((versions[1] if len(versions) > 1 else {}).get("status") or "").upper() or None
            flags: list[str] = []
            status = str(thesis.get("status") or "").upper()
            if status in {"WEAKENING", "FALSIFIED"}:
                flags.append(f"Thesis {status}")
            evidence = [
                row for row in self.evidence_repository.list_evidence_for_company("CN", code)
                if row.get("is_active")
                and str(row.get("data_as_of") or "") == research_as_of
                and str(row.get("effect") or "").upper() == "CHALLENGE"
            ]
            evidence_summaries = [
                str(row.get("summary") or row.get("claim") or "").strip()
                for row in evidence
            ]
            evidence_summaries = [summary for summary in evidence_summaries if summary]
            if evidence:
                flags.append("新增 active Challenge Evidence")
            review = self.review_repository.get_latest_review("CN", code)
            if flags and review and review.get("is_stale"):
                flags.append("Review stale")
            if flags:
                changes.append({
                    "stock_code": code,
                    "company_name": str(item.get("company_name") or code),
                    "changes": flags,
                    "challenge_evidence": evidence_summaries,
                    "previous_status": previous_status,
                    "thesis_status": status or "UNKNOWN",
                    "source_data_as_of": research_as_of,
                })
        return changes

    def _historical_support(self, stock_code: str, research_as_of: str) -> dict[str, Any]:
        try:
            entry = self.entry_research_service.get_entry_research(
                "CN", stock_code, as_of=research_as_of,
            )
            support = dict((entry.get("focus_zones") or {}).get("historical_support") or {})
        except Exception:
            support = {}
        low, high = support.get("low"), support.get("high")
        if low is None and high is None:
            return {"status": "INSUFFICIENT", "low": None, "high": None, "strength": None}
        return {
            "status": "READY",
            "low": low,
            "high": high,
            "strength": support.get("strength"),
        }

    @staticmethod
    def _low_value_leader_table(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in pool:
            current_price = item.get("current_price")
            fair_value_mid = item.get("fair_value_mid")
            valuation_gap_percent: float | None = None
            try:
                if float(current_price) > 0 and float(fair_value_mid) > 0:
                    valuation_gap_percent = round((float(fair_value_mid) / float(current_price) - 1) * 100, 2)
            except (TypeError, ValueError):
                pass
            # 池快照自带支撑区间；没有时如实标记 INSUFFICIENT，供表格与
            # Excel 直接使用，避免“未在简报中计算”被误显示成数据不足。
            support_low, support_high = item.get("support_zone_low"), item.get("support_zone_high")
            rows.append({
                "stock_code": str(item.get("stock_code") or "").upper(),
                "company_name": str(item.get("company_name") or ""),
                "industry_name": str(item.get("industry_name") or ""),
                "valuation_label": _VALUATION_LABELS.get(str(item.get("valuation_status") or ""), ""),
                "current_price": current_price,
                "fair_value_low": item.get("fair_value_low"),
                "fair_value_mid": fair_value_mid,
                "fair_value_high": item.get("fair_value_high"),
                "valuation_gap_percent": valuation_gap_percent,
                "historical_support": {
                    "low": support_low,
                    "high": support_high,
                    "status": "READY" if support_low is not None and support_high is not None else "INSUFFICIENT",
                },
            })
        return rows

    def _deeply_undervalued_companies(
        self,
        pool: list[dict[str, Any]],
        research_as_of: str,
    ) -> list[dict[str, Any]]:
        companies: list[dict[str, Any]] = []
        for item in pool:
            if item.get("valuation_status") != "DEEPLY_UNDERVALUED":
                continue
            code = str(item.get("stock_code") or "").upper()
            current_price = item.get("current_price")
            fair_value_mid = item.get("fair_value_mid")
            valuation_gap_percent: float | None = None
            try:
                if float(current_price) > 0 and float(fair_value_mid) > 0:
                    valuation_gap_percent = round((float(fair_value_mid) / float(current_price) - 1) * 100, 2)
            except (TypeError, ValueError):
                pass
            companies.append({
                "stock_code": code,
                "company_name": str(item.get("company_name") or code),
                "industry_name": str(item.get("industry_name") or "资料不足"),
                "current_price": current_price,
                "fair_value_low": item.get("fair_value_low"),
                "fair_value_mid": fair_value_mid,
                "fair_value_high": item.get("fair_value_high"),
                "valuation_status": "DEEPLY_UNDERVALUED",
                "valuation_label": _VALUATION_LABELS["DEEPLY_UNDERVALUED"],
                "valuation_gap_percent": valuation_gap_percent,
                "historical_support": self._historical_support(code, research_as_of),
                "company_research_url": _company_url(code, self.web_base_url),
            })
        return companies

    def _priority_companies(
        self,
        pool: list[dict[str, Any]],
        entered: dict[str, dict[str, Any]],
        risks: dict[str, dict[str, Any] | None],
        thesis_changes: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(5)]
        gaps: list[str] = []
        for item in pool:
            code = str(item.get("stock_code") or "").upper()
            risk = risks.get(code)
            high = int((risk or {}).get("high_risk_count") or 0) > 0
            medium = int((risk or {}).get("medium_risk_count") or 0) > 0
            is_entered = code in entered
            if is_entered and high:
                bucket = 0
            elif is_entered and medium:
                bucket = 1
            elif is_entered and item.get("valuation_status") == "DEEPLY_UNDERVALUED":
                bucket = 2
            elif code in thesis_changes:
                bucket = 3
            elif is_entered:
                bucket = 4
            else:
                continue
            reason: list[str] = []
            if is_entered:
                reason.append("今日新进入低估龙头池")
            if item.get("valuation_status") == "DEEPLY_UNDERVALUED":
                reason.append("当前为深度低估状态")
            if high or medium:
                reason.append(_risk_label(risk))
            if code in thesis_changes:
                reason.append("公司核心逻辑出现需要复核的变化")
            if not risk:
                gaps.append(f"{code}：缺少同日风险快照")
            record = {
                "stock_code": code,
                "company_name": str(item.get("company_name") or code),
                "industry_name": str(item.get("industry_name") or "资料不足"),
                "valuation_status": str(item.get("valuation_status") or "UNKNOWN"),
                "valuation_label": _VALUATION_LABELS.get(str(item.get("valuation_status") or ""), "资料不足"),
                "risk_summary": _risk_label(risk),
                "why_today": "；".join(reason) if reason else "已有研究资料需要优先复核",
                "company_research_url": _company_url(code, self.web_base_url),
            }
            buckets[bucket].append(record)
        return [item for bucket in buckets for item in bucket][:5], _dedupe(gaps)

    @staticmethod
    def _risk_summary(
        pool: list[dict[str, Any]], risks: dict[str, dict[str, Any] | None],
    ) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
        high = medium = unknown = 0
        details: list[dict[str, Any]] = []
        gaps: list[str] = []
        for item in pool:
            code = str(item.get("stock_code") or "").upper()
            risk = risks.get(code)
            if not risk or risk.get("error") or str(risk.get("overall_risk") or "") == "UNKNOWN":
                unknown += 1
                gaps.append(f"{code}：风险资料不足")
                continue
            if int(risk.get("high_risk_count") or 0) > 0:
                high += 1
                details.append({
                    "stock_code": code, "company_name": str(item.get("company_name") or code),
                    "risk_summary": str(risk.get("risk_summary") or "资料不足"),
                    "risk_level": "HIGH",
                })
            elif int(risk.get("medium_risk_count") or 0) > 0 or int(risk.get("material_risk_count") or 0) > 0:
                medium += 1
                details.append({
                    "stock_code": code, "company_name": str(item.get("company_name") or code),
                    "risk_summary": str(risk.get("risk_summary") or "资料不足"),
                    "risk_level": "MEDIUM",
                })
        details.sort(key=lambda item: 0 if item["risk_level"] == "HIGH" else 1)
        return {
            "high_risk_review_count": high,
            "risk_review_count": medium,
            "insufficient_data_count": unknown,
        }, details[:3], _dedupe(gaps)

    def _financial_changes(self, pool: list[dict[str, Any]], research_as_of: str) -> tuple[list[dict[str, Any]], list[str]]:
        changes: list[dict[str, Any]] = []
        gaps: list[str] = []
        for item in pool:
            code = str(item.get("stock_code") or "").upper()
            snapshot = self.financial_store.latest(code, as_of=research_as_of)
            if not snapshot:
                gaps.append(f"{code}：缺少截止研究日期的财务研究")
                continue
            if str(snapshot.get("as_of") or "") != research_as_of:
                gaps.append(f"{code}：财务研究未更新至 {research_as_of}")
                continue
            for change in list((snapshot.get("feature") or {}).get("latest_changes") or []):
                if change.get("fact") is not True:
                    continue
                changes.append({
                    "type": "FINANCIAL",
                    "stock_code": code,
                    "company_name": str(item.get("company_name") or code),
                    "metric": str(change.get("metric") or "资料不足"),
                    "previous": change.get("previous"),
                    "current": change.get("current"),
                    "change_percent": change.get("change_percent"),
                    "report_date": str(change.get("report_date") or ""),
                    "source_as_of": research_as_of,
                })
        return changes[:10], _dedupe(gaps)

    def _business_changes(self, pool: list[dict[str, Any]], research_as_of: str) -> tuple[list[dict[str, Any]], list[str]]:
        changes: list[dict[str, Any]] = []
        gaps: list[str] = []
        for item in pool:
            code = str(item.get("stock_code") or "").upper()
            snapshot = self.business_store.latest(code, as_of=research_as_of)
            if not snapshot:
                gaps.append(f"{code}：缺少截止研究日期的经营研究")
                continue
            if str(snapshot.get("data_as_of") or "") != research_as_of:
                gaps.append(f"{code}：经营研究未更新至 {research_as_of}")
                continue
            analysis = snapshot.get("analysis") or {}
            summary = str(analysis.get("executive_summary") or analysis.get("summary") or "").strip()
            if summary:
                changes.append({
                    "type": "BUSINESS",
                    "stock_code": code,
                    "company_name": str(item.get("company_name") or code),
                    "summary": summary,
                    "source_as_of": research_as_of,
                })
        return changes[:5], _dedupe(gaps)

    @staticmethod
    def _valuation_caveat(valuation_gap_percent: Any) -> str | None:
        if isinstance(valuation_gap_percent, (int, float)) and valuation_gap_percent >= 300:
            return "现价与估值中枢偏离显著，需要优先核验盈利与估值假设"
        return None

    def _focus_a_tier(self, research_as_of: str) -> tuple[list[dict[str, Any]], list[str], bool]:
        """Read the A-tier focus list so the brief matches /value/opportunities.

        The A tier has already passed the risk / entry / financial readiness
        gates; reusing the projection keeps the brief and the research
        priority page from quoting different lists on the same day.
        """
        try:
            selection = self.focus_selection_service.get_focus_selection(as_of=research_as_of)
        except Exception as exc:  # noqa: BLE001 - the brief must still ship other sections
            gap = f"机会与风险筛选不可用，重点观察回退为深度低估名单（{type(exc).__name__}）"
            return [], [gap], False
        return list(selection.get("A") or []), [], True

    def _executive_watchlist(
        self,
        *,
        focus_available: bool,
        focus_a: list[dict[str, Any]],
        deeply_undervalued: list[dict[str, Any]],
        pool: list[dict[str, Any]],
        risks: dict[str, dict[str, Any] | None],
        thesis_changes: list[dict[str, Any]],
        financial_changes: list[dict[str, Any]],
        business_changes: list[dict[str, Any]],
        research_as_of: str,
    ) -> list[dict[str, Any]]:
        pool_by_code = {str(item.get("stock_code") or "").upper(): item for item in pool}
        thesis_by_code = {str(item.get("stock_code") or "").upper(): item for item in thesis_changes}
        financial_by_code = {str(item.get("stock_code") or "").upper(): item for item in financial_changes}
        business_by_code = {str(item.get("stock_code") or "").upper(): item for item in business_changes}
        # 深度低估列表已经取过历史支撑；重点观察复用它，避免重复查询。
        support_cache: dict[str, dict[str, Any] | None] = {
            str(item.get("stock_code") or "").upper(): dict(item.get("historical_support") or {})
            for item in deeply_undervalued
        }
        watchlist: list[dict[str, Any]] = []
        if focus_available:
            # A 级名单已按研究优先级排序且不超过 10 家；行情或估值证据不完整的
            # 成员当日跳过，不进入对外重点观察。
            for focus in focus_a:
                if len(watchlist) >= 10:
                    break
                code = str(focus.get("stock_code") or "").upper()
                item = pool_by_code.get(code)
                if item is None:
                    continue
                if any(item.get(field) is None for field in (
                    "current_price", "fair_value_low", "fair_value_mid", "fair_value_high",
                )):
                    continue
                watchlist.append(self._watchlist_item(
                    item,
                    code,
                    research_as_of=research_as_of,
                    risks=risks,
                    thesis_by_code=thesis_by_code,
                    financial_by_code=financial_by_code,
                    business_by_code=business_by_code,
                    support_cache=support_cache,
                    focus_reasons=[str(reason) for reason in focus.get("focus_reasons") or []],
                    focus_cautions=[str(note) for note in focus.get("focus_cautions") or []],
                ))
            return watchlist
        for item in deeply_undervalued:
            if len(watchlist) >= 10:
                break
            if any(item.get(field) is None for field in (
                "current_price", "fair_value_low", "fair_value_mid", "fair_value_high",
            )):
                continue
            code = str(item.get("stock_code") or "").upper()
            watchlist.append(self._watchlist_item(
                item,
                code,
                research_as_of=research_as_of,
                risks=risks,
                thesis_by_code=thesis_by_code,
                financial_by_code=financial_by_code,
                business_by_code=business_by_code,
                support_cache=support_cache,
            ))
        return watchlist

    def _watchlist_item(
        self,
        item: dict[str, Any],
        code: str,
        *,
        research_as_of: str,
        risks: dict[str, dict[str, Any] | None],
        thesis_by_code: dict[str, dict[str, Any]],
        financial_by_code: dict[str, dict[str, Any]],
        business_by_code: dict[str, dict[str, Any]],
        support_cache: dict[str, dict[str, Any] | None] | None = None,
        focus_reasons: list[str] | None = None,
        focus_cautions: list[str] | None = None,
    ) -> dict[str, Any]:
        change: str | None = None
        thesis = thesis_by_code.get(code)
        if thesis:
            flags = [str(value) for value in thesis.get("changes") or []]
            thesis_status = str(thesis.get("thesis_status") or "").upper()
            thesis_change: list[str] = []
            if thesis_status == "WEAKENING":
                thesis_change.append("核心研究逻辑转弱")
            elif thesis_status == "FALSIFIED":
                thesis_change.append("核心研究逻辑出现证伪信号")
            if "新增 active Challenge Evidence" in flags:
                thesis_change.append("出现新增挑战证据")
            change = "；".join(thesis_change) or "核心研究逻辑出现需要复核的变化"
        elif business := business_by_code.get(code):
            change = str(business.get("summary") or "").strip() or None
        elif financial := financial_by_code.get(code):
            metric_key = str(financial.get("metric") or "")
            metric = _FINANCIAL_METRIC_LABELS.get(metric_key, metric_key or "财务指标")
            percent = financial.get("change_percent")
            change = (
                f"{metric}变动 {percent:.2f}%"
                if isinstance(percent, (int, float))
                else f"{metric}出现更新"
            )
        current_price = item.get("current_price")
        fair_value_mid = item.get("fair_value_mid")
        valuation_gap_percent: float | None = None
        try:
            if float(current_price) > 0 and float(fair_value_mid) > 0:
                valuation_gap_percent = round((float(fair_value_mid) / float(current_price) - 1) * 100, 2)
        except (TypeError, ValueError):
            pass
        risk = risks.get(code)
        if support_cache is not None and code in support_cache:
            support = dict(support_cache[code] or {})
        else:
            support = self._historical_support(code, research_as_of)
            if support_cache is not None:
                support_cache[code] = dict(support)
        return {
            "stock_code": code,
            "company_name": str(item.get("company_name") or code),
            "industry_name": str(item.get("industry_name") or ""),
            "current_price": current_price,
            "fair_value_low": item.get("fair_value_low"),
            "fair_value_mid": item.get("fair_value_mid"),
            "fair_value_high": item.get("fair_value_high"),
            "valuation_gap_percent": valuation_gap_percent,
            "valuation_caveat": self._valuation_caveat(valuation_gap_percent),
            "historical_support": support,
            "research_change": change,
            "research_priority_reason": "；".join(focus_reasons) if focus_reasons else "当前处于深度低估状态",
            "research_cautions": list(focus_cautions or []),
            "risk_summary": (
                _specific_counterpoint(risk)
                if risk and _risk_label(risk) not in {"暂无明显风险", "资料不足"}
                else None
            ),
            "company_research_url": _company_url(code, self.web_base_url),
        }

    def _executive_situations(
        self,
        *,
        risks: dict[str, dict[str, Any] | None],
        thesis_changes: list[dict[str, Any]],
        low_value_leader_table: list[dict[str, Any]],
        previous_brief: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        situations: list[dict[str, Any]] = []
        seen: set[str] = set()
        previous_as_of = str((previous_brief or {}).get("research_as_of") or "")
        previous_payload = dict((previous_brief or {}).get("brief_payload") or {})
        previous_rows = {
            str(item.get("stock_code") or "").upper(): item
            for item in list(previous_payload.get("low_value_leader_table") or [])
        }

        def add(code: str, company_name: str, basis: str, impact: str) -> None:
            if code in seen or len(situations) >= 3:
                return
            situations.append({
                "stock_code": code,
                "company_name": company_name,
                "basis": basis,
                "impact": impact,
            })
            seen.add(code)

        for item in thesis_changes:
            code = str(item.get("stock_code") or "").upper()
            current_status = str(item.get("thesis_status") or "").upper()
            previous_status = str(item.get("previous_status") or "").upper()
            evidence = "；".join(str(value) for value in item.get("challenge_evidence") or [] if value)
            if not evidence and previous_status == current_status:
                continue
            if previous_status and previous_status != current_status:
                basis = f"Thesis 状态由 {previous_status} 调整为 {current_status}"
            elif evidence:
                basis = f"新增挑战证据：{evidence}"
            else:
                continue
            if evidence and previous_status and previous_status != current_status:
                basis = f"{basis}；新增挑战证据：{evidence}"
            impact = (
                "核心逻辑的成立条件受到挑战，原有盈利预测和合理价值假设需要下调复核。"
                if current_status in {"WEAKENING", "FALSIFIED"}
                else "核心逻辑的判断状态已调整，盈利预测和合理价值假设需要据此重新审视。"
            )
            add(code, str(item.get("company_name") or code), basis, impact)

        if previous_as_of:
            for item in low_value_leader_table:
                code = str(item.get("stock_code") or "").upper()
                previous = dict(previous_rows.get(code) or {})
                if not previous:
                    continue
                current_mid = item.get("fair_value_mid")
                previous_mid = previous.get("fair_value_mid")
                try:
                    revision = (float(current_mid) / float(previous_mid) - 1) * 100
                except (TypeError, ValueError, ZeroDivisionError):
                    revision = None
                if isinstance(revision, float) and abs(revision) >= 10:
                    direction = "上调" if revision > 0 else "下调"
                    add(
                        code,
                        str(item.get("company_name") or code),
                        f"合理价值中枢较 {previous_as_of} {direction} {abs(revision):.2f}%",
                        (
                            "估值锚已实质上移，需要重新评估上行空间与盈利兑现条件。"
                            if revision > 0
                            else "估值锚已实质下移，需要重新评估盈利假设和安全边际。"
                        ),
                    )
                    continue
                previous_label = str(previous.get("valuation_label") or "")
                current_label = str(item.get("valuation_label") or "")
                if previous_label and current_label and previous_label != current_label:
                    add(
                        code,
                        str(item.get("company_name") or code),
                        f"估值状态较 {previous_as_of} 由“{previous_label}”变为“{current_label}”",
                        "价格与合理价值的关系已跨越原有区间，需要重新审视安全边际而非单纯追踪价格波动。",
                    )

            for code, risk in risks.items():
                previous_risk = self.risk_repository.get("CN", code, previous_as_of)
                if _risk_tier(risk) <= _risk_tier(previous_risk):
                    continue
                summary = _specific_counterpoint(risk)
                if not summary:
                    continue
                add(
                    code,
                    next((str(item.get("company_name") or code) for item in low_value_leader_table if item.get("stock_code") == code), code),
                    f"风险状态较 {previous_as_of} 升级：{summary}",
                    "新增风险改变了原有安全边际，需要重新审视盈利预测的可靠性与合理价值折价。",
                )
        return situations

    @staticmethod
    def _render_executive(
        *,
        research_as_of: str,
        situations: list[dict[str, Any]],
        watchlist: list[dict[str, Any]],
        watchlist_basis: str = "FOCUS_A",
    ) -> str:
        def cell(value: Any) -> str:
            return str(value if value is not None else "—").replace("|", "／").replace("\n", "；")

        lines = ["【投资研究日报】", f"研究日期：{research_as_of}", "", "一、今日投资判断变化"]
        if situations:
            lines.append("- 仅列示相对上一份日报已经改变核心逻辑、合理价值锚或风险状态的事项。")
            for item in situations:
                lines.append(f"- {item['company_name']} / {item['stock_code']}")
                lines.append(f"  变化依据：{item['basis']}。")
                lines.append(f"  判断影响：{item['impact']}")
        else:
            lines.append("- 相对上一份日报，今日无已确认的核心逻辑、合理价值锚或风险状态变化。")
        lines.extend(["", "二、重点研究观察"])
        if watchlist_basis == "FOCUS_A":
            lines.append("- 名单与「机会与风险」页 A 级重点研究一致：已通过风险与资料条件筛选，最多 10 家。")
        else:
            lines.append("- 机会与风险筛选当日不可用，以下沿用深度低估名单。")
        if watchlist:
            lines.extend([
                "| 公司 / 代码 | 行业 | 现价 | 合理价值（低 / 中 / 高） | 中位值差距 | 历史价格支撑 | 研究重点 |",
                "| --- | --- | ---: | ---: | ---: | --- | --- |",
            ])
            for item in watchlist:
                support = item.get("historical_support") or {}
                support_text = (
                    f"{support['low']}–{support['high']}"
                    if support.get("low") is not None and support.get("high") is not None
                    else "—"
                )
                gap = item.get("valuation_gap_percent")
                gap_text = f"{gap:.2f}%" if isinstance(gap, (int, float)) else "—"
                focus = str(item.get("research_change") or item.get("research_priority_reason") or "")
                if item.get("research_cautions"):
                    focus = f"{focus}；注意：{'；'.join(str(note) for note in item['research_cautions'])}"
                if item.get("valuation_caveat"):
                    focus = f"{focus}；{item['valuation_caveat']}"
                if item.get("risk_summary"):
                    focus = f"{focus}；反证：{item['risk_summary']}"
                values = " / ".join(cell(item.get(key)) for key in (
                    "fair_value_low", "fair_value_mid", "fair_value_high",
                ))
                lines.append(
                    f"| {cell(item['company_name'])} / {cell(item['stock_code'])} | {cell(item['industry_name'])} | "
                    f"{cell(item['current_price'])} | {values} | {gap_text} | {support_text} | {cell(focus)} |"
                )
        else:
            lines.append(
                "- 今日暂无通过风险与资料条件筛选的 A 级重点研究。"
                if watchlist_basis == "FOCUS_A"
                else "- 今日暂无可纳入重点观察的完整估值证据。"
            )
        lines.extend([
            "",
            "三、低估龙头表格",
            f"- [打开当前低估龙头池（不保留历史）]({LOW_VALUE_LEADER_BITABLE_URL})",
            f"- 链接：{LOW_VALUE_LEADER_BITABLE_URL}",
        ])
        return "\n".join(lines)


_service: InvestmentResearchDailyBriefService | None = None


def get_investment_research_daily_brief_service() -> InvestmentResearchDailyBriefService:
    global _service
    if _service is None:
        _service = InvestmentResearchDailyBriefService()
    return _service

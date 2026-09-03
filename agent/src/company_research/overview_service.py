"""Read-only projection across existing company research records.

The Overview deliberately never calls an LLM, creates a snapshot, refreshes a
Review, or writes Evidence/Thesis state.  It is only a compact presentation of
persisted Financial Research, Business Research and Company Thesis records.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from src.business_research.citations import BusinessClaimCitationResolver
from src.business_research.store import BusinessResearchStore
from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.company_thesis.history_store import CompanyThesisHistoryRepository
from src.company_thesis.review_store import CompanyThesisReviewRepository
from src.company_thesis.store import CompanyThesisRepository
from src.financial_analysis.citations import FinancialClaimCitationResolver
from src.financial_analysis.service import FinancialAnalysisService
from src.financial_analysis.store import FinancialAnalysisStore
from src.research_workspace.store import normalize_market, normalize_symbol


_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_THESIS_LABELS = {
    "FORMING": "正在形成",
    "STRENGTHENING": "逻辑正在增强",
    "UNCHANGED": "基本没有变化",
    "WEAKENING": "逻辑正在减弱",
    "FALSIFIED": "核心逻辑已被明显否定",
}


def _available_as_of(
    row: dict[str, Any] | None, as_of: str | None, *, data_key: str = "data_as_of", require_created: bool = True,
) -> bool:
    """Return whether a persisted item was both sourced and saved by ``as_of``."""
    if not row or not as_of:
        return bool(row)
    target = str(as_of)[:10]
    created = str(row.get("created_at") or "")[:10]
    source = str(row.get(data_key) or row.get("source_data_as_of") or "")[:10]
    return bool((not require_created or (created and created <= target)) and (not source or source <= target))


class CompanyResearchOverviewService:
    """Assemble one company's persisted research without side effects."""

    def __init__(
        self,
        *,
        financial_store: FinancialAnalysisStore | None = None,
        business_store: BusinessResearchStore | None = None,
        thesis_repository: CompanyThesisRepository | None = None,
        evidence_repository: CompanyThesisEvidenceRepository | None = None,
        review_repository: CompanyThesisReviewRepository | None = None,
        history_repository: CompanyThesisHistoryRepository | None = None,
        db_path: Path | None = None,
    ) -> None:
        root = db_path
        self.financial_store = financial_store or FinancialAnalysisStore(root)
        resolved_path = self.financial_store.db_path
        self.business_store = business_store or BusinessResearchStore(resolved_path)
        self.thesis_repository = thesis_repository or CompanyThesisRepository(resolved_path)
        self.evidence_repository = evidence_repository or CompanyThesisEvidenceRepository(resolved_path)
        self.review_repository = review_repository or CompanyThesisReviewRepository(resolved_path)
        self.history_repository = history_repository or CompanyThesisHistoryRepository(resolved_path)
        self._owns = {
            "financial": financial_store is None,
            "business": business_store is None,
            "thesis": thesis_repository is None,
            "evidence": evidence_repository is None,
            "review": review_repository is None,
            "history": history_repository is None,
        }

    def close(self) -> None:
        if self._owns["financial"]:
            self.financial_store.close()
        if self._owns["business"]:
            self.business_store.close()
        if self._owns["thesis"]:
            self.thesis_repository.close()
        if self._owns["evidence"]:
            self.evidence_repository.close()
        if self._owns["review"]:
            self.review_repository.close()
        if self._owns["history"]:
            self.history_repository.close()

    @staticmethod
    def _claim_text(claim: dict[str, Any]) -> str:
        return str(claim.get("text") or claim.get("statement") or "").strip()

    @staticmethod
    def _compact_evidence(row: dict[str, Any]) -> dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        citations = metadata.get("resolved_citations") if isinstance(metadata.get("resolved_citations"), list) else []
        return {
            "evidence_id": row.get("evidence_id"),
            "evidence_type": row.get("evidence_type"),
            "effect": row.get("effect"),
            "claim": row.get("claim"),
            "summary": row.get("summary"),
            "confidence": row.get("confidence"),
            "created_by": row.get("created_by"),
            "created_at": row.get("created_at"),
            "source_type": row.get("source_type"),
            "source_id": row.get("source_id"),
            "source_ref": row.get("source_ref"),
            "citations": citations,
            "research_domain": metadata.get("research_domain") or row.get("evidence_type"),
        }

    @staticmethod
    def _metric_text(metric: str, value: Any) -> str | None:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        direction = "增长" if amount >= 0 else "下降"
        absolute = f"{abs(amount):.1f}%"
        if metric == "revenue":
            return f"最近一期收入同比{direction}{absolute}，反映销售规模正在变化。"
        if metric == "net_profit":
            return f"最近一期净利润同比{direction}{absolute}，需要结合收入和现金流一起判断经营质量。"
        if metric == "operating_cash_flow":
            return f"最近一期经营活动现金流同比{direction}{absolute}，这是公司把经营成果转成现金的情况。"
        if metric == "roe":
            return f"最近一期 ROE 同比{'提高' if amount >= 0 else '下降'}{absolute}；ROE 是公司使用股东资金赚钱效率的一个指标。"
        if metric == "debt_ratio":
            return f"最近一期资产负债率同比{'上升' if amount >= 0 else '下降'}{absolute}，需要继续关注负债压力是否变化。"
        return None

    @staticmethod
    def _financial_status(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if snapshot is None:
            return {"status": "UNKNOWN", "analysis_status": "NOT_CREATED", "message": "尚未生成财务研究快照。"}
        feature = str(snapshot.get("feature_status") or "UNKNOWN")
        forecast = str(snapshot.get("forecast_status") or "UNKNOWN")
        analysis = str(snapshot.get("analysis_status") or "NOT_RUN")
        status = "READY" if feature == "READY" else "PARTIAL" if feature in {"PARTIAL", "LIMITED"} else "UNKNOWN"
        return {"status": status, "feature_status": feature, "forecast_status": forecast, "analysis_status": analysis,
                "message": "AI 财报解读尚未生成。" if analysis != "COMPLETED" else "财务研究已就绪。"}

    def _financial_summary(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        status = self._financial_status(snapshot)
        if snapshot is None:
            return {**status, "items": [], "claims": [], "snapshot_id": None}
        manifest = FinancialAnalysisService._evidence_manifest(snapshot)
        resolver = FinancialClaimCitationResolver()
        items: list[dict[str, Any]] = []
        change_order = ("revenue", "net_profit", "operating_cash_flow", "roe", "debt_ratio")
        changes = snapshot.get("feature", {}).get("latest_changes") if isinstance(snapshot.get("feature"), dict) else []
        by_metric = {str(item.get("metric")): item for item in changes if isinstance(item, dict)}
        for metric in change_order:
            row = by_metric.get(metric)
            if not row:
                continue
            text = self._metric_text(metric, row.get("change_percent"))
            if not text:
                continue
            period = str(row.get("report_date") or "").replace("-", "")
            source_key = f"FEATURE_{metric.upper()}_CHANGE_{period[:4]}{'Q1' if period.endswith('0331') else 'Q3' if period.endswith('0930') else 'H1' if period.endswith('0630') else ''}"
            # The canonical helper already knows the complete source shape.
            citations = [resolver.resolve_citation(source_key, manifest)] if source_key in manifest else []
            items.append({"category": metric, "text": text, "citations": citations, "source_keys": [source_key] if citations else []})
        analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
        claims = analysis.get("claims") if isinstance(analysis.get("claims"), list) else []
        resolved = resolver.resolve_snapshot(snapshot, fallback_manifest=manifest)
        resolved_claims = (resolved.get("analysis") or {}).get("claims") or []
        if len(items) < 3:
            for claim in resolved_claims:
                text = self._claim_text(claim)
                if not text or str(claim.get("type") or "").upper() == "UNKNOWN":
                    continue
                items.append({"category": str(claim.get("type") or "FACT"), "text": text,
                              "citations": claim.get("citations") or [], "source_keys": claim.get("source_keys") or []})
                if len(items) >= 5:
                    break
        feature = snapshot.get("feature") if isinstance(snapshot.get("feature"), dict) else {}
        quality = feature.get("data_quality") if isinstance(feature.get("data_quality"), dict) else {}
        return {**status, "snapshot_id": snapshot.get("id"), "as_of": snapshot.get("as_of"),
                "latest_announcement_date": quality.get("latest_announcement_date"),
                "latest_report_date": quality.get("latest_report_date"),
                "items": items[:5], "claims": resolved_claims, "analysis_summary": analysis.get("executive_summary")}

    def _business_summary(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if snapshot is None:
            return {"status": "UNKNOWN", "message": "尚未生成公司经营研究。", "claims": [], "changes": [], "snapshot_id": None}
        source = snapshot.get("snapshot") if isinstance(snapshot.get("snapshot"), dict) else {}
        analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
        resolved = BusinessClaimCitationResolver().resolve_snapshot({**source, "id": snapshot.get("id"), "analysis": analysis})
        claims = (resolved.get("analysis") or {}).get("claims") or []
        model_claim = next((item for item in claims if item.get("topic") == "BUSINESS_MODEL" and item.get("type") != "UNKNOWN"), None)
        change_claims = [item for item in claims if item.get("topic") == "BUSINESS_CHANGE"]
        changes = [self._claim_text(item) for item in change_claims if self._claim_text(item)]
        if not changes:
            changes = ["目前缺少前后两期可比较的经营资料，暂时无法判断经营方向是否发生明显变化。"]
        main_business = str(source.get("main_business") or "UNKNOWN")
        products = source.get("products") if isinstance(source.get("products"), list) else []
        return {
            "status": str((source.get("data_quality") or {}).get("status") or "PARTIAL"),
            "snapshot_id": snapshot.get("id"), "data_as_of": source.get("data_as_of"),
            "main_business": main_business,
            "description": "公司主要业务包括：" + main_business + "。" if main_business != "UNKNOWN" else "目前资料不足，尚无法说明公司主要做什么。",
            "products": products,
            "product_note": source.get("product_note"),
            "business_model": self._claim_text(model_claim) if model_claim else "资料不足，暂时无法可靠判断公司主要通过什么方式获得收入。",
            "changes": changes,
            "claims": claims,
            "traceability_status": resolved.get("traceability_status"),
        }

    @staticmethod
    def _watch_items(
        thesis: dict[str, Any] | None,
        financial: dict[str, Any],
        business: dict[str, Any],
        challenges: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        candidates: list[tuple[str, str]] = []
        for item in (thesis or {}).get("invalid_conditions") or []:
            text = str(item.get("condition") or "").strip() if isinstance(item, dict) else str(item).strip()
            if text:
                candidates.append(("THESIS_INVALID_CONDITION", text))
        analysis_claims = financial.get("claims") if isinstance(financial.get("claims"), list) else []
        for claim in analysis_claims:
            if str(claim.get("type") or "").upper() == "UNKNOWN" and CompanyResearchOverviewService._claim_text(claim):
                candidates.append(("FINANCIAL_UNKNOWN", CompanyResearchOverviewService._claim_text(claim)))
        business_claims = business.get("claims") if isinstance(business.get("claims"), list) else []
        for claim in business_claims:
            if str(claim.get("type") or "").upper() == "UNKNOWN" and CompanyResearchOverviewService._claim_text(claim):
                candidates.append(("BUSINESS_UNKNOWN", CompanyResearchOverviewService._claim_text(claim)))
        for evidence in challenges:
            if str(evidence.get("claim") or "").strip():
                candidates.append(("CHALLENGE_EVIDENCE", f"继续核验：{str(evidence['claim']).strip()}"))
        for metric in ((financial.get("key_metrics_to_monitor") or []) if isinstance(financial, dict) else []):
            candidates.append(("FINANCIAL_MONITOR", f"继续观察：{metric}。"))
        seen: set[str] = set()
        output: list[dict[str, str]] = []
        for source, text in candidates:
            key = text.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            output.append({"source": source, "text": key})
            if len(output) == 5:
                break
        return output

    @staticmethod
    def _projection(
        market: str,
        stock_code: str,
        as_of: str | None,
        *,
        thesis: dict[str, Any] | None = None,
        financial_snapshot: dict[str, Any] | None = None,
        business_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            from src.value_watchpoints.service import ValueWatchpointProjectionService

            return ValueWatchpointProjectionService(
                thesis_loader=lambda *_args, **_kwargs: thesis,
                risk_loader=lambda *_args, **_kwargs: {},
                financial_loader=lambda *_args, **_kwargs: financial_snapshot or {},
                business_loader=lambda *_args, **_kwargs: business_snapshot or {},
                strategy_loader=lambda *_args, **_kwargs: {},
                normalized_loader=lambda *_args, **_kwargs: {},
                cycle_loader=lambda *_args, **_kwargs: {},
                reliability_loader=lambda *_args, **_kwargs: {},
                moat_loader=lambda *_args, **_kwargs: {},
                capital_loader=lambda *_args, **_kwargs: {},
                deep_loader=lambda *_args, **_kwargs: {},
            ).get_watchpoints(market, stock_code, research_as_of=as_of)
        except Exception:  # noqa: BLE001
            return {}

    def get_overview(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        # Direct repository/store reads only: do not call FinancialAnalysisService.get,
        # BusinessResearchService.get or ReviewService.get_latest_review because those
        # higher-level helpers can prepare snapshots or refresh stale state.
        target = str(as_of)[:10] if as_of else None
        financial_snapshot = self.financial_store.latest(symbol, as_of=target)
        if not _available_as_of(financial_snapshot, target, data_key="as_of", require_created=False):
            financial_snapshot = None
        business_snapshot = self.business_store.latest(symbol, as_of=target)
        if not _available_as_of(business_snapshot, target, require_created=False):
            business_snapshot = None
        if target:
            thesis = next((row for row in self.thesis_repository.list_thesis_versions(normalized_market, symbol)
                           if _available_as_of(row, target, data_key="source_data_as_of")), None)
        else:
            thesis = self.thesis_repository.get_current_thesis(normalized_market, symbol)
        active = self.evidence_repository.list_active_evidence_for_thesis(thesis["thesis_id"]) if thesis else []
        active = [row for row in active if _available_as_of(row, target)]
        # Newer evidence wins within the same confidence tier; confidence still
        # remains the primary ordering required by the overview.
        active = sorted(active, key=lambda row: str(row.get("created_at") or ""), reverse=True)
        active = sorted(active, key=lambda row: _CONFIDENCE_ORDER.get(str(row.get("confidence") or ""), 9))
        supporting = [self._compact_evidence(row) for row in active if row.get("effect") == "SUPPORT"][:5]
        challenging = [self._compact_evidence(row) for row in active if row.get("effect") == "CHALLENGE"][:5]
        neutral_count = sum(1 for row in active if row.get("effect") == "NEUTRAL")
        if target:
            review = next((row for row in self.review_repository.list_reviews_for_company(normalized_market, symbol)
                           if _available_as_of(row, target)), None)
            history = [row for row in self.history_repository.list_history_for_company(normalized_market, symbol)
                       if _available_as_of(row, target)]
        else:
            review = self.review_repository.get_latest_review(normalized_market, symbol)
            history = self.history_repository.list_history_for_company(normalized_market, symbol)
        financial = self._financial_summary(financial_snapshot)
        financial["key_metrics_to_monitor"] = list(((financial_snapshot or {}).get("analysis") or {}).get("key_metrics_to_monitor") or [])[:5]
        business = self._business_summary(business_snapshot)
        company_name = (
            (business_snapshot or {}).get("company_name")
            or (financial_snapshot or {}).get("stock_name")
            or symbol
        )
        projection = self._projection(
            normalized_market, symbol, target,
            thesis=thesis, financial_snapshot=financial_snapshot, business_snapshot=business_snapshot,
        )
        top_watchpoints = list((projection or {}).get("top_watchpoints") or [])
        data_gaps = list((projection or {}).get("data_gaps") or [])
        watch_items = [
            {"source": str(item.get("category") or "WATCH"), "text": str(item.get("title") or ""), **item}
            for item in top_watchpoints
        ] if top_watchpoints else self._watch_items(thesis, financial, business, challenging)
        thesis_view = None if thesis is None else {
            "thesis_id": thesis.get("thesis_id"), "core_thesis": thesis.get("core_thesis"), "title": thesis.get("title"),
            "status": thesis.get("status"), "status_label": _THESIS_LABELS.get(str(thesis.get("status") or ""), thesis.get("status")),
            "confidence": thesis.get("confidence"), "version": thesis.get("version"), "updated_at": thesis.get("updated_at"),
            "invalid_conditions": thesis.get("invalid_conditions") or [], "history_count": len(history),
        }
        review_view = None if review is None else {
            "review_id": review.get("review_id"), "review_status": review.get("review_status"), "is_stale": bool(review.get("is_stale")),
            "support_count": review.get("support_count"), "challenge_count": review.get("challenge_count"),
            "neutral_count": review.get("neutral_count"), "recommended_status": review.get("recommended_status"),
            "recommended_confidence": review.get("recommended_confidence"), "review_reason": review.get("review_reason"),
            "created_at": review.get("created_at"),
        }
        return {
            "company": {"market": normalized_market, "stock_code": symbol, "stock_name": company_name},
            "research_as_of": target,
            "business_summary": business,
            "financial_summary": financial,
            "supporting_evidence": supporting,
            "challenging_evidence": challenging,
            "neutral_evidence_count": neutral_count,
            "thesis": thesis_view,
            "review": review_view,
            "watch_items": watch_items,
            "top_watchpoints": top_watchpoints,
            "data_gaps": data_gaps,
            "data_status": {
                "financial": financial.get("status"), "business": business.get("status"),
                "thesis": "CREATED" if thesis else "NOT_CREATED",
                "review": "NOT_CREATED" if review is None else "STALE" if review.get("is_stale") else "CURRENT",
            },
        }


_service: CompanyResearchOverviewService | None = None


def get_company_research_overview_service() -> CompanyResearchOverviewService:
    global _service
    if _service is None:
        _service = CompanyResearchOverviewService()
    return _service

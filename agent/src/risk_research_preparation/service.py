"""Prepare bounded, PIT-safe source material for low-value risk research."""

from __future__ import annotations

import threading
from typing import Any

from src.business_research import BusinessResearchService, get_business_research_service
from src.company_thesis import CompanyThesisRepository
from src.disclosure_materials import DisclosureMaterialService, get_disclosure_material_service
from src.financial_analysis.service import FinancialAnalysisService, get_financial_analysis_service
from src.level3_leaders.business_profiles import CompanyBusinessProfileService
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.tdx_data.financial_history import FinancialHistoryService

from .store import RiskResearchPreparationRepository


COMPONENT_STATUSES = {"READY", "PARTIAL", "MISSING", "FAILED"}
PERIODIC_REPORT_KINDS = {"ANNUAL", "SEMIANNUAL", "Q1", "Q3"}
_schedule_lock = threading.Lock()
_scheduled_thread: threading.Thread | None = None


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _status_from_profile(profile: dict[str, Any] | None, *, as_of: str) -> tuple[str, dict[str, Any]]:
    if not profile:
        return "MISSING", {"reason": "MISSING_SOURCE"}
    source_date = _day(profile.get("updated_at"))
    if source_date and source_date > as_of:
        return "MISSING", {"reason": "MISSING_SOURCE", "source_date": source_date}
    status = str(profile.get("data_status") or "MISSING")
    mapped = "READY" if status == "REAL" else "PARTIAL" if status == "PARTIAL" else "MISSING"
    return mapped, {"source_date": source_date, "profile_status": status}


class RiskResearchPreparationService:
    """A preparation worker, intentionally separate from RiskResearchService."""

    def __init__(
        self,
        *,
        repository: RiskResearchPreparationRepository | None = None,
        pool_repository: LowValueLeaderPoolRepository | None = None,
        financial_service: FinancialAnalysisService | Any | None = None,
        financial_history: FinancialHistoryService | Any | None = None,
        business_profiles: CompanyBusinessProfileService | Any | None = None,
        business_research: BusinessResearchService | Any | None = None,
        disclosure_service: DisclosureMaterialService | Any | None = None,
        thesis_repository: CompanyThesisRepository | Any | None = None,
        risk_snapshot_service: Any | None = None,
    ) -> None:
        self.repository = repository or RiskResearchPreparationRepository()
        self.pool_repository = pool_repository or LowValueLeaderPoolRepository(self.repository.db_path)
        self.financial_service = financial_service or get_financial_analysis_service()
        self.financial_history = financial_history or FinancialHistoryService()
        self.business_profiles = business_profiles or CompanyBusinessProfileService()
        self.business_research = business_research or get_business_research_service()
        self.disclosure_service = disclosure_service or get_disclosure_material_service()
        self.thesis_repository = thesis_repository or CompanyThesisRepository(self.repository.db_path)
        self.risk_snapshot_service = risk_snapshot_service

    @staticmethod
    def _component_status(value: Any) -> str:
        return value if value in COMPONENT_STATUSES else "MISSING"

    def _financial_status(self, stock_code: str, *, as_of: str) -> tuple[str, dict[str, Any]]:
        snapshot = self.financial_service.get_saved_resolved_analysis(stock_code, as_of=as_of)
        history = self.financial_history.query(stock_code, as_of=as_of)
        rows = list(history.get("items") or history.get("rows") or []) if isinstance(history, dict) else list(history or [])
        if not snapshot and not rows:
            return "MISSING", {"reason": "MISSING_SOURCE", "history_periods": 0}
        feature_status = str((snapshot or {}).get("feature_status") or "MISSING")
        if feature_status == "READY" and rows:
            status = "READY"
        elif snapshot or rows:
            status = "PARTIAL"
        else:
            status = "MISSING"
        return status, {
            "snapshot_as_of": _day((snapshot or {}).get("as_of")),
            "feature_status": feature_status,
            "forecast_status": str((snapshot or {}).get("forecast_status") or "MISSING"),
            "history_periods": len(rows),
        }

    def _business_research_status(self, stock_code: str, *, as_of: str) -> tuple[str, str, dict[str, Any]]:
        existing = self.business_research.get_saved_research(stock_code, as_of=as_of)
        if existing and str(existing.get("analysis_status") or "") == "COMPLETED":
            return "READY", "REUSED", {"snapshot_id": existing.get("id"), "data_as_of": _day(existing.get("data_as_of"))}
        # Analyze is intentionally called only by this worker.  Page GETs and
        # chats remain read-only; the service's own source/citation contract is
        # retained without duplicating its logic here.
        result = self.business_research.analyze(stock_code, as_of=as_of)
        analysis_status = str(result.get("analysis_status") or "MISSING")
        metadata = {"snapshot_id": result.get("id"), "data_as_of": _day(result.get("data_as_of")), "analysis_status": analysis_status}
        if analysis_status == "COMPLETED":
            return "READY", "PREPARED", metadata
        if analysis_status == "FAILED":
            metadata["error"] = str(result.get("agent_error") or "business research failed")
            return "FAILED", "FAILED", metadata
        if analysis_status == "CONFIGURATION_REQUIRED":
            metadata["reason"] = "MISSING_SOURCE"
            return "PARTIAL", "NOT_READY", metadata
        return "PARTIAL", "NOT_READY", metadata

    @staticmethod
    def _disclosure_coverage(documents: list[dict[str, Any]], materials: list[dict[str, Any]], *, profile: dict[str, Any] | None,
                             industry_name: str) -> tuple[str, dict[str, Any]]:
        ready_docs = [item for item in documents if str(item.get("extraction_status") or "") == "READY"]
        kinds = {str(item.get("report_kind") or "") for item in ready_docs}
        status = "READY" if PERIODIC_REPORT_KINDS.issubset(kinds) else "PARTIAL" if ready_docs else "MISSING"
        found_types = {str(item.get("material_type") or "") for item in materials if str(item.get("status") or "") == "FOUND"}
        raw_text = " ".join(
            str((profile or {}).get(field) or "") for field in ("main_business", "main_products", "business_scope", "company_description")
        ) + " " + str(industry_name or "")
        for item in materials:
            for excerpt in item.get("excerpts") or []:
                if isinstance(excerpt, dict):
                    raw_text += " " + str(excerpt.get("text") or "")
        ppp_applicable = "PPP" in raw_text.upper()
        material_coverage: dict[str, str] = {}
        for material_type in (
            "ACCOUNTS_RECEIVABLE_AGEING", "RECEIVABLES_IMPAIRMENT", "CUSTOMER_CONCENTRATION",
            "BUSINESS_PRODUCT_STRUCTURE", "DEBT_MATURITY", "GUARANTEES_CONTINGENCIES",
        ):
            material_coverage[material_type] = "FOUND" if material_type in found_types else (
                "NOT_FOUND_IN_COLLECTED_DOCUMENTS" if ready_docs else "NOT_COLLECTED"
            )
        material_coverage["PPP_COLLECTION"] = (
            "FOUND" if "PPP_COLLECTION" in found_types else
            ("NOT_FOUND_IN_COLLECTED_DOCUMENTS" if ppp_applicable and ready_docs else "NOT_COLLECTED") if ppp_applicable
            else "NOT_APPLICABLE"
        )
        return status, {
            "document_count": len(ready_docs), "report_kinds": sorted(kinds), "material_coverage": material_coverage,
            "ppp_applicability": "APPLICABLE" if ppp_applicable else "NOT_APPLICABLE",
            "latest_announcement_date": max((_day(item.get("announcement_date")) for item in ready_docs), default=None),
        }

    def _disclosure_status(self, stock_code: str, *, as_of: str, profile: dict[str, Any] | None,
                           industry_name: str) -> tuple[str, str, dict[str, Any]]:
        before = self.disclosure_service.get_materials(stock_code, as_of=as_of)
        status, metadata = self._disclosure_coverage(
            list(before.get("documents") or []), list(before.get("materials") or []), profile=profile, industry_name=industry_name,
        )
        if status == "READY":
            return status, "REUSED", metadata
        try:
            synced = self.disclosure_service.sync_periodic_reports(stock_code, as_of=as_of, max_documents_per_kind=1)
        except Exception as exc:
            metadata["error"] = f"{type(exc).__name__}: {exc}"[:500]
            return "FAILED", "FAILED", metadata
        status, metadata = self._disclosure_coverage(
            list(synced.get("documents") or []), list(synced.get("materials") or []), profile=profile, industry_name=industry_name,
        )
        metadata["sync"] = {key: synced.get(key) for key in ("available_reports", "selected_reports", "synced", "reused", "failed")}
        if int(synced.get("failed") or 0) > 0 and not metadata["document_count"]:
            return "FAILED", "FAILED", metadata
        return status, "PREPARED", metadata

    def _thesis_status(self, stock_code: str, *, as_of: str) -> tuple[str, dict[str, Any]]:
        thesis = self.thesis_repository.get_current_thesis("CN", stock_code.upper())
        if thesis and _day(thesis.get("created_at")) <= as_of:
            return "READY", {"thesis_id": thesis.get("thesis_id"), "version": thesis.get("version"), "created_at": thesis.get("created_at")}
        return "MISSING", {"reason": "NEEDS_HUMAN_THESIS"}

    @staticmethod
    def _overall(components: dict[str, str]) -> str:
        # Thesis is an explicitly non-blocking preparation gap.  It remains
        # visible but cannot turn otherwise collected source material into a
        # failed job.
        blocking = [components[key] for key in ("financial", "business_profile", "business_research", "disclosure")]
        if "FAILED" in blocking:
            return "FAILED"
        if all(value == "READY" for value in blocking):
            return "READY"
        if all(value == "MISSING" for value in blocking):
            return "MISSING"
        return "PARTIAL"

    def prepare_company(self, item: dict[str, Any], *, research_as_of: str | None = None) -> dict[str, Any]:
        market = str(item.get("market") or "CN").upper()
        stock_code = str(item["stock_code"]).upper()
        as_of = _day(research_as_of or item.get("source_as_of"))
        if not as_of:
            raise ValueError("research_as_of is required for risk preparation")
        company_name = str(item.get("company_name") or stock_code)
        industry_name = str(item.get("industry_name") or "")
        errors: list[str] = []

        financial, financial_meta = self._financial_status(stock_code, as_of=as_of)
        profile = self.business_profiles.profile(stock_code)
        business_profile, profile_meta = _status_from_profile(profile, as_of=as_of)
        try:
            business_research, business_action, business_meta = self._business_research_status(stock_code, as_of=as_of)
        except Exception as exc:
            business_research, business_action = "FAILED", "FAILED"
            business_meta = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            errors.append(f"business_research: {business_meta['error']}")
        try:
            disclosure, disclosure_action, disclosure_meta = self._disclosure_status(
                stock_code, as_of=as_of, profile=profile, industry_name=industry_name,
            )
        except Exception as exc:
            disclosure, disclosure_action = "FAILED", "FAILED"
            disclosure_meta = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            errors.append(f"disclosure: {disclosure_meta['error']}")
        thesis, thesis_meta = self._thesis_status(stock_code, as_of=as_of)
        components = {
            "financial": self._component_status(financial), "business_profile": self._component_status(business_profile),
            "business_research": self._component_status(business_research), "disclosure": self._component_status(disclosure),
            "thesis": self._component_status(thesis),
        }
        missing = []
        for key, value in components.items():
            if value == "READY":
                continue
            reason = (
                "NEEDS_HUMAN_THESIS" if key == "thesis" else
                "PREPARATION_FAILED" if value == "FAILED" else
                "PARTIAL_SOURCE" if value == "PARTIAL" else "MISSING_SOURCE"
            )
            missing.append({"capability": key, "status": value, "reason": reason})
        row = self.repository.upsert({
            "market": market, "stock_code": stock_code, "research_as_of": as_of, "company_name": company_name,
            "financial_status": components["financial"], "business_profile_status": components["business_profile"],
            "business_research_status": components["business_research"], "disclosure_status": components["disclosure"],
            "thesis_status": components["thesis"], "overall_status": self._overall(components),
            "missing_capabilities": missing, "last_error": "; ".join(errors),
            "metadata": {
                "job_type": "LOW_VALUE_RISK_DATA_PREPARATION", "financial": financial_meta,
                "business_profile": profile_meta, "business_research": {"action": business_action, **business_meta},
                "disclosure": {"action": disclosure_action, **disclosure_meta}, "thesis": thesis_meta,
                "industry_context": {
                    "industry_code": item.get("industry_code"), "industry_name": industry_name,
                    "leader_rank": item.get("leader_rank"), "leader_score": item.get("leader_score"),
                },
            },
        })
        prepared = self._prepare_provisional(row)
        return self._refresh_company_risk_snapshot(prepared)

    def _refresh_company_risk_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        """Refresh the list projection after source preparation, never on GET."""
        try:
            service = self.risk_snapshot_service
            if service is None:
                from src.low_value_risk_snapshot import get_low_value_pool_risk_snapshot_service
                service = get_low_value_pool_risk_snapshot_service()
            refreshed = service.refresh_company_snapshot(
                market=row["market"], stock_code=row["stock_code"], source_as_of=row["research_as_of"],
            )
            metadata = dict(row.get("metadata") or {})
            metadata["risk_snapshot_refresh"] = {
                "status": refreshed.get("status"),
                "source_as_of": row["research_as_of"],
                "error": refreshed.get("error"),
            }
            error = str(row.get("last_error") or "")
            if refreshed.get("status") == "FAILED":
                detail = str(refreshed.get("error") or "risk snapshot refresh failed")
                error = "; ".join(item for item in (error, f"risk_snapshot: {detail}") if item)[:2000]
            return self.repository.upsert({**row, "metadata": metadata, "last_error": error})
        except Exception as exc:
            # A list-projection failure must not discard already prepared
            # source material or downgrade the provisional-Thesis result.
            metadata = dict(row.get("metadata") or {})
            metadata["risk_snapshot_refresh"] = {"status": "FAILED", "source_as_of": row["research_as_of"], "error": f"{type(exc).__name__}: {exc}"[:500]}
            return self.repository.upsert({
                **row, "metadata": metadata,
                "last_error": "; ".join(item for item in (str(row.get("last_error") or ""), f"risk_snapshot: {type(exc).__name__}: {exc}") if item)[:2000],
            })

    def _prepare_provisional(self, row: dict[str, Any]) -> dict[str, Any]:
        """Auto-promote only a validated draft; never confirms a human Thesis."""
        service = None
        try:
            # This inexpensive guard is deliberately before Draft construction:
            # every current authority (AI, human, or legacy) is protected.
            current = self.thesis_repository.get_current_thesis(row["market"], row["stock_code"])
            if current:
                return self.repository.upsert({
                    **row, "draft_status": "REUSED", "validation_status": "SKIPPED_EXISTING_THESIS",
                    "provisional_thesis_status": "SKIPPED_EXISTING_THESIS", "provisional_thesis_id": current.get("thesis_id"),
                })
            from src.company_thesis.draft_service import CompanyThesisDraftService
            service = CompanyThesisDraftService(db_path=self.repository.db_path)
            profile = service.business_profiles.profile(row["stock_code"])
            usable_profile = bool(profile and any(str(profile.get(field) or "").strip() for field in ("main_business", "main_products", "business_scope", "company_description")))
            if (row["financial_status"] != "READY" or row["business_research_status"] != "READY"
                    or row["business_profile_status"] not in {"READY", "PARTIAL"} or not usable_profile):
                return self.repository.upsert({
                    **row, "draft_status": "DATA_INSUFFICIENT", "validation_status": "DATA_INSUFFICIENT",
                    "provisional_thesis_status": "DATA_INSUFFICIENT",
                })
            industry_context = dict((row.get("metadata") or {}).get("industry_context") or {})
            if not str(industry_context.get("industry_name") or "").strip():
                return self.repository.upsert({
                    **row, "draft_status": "DATA_INSUFFICIENT", "validation_status": "MISSING_INDUSTRY_CONTEXT",
                    "provisional_thesis_status": "DATA_INSUFFICIENT",
                })
            # These are read-only gates.  A deterministic risk result and a
            # valuation projection must be reachable, but neither is changed.
            risk = service.risk_service.get_risk_research(row["market"], row["stock_code"], as_of=row["research_as_of"])
            zones = service.price_zone_service.get_price_zones(row["market"], row["stock_code"], as_of=row["research_as_of"])
            if not isinstance(risk, dict) or not isinstance(zones, dict):
                return self.repository.upsert({
                    **row, "draft_status": "DATA_INSUFFICIENT", "validation_status": "MISSING_RISK_OR_VALUATION",
                    "provisional_thesis_status": "DATA_INSUFFICIENT",
                })
            result = service.promote_to_provisional(
                row["market"], row["stock_code"], research_as_of=row["research_as_of"], industry_context=industry_context,
            )
            thesis = result.get("thesis") or {}
            if result.get("status") == "VALIDATION_FAILED":
                return self.repository.upsert({
                    **row, "draft_status": "VALIDATION_FAILED", "validation_status": str(result.get("message") or "VALIDATION_FAILED"),
                    "provisional_thesis_status": "VALIDATION_FAILED",
                })
            if result.get("status") == "DATA_INSUFFICIENT":
                return self.repository.upsert({
                    **row, "draft_status": "DATA_INSUFFICIENT", "validation_status": "DATA_INSUFFICIENT",
                    "provisional_thesis_status": "DATA_INSUFFICIENT",
                })
            state = "CREATED" if result.get("status") == "AI_PROVISIONAL_CREATED" else "REUSED"
            return self.repository.upsert({
                **row, "draft_status": state, "validation_status": "VALID", "provisional_thesis_status": state,
                "provisional_thesis_id": thesis.get("thesis_id"),
            })
        except Exception as exc:
            return self.repository.upsert({**row, "draft_status": "FAILED", "validation_status": "VALIDATION_FAILED", "provisional_thesis_status": "FAILED", "last_error": f"{type(exc).__name__}: {exc}"[:2000]})
        finally:
            if service is not None:
                service.close()

    def prepare_current_active_low_value_pool(self, *, source_as_of: str | None = None) -> dict[str, Any]:
        """Bounded worker entry point: only current ACTIVE low-value leaders."""
        active = self.pool_repository.active("CN")
        if source_as_of:
            active = [item for item in active if _day(item.get("source_as_of")) == _day(source_as_of)]
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for item in active:
            try:
                results.append(self.prepare_company(item, research_as_of=source_as_of or item.get("source_as_of")))
            except Exception as exc:
                errors.append({"stock_code": str(item.get("stock_code") or ""), "error": f"{type(exc).__name__}: {exc}"[:500]})
        counts = {status: sum(row.get("overall_status") == status for row in results) for status in ("READY", "PARTIAL", "MISSING", "FAILED")}
        provisional_counts = {
            status: sum(row.get("provisional_thesis_status") == status for row in results)
            for status in ("CREATED", "REUSED", "SKIPPED_EXISTING_THESIS", "DATA_INSUFFICIENT", "VALIDATION_FAILED", "FAILED")
        }
        return {
            "job_type": "LOW_VALUE_RISK_DATA_PREPARATION", "source_as_of": _day(source_as_of) if source_as_of else None,
            "active_low_value_count": len(active), "processed": len(results), "errors": errors, **counts,
            "provisional_thesis": provisional_counts,
            "status": "PARTIAL" if errors or counts["FAILED"] or provisional_counts["FAILED"] else "COMPLETED", "items": results,
        }

    def list_current_preparation(self, *, research_as_of: str | None = None) -> dict[str, Any]:
        # Read-only projection used by the API.  It must never collect reports
        # or run business research.
        active = self.pool_repository.active("CN")
        active_keys = {(str(item.get("market") or "CN").upper(), str(item.get("stock_code") or "").upper(), _day(item.get("source_as_of"))) for item in active}
        rows = self.repository.list_for_as_of(research_as_of)
        items = [row for row in rows if (row["market"], row["stock_code"], row["research_as_of"]) in active_keys]
        return {"items": items, "total": len(items), "research_as_of": _day(research_as_of) if research_as_of else None}


_service: RiskResearchPreparationService | None = None


def get_risk_research_preparation_service() -> RiskResearchPreparationService:
    global _service
    if _service is None:
        _service = RiskResearchPreparationService()
    return _service


def schedule_current_low_value_preparation(*, source_as_of: str) -> bool:
    """Queue one non-blocking EOD worker without starting duplicate runs."""
    global _scheduled_thread
    with _schedule_lock:
        if _scheduled_thread and _scheduled_thread.is_alive():
            return False

        def run() -> None:
            try:
                get_risk_research_preparation_service().prepare_current_active_low_value_pool(source_as_of=source_as_of)
            finally:
                pass

        _scheduled_thread = threading.Thread(
            target=run, name="low-value-risk-data-preparation", daemon=True,
        )
        _scheduled_thread.start()
        return True

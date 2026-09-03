"""DeepResearchPreparationService — idempotent gap-filling for one company.

Only orchestrates existing single-company capabilities in dependency order
(profile → business research → disclosure → moat evidence → thesis DRAFT),
never invents research, never touches the low-value pool or Focus, never
auto-promotes a thesis.  Bounded: ≤2 automatic preparations per company per
day, per-company in-flight lock, partial failures keep all prior results.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

from src.deep_research.coverage import (
    DeepResearchCoverageService,
    MISSING,
    PARTIAL,
    READY,
    get_deep_research_coverage_service,
)

MAX_AUTO_PREPARATIONS_PER_DAY = 2
DEFAULT_DOCUMENTS_PER_KIND = 2


class DeepResearchBusyError(RuntimeError):
    """Another preparation for the same company is still running."""


class DeepResearchPreparationService:
    def __init__(self, coverage_service: DeepResearchCoverageService | None = None,
                 state_path: Path | None = None) -> None:
        self.coverage = coverage_service or get_deep_research_coverage_service()
        from src.config.paths import get_runtime_root

        self.state_path = Path(state_path or (Path(get_runtime_root()) / "deep_research_usage.json"))
        self._lock = threading.RLock()
        self._in_flight: set[str] = set()

    # ------------------------------------------------------------------
    # usage guard: daily cap + in-flight slot held for the whole run (§9)
    # ------------------------------------------------------------------
    def _acquire(self, market: str, code: str) -> None:
        today = date.today().isoformat()
        key = f"{market}:{code}"
        with self._lock:
            if key in self._in_flight:
                raise DeepResearchBusyError(f"DEEP_PREPARE_BUSY: {key}")
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            entries = [e for e in state.get(key, []) if str(e)[:10] >= today]
            if len(entries) >= MAX_AUTO_PREPARATIONS_PER_DAY:
                raise RuntimeError(
                    f"DEEP_PREPARE_DAILY_LIMIT: {code} already prepared {len(entries)} times today")
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({**state, key: [*entries, today]}, ensure_ascii=False), encoding="utf-8")
            self._in_flight.add(key)

    def _release(self, market: str, code: str) -> None:
        with self._lock:
            self._in_flight.discard(f"{market}:{code}")

    # ------------------------------------------------------------------
    # main entry (task §4/§5)
    # ------------------------------------------------------------------
    def prepare(self, market: str, stock_code: str, *, as_of: str | None = None,
                include_p1: bool = True, max_documents_per_kind: int = DEFAULT_DOCUMENTS_PER_KIND,
                skip_usage_guard: bool = False) -> dict[str, Any]:
        market, code = market.upper(), stock_code.upper()
        from src.cio_report.service import CioReportService

        research_as_of = str(as_of or CioReportService._default_research_as_of())[:10]
        if not skip_usage_guard:
            self._acquire(market, code)
        try:
            return self._prepare_locked(market, code, research_as_of, include_p1, max_documents_per_kind)
        finally:
            self._release(market, code)

    def _prepare_locked(self, market: str, code: str, research_as_of: str,
                        include_p1: bool, max_documents_per_kind: int) -> dict[str, Any]:
        prepared: list[str] = []
        reused: list[str] = []
        failed: list[dict[str, str]] = []
        llm_calls = 0
        network_documents = 0
        thesis_draft_status = "NOT_NEEDED"
        before = self.coverage.coverage(market, code, as_of=research_as_of)
        dims = dict(before["dimensions"])

        # Gate: financial basics must exist (task §9.3).
        if dims.get("financial") == MISSING:
            return self._result(code, research_as_of, before, before, [], [], [
                {"capability": "financial", "error": "FINANCIAL_MISSING"}],
                0, 0, "NOT_NEEDED", [])

        # STEP 1 — business profile is a pure read; always usable if present.
        if dims.get("business_profile") in (READY, PARTIAL):
            reused.append("business_profile")
        else:
            failed.append({"capability": "business_profile", "error": "PROFILE_SOURCE_MISSING"})

        # STEP 2 — business research prepare+analyze (≤1 LLM).
        if dims.get("business_research") == READY:
            reused.append("business_research")
        else:
            try:
                from src.business_research import get_business_research_service

                service = get_business_research_service()
                # No restrictive as_of here: the TDX profile's updated_at is
                # its own natural date, and a same-day cache refresh would
                # otherwise trip the PIT gate (profile_after_as_of).
                service.get(code)
                result = service.analyze(code)
                status = str(result.get("analysis_status") or "")
                if status == "COMPLETED":
                    prepared.append("business_research")
                    llm_calls += 1
                elif status in ("DATA_INSUFFICIENT", "NO_SOURCES"):
                    # Deterministic UNKNOWN claims without any LLM spend.
                    prepared.append("business_research")
                else:
                    failed.append({"capability": "business_research", "error": f"status={status}"})
            except Exception as exc:  # noqa: BLE001 - keep going, keep old results
                failed.append({"capability": "business_research", "error": f"{type(exc).__name__}: {exc}"[:200]})

        # STEP 3 — bounded disclosure sync (CNINFO, ≤N per report kind).
        if include_p1 and dims.get("disclosure") == READY:
            reused.append("disclosure")
        elif include_p1:
            try:
                from src.disclosure_materials import get_disclosure_material_service

                sync = get_disclosure_material_service().sync_periodic_reports(
                    code, as_of=research_as_of, max_documents_per_kind=max_documents_per_kind)
                network_documents += int(sync.get("selected_reports") or 0)
                prepared.append("disclosure")
            except Exception as exc:  # noqa: BLE001
                failed.append({"capability": "disclosure", "error": f"{type(exc).__name__}: {exc}"[:200]})

        # STEP 4 — moat evidence extraction (deterministic, zero LLM).
        if include_p1 and dims.get("moat_evidence") == MISSING:
            try:
                from src.moat_evidence import get_moat_evidence_extraction_service

                get_moat_evidence_extraction_service().extract(market, code, as_of=research_as_of)
                prepared.append("moat_evidence")
                # Zero extractable evidence is a legal outcome (task §16).
            except Exception as exc:  # noqa: BLE001
                failed.append({"capability": "moat_evidence", "error": f"{type(exc).__name__}: {exc}"[:200]})
        elif include_p1 and dims.get("moat_evidence") == READY:
            reused.append("moat_evidence")

        # STEP 5 — moat research is a pure read; nothing to prepare.

        # STEP 5b — historical valuation on-demand backfill (stabilization §6.3).
        # Pool-external companies like 600460 have zero valuation history;
        # pool companies may have fallen behind.  Backfill to latest close.
        if include_p1:
            try:
                val_status = self._backfill_valuation(market, code, research_as_of)
                if val_status == "BACKFILLED":
                    prepared.append("historical_valuation")
                elif val_status == "REUSED":
                    reused.append("historical_valuation")
            except Exception as exc:  # noqa: BLE001
                failed.append({"capability": "historical_valuation", "error": f"{type(exc).__name__}: {exc}"[:200]})

        # STEP 6 — thesis DRAFT only, never promote (task §5 STEP 6).
        if include_p1:
            try:
                draft_status = self._prepare_thesis_draft(market, code, research_as_of)
                if draft_status not in ("NOT_NEEDED",):
                    thesis_draft_status = draft_status
                if draft_status == "GENERATED":
                    prepared.append("thesis_draft")
                elif draft_status in ("REUSED", "SKIPPED_EXISTING_THESIS", "DATA_INSUFFICIENT",
                                      "VALIDATION_FAILED", "HUMAN_CONFIRMED_LOCKED"):
                    reused.append(f"thesis_draft:{draft_status}")
            except Exception as exc:  # noqa: BLE001
                failed.append({"capability": "thesis_draft", "error": f"{type(exc).__name__}: {exc}"[:200]})

        after = self.coverage.coverage(market, code, as_of=research_as_of)

        # CIO selective rebuild — fingerprint-gated: only changed sections get
        # new fingerprints and only then does synthesis rerun (task §7).
        invalidated: list[str] = []
        try:
            from src.cio_report import get_cio_report_service

            report = get_cio_report_service().build_report(market, code, as_of=research_as_of)
            if not report.get("idempotent_reuse"):
                invalidated = [s.get("section_type") for s in report.get("sections") or []
                               if s.get("freshness_status") == "REFRESHED"]
            if report.get("synthesis_status") == "LLM_COMPLETED":
                llm_calls += 1
        except Exception as exc:  # noqa: BLE001 - the report must never block preparation results
            failed.append({"capability": "cio_rebuild", "error": f"{type(exc).__name__}: {exc}"[:200]})

        return self._result(code, research_as_of, before, after, prepared, reused, failed,
                            llm_calls, network_documents, thesis_draft_status, invalidated)

    # ------------------------------------------------------------------
    def _backfill_valuation(self, market: str, code: str, research_as_of: str) -> str:
        """On-demand historical valuation backfill for one company (§6.3).

        If the valuation series is missing or stale relative to the research
        date, refresh just this company.  Idempotent: up-to-date → REUSED.
        """
        from src.historical_valuation.service import get_historical_valuation_service

        service = get_historical_valuation_service()
        try:
            series = service.get_valuation_history(market, code, as_of=research_as_of) or {}
            coverage = dict(series.get("coverage") or {})
            last_date = str(coverage.get("last_date") or "")[:10]
            if last_date >= research_as_of:
                return "REUSED"
        except Exception:  # noqa: BLE001 — missing series is the backfill trigger
            pass
        # Backfill this single company (fetches bars + computes PE/PB series).
        try:
            service.refresh_company(market, code, as_of=research_as_of)
            return "BACKFILLED"
        except Exception:
            # Some companies may lack bars/financial data — not a hard failure.
            return "INSUFFICIENT_DATA"

    def _prepare_thesis_draft(self, market: str, code: str, research_as_of: str) -> str:
        from src.company_thesis.draft_service import CompanyThesisDraftService
        from src.company_thesis.store import CompanyThesisRepository

        current = CompanyThesisRepository().get_current_thesis(market, code)
        if current:
            # Every existing authority — including HUMAN_CONFIRMED — is a hard
            # stop for any automatic thesis creation (task §5 STEP 6).
            return "HUMAN_CONFIRMED_LOCKED" if str(current.get("authority_status")) == "HUMAN_CONFIRMED" \
                else "SKIPPED_EXISTING_THESIS"

        service = CompanyThesisDraftService()
        existing = service.repository.latest(market, code)
        if existing and str(existing.get("draft_status") or "") == "DRAFT":
            return "REUSED"

        # industry context from the financial identity (read-only).
        industry_name = ""
        try:
            from src.financial_analysis.service import get_financial_analysis_service

            row = get_financial_analysis_service().store.latest(code, as_of=research_as_of) or {}
            identity = row.get("identity") or {}
            if hasattr(identity, "get"):
                industry_name = str(identity.get("level3_name") or identity.get("level2_name") or "")
        except Exception:  # noqa: BLE001
            industry_name = ""
        if not industry_name:
            return "DATA_INSUFFICIENT"

        result = service.generate(
            market, code, research_as_of=research_as_of,
            industry_context={"industry_name": industry_name},
        )
        if result.get("draft"):
            return "GENERATED"
        status = str(result.get("status") or "")
        return status if status else "VALIDATION_FAILED"

    # ------------------------------------------------------------------
    @staticmethod
    def _result(code: str, research_as_of: str, before: dict[str, Any], after: dict[str, Any],
                prepared: list[str], reused: list[str], failed: list[dict[str, str]],
                llm_calls: int, network_documents: int, thesis_draft_status: str,
                invalidated: list[str]) -> dict[str, Any]:
        return {
            "stock_code": code, "research_as_of": research_as_of,
            "coverage_before": before, "coverage_after": after,
            "overall_after": after.get("overall_coverage"),
            "prepared": prepared, "reused": reused, "failed": failed,
            "cio_sections_invalidated": invalidated,
            "llm_calls": llm_calls, "network_documents_synced": network_documents,
            "thesis_draft_status": thesis_draft_status,
        }


_service: DeepResearchPreparationService | None = None


def get_deep_research_preparation_service() -> DeepResearchPreparationService:
    global _service
    if _service is None:
        _service = DeepResearchPreparationService()
    return _service

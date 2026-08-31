"""Deep Research Coverage projection — read-only, no scores (task §1/§2).

11 dimensions, each READY / PARTIAL / MISSING / NOT_APPLICABLE; overall
COMPLETE / USABLE / PARTIAL from the P0/P1 layering.  Every verdict is a
direct lookup against persisted rows (or a deterministic live projection
that the CIO builder already uses), never a new computation.
"""

from __future__ import annotations

from typing import Any

P0_DIMENSIONS = ("financial", "business_profile", "business_research", "valuation", "risk")
P1_DIMENSIONS = ("disclosure", "moat_evidence", "moat_research", "thesis")
P2_DIMENSIONS = ("capital_allocation", "leader_quality")
ALL_DIMENSIONS = P0_DIMENSIONS + P1_DIMENSIONS + P2_DIMENSIONS

DIMENSION_TITLES = {
    "financial": "财务研究", "business_profile": "公司业务画像", "business_research": "经营研究",
    "valuation": "估值", "risk": "风险研究", "disclosure": "公告材料",
    "moat_evidence": "护城河证据", "moat_research": "护城河研究", "thesis": "核心逻辑（Thesis/草稿）",
    "capital_allocation": "资本配置", "leader_quality": "龙头质量",
}

READY = "READY"
PARTIAL = "PARTIAL"
MISSING = "MISSING"
NOT_APPLICABLE = "NOT_APPLICABLE"

_PROFILE_TEXT_FIELDS = ("main_business", "main_products", "business_scope", "company_description")


class DeepResearchCoverageService:
    """Read-only coverage projection for any resolved company."""

    def coverage(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any]:
        market, code = market.upper(), stock_code.upper()
        dimensions = {
            "financial": self._financial(code, as_of),
            "business_profile": self._business_profile(code),
            "business_research": self._business_research(code, as_of),
            "valuation": self._live_projection("valuation", market, code, as_of),
            "risk": self._live_projection("risk", market, code, as_of),
            "disclosure": self._disclosure(code, as_of),
            "moat_evidence": self._moat_evidence(market, code, as_of),
            "moat_research": self._moat_research(market, code, as_of),
            "thesis": self._thesis(market, code),
            "capital_allocation": self._live_projection("capital_allocation", market, code, as_of),
            "leader_quality": self._live_projection("leader_quality", market, code, as_of),
        }
        return {
            "market": market, "stock_code": code, "research_as_of": as_of,
            "dimensions": dimensions,
            "overall_coverage": self._overall(dimensions),
            "layers": {"P0": P0_DIMENSIONS, "P1": P1_DIMENSIONS, "P2": P2_DIMENSIONS},
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _overall(dimensions: dict[str, str]) -> str:
        p0 = [dimensions.get(key, MISSING) for key in P0_DIMENSIONS]
        p1 = [dimensions.get(key, MISSING) for key in P1_DIMENSIONS]
        if any(status != READY for status in p0):
            return "PARTIAL"
        if any(status == MISSING for status in p1):
            return "USABLE"
        return "COMPLETE"

    def _financial(self, code: str, as_of: str | None) -> str:
        from src.financial_analysis.service import get_financial_analysis_service

        row = get_financial_analysis_service().store.latest(code, as_of=as_of)
        if not row:
            return MISSING
        return READY if str(row.get("feature_status") or "") == "READY" else PARTIAL

    def _business_profile(self, code: str) -> str:
        from src.level3_leaders.business_profiles import CompanyBusinessProfileService

        profile = CompanyBusinessProfileService().profile(code)
        if not profile:
            return MISSING
        return READY if any(str(profile.get(f) or "").strip() for f in _PROFILE_TEXT_FIELDS) else PARTIAL

    def _business_research(self, code: str, as_of: str | None) -> str:
        from src.business_research import get_business_research_service

        # No as_of filter: the snapshot's own data_as_of is the natural PIT
        # date (a same-day TDX refresh would otherwise hide a valid snapshot).
        row = get_business_research_service().store.latest(code)
        if not row:
            return MISSING
        return READY if str(row.get("analysis_status") or "") == "COMPLETED" else PARTIAL

    def _disclosure(self, code: str, as_of: str | None) -> str:
        from src.disclosure_materials.store import DisclosureMaterialStore

        documents = DisclosureMaterialStore().list_documents(code, as_of=as_of)
        if not documents:
            return MISSING
        ready = sum(1 for d in documents if str(d.get("extraction_status") or "") == "READY")
        return READY if ready else PARTIAL

    def _moat_evidence(self, market: str, code: str, as_of: str | None) -> str:
        from src.moat_evidence.store import MoatEvidenceStore

        rows = MoatEvidenceStore().list(market, code, as_of=as_of)
        return READY if rows else MISSING

    def _moat_research(self, market: str, code: str, as_of: str | None) -> str:
        # Pure deterministic read of the evidence above — MISSING evidence
        # means the research layer has nothing to synthesize; it must never
        # be forced to SUPPORTED for coverage's sake (task §5 STEP 5).
        return self._moat_evidence(market, code, as_of)

    def _thesis(self, market: str, code: str) -> str:
        from src.company_thesis.draft_store import CompanyThesisDraftRepository
        from src.company_thesis.store import CompanyThesisRepository

        if CompanyThesisRepository().get_current_thesis(market, code):
            return READY
        draft = CompanyThesisDraftRepository().latest(market, code)
        if draft and str(draft.get("draft_status") or "") == "DRAFT":
            return PARTIAL
        return MISSING

    def _live_projection(self, kind: str, market: str, code: str, as_of: str | None) -> str:
        """Deterministic live layers (same reads the CIO builder performs)."""
        try:
            if kind == "valuation":
                from src.value_price_zones import get_value_price_zone_service

                zones = get_value_price_zone_service().get_price_zones(market, code, as_of=as_of)
                return READY if zones else MISSING
            if kind == "risk":
                from src.risk_research import get_risk_research_service

                risk = get_risk_research_service().get_risk_research(market, code, as_of=as_of)
                return READY if risk else MISSING
            if kind == "capital_allocation":
                from src.capital_allocation_research import get_capital_allocation_research_service

                research = get_capital_allocation_research_service().get_research(market, code, as_of=as_of)
                return READY if research else MISSING
            if kind == "leader_quality":
                from src.leader_quality_profile import get_leader_quality_profile_service

                profile = get_leader_quality_profile_service().get_profile(market, code, as_of=as_of)
                return READY if profile else PARTIAL
        except Exception:  # noqa: BLE001 - coverage must degrade, never fail
            return PARTIAL
        return PARTIAL


_service: DeepResearchCoverageService | None = None


def get_deep_research_coverage_service() -> DeepResearchCoverageService:
    global _service
    if _service is None:
        _service = DeepResearchCoverageService()
    return _service

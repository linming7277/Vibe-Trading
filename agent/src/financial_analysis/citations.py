"""Deterministic citation resolution for persisted Financial Claims."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_SOURCE_TYPE_ALIASES = {
    "PIT_FINANCIAL_HISTORY": "FINANCIAL_HISTORY",
    "FINANCIAL_HISTORY": "FINANCIAL_HISTORY",
    "FINANCIAL_FEATURE": "FINANCIAL_FEATURE",
    "DETERMINISTIC_FORECAST": "DETERMINISTIC_FORECAST",
}
_SOURCE_LABELS = {
    "FINANCIAL_HISTORY": "TDX PIT 财务",
    "FINANCIAL_FEATURE": "财务特征引擎",
    "DETERMINISTIC_FORECAST": "Forecast Engine",
}
_SOURCE_REQUIRED_TYPES = {"FACT", "INFERENCE", "FORECAST"}


class FinancialClaimCitationResolver:
    """Resolve Claims against their persisted or reconstructible Evidence Manifest.

    This class deliberately never reaches a model, provider, filesystem source,
    or external API.  It is an API presentation adapter over deterministic data.
    """

    @staticmethod
    def canonical_source_type(value: Any) -> str:
        return _SOURCE_TYPE_ALIASES.get(str(value or "").upper(), "UNKNOWN")

    @staticmethod
    def _safe_source_label(entry: dict[str, Any], source_type: str) -> str:
        source = str(entry.get("source") or "").strip()
        # A citation may expose the named source, but never a local path.
        if source and not re.match(r"^(?:[A-Za-z]:[\\/]|[\\/])", source):
            return source[:160]
        return _SOURCE_LABELS.get(source_type, "确定性财务数据")

    def resolve_citation(self, source_key: str, manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
        entry = manifest.get(source_key)
        if not isinstance(entry, dict):
            return {"source_key": source_key, "status": "UNRESOLVED"}
        source_type = self.canonical_source_type(entry.get("source_type"))
        citation: dict[str, Any] = {
            "source_key": source_key,
            "status": "RESOLVED",
            "source_type": source_type,
            "source": self._safe_source_label(entry, source_type),
            "metric": entry.get("metric"),
            "period": entry.get("period"),
            "value": entry.get("value"),
            "unit": entry.get("unit"),
            "data_as_of": entry.get("data_as_of"),
            "source_snapshot_id": entry.get("source_snapshot_id"),
            "source_hash": entry.get("source_hash"),
        }
        if source_type == "DETERMINISTIC_FORECAST":
            citation.update({
                "scenario": entry.get("scenario"),
                "forecast_year": entry.get("forecast_year"),
                "forecast_version": entry.get("forecast_version"),
            })
        return citation

    @staticmethod
    def _manifest_from_snapshot(snapshot: dict[str, Any], fallback_manifest: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        analysis = snapshot.get("analysis") if isinstance(snapshot.get("analysis"), dict) else {}
        metadata = analysis.get("analysis_metadata") if isinstance(analysis, dict) else {}
        persisted = metadata.get("evidence_manifest") if isinstance(metadata, dict) else None
        if isinstance(persisted, dict):
            return {str(key): value for key, value in persisted.items() if isinstance(value, dict)}
        return dict(fallback_manifest or {})

    def resolve_snapshot(self, snapshot: dict[str, Any], *, fallback_manifest: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return a copied analysis snapshot with API-only citation fields."""
        result = deepcopy(snapshot)
        analysis = result.get("analysis")
        if not isinstance(analysis, dict):
            result["traceability_status"] = "NOT_APPLICABLE"
            result["citation_stats"] = {
                "claims_total": 0, "claims_with_citations": 0,
                "resolved_source_keys": 0, "unresolved_source_keys": 0,
            }
            return result

        manifest = self._manifest_from_snapshot(snapshot, fallback_manifest)
        raw_claims = analysis.get("claims")
        claims = raw_claims if isinstance(raw_claims, list) else []
        diagnostics: list[dict[str, Any]] = []
        resolved_count = 0
        unresolved_count = 0
        claims_with_citations = 0
        required_claims = 0
        any_resolved_required = False

        for index, raw_claim in enumerate(claims):
            if not isinstance(raw_claim, dict):
                continue
            claim = raw_claim
            claim_type = str(claim.get("type") or "UNKNOWN").upper()
            source_keys = claim.get("source_keys", claim.get("evidence_keys", []))
            source_keys = source_keys if isinstance(source_keys, list) else []
            normalized_keys = [str(key).strip() for key in source_keys if str(key).strip()]
            citations = [self.resolve_citation(key, manifest) for key in normalized_keys]
            claim["citations"] = citations
            is_required = claim_type in _SOURCE_REQUIRED_TYPES
            if is_required:
                required_claims += 1
            claim_has_resolved = bool(citations) and all(item["status"] == "RESOLVED" for item in citations)
            if claim_has_resolved:
                claims_with_citations += 1
                if is_required:
                    any_resolved_required = True
            for citation in citations:
                if citation["status"] == "RESOLVED":
                    resolved_count += 1
                else:
                    unresolved_count += 1
                    diagnostics.append({"claim_index": index, "source_key": citation["source_key"], "status": "UNRESOLVED"})
            if is_required and not normalized_keys:
                diagnostics.append({"claim_index": index, "source_key": None, "status": "UNRESOLVED", "reason": "MISSING_SOURCE_KEY"})

        if not claims:
            traceability_status = "NOT_APPLICABLE"
        elif not diagnostics:
            traceability_status = "COMPLETE"
        elif any_resolved_required:
            traceability_status = "PARTIAL"
        else:
            traceability_status = "UNRESOLVED"
        stats = {
            "claims_total": len(claims),
            "claims_with_citations": claims_with_citations,
            "resolved_source_keys": resolved_count,
            "unresolved_source_keys": unresolved_count,
        }
        metadata = analysis.get("analysis_metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata.update({
            "traceability_status": traceability_status,
            "citation_stats": stats,
            "citation_diagnostics": diagnostics,
        })
        analysis["analysis_metadata"] = metadata
        result["analysis"] = analysis
        result["traceability_status"] = traceability_status
        result["citation_stats"] = stats
        return result

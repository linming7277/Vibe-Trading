"""Deterministic Business Claim citation resolution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class BusinessClaimCitationResolver:
    def resolve_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(snapshot)
        manifest = dict(result.get("sources") or {})
        analysis = result.get("analysis")
        claims = analysis.get("claims") if isinstance(analysis, dict) else []
        claims = claims if isinstance(claims, list) else []
        required = 0
        resolved_required = 0
        unresolved = 0
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_type = str(claim.get("type") or "UNKNOWN").upper()
            keys = claim.get("source_keys") if isinstance(claim.get("source_keys"), list) else []
            citations: list[dict[str, Any]] = []
            for raw_key in keys:
                key = str(raw_key)
                source = manifest.get(key)
                if not isinstance(source, dict):
                    citations.append({"source_key": key, "status": "UNRESOLVED"})
                    unresolved += 1
                    continue
                citations.append({
                    "source_key": key,
                    "status": "RESOLVED",
                    "source_type": source.get("source_type"),
                    "source_id": source.get("source_id"),
                    "data_as_of": source.get("data_as_of"),
                    "field": source.get("field"),
                    "value": source.get("value"),
                    "source_hash": source.get("source_hash"),
                    "profile_role": source.get("profile_role", "CURRENT"),
                })
            claim["citations"] = citations
            if claim_type in {"FACT", "INFERENCE"}:
                required += 1
                if citations and all(item["status"] == "RESOLVED" for item in citations):
                    resolved_required += 1
                else:
                    unresolved += 1
        if not claims or required == 0:
            status = "NOT_APPLICABLE"
        elif unresolved == 0 and resolved_required == required:
            status = "COMPLETE"
        elif resolved_required:
            status = "PARTIAL"
        else:
            status = "UNRESOLVED"
        result["traceability_status"] = status
        result["citation_stats"] = {
            "claims_total": len(claims),
            "required_claims": required,
            "resolved_required_claims": resolved_required,
            "unresolved": unresolved,
        }
        return result

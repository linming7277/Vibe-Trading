"""Research Freshness Service V1 (research-cache plan Phase 2).

Thin, read-mostly service that answers one question per module: "are the
persisted results still valid for the current inputs?"  It never executes a
refresh itself and never treats "not generated today" as staleness — only
real input-fingerprint changes do (plan §7.1/§7.2).
"""

from src.research_freshness.manifests import ResearchManifestStore
from src.research_freshness.service import ResearchFreshnessService, get_research_freshness_service

__all__ = [
    "ResearchManifestStore",
    "ResearchFreshnessService",
    "get_research_freshness_service",
]

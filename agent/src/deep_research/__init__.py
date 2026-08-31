"""Deep Research On-Demand V1 (audit: pool-external-deep-research-on-demand-audit-v1.md).

Thin orchestration over existing single-company capabilities: coverage
projection (read-only) + gap-filling preparation (idempotent, bounded).
No new research algorithms, no pool/Focus mutation, no auto thesis promotion.
"""

from src.deep_research.coverage import (
    DeepResearchCoverageService,
    get_deep_research_coverage_service,
)
from src.deep_research.preparation import (
    DeepResearchPreparationService,
    get_deep_research_preparation_service,
)

__all__ = [
    "DeepResearchCoverageService",
    "get_deep_research_coverage_service",
    "DeepResearchPreparationService",
    "get_deep_research_preparation_service",
]

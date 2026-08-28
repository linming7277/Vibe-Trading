"""Company CIO Deep Research Report V1 (research-cache plan Phase 3).

The CIO report is not a new research algorithm: it is a persisted, unified
product view over the existing research results (14 fixed sections, plan
§14), built 70% by deterministic templates and 30% by one synthesis LLM call
with a template fallback (plan §15).
"""

from src.cio_report.service import CioReportService, get_cio_report_service
from src.cio_report.store import CioReportStore

__all__ = ["CioReportService", "CioReportStore", "get_cio_report_service"]

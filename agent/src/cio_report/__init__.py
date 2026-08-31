"""Company CIO Deep Research Report V1 (research-cache plan Phase 3).

The CIO report is not a new research algorithm: it is a persisted, unified
product view over the existing research results (14 fixed sections, plan
§14), built 70% by deterministic templates and 30% by one synthesis LLM call
with a template fallback (plan §15).  The Quick Brief (delivery polish §7)
is a zero-LLM deterministic projection of the persisted Full Report.
"""

from src.cio_report.quick_brief import CioQuickBrief, build_quick_brief, render_quick_brief_md
from src.cio_report.narrative import BOSS_SECTIONS, render_boss_report
from src.cio_report.service import CioReportService, get_cio_report_service
from src.cio_report.store import CioReportStore

__all__ = [
    "CioReportService",
    "CioReportStore",
    "CioQuickBrief",
    "build_quick_brief",
    "render_quick_brief_md",
    "BOSS_SECTIONS",
    "render_boss_report",
    "get_cio_report_service",
]

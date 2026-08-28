"""Read-only company-research conclusion projection."""

from __future__ import annotations

from typing import Any

from src.entry_research import EntryResearchService, get_entry_research_service
from src.exit_research import ExitResearchService, get_exit_research_service
from src.research_workspace.store import normalize_market, normalize_symbol

from .overview_service import CompanyResearchOverviewService, get_company_research_overview_service


def _range(zone: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(zone, dict):
        return None
    low, high = zone.get("low"), zone.get("high")
    if low is None and high is None:
        return None
    return {
        "label": zone.get("label"),
        "low": low,
        "high": high,
        "kind": zone.get("kind"),
        "strength": zone.get("strength"),
    }


class CompanyResearchConclusionService:
    """Combine already-computed company projections without side effects."""

    def __init__(
        self,
        *,
        overview_service: CompanyResearchOverviewService | None = None,
        entry_service: EntryResearchService | None = None,
        exit_service: ExitResearchService | None = None,
    ) -> None:
        self.overview_service = overview_service or get_company_research_overview_service()
        self.entry_service = entry_service or get_entry_research_service()
        self.exit_service = exit_service or get_exit_research_service()

    @staticmethod
    def _conclusion(
        thesis: dict[str, Any] | None,
        entry: dict[str, Any] | None,
        exit_research: dict[str, Any] | None,
        support_count: int,
        challenge_count: int,
    ) -> str:
        status = str((thesis or {}).get("status") or "")
        thesis_label = str((thesis or {}).get("status_label") or "")
        entry_level = str((entry or {}).get("entry_level") or "")
        exit_level = str((exit_research or {}).get("exit_level") or "")
        entry_gaps = list((entry or {}).get("data_gaps") or [])
        exit_gaps = list((exit_research or {}).get("data_gaps") or [])
        if status == "FALSIFIED":
            return "公司的核心逻辑已被明显否定，目前应优先重新检查已有研究依据和估值假设。"
        if status == "WEAKENING":
            return "公司的核心逻辑正在减弱；即使价格或估值出现吸引力，也应先重新检查基本面变化。"
        if exit_level in {"CRITICAL_REVIEW", "REVIEW"}:
            return "当前价格或估值压力较高，建议重新检查公司的基本面逻辑和估值假设。"
        if entry_level in {"HIGH_ATTENTION", "ATTENTION"} and status in {"STRENGTHENING", "UNCHANGED"}:
            return "当前估值和价格位置较有吸引力，公司的核心逻辑没有明显恶化，值得重点跟踪。"
        if not thesis:
            return "当前尚未建立公司核心逻辑；现有价格和估值资料只能作为参考，暂不宜形成完整结论。"
        if entry_gaps or exit_gaps:
            return f"公司当前逻辑{thesis_label or '资料不足'}，但部分价格、估值或研究资料仍不完整，适合先继续观察。"
        evidence_text = f"已有 {support_count} 条支持证据和 {challenge_count} 条挑战证据"
        if status == "FORMING":
            return f"公司当前基本面逻辑仍在形成，{evidence_text}，目前更适合继续观察。"
        return f"公司当前逻辑{thesis_label or '基本稳定'}，{evidence_text}；结合现有估值和价格位置，保持持续跟踪。"

    def get_conclusion(self, market: str, stock_code: str) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        overview = self.overview_service.get_overview(normalized_market, symbol)
        entry_result = self.entry_service.get_entry_research(normalized_market, symbol)
        exit_result = self.exit_service.get_exit_research(normalized_market, symbol)
        entry = entry_result if isinstance(entry_result, dict) else {}
        exit_research = exit_result if isinstance(exit_result, dict) else {}
        thesis = overview.get("thesis") if isinstance(overview.get("thesis"), dict) else None
        supporting = list(overview.get("supporting_evidence") or [])
        challenging = list(overview.get("challenging_evidence") or [])
        entry_focus = dict(entry.get("focus_zones") or {})
        return {
            "company": overview.get("company") or {"market": normalized_market, "stock_code": symbol, "stock_name": symbol},
            "thesis": None if thesis is None else {
                "status": thesis.get("status"), "label": thesis.get("status_label"), "confidence": thesis.get("confidence"),
            },
            "entry": {
                "available": bool(entry), "level": entry.get("entry_level"), "label": entry.get("entry_level_label") or "入场研究数据不足",
                "confidence": entry.get("confidence"), "data_gaps": entry.get("data_gaps") or [],
            },
            "exit": {
                "available": bool(exit_research), "level": exit_research.get("exit_level"), "label": exit_research.get("exit_level_label") or "退出研究数据不足",
                "confidence": exit_research.get("confidence"), "data_gaps": exit_research.get("data_gaps") or [],
            },
            "fair_value_range": _range(entry_focus.get("fair_value")),
            "focus_zone": _range(entry_focus.get("focus_zone")),
            "evidence_counts": {"support": len(supporting), "challenge": len(challenging)},
            "research_conclusion": self._conclusion(thesis, entry, exit_research, len(supporting), len(challenging)),
            "data_status": overview.get("data_status") or {},
            "formula_version": "company-research-conclusion-card-v1.0.0",
        }


_service: CompanyResearchConclusionService | None = None


def get_company_research_conclusion_service() -> CompanyResearchConclusionService:
    global _service
    if _service is None:
        _service = CompanyResearchConclusionService()
    return _service

"""Agent tool for publishing structured research-workspace refresh results."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool
from src.research_workspace.store import ResearchWorkspaceStore


class PublishWorkspaceResearchTool(BaseTool):
    """Persist one complete dashboard refresh with evidence and data dates."""

    name = "publish_workspace_research"
    description = (
        "Publish narrative snapshots, macro research and evidence for a workspace refresh run. "
        "Call exactly once with every target market. The run itself determines "
        "which sections are required. Never invent unavailable data: set that "
        "market's source_status to unavailable so the last usable snapshot remains visible. "
        "Do not submit sector or security component scores; deterministic strategy engines own scoring."
    )
    is_readonly = False
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Research run id supplied in the refresh prompt"},
            "results": {
                "type": "array",
                "description": "Exactly one structured result for every target market",
                "items": {
                    "type": "object",
                    "properties": {
                        "market": {"type": "string", "enum": ["CN", "HK", "US"]},
                        "data_as_of": {"type": "string", "description": "ISO evidence date, YYYY-MM-DD"},
                        "source_status": {"type": "string", "enum": ["live", "stale", "unavailable"]},
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source": {"type": "string"},
                                    "url": {"type": "string"},
                                    "data_as_of": {"type": "string"},
                                    "metadata": {"type": "object"},
                                },
                                "required": ["source", "data_as_of"],
                            },
                        },
                        "snapshot": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string"},
                                "summary": {"type": "string"},
                                "metrics": {"type": "object"},
                                "risks": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "macro": {
                            "type": "object",
                            "properties": {
                                "headline": {"type": "string"},
                                "stance": {"type": "string"},
                                "summary": {"type": "string"},
                                "themes": {"type": "array", "items": {"type": "string"}},
                                "risks": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "sectors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sector_code": {"type": "string"},
                                    "sector_name": {"type": "string"},
                                    "momentum": {"type": "number"},
                                    "earnings": {"type": "number"},
                                    "fund_flow": {"type": "number"},
                                    "breadth": {"type": "number"},
                                    "valuation": {"type": "number"},
                                    "risk": {"type": "number"},
                                    "agent_adjustment": {"type": "number", "minimum": -5, "maximum": 5},
                                    "agent_reason": {"type": "string"},
                                },
                            },
                        },
                        "candidates": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "name": {"type": "string"},
                                    "exchange": {"type": "string"},
                                    "sector_code": {"type": "string"},
                                    "sector_name": {"type": "string"},
                                    "industry_position": {"type": "number"},
                                    "growth": {"type": "number"},
                                    "quality": {"type": "number"},
                                    "valuation": {"type": "number"},
                                    "momentum": {"type": "number"},
                                    "liquidity": {"type": "number"},
                                    "agent_adjustment": {"type": "number", "minimum": -5, "maximum": 5},
                                    "agent_reason": {"type": "string"},
                                    "excluded": {"type": "boolean"},
                                    "exclusion_reason": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["market", "data_as_of", "source_status", "evidence"],
                },
            },
        },
        "required": ["run_id", "results"],
        "additionalProperties": False,
    }

    def __init__(self, store: ResearchWorkspaceStore | None = None) -> None:
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        store = self._store or ResearchWorkspaceStore()
        owns_store = self._store is None
        try:
            results = kwargs.get("results") if isinstance(kwargs.get("results"), list) else []
            if any(item.get("sectors") or item.get("candidates") for item in results if isinstance(item, dict)):
                raise ValueError("Agent-authored sector/candidate scores are disabled; run the deterministic strategy engines")
            result = store.publish_research_results(
                str(kwargs.get("run_id") or ""),
                results,
            )
            return json.dumps({"status": "ok", **result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        finally:
            if owns_store:
                store.close()

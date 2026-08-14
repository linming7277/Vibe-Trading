"""Agent tool for publishing a validated, structured committee decision."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.agent.tools import BaseTool
from src.strategy_engines.common.contracts import CommitteeDecision, DecisionStatus, StrategyLine
from src.strategy_engines.store import StrategyEngineStore


class PublishCommitteeDecisionTool(BaseTool):
    name = "publish_committee_decision"
    description = (
        "Publish the committee's structured decision for one deterministic strategy signal. "
        "The tool rejects unknown evidence, score changes, larger position caps, looser stops, "
        "or any approval of a signal that is not proposed. Publish wait when evidence is incomplete."
    )
    is_readonly = False
    parameters = {
        "type": "object",
        "properties": {
            "committee_id": {"type": "string"},
            "signal_id": {"type": "string"},
            "strategy_line": {"type": "string", "enum": ["value", "emotion"]},
            "status": {"type": "string", "enum": ["approve", "reject", "wait"]},
            "direction": {"type": "string", "enum": ["buy", "sell", "wait"]},
            "position_cap": {"type": "number", "minimum": 0, "maximum": 1},
            "entry_low": {"type": ["number", "null"]},
            "entry_high": {"type": ["number", "null"]},
            "stop_price": {"type": ["number", "null"]},
            "target_low": {"type": ["number", "null"]},
            "target_high": {"type": ["number", "null"]},
            "holding_period": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
            "review_triggers": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "engine_run_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "committee_id", "signal_id", "strategy_line", "status", "direction",
            "position_cap", "holding_period", "confidence", "summary",
            "review_triggers", "evidence_ids", "engine_run_ids",
        ],
        "additionalProperties": False,
    }

    def __init__(self, store: StrategyEngineStore | None = None) -> None:
        self._store = store

    def execute(self, **kwargs: Any) -> str:
        store = self._store or StrategyEngineStore()
        owns = self._store is None
        try:
            decision = CommitteeDecision(
                id=f"decision_{uuid.uuid4().hex[:16]}",
                committee_id=str(kwargs["committee_id"]), signal_id=str(kwargs["signal_id"]),
                strategy_line=StrategyLine(str(kwargs["strategy_line"])),
                status=DecisionStatus(str(kwargs["status"])), direction=str(kwargs["direction"]),
                position_cap=float(kwargs["position_cap"]),
                entry_low=kwargs.get("entry_low"), entry_high=kwargs.get("entry_high"),
                stop_price=kwargs.get("stop_price"), target_low=kwargs.get("target_low"),
                target_high=kwargs.get("target_high"), holding_period=str(kwargs["holding_period"]),
                confidence=float(kwargs["confidence"]), summary=str(kwargs["summary"]),
                review_triggers=tuple(str(item) for item in kwargs.get("review_triggers", [])),
                evidence_ids=tuple(str(item) for item in kwargs.get("evidence_ids", [])),
                engine_run_ids=tuple(str(item) for item in kwargs.get("engine_run_ids", [])),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            return json.dumps({"status": "ok", "decision": store.publish_decision(decision)}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        finally:
            if owns:
                store.close()

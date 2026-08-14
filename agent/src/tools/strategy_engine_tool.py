"""Agent-callable bridge into deterministic strategy engines."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from src.agent.tools import BaseTool
from src.strategy_engines.service import StrategyEngineService


class RunStrategyEngineTool(BaseTool):
    name = "run_strategy_engine"
    description = (
        "Run the deterministic value or emotion strategy engine for CN/HK. "
        "The engine owns features, weights, regimes and signals; this tool does not accept component scores."
    )
    is_readonly = False
    parameters = {
        "type": "object",
        "properties": {
            "strategy_line": {"type": "string", "enum": ["value", "emotion"]},
            "market": {"type": "string", "enum": ["CN", "HK"]},
            "as_of": {"type": "string", "description": "YYYY-MM-DD; defaults to today"},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "force_refresh": {"type": "boolean", "default": False},
        },
        "required": ["strategy_line", "market"],
        "additionalProperties": False,
    }

    def execute(self, **kwargs: Any) -> str:
        service = StrategyEngineService()
        try:
            run = service.run(
                strategy_line=str(kwargs["strategy_line"]), market=str(kwargs["market"]),
                as_of=str(kwargs.get("as_of") or date.today().isoformat()),
                symbols=[str(item) for item in kwargs.get("symbols", [])],
                force_refresh=bool(kwargs.get("force_refresh", False)),
            )
            return json.dumps({"status": "ok", "run": run}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        finally:
            service.close()

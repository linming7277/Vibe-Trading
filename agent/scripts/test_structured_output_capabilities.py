"""Probe one configured model's output capabilities without touching business data.

Usage:
    python agent/scripts/test_structured_output_capabilities.py --live

The script makes at most one request per selected mode and never opens the
financial-analysis database tables.  A result is an audit aid, not a permanent
rule for a provider or model family.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "agent") not in sys.path:
    sys.path.insert(0, str(ROOT / "agent"))

from src.research_tasks.store import ResearchTaskStore  # noqa: E402
from src.structured_output import (  # noqa: E402
    StructuredOutputCapabilities, StructuredOutputMode, StructuredOutputRuntime,
)


CONTRACT = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "claims": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["FACT", "INFERENCE", "FORECAST", "UNKNOWN"]},
                    "text": {"type": "string"},
                    "source_keys": {"type": "array", "items": {"type": "string", "enum": ["KEY_A"]}},
                    "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                },
                "required": ["type", "text", "source_keys", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "claims"], "additionalProperties": False,
}


def capabilities_for(mode: StructuredOutputMode) -> StructuredOutputCapabilities:
    return StructuredOutputCapabilities(
        supports_json_schema=mode is StructuredOutputMode.JSON_SCHEMA,
        supports_json_object=mode is StructuredOutputMode.JSON_OBJECT,
        supports_plain_json=True,
        supports_enum=True, supports_array_constraints=True, supports_additional_properties_false=True,
        preferred_mode=mode, fallback_order=(mode,), source="capability_harness",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="perform provider calls (otherwise print the planned probes)")
    parser.add_argument("--mode", choices=[mode.value for mode in StructuredOutputMode if mode is not StructuredOutputMode.TEXT_ONLY], action="append")
    args = parser.parse_args()
    modes = [StructuredOutputMode(value) for value in (args.mode or ["JSON_SCHEMA", "JSON_OBJECT", "PROMPT_JSON"])]
    store = ResearchTaskStore()
    try:
        config = store.get_runtime_config("financial_analyst")
    finally:
        store.close()
    safe_identity = {"provider": config["provider"], "model": config["model"], "has_connection": bool(config.get("base_url"))}
    if not args.live:
        print(json.dumps({"live": False, "current": safe_identity, "planned_modes": [mode.value for mode in modes], "calls": len(modes)}, ensure_ascii=False))
        return 0
    runtime = StructuredOutputRuntime()
    for mode in modes:
        result = runtime.run(
            config=config,
            instruction="只返回 JSON：summary 为 test，claims 为空数组。",
            payload={"contract": "minimal"}, contract_schema=CONTRACT, capabilities=capabilities_for(mode),
            text_instruction="只返回 test", text_payload={},
        )
        print(json.dumps({
            "mode": mode.value, "success": result.parsed is not None,
            "parse_success": result.parsed is not None,
            "schema_success": result.parsed is not None and set(result.parsed) == {"summary", "claims"},
            "mode_used": result.mode_used, "attempts": result.attempts, "error_types": result.error_types,
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

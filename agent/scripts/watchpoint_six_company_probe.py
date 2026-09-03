"""Print the watchpoint projection for the six acceptance companies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CODES = ["600460.SH", "000544.SZ", "600210.SH", "605108.SH", "000651.SZ", "002371.SZ"]


def main() -> None:
    from src.value_watchpoints import get_value_watchpoint_projection_service

    service = get_value_watchpoint_projection_service()
    report: dict[str, Any] = {}
    for code in CODES:
        result = service.get_watchpoints("CN", code)
        report[code] = {
            "stock_name": result.get("stock_name"),
            "research_as_of": result.get("research_as_of"),
            "primary_action": result.get("primary_action"),
            "focus_tier": result.get("focus_tier"),
            "top": [
                {
                    "title": item.get("title"),
                    "category": item.get("category"),
                    "importance": item.get("importance_tier"),
                    "canonical_metric": item.get("canonical_metric"),
                    "semantic_key": item.get("semantic_key"),
                    "sources": [ref.get("module") for ref in item.get("source_refs") or []],
                    "submetrics": item.get("submetrics"),
                }
                for item in result.get("top_watchpoints") or []
            ],
            "all_titles": [item.get("title") for item in result.get("watchpoints") or []],
            "data_gaps": [gap.get("description") for gap in result.get("data_gaps") or []],
        }
    dest = Path(__file__).resolve().parents[2] / "docs" / "value-strategy" / "_watchpoint_six_company_probe.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

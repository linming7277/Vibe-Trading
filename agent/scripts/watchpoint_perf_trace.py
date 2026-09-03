"""Component trace of the optimized Watchpoint projection for 600460.SH."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import src.value_watchpoints.service as service_module
from src.value_watchpoints.service import ValueWatchpointProjectionService


def main() -> None:
    service = ValueWatchpointProjectionService()
    service._default_loaders()
    timings: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"calls": 0, "total_ms": 0.0}
    )

    def timed(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                timings[name]["calls"] += 1
                timings[name]["total_ms"] += (time.perf_counter() - started) * 1000

        return call

    loader_names = {
        "strategy_loader": "get_strategy_state",
        "thesis_loader": "thesis_load",
        "risk_loader": "risk_research",
        "financial_loader": "financial_saved",
        "normalized_loader": "normalized_earnings",
        "cycle_loader": "cycle_scenario",
        "business_loader": "business_research",
        "reliability_loader": "valuation_reliability",
        "moat_loader": "moat_research",
        "capital_loader": "capital_allocation",
        "deep_loader": "deep_coverage",
    }
    for attribute, label in loader_names.items():
        loader = getattr(service, attribute)
        if loader is not None:
            setattr(service, attribute, timed(label, loader))

    original_merge = service_module.merge_watchpoints
    original_public = service_module.public_watchpoint
    original_rank = service._rank
    service_module.merge_watchpoints = timed("dedupe", original_merge)
    service_module.public_watchpoint = timed("public_projection", original_public)
    service._rank = timed("ranking", original_rank)
    try:
        started = time.perf_counter()
        result = service.get_watchpoints("CN", "600460.SH")
        projection_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        json.dumps(result, ensure_ascii=False, default=str)
        serialization_ms = (time.perf_counter() - started) * 1000
    finally:
        service_module.merge_watchpoints = original_merge
        service_module.public_watchpoint = original_public
        service._rank = original_rank

    timings["serialization"] = {"calls": 1, "total_ms": serialization_ms}
    rows = []
    for component, values in timings.items():
        total_ms = round(float(values["total_ms"]), 2)
        rows.append(
            {
                "component": component,
                "calls": int(values["calls"]),
                "total_ms": total_ms,
                "percentage": round(total_ms / projection_ms * 100, 2),
                "database_queries": "not instrumented",
                "network_calls": 0,
                "llm_calls": 0,
            }
        )
    rows.sort(key=lambda item: -float(item["total_ms"]))
    report = {
        "stock_code": "600460.SH",
        "projection_ms": round(projection_ms, 2),
        "components": rows,
        "network_calls": 0,
        "llm_calls": 0,
    }
    destination = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "value-strategy"
        / "_watchpoint_perf_trace.json"
    )
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

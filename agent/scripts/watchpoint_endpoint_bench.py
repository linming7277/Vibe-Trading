"""Measure the public Watchpoint API and MCP tool without model time."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_server import get_value_watchpoints
from src.api.value_strategy_routes import register_value_strategy_routes


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return round(ordered[index], 1)


def samples(call, count: int = 10) -> dict[str, object]:
    values: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        call()
        values.append((time.perf_counter() - started) * 1000)
    return {
        "samples_ms": [round(value, 1) for value in values],
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "mean_ms": round(statistics.fmean(values), 1),
    }


def main() -> None:
    app = FastAPI()
    register_value_strategy_routes(app, lambda: None)
    client = TestClient(app)

    # Warm module singletons, SQLite page cache and projection memos first.
    response = client.get("/api/value/companies/600460.SH/watchpoints")
    response.raise_for_status()
    json.loads(get_value_watchpoints("600460.SH"))

    report = {
        "api": samples(lambda: client.get("/api/value/companies/600460.SH/watchpoints").raise_for_status()),
        "mcp": samples(lambda: json.loads(get_value_watchpoints("600460.SH"))),
    }
    destination = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "value-strategy"
        / "_watchpoint_endpoint_bench.json"
    )
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

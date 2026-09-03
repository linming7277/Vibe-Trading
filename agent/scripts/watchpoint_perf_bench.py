"""Cold/warm latency benchmark for the watchpoint projection read path."""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

CODES = ["600460.SH", "000544.SZ", "600210.SH", "605108.SH", "000651.SZ", "002371.SZ"]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def main() -> None:
    from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
    from src.value_watchpoints import get_value_watchpoint_projection_service
    from src.value_watchpoints.read_cache import clear_memos, memo_stats

    service = get_value_watchpoint_projection_service()
    report: dict[str, Any] = {}

    # Start the process and initialize modules/connections first.  COLD below
    # means the Watchpoint projection memos are empty, not Python import time.
    service.get_watchpoints("CN", "600460.SH")

    # COLD: every projection memo dropped, per-request cache empty.
    clear_memos()
    cold: list[float] = []
    for _ in range(10):
        clear_memos()
        started = time.perf_counter()
        service.get_watchpoints("CN", "600460.SH")
        cold.append((time.perf_counter() - started) * 1000)
    report["600460_cold"] = {"samples": [round(v, 1) for v in cold], "p50": percentile(cold, 0.5), "p95": percentile(cold, 0.95)}

    warm: list[float] = []
    for _ in range(10):
        started = time.perf_counter()
        service.get_watchpoints("CN", "600460.SH")
        warm.append((time.perf_counter() - started) * 1000)
    report["600460_warm"] = {
        "samples": [round(v, 1) for v in warm],
        "p50": percentile(warm, 0.5), "p95": percentile(warm, 0.95),
        "mean": round(statistics.fmean(warm), 1),
    }

    # Focus A batch (10 companies from the active pool, cursor fast path).
    pool = LowValueLeaderPoolRepository()
    focus_codes = [str(item["stock_code"]) for item in list(pool.active("CN"))[:10]]
    started = time.perf_counter()
    batch = service.get_watchpoints_batch("CN", focus_codes)
    report["focus_a_batch"] = {
        "companies": len(focus_codes), "seconds": round(time.perf_counter() - started, 2),
        "returned": len(batch),
    }

    started = time.perf_counter()
    single = [service.get_watchpoints("CN", code) for code in focus_codes]
    report["focus_a_sequential_single"] = {"seconds": round(time.perf_counter() - started, 2)}
    report["batch_matches_single"] = all(
        json.dumps(batch[item["stock_code"]], ensure_ascii=False, sort_keys=True, default=str)
        == json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        for item in single
    )

    if "--full" in sys.argv:
        all_codes = [str(item["stock_code"]) for item in pool.active("CN")]
        started = time.perf_counter()
        service.get_watchpoints_batch("CN", all_codes)
        report["full_pool"] = {"companies": len(all_codes), "seconds": round(time.perf_counter() - started, 2)}

    report["memos"] = memo_stats()

    dest = Path(__file__).resolve().parents[2] / "docs" / "value-strategy" / "_watchpoint_perf_bench.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

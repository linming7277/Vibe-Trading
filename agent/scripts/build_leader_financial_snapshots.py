"""Prebuild deterministic financial snapshots for the persisted Level-3 leader pool.

This command only reads cached TongDaXin financial history and writes the
versioned ``company_financial_analysis_snapshots`` cache.  It never refreshes
vendor data and never invokes the financial LLM, so it is safe to run after a
leader snapshot has been built.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.financial_analysis.service import FinancialAnalysisService  # noqa: E402
from src.level3_leaders.constants import VALUE_LINE_LEADER_LIMIT  # noqa: E402
from src.level3_leaders.service import get_level3_leader_service  # noqa: E402


def _close(service: FinancialAnalysisService) -> None:
    """Close the short-lived SQLite readers owned by one worker."""
    try:
        service.close()
    finally:
        history_store = getattr(getattr(service, "history", None), "store", None)
        if history_store is not None:
            history_store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="Leader snapshot date (YYYY-MM-DD); defaults to latest snapshot")
    parser.add_argument("--workers", type=int, default=3, help="Local worker count (default: 3, max: 4)")
    args = parser.parse_args()
    workers = max(1, min(args.workers, 4))

    leaders = get_level3_leader_service().get_all_level3_top_leaders(
        as_of=args.as_of, limit=VALUE_LINE_LEADER_LIMIT,
    )
    if leaders.get("snapshot_status") != "ready":
        raise RuntimeError("level3_leader_snapshot_not_ready")
    as_of = str(leaders.get("as_of") or args.as_of or "")
    if not as_of:
        raise RuntimeError("leader_snapshot_date_missing")

    companies: dict[str, dict[str, Any]] = {}
    for items in dict(leaders.get("items") or {}).values():
        for item in items or []:
            stock_code = str(item.get("stock_code") or "").upper()
            if stock_code:
                companies[stock_code] = item
    if not companies:
        raise RuntimeError("leader_pool_empty")

    local = threading.local()
    services: list[FinancialAnalysisService] = []
    services_lock = threading.Lock()

    def prepare(item: dict[str, Any]) -> dict[str, Any]:
        service = getattr(local, "service", None)
        if service is None:
            service = FinancialAnalysisService()
            local.service = service
            with services_lock:
                services.append(service)
        stock_code = str(item["stock_code"]).upper()
        for attempt in range(3):
            try:
                snapshot = service.prepare(stock_code, as_of=as_of)
                return {
                    "stock_code": stock_code,
                    "created": not bool(snapshot.get("idempotent_reuse")),
                    "feature_status": snapshot.get("feature_status"),
                    "forecast_status": snapshot.get("forecast_status"),
                }
            except Exception as exc:  # SQLite write contention is transient under local concurrency.
                if attempt == 2:
                    raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
                time.sleep(0.25 * (attempt + 1))
        raise AssertionError("unreachable")

    started = time.monotonic()
    total = len(companies)
    created = reused = completed = 0
    failures: list[dict[str, str]] = []
    print(json.dumps({"event": "started", "as_of": as_of, "total": total, "workers": workers,
                      "llm_invoked": False, "vendor_refresh": False}, ensure_ascii=False), flush=True)
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="financial-prebuild") as executor:
            futures = {executor.submit(prepare, item): code for code, item in companies.items()}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    result = future.result()
                    created += int(result["created"])
                    reused += int(not result["created"])
                except Exception as exc:  # noqa: BLE001 - keep the rest of the leader pool progressing.
                    failures.append({"stock_code": code, "error": f"{type(exc).__name__}: {exc}"})
                completed += 1
                if completed == total or completed % 20 == 0:
                    print(json.dumps({"event": "progress", "completed": completed, "total": total,
                                      "created": created, "reused": reused, "failed": len(failures)}, ensure_ascii=False), flush=True)
    finally:
        for service in services:
            _close(service)

    result = {
        "event": "completed", "as_of": as_of, "total": total, "created": created,
        "reused": reused, "failed": len(failures), "failures": failures[:100],
        "elapsed_seconds": round(time.monotonic() - started, 2), "llm_invoked": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

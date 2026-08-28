"""Backfill PIT risk-financial inputs for the current L3 leader pool.

This script only refreshes the local TongDaXin professional-finance cache.  It
does not run an LLM, build a Company Thesis, or change risk/valuation results.
Each completed batch is committed by ``FinancialHistoryService`` so rerunning
the command is safe after an interruption.
"""

from __future__ import annotations

import argparse
import json

from src.level3_leaders import get_level3_leader_service
from src.tdx_data.financial_history import FinancialHistoryService


ACTIVE_STATES = {"ACTIVE", "NEW", "REENTERED"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 50:
        parser.error("--batch-size must be between 1 and 50")

    leaders = get_level3_leader_service()
    history = FinancialHistoryService()
    try:
        pool = leaders.store.current_pool()
        if not pool:
            raise RuntimeError("current_l3_pool_missing")
        resolved = leaders.store.get_pool(str(pool["id"]), include_inactive=True) or {}
        symbols = sorted({
            str(member["stock_code"]).upper()
            for member in resolved.get("members") or []
            if str(member.get("lifecycle_status") or "") in ACTIVE_STATES
        })
        print(json.dumps({
            "stage": "started", "pool_id": pool["id"], "as_of": pool["as_of"], "companies": len(symbols),
        }, ensure_ascii=False), flush=True)
        result = history.collect_incremental(
            symbols, batch_size=args.batch_size,
            progress=lambda done, total, _label: print(json.dumps({
                "stage": "progress", "processed": done, "total": total,
            }, ensure_ascii=False), flush=True),
        )
        print(json.dumps({"stage": "completed", **result}, ensure_ascii=False), flush=True)
        return 0 if result["status"] == "ready" else 2
    finally:
        history.store.close()
        leaders.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Project current L3 PIT financial history into deterministic company snapshots.

No LLM is invoked.  The script rebuilds only the Python financial feature and
forecast snapshots so downstream risk research can read the newly cached
balance-sheet inputs.
"""

from __future__ import annotations

import json

from src.financial_analysis.service import FinancialAnalysisService
from src.level3_leaders import get_level3_leader_service


ACTIVE_STATES = {"ACTIVE", "NEW", "REENTERED"}


def main() -> int:
    leaders = get_level3_leader_service()
    financial = FinancialAnalysisService()
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
        as_of = str(pool["as_of"])
        print(json.dumps({"stage": "started", "companies": len(symbols), "as_of": as_of}, ensure_ascii=False), flush=True)
        completed = failed = ready = partial = 0
        errors: list[dict[str, str]] = []
        for index, symbol in enumerate(symbols, start=1):
            try:
                snapshot = financial.prepare(symbol, as_of=as_of)
                completed += 1
                status = str((snapshot.get("feature") or {}).get("data_quality", {}).get("risk_financial_input_status") or "MISSING")
                if status == "READY":
                    ready += 1
                else:
                    partial += 1
            except Exception as exc:
                failed += 1
                errors.append({"stock_code": symbol, "error": f"{type(exc).__name__}:{exc}"})
            if index % 20 == 0 or index == len(symbols):
                print(json.dumps({
                    "stage": "progress", "processed": index, "total": len(symbols), "completed": completed,
                    "risk_input_ready": ready, "partial_or_missing": partial, "failed": failed,
                }, ensure_ascii=False), flush=True)
        print(json.dumps({
            "stage": "completed", "total": len(symbols), "completed": completed, "risk_input_ready": ready,
            "partial_or_missing": partial, "failed": failed, "errors": errors[:50],
        }, ensure_ascii=False), flush=True)
        return 0 if failed == 0 else 2
    finally:
        financial.close()
        leaders.close()


if __name__ == "__main__":
    raise SystemExit(main())

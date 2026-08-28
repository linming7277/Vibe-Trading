"""Backfill balance-sheet detail fields in the PIT financial-history cache.

A full-market collection run can leave ``cash_and_equivalents``,
``accounts_receivable``, ``inventory`` and ``interest_bearing_debt_ratio`` NULL
for symbols whose vendor batch returned partial rows.  The field pipeline is
already integrated (see ``FIELD_MAP``); this script re-collects a bounded
universe through ``FinancialHistoryService.collect_incremental`` and upserts the
repaired rows.  It touches only the TongDaXin cache: no LLM, no Thesis change,
no risk/valuation recalculation.
"""

from __future__ import annotations

import argparse
import json

from src.level3_leaders import get_level3_leader_service
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.tdx_data.financial_history import FinancialHistoryService


ACTIVE_STATES = {"ACTIVE", "NEW", "REENTERED"}

# Fields whose absence in every stored period marks a symbol as needing a
# re-collection.  debt_ratio/capex come through reliably and serve as proof
# that the vendor row itself exists.
DETAIL_FIELDS = (
    "cash_and_equivalents", "accounts_receivable", "inventory",
    "current_assets", "current_liabilities", "interest_bearing_debt_ratio",
)


def _symbol_field_gaps(history: FinancialHistoryService, symbols: list[str]) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {}
    for symbol in symbols:
        package = history.query(symbol)
        rows = [row for row in list(package.get("items") or []) if isinstance(row, dict)]
        if not rows:
            gaps[symbol] = list(DETAIL_FIELDS)
            continue
        missing = [field for field in DETAIL_FIELDS if all(row.get(field) is None for row in rows)]
        if missing:
            gaps[symbol] = missing
    return gaps


def _research_universe() -> list[str]:
    symbols: set[str] = set()
    leaders = get_level3_leader_service()
    try:
        pool = leaders.store.current_pool()
        if pool:
            resolved = leaders.store.get_pool(str(pool["id"]), include_inactive=True) or {}
            symbols.update(
                str(member["stock_code"]).upper()
                for member in resolved.get("members") or []
                if str(member.get("lifecycle_status") or "") in ACTIVE_STATES
            )
    finally:
        leaders.close()
    low_value = LowValueLeaderPoolRepository()
    try:
        symbols.update(
            str(row.get("stock_code") or "").upper()
            for row in low_value.active()
        )
    finally:
        low_value.close()
    return sorted(symbols)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="逗号分隔的显式代码列表（如 600460.SH），优先于研究池范围")
    parser.add_argument("--pool", action="store_true", help="回补当前 L3 龙头池与低估龙头池全部成员")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 50:
        parser.error("--batch-size must be between 1 and 50")

    explicit = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if explicit:
        universe = sorted(set(explicit))
    elif args.pool:
        universe = _research_universe()
        if not universe:
            raise RuntimeError("research universe is empty")
    else:
        parser.error("provide --symbols or --pool")

    history = FinancialHistoryService()
    try:
        before = _symbol_field_gaps(history, universe)
        print(json.dumps({
            "stage": "started", "companies": len(universe),
            "companies_with_gaps": len(before),
            "gap_sample": dict(list(before.items())[:5]),
        }, ensure_ascii=False), flush=True)
        result = history.collect_incremental(
            universe, batch_size=args.batch_size,
            progress=lambda done, total, _label: print(json.dumps({
                "stage": "progress", "processed": done, "total": total,
            }, ensure_ascii=False), flush=True),
        )
        after = _symbol_field_gaps(history, universe)
        print(json.dumps({
            "stage": "completed", **result,
            "companies_with_gaps_before": len(before),
            "companies_with_gaps_after": len(after),
            "remaining_gap_sample": dict(list(after.items())[:5]),
        }, ensure_ascii=False), flush=True)
        return 0 if result["status"] == "ready" and not after else 2
    finally:
        history.store.close()


if __name__ == "__main__":
    raise SystemExit(main())

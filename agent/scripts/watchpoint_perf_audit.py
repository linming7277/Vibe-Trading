"""Component-level timing audit for the Watchpoint projection read path.

Read-only.  Writes nothing but a JSON report next to the docs folder.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

CODE = "600460.SH"
MARKET = "CN"


class Counter:
    """sqlite3.Connection is immutable in CPython, so queries are not counted.

    Kept as a no-op so the timing table shape stays stable.
    """

    def install(self) -> None:
        return None

    def restore(self) -> None:
        return None

    def reset(self) -> int:
        return 0


def timed(counter: Counter, label: str, fn, results: list[dict[str, Any]]) -> Any:
    counter.reset()
    started = time.perf_counter()
    error = None
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - audit must continue
        value, error = None, f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - started) * 1000
    results.append({
        "component": label,
        "calls": 1,
        "total_ms": round(elapsed, 1),
        "db_queries": counter.reset(),
        "error": error,
    })
    return value


def main() -> None:
    counter = Counter()
    counter.install()
    report: dict[str, Any] = {"stock_code": CODE}
    rows: list[dict[str, Any]] = []

    try:
        from src.business_research import get_business_research_service
        from src.capital_allocation_research import get_capital_allocation_research_service
        from src.company_thesis.store import CompanyThesisRepository
        from src.cycle_profit_scenario import get_cycle_profit_scenario_service
        from src.deep_research import get_deep_research_coverage_service
        from src.financial_analysis.service import get_financial_analysis_service
        from src.moat_research import get_moat_research_service
        from src.normalized_earnings import get_normalized_earnings_reference_service
        from src.risk_research import get_risk_research_service
        from src.value_price_zones import get_value_price_zone_service
        from src.value_strategy import get_value_strategy_state_service, valuation_reliability
        from src.value_watchpoints.dedupe import merge_watchpoints
        from src.value_watchpoints import projectors

        # --- warm every module import / connection first so timings measure work
        timed(counter, "imports_and_singletons", lambda: (
            get_value_strategy_state_service(), get_risk_research_service(),
            get_financial_analysis_service(), get_moat_research_service(),
        ), rows)

        state_service = get_value_strategy_state_service()
        state = timed(counter, "1_get_strategy_state", lambda: state_service.get_strategy_state(MARKET, CODE), rows)
        as_of = str((state or {}).get("research_as_of") or "")[:10] or None
        report["research_as_of"] = as_of

        # Break the strategy state into its own internal parts.
        inner: list[dict[str, Any]] = []
        timed(counter, "1a_pool_active", lambda: list(state_service.pool_repository.active(MARKET)), inner)
        timed(counter, "1b_focus_selection", lambda: state_service.focus_service.get_focus_selection(as_of=as_of), inner)
        timed(counter, "1c_price_zones", lambda: state_service.price_zone_service.get_price_zones(MARKET, CODE, as_of=as_of), inner)
        timed(counter, "1d_entry_research", lambda: state_service.entry_service.get_entry_research(MARKET, CODE, as_of=as_of), inner)
        timed(counter, "1e_exit_research", lambda: state_service.exit_service.get_exit_research(MARKET, CODE, as_of=as_of), inner)
        timed(counter, "1f_risk_research", lambda: state_service.risk_service.get_risk_research(MARKET, CODE, as_of=as_of), inner)
        timed(counter, "1g_thesis", lambda: state_service.thesis_repository.get_current_thesis(MARKET, CODE), inner)
        timed(counter, "1h_leader_profile", lambda: state_service.leader_service.get_profile(MARKET, CODE, as_of=as_of), inner)
        report["strategy_state_internals"] = inner

        repo = CompanyThesisRepository()
        thesis = timed(counter, "2_thesis_load", lambda: repo.get_current_thesis(MARKET, CODE), rows)
        risk = timed(counter, "3_risk_research", lambda: get_risk_research_service().get_risk_research(MARKET, CODE, as_of=as_of), rows)
        financial = timed(
            counter, "4_financial_saved",
            lambda: get_financial_analysis_service().get_saved_resolved_analysis(CODE, as_of=as_of), rows,
        )
        normalized = timed(
            counter, "5_normalized_earnings",
            lambda: get_normalized_earnings_reference_service().reference(MARKET, CODE, as_of=as_of), rows,
        )
        cycle = timed(
            counter, "6_cycle_scenario",
            lambda: get_cycle_profit_scenario_service().scenario(MARKET, CODE, as_of=as_of), rows,
        )
        business_store = get_business_research_service().store
        business = timed(counter, "7_business_research", lambda: business_store.latest(CODE, as_of=as_of), rows)
        zones = timed(
            counter, "8a_price_zones_for_reliability",
            lambda: get_value_price_zone_service().get_price_zones(MARKET, CODE, as_of=as_of), rows,
        )
        timed(counter, "8b_valuation_reliability_calc", lambda: valuation_reliability(zones or {}), rows)
        moat = timed(counter, "9_moat_research", lambda: get_moat_research_service().get_research(MARKET, CODE, as_of=as_of), rows)
        capital = timed(
            counter, "10_capital_allocation",
            lambda: get_capital_allocation_research_service().get_research(MARKET, CODE, as_of=as_of), rows,
        )
        timed(counter, "10b_deep_coverage", lambda: get_deep_research_coverage_service().coverage(MARKET, CODE, as_of=as_of), rows)

        candidates: list[dict[str, Any]] = []

        def build() -> None:
            for producer in (
                lambda: projectors.thesis_items(thesis, research_as_of=as_of),
                lambda: projectors.risk_items(risk, research_as_of=as_of),
                lambda: projectors.financial_items(financial or {}, normalized, cycle, research_as_of=as_of),
                lambda: projectors.business_items(business or {}, research_as_of=as_of),
                lambda: projectors.valuation_items(valuation_reliability(zones or {}), research_as_of=as_of),
                lambda: projectors.moat_items(moat, research_as_of=as_of),
                lambda: projectors.capital_items(capital, research_as_of=as_of),
            ):
                produced, _gaps = producer()
                candidates.extend(produced)

        timed(counter, "11a_projectors", build, rows)
        merged = timed(counter, "11b_dedupe", lambda: merge_watchpoints(candidates), rows)
        timed(counter, "12_ranking", lambda: sorted(merged or [], key=lambda item: str(item.get("title"))), rows)
        timed(counter, "13_serialization", lambda: json.dumps(merged or [], ensure_ascii=False, default=str), rows)

        total = sum(row["total_ms"] for row in rows if not row["component"].startswith("imports"))
        for row in rows:
            row["percentage"] = round(row["total_ms"] / total * 100, 1) if total else None
        report["components"] = rows
        report["measured_total_ms"] = round(total, 1)
        report["top3_components"] = sorted(rows, key=lambda row: -row["total_ms"])[:3]
        report["network_calls"] = 0
        report["llm_calls"] = 0

        # Cursor availability for the fast path decision.
        db = get_runtime_root() / "research.db"
        conn = sqlite3.connect(str(db))
        cursor_cols = [row[1] for row in conn.execute("PRAGMA table_info(value_strategy_state_cursors)")]
        row = conn.execute(
            "SELECT * FROM value_strategy_state_cursors WHERE market=? AND stock_code=?", (MARKET, CODE),
        ).fetchone()
        latest = conn.execute(
            "SELECT MAX(research_as_of) FROM value_strategy_state_cursors WHERE market=?", (MARKET,),
        ).fetchone()[0]
        cursor_count = conn.execute(
            "SELECT COUNT(*) FROM value_strategy_state_cursors WHERE market=?", (MARKET,),
        ).fetchone()[0]
        report["cursor"] = {
            "columns": cursor_cols,
            "row_exists": row is not None,
            "row_research_as_of": (dict(zip(cursor_cols, row)).get("research_as_of") if row else None),
            "latest_cursor_research_as_of": latest,
            "cursor_count": cursor_count,
            "state_json_len": (len(str(dict(zip(cursor_cols, row)).get("state_json") or "")) if row else 0),
        }
        conn.close()
    finally:
        counter.restore()

    dest = Path(__file__).resolve().parents[2] / "docs" / "value-strategy" / "_watchpoint_perf_audit.json"
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(dest)
    print(json.dumps(report.get("top3_components"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

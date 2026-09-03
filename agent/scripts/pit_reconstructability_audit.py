"""Read-only historical reconstructability audit for Value Line PIT remediation V1.

Answers, per historical research date, whether each PIT input category is
RECONSTRUCTABLE / PARTIAL / NOT_RECONSTRUCTABLE from currently stored data.
Never writes to any database.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

RUNTIME = Path.home() / ".vibe-trading"
RESEARCH_DB = RUNTIME / "research.db"
TDX_DB = RUNTIME / "tdx_data.db"

DATES = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01"]


def research() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{RESEARCH_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tdx() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{TDX_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def market_close_status(t: sqlite3.Connection, day: str) -> dict:
    run = t.execute(
        """SELECT rr.snapshot_id, rr.status, rr.completed_at,
                  (SELECT ds.item_count FROM dataset_snapshots ds
                    WHERE ds.snapshot_id=rr.snapshot_id AND ds.dataset='quotes') AS quotes_count,
                  (SELECT ds.status FROM dataset_snapshots ds
                    WHERE ds.snapshot_id=rr.snapshot_id AND ds.dataset='quotes') AS quotes_status
           FROM refresh_runs rr
           WHERE rr.profile='market_close' AND rr.market='CN' AND rr.market_date=?
           ORDER BY rr.completed_at DESC LIMIT 1""",
        (day,),
    ).fetchone()
    if not run:
        return {"run": None, "status": "NO_RUN"}
    if run["status"] != "completed":
        return {"run": dict(run), "status": f"RUN_{str(run['status']).upper()}"}
    if run["quotes_status"] != "ready":
        return {"run": dict(run), "status": "QUOTES_NOT_READY"}
    count = int(run["quotes_count"] or 0)
    return {"run": dict(run), "status": "QUALIFIED" if count >= 5000 else f"QUOTES_SMALL({count})"}


def pool_snapshot_day(r: sqlite3.Connection, day: str) -> dict:
    archived = r.execute(
        """SELECT COUNT(*) AS n, COUNT(DISTINCT stock_code) AS codes
           FROM company_low_value_leader_pool_snapshots
           WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE'""",
        (day,),
    ).fetchone()
    active = r.execute(
        """SELECT COUNT(*) AS n FROM company_low_value_leader_pool
           WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE'""",
        (day,),
    ).fetchone()
    return {"archived": int(archived["n"]), "archived_codes": int(archived["codes"]), "active": int(active["n"])}


def snapshot_payload_keys(r: sqlite3.Connection, day: str) -> dict:
    """Inspect what valuation provenance historical snapshot payloads actually carry."""
    row = r.execute(
        """SELECT payload_json FROM company_low_value_leader_pool_snapshots
           WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE' LIMIT 1""",
        (day,),
    ).fetchone()
    if not row:
        return {"exists": False}
    payload = json.loads(row[0] or "{}")
    metadata = payload.get("metadata") or {}
    quality = metadata.get("valuation_quality")
    dq = metadata.get("data_quality") or {}
    peers = dq.get("peer_comparables") or {}
    return {
        "exists": True,
        "has_valuation_quality": isinstance(quality, dict),
        "valuation_quality": quality,
        "peer_comparables_keys": sorted(peers.keys()),
        "peer_count_value": peers.get("peer_count"),
        "has_price_zone_formula_version": "price_zone_formula_version" in metadata,
        "metadata_keys": sorted(metadata.keys()),
    }


def active_payload_keys(r: sqlite3.Connection, day: str) -> dict:
    row = r.execute(
        """SELECT metadata_json FROM company_low_value_leader_pool
           WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE' LIMIT 1""",
        (day,),
    ).fetchone()
    if not row:
        return {"exists": False}
    metadata = json.loads(row[0] or "{}")
    return {
        "exists": True,
        "valuation_quality": metadata.get("valuation_quality"),
        "peer_comparables": (metadata.get("data_quality") or {}).get("peer_comparables"),
    }


def l3_run(r: sqlite3.Connection, day: str) -> dict:
    row = r.execute(
        "SELECT COUNT(*) AS n FROM value_level3_leaders WHERE as_of=?", (day,),
    ).fetchone()
    top = r.execute(
        "SELECT COUNT(DISTINCT stock_code) AS n FROM value_level3_leaders WHERE as_of=? AND leader_rank<=2",
        (day,),
    ).fetchone()
    return {"rows": int(row["n"]), "top2_codes": int(top["n"])}


def financial_pit(r: sqlite3.Connection, day: str) -> dict:
    row = r.execute(
        """SELECT COUNT(DISTINCT stock_code) AS n, MAX(as_of) AS latest
           FROM company_financial_analysis_snapshots WHERE as_of<=?""",
        (day,),
    ).fetchone()
    return {"snapshots": int(row["n"]), "latest_as_of": row["latest"]}


def historical_valuation(t: sqlite3.Connection, day: str, codes: list[str]) -> dict:
    if not codes:
        return {"with_series": 0, "missing": []}
    placeholders = ",".join("?" for _ in codes)
    rows = t.execute(
        f"""SELECT stock_code, MAX(trade_date) AS last_trade, MAX(financial_data_as_of) AS fin
            FROM historical_valuation_series
            WHERE market='CN' AND stock_code IN ({placeholders}) AND trade_date<=?
            GROUP BY stock_code""",  # noqa: S608
        (*codes, day),
    ).fetchall()
    have = {str(row["stock_code"]) for row in rows}
    return {"with_series": len(have), "missing": sorted(set(codes) - have)}


def thesis_pit(r: sqlite3.Connection, day: str) -> dict:
    by_source = r.execute(
        "SELECT COUNT(DISTINCT stock_code) AS n FROM company_theses WHERE source_data_as_of<=?",
        (day,),
    ).fetchone()
    by_created = r.execute(
        "SELECT COUNT(DISTINCT stock_code) AS n FROM company_theses WHERE substr(created_at,1,10)<=?",
        (day,),
    ).fetchone()
    history = r.execute("SELECT COUNT(*) AS n FROM company_thesis_history").fetchone()
    theses = r.execute(
        """SELECT MIN(substr(created_at,1,10)) AS earliest, COUNT(*) AS total,
                  SUM(CASE WHEN is_current=1 THEN 1 ELSE 0 END) AS current_n
           FROM company_theses"""
    ).fetchone()
    return {
        "visible_by_source_data_as_of": int(by_source["n"]),
        "visible_by_created_at": int(by_created["n"]),
        "history_rows": int(history["n"]),
        "theses_total": int(theses["total"] or 0),
        "theses_current": int(theses["current_n"] or 0),
        "earliest_created": theses["earliest"],
    }


def risk_snapshots(r: sqlite3.Connection, day: str) -> int:
    return int(r.execute(
        "SELECT COUNT(*) AS n FROM company_low_value_risk_snapshots WHERE source_as_of=?", (day,),
    ).fetchone()["n"])


def records_versioning(t: sqlite3.Connection, dataset: str) -> dict:
    cols = [row[1] for row in t.execute(f"PRAGMA table_info({dataset})")] if False else None
    row = t.execute(
        "SELECT COUNT(*) AS total, MIN(updated_at) AS min_u, MAX(updated_at) AS max_u FROM records WHERE dataset=?",
        (dataset,),
    ).fetchone()
    # per-day counts of updates: how many records were touched per business day
    per_day = t.execute(
        """SELECT substr(updated_at,1,10) AS d, COUNT(*) AS n FROM records
           WHERE dataset=? GROUP BY d ORDER BY d DESC LIMIT 8""",
        (dataset,),
    ).fetchall()
    return {
        "table_columns": cols,
        "total": int(row["total"]),
        "min_updated": row["min_u"],
        "max_updated": row["max_u"],
        "recent_update_days": [(str(x["d"]), int(x["n"])) for x in per_day],
    }


def fundamentals_pit_scope(t: sqlite3.Connection, day: str) -> dict:
    row = t.execute(
        """SELECT COUNT(*) AS usable FROM records
           WHERE dataset='fundamentals' AND substr(updated_at,1,10)<=?""",
        (day,),
    ).fetchone()
    total = t.execute("SELECT COUNT(*) AS n FROM records WHERE dataset='fundamentals'").fetchone()
    return {"usable_at_day": int(row["usable"]), "total_now": int(total["n"])}


def main() -> None:
    r, t = research(), tdx()
    report: dict = {}

    # codes from the actual archived pool snapshot for the earliest replayable day
    base_codes: list[str] = []
    for day in ("2026-08-27", "2026-08-25"):
        rows = r.execute(
            """SELECT stock_code FROM company_low_value_leader_pool_snapshots
               WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE'""",
            (day,),
        ).fetchall()
        if rows:
            base_codes = sorted({str(x[0]) for x in rows})
            break

    report["universe"] = {"low_value_codes_0827": len(base_codes)}
    report["market_close"] = {day: market_close_status(t, day) for day in DATES}
    report["low_value_snapshot"] = {day: pool_snapshot_day(r, day) for day in DATES}
    report["snapshot_payload_provenance"] = {}
    for day in DATES:
        entry = snapshot_payload_keys(r, day)
        if not entry.get("exists"):
            entry.update(active_payload_keys(r, day))
            entry["source"] = "ACTIVE_PROJECTION" if entry.get("exists") else "NONE"
        else:
            entry["source"] = "ARCHIVED_SNAPSHOT"
        report["snapshot_payload_provenance"][day] = entry
    report["l3_run"] = {day: l3_run(r, day) for day in DATES}
    report["financial_pit"] = {day: financial_pit(r, day) for day in DATES}
    report["historical_valuation"] = historical_valuation(t, "2026-08-31", base_codes)
    report["thesis_pit"] = thesis_pit(r, DATES[-1])
    report["thesis_by_day"] = {day: {
        "by_source": int(r.execute(
            "SELECT COUNT(DISTINCT stock_code) AS n FROM company_theses WHERE source_data_as_of<=?", (day,)).fetchone()["n"]),
        "by_created": int(r.execute(
            "SELECT COUNT(DISTINCT stock_code) AS n FROM company_theses WHERE substr(created_at,1,10)<=?", (day,)).fetchone()["n"]),
    } for day in DATES}
    report["risk_snapshots"] = {day: risk_snapshots(r, day) for day in DATES}
    report["records_versioning"] = {
        "research_terminal_industry_members": records_versioning(t, "research_terminal_industry_members"),
        "fundamentals": records_versioning(t, "fundamentals"),
    }
    report["fundamentals_pit_scope"] = {day: fundamentals_pit_scope(t, day) for day in DATES}

    # adjusted bars / historical valuation PIT write discipline
    report["adjusted_bar_updates_by_day"] = [
        (str(x["d"]), int(x["n"]))
        for x in t.execute(
            """SELECT substr(fetched_at,1,10) AS d, COUNT(DISTINCT stock_code) AS n
               FROM adjusted_daily_bar_coverage GROUP BY d ORDER BY d DESC LIMIT 10"""
        ).fetchall()
    ]

    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    r.close()
    t.close()


if __name__ == "__main__":
    sys.exit(main())

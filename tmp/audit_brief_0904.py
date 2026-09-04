"""Explain why 2026-09-04 Daily Brief looks empty."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("=== BRIEF 09-04 headline fields ===")
row = conn.execute(
    "SELECT research_as_of, formula_version, "
    "json_extract(brief_payload_json, '$.executive_watchlist') AS wl, "
    "json_extract(brief_payload_json, '$.executive_watchlist_basis') AS basis, "
    "json_extract(brief_payload_json, '$.strategy_changes') AS sc, "
    "json_extract(brief_payload_json, '$.macro_environment.changed') AS mchg, "
    "json_extract(brief_payload_json, '$.deeply_undervalued_count') AS deep, "
    "json_extract(brief_payload_json, '$.research_appendix.priority_companies') AS prio "
    "FROM investment_research_daily_briefs WHERE research_as_of='2026-09-04'"
).fetchone()
if not row:
    print("no brief")
else:
    wl = json.loads(row["wl"] or "[]")
    sc = json.loads(row["sc"] or "{}")
    prio = json.loads(row["prio"] or "[]")
    print({
        "formula_version": row["formula_version"],
        "watchlist_count": len(wl),
        "watchlist_basis": row["basis"],
        "deeply_undervalued_count": row["deep"],
        "priority_count": len(prio) if isinstance(prio, list) else prio,
        "macro_changed": row["mchg"],
        "strategy_changes_keys": list(sc.keys()) if isinstance(sc, dict) else type(sc).__name__,
        "strategy_empty": (sc.get("empty") if isinstance(sc, dict) else None),
        "strategy_line_count": len(sc.get("lines") or []) if isinstance(sc, dict) else None,
    })

print("\n=== pool / events 09-04 ===")
print("ACTIVE pool", dict(conn.execute(
    "SELECT COUNT(*) AS c FROM company_low_value_leader_pool WHERE pool_status='ACTIVE'"
).fetchone()))
for tbl, q in [
    ("enter/exit events", "SELECT event_type, COUNT(*) c FROM company_low_value_leader_pool_events WHERE event_date='2026-09-04' GROUP BY event_type"),
]:
    try:
        rows = conn.execute(q).fetchall()
        print(tbl, [dict(r) for r in rows] or "0")
    except Exception as exc:
        print(tbl, type(exc).__name__, exc)

print("\n=== focus selection if table exists ===")
names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for name in names:
    if "focus" in name.lower():
        print("table", name)

# try common focus snapshot
for q in [
    "SELECT COUNT(*) c, MIN(tier) tmin, MAX(tier) tmax FROM focus_selection_members WHERE research_as_of='2026-09-04'",
    "SELECT research_as_of, json_extract(payload_json,'$.tier_a_count') FROM focus_snapshots ORDER BY research_as_of DESC LIMIT 3",
]:
    try:
        print(q[:60], conn.execute(q).fetchall())
    except Exception:
        pass

conn.close()

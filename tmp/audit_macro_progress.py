"""Read-only macro line progress audit."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
db = Path.home() / ".vibe-trading" / "research.db"
c = sqlite3.connect(str(db))
c.row_factory = sqlite3.Row

print("=== automation ===")
row = c.execute(
    "SELECT last_status, last_error, updated_at, next_run_at FROM value_research_automation WHERE id='default'"
).fetchone()
if row:
    d = dict(row)
    print(d)
    print("MACRO_LINE in stages:", "MACRO_LINE" in (d.get("last_error") or ""))

print("\n=== snapshots (latest 5) ===")
for r in c.execute(
    "SELECT as_of, regime, status, coverage, series_count, series_total, created_at "
    "FROM macro_snapshots ORDER BY as_of DESC, created_at DESC LIMIT 5"
):
    print(dict(r))

print("\n=== latest snapshot ===")
r = c.execute(
    "SELECT as_of, regime, status, missing_fields_json, axes_json, states_json "
    "FROM macro_snapshots ORDER BY as_of DESC, created_at DESC LIMIT 1"
).fetchone()
if r:
    d = dict(r)
    print("as_of", d["as_of"], "regime", d["regime"], "status", d["status"])
    print("missing", d["missing_fields_json"])
    print("axes", d["axes_json"][:200])
    print("states", d["states_json"][:200])

print("\n=== macro_events ===")
print("total", c.execute("SELECT COUNT(*) FROM macro_events").fetchone()[0])
for r in c.execute(
    "SELECT research_as_of, event_type, axis_key, from_value, to_value, status, created_at "
    "FROM macro_events ORDER BY created_at DESC LIMIT 12"
):
    print(dict(r))
print("by date:", list(c.execute(
    "SELECT research_as_of, COUNT(*) FROM macro_events GROUP BY research_as_of ORDER BY 1 DESC"
)))

print("\n=== daily brief ===")
for x in c.execute(
    "SELECT research_as_of, status, formula_version, updated_at "
    "FROM investment_research_daily_briefs ORDER BY research_as_of DESC LIMIT 3"
):
    print(dict(x))
r = c.execute(
    "SELECT research_as_of, brief_payload_json FROM investment_research_daily_briefs "
    "ORDER BY research_as_of DESC LIMIT 1"
).fetchone()
if r and r[1]:
    p = json.loads(r[1])
    env = p.get("macro_environment") or {}
    print("brief date", r[0])
    print("macro_environment keys", list(env.keys()) if env else None)
    if env:
        print("text", (env.get("text") or "")[:220])
        print("changed", env.get("changed"), "changes", env.get("changes"))

print("\n=== pool ===")
print("active", c.execute(
    "SELECT COUNT(*) FROM company_low_value_leader_pool WHERE pool_status='ACTIVE'"
).fetchone()[0])

print("\n=== macro_series count ===")
try:
    print("series rows", c.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0])
    print("distinct series", c.execute("SELECT COUNT(DISTINCT series_id) FROM macro_series").fetchone()[0])
except Exception as exc:
    print("macro_series error", exc)

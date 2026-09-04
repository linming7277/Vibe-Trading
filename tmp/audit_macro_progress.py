"""Quick macro line production audit."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

row = conn.execute(
    "SELECT last_status, last_error FROM value_research_automation WHERE id='default'"
).fetchone()
print("=== EOD ===")
print(dict(row) if row else "no row")

snap = conn.execute(
    "SELECT as_of, regime, status, series_count, series_total, missing_series_json "
    "FROM macro_snapshots ORDER BY as_of DESC LIMIT 1"
).fetchone()
print("=== SNAPSHOT ===")
if snap:
    d = dict(snap)
    d["missing_series_json"] = json.loads(d.get("missing_series_json") or "[]")
    print(d)

print("=== EVENTS (latest 8) ===")
for e in conn.execute(
    "SELECT research_as_of, event_type, axis_key, from_value, to_value, status "
    "FROM macro_events ORDER BY created_at DESC LIMIT 8"
):
    print(dict(e))

print("=== BRIEF macro_environment ===")
for b in conn.execute(
    "SELECT research_as_of, "
    "json_extract(brief_payload_json, '$.macro_environment.available') AS avail, "
    "json_extract(brief_payload_json, '$.macro_environment.changed') AS changed "
    "FROM investment_research_daily_briefs ORDER BY research_as_of DESC LIMIT 5"
):
    print(dict(b))

pool = conn.execute(
    "SELECT COUNT(*) AS c FROM company_low_value_leader_pool WHERE pool_status='ACTIVE'"
).fetchone()
print("=== POOL ACTIVE ===", dict(pool))

mb = conn.execute("SELECT COUNT(*) AS c FROM macro_briefs").fetchone()
print("=== macro_briefs count ===", dict(mb))

today = date.today().isoformat()
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from src.macro_line.freshness import check_macro_source_freshness

print("=== FRESHNESS ===", check_macro_source_freshness(as_of=today))
conn.close()

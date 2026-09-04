"""Confirm as_of mismatch between pool and 09-04 brief."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("=== pool source_as_of ===")
print([dict(r) for r in conn.execute(
    "SELECT source_as_of, COUNT(*) c FROM company_low_value_leader_pool WHERE pool_status='ACTIVE' GROUP BY 1"
)])
print("valuation", [dict(r) for r in conn.execute(
    "SELECT valuation_status, COUNT(*) c FROM company_low_value_leader_pool WHERE pool_status='ACTIVE' GROUP BY 1"
)])
print("snapshots 09-04", conn.execute(
    "SELECT COUNT(*) FROM company_low_value_leader_pool WHERE source_as_of='2026-09-04'"
).fetchone()[0])
print("snapshots 09-03", conn.execute(
    "SELECT COUNT(*) FROM company_low_value_leader_pool WHERE source_as_of='2026-09-03'"
).fetchone()[0])

print("=== briefs ===")
for r in conn.execute(
    "SELECT research_as_of, formula_version FROM investment_research_daily_briefs ORDER BY research_as_of DESC LIMIT 6"
):
    print(dict(r))

print("=== automation ===")
print(dict(conn.execute("SELECT last_status, last_error FROM value_research_automation WHERE id='default'").fetchone() or {}))

print("=== low_value events 09-03/04 ===")
print([dict(r) for r in conn.execute(
    "SELECT event_date, event_type, COUNT(*) c FROM low_value_leader_events WHERE event_date>='2026-09-01' GROUP BY 1,2 ORDER BY 1 DESC"
)])

print("=== strategy events 09-03/04 ===")
print([dict(r) for r in conn.execute(
    "SELECT research_as_of, COUNT(*) c FROM value_strategy_state_events WHERE research_as_of>='2026-09-01' GROUP BY 1 ORDER BY 1 DESC"
)])
conn.close()

from src.focus_selection import FocusSelectionService
svc = FocusSelectionService()
for as_of in ("2026-09-03", "2026-09-04"):
    try:
        sel = svc.get_focus_selection(as_of=as_of)
        print(f"Focus {as_of}", {k: sel.get(k) for k in ("research_as_of", "total_low_value", "A_count", "B_count", "C_count", "hard_c_count")})
        print("  A", [x.get("stock_code") for x in (sel.get("A") or [])[:10]])
    except Exception as exc:
        print(f"Focus {as_of} FAIL", type(exc).__name__, exc)

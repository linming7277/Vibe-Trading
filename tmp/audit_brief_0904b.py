"""Deeper 09-04 brief emptiness: focus fallback + deep undervaluation."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository

repo = InvestmentResearchDailyBriefRepository()
brief = repo.get_completed("2026-09-04")
payload = (brief or {}).get("brief_payload") or {}
print("basis", payload.get("executive_watchlist_basis"))
print("data_gaps sample", (brief or {}).get("data_gaps", payload.get("data_gaps")) )
gaps = (brief or {}).get("data_gaps") or payload.get("data_gaps") or []
print("gaps count", len(gaps))
for g in gaps[:12]:
    print(" GAP", g)

print("\n=== try live Focus A ===")
try:
    from src.value_focus_selection import FocusSelectionService
    svc = FocusSelectionService()
    sel = svc.get_focus_selection(as_of="2026-09-04")
    print("keys", list(sel.keys())[:20])
    a = sel.get("A") or sel.get("tier_a") or []
    print("A count", len(a) if isinstance(a, list) else type(a), a if not isinstance(a, list) else [x.get("stock_code") for x in a[:5]])
except Exception as exc:
    print("FOCUS FAIL", type(exc).__name__, exc)

print("\n=== valuation status in pool ===")
db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
# discover pool table columns
cols = [r[1] for r in conn.execute("PRAGMA table_info(company_low_value_leader_pool)").fetchall()]
print("pool cols", cols[:40])
if "valuation_status" in cols:
    print(conn.execute(
        "SELECT valuation_status, COUNT(*) c FROM company_low_value_leader_pool WHERE pool_status='ACTIVE' GROUP BY 1"
    ).fetchall())
# events table names
print("event-like tables", [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%event%'"
).fetchall()])
print("focus-like tables", [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%focus%' OR name LIKE '%opportunity%')"
).fetchall()])
conn.close()

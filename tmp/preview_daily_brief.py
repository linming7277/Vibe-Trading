# -*- coding: utf-8 -*-
"""Preview latest daily brief locally; never send Feishu."""
from __future__ import annotations

import json
import sqlite3
from html import escape
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
print("briefs:")
for r in conn.execute(
    """SELECT research_as_of, status, formula_version, updated_at
       FROM investment_research_daily_briefs
       ORDER BY research_as_of DESC LIMIT 8"""
):
    print(dict(r))
row = conn.execute(
    """SELECT research_as_of, status, formula_version, brief_payload_json
       FROM investment_research_daily_briefs
       WHERE status='READY'
       ORDER BY research_as_of DESC LIMIT 1"""
).fetchone()
if not row:
    raise SystemExit("no READY brief")
payload = json.loads(row["brief_payload_json"] or "{}")
watch = list(payload.get("executive_watchlist") or [])
print("latest", row["research_as_of"], "formula", row["formula_version"], "watch", len(watch))
print("basis", payload.get("executive_watchlist_basis"))
for item in watch:
    support = dict(item.get("historical_support") or {})
    print(json.dumps({
        "code": item.get("stock_code"),
        "name": item.get("company_name"),
        "price": item.get("current_price"),
        "fv": [item.get("fair_value_low"), item.get("fair_value_mid"), item.get("fair_value_high")],
        "gap": item.get("valuation_gap_percent"),
        "support": [support.get("low"), support.get("high")],
        "zone": [item.get("support_zone_low"), item.get("support_zone_high")],
    }, ensure_ascii=False))
conn.close()

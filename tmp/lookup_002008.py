"""Lookup 002008 in production DB."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("=== low value pool ===")
for r in conn.execute(
    """SELECT stock_code, company_name, industry_name, pool_status, valuation_status,
              current_price, fair_value_low, fair_value_mid, fair_value_high,
              leader_score, source_as_of, entry_level
       FROM company_low_value_leader_pool WHERE stock_code LIKE '002008%'"""
):
    print(dict(r))

print("\n=== l3 pool members ===")
for r in conn.execute(
    """SELECT stock_code, company_name, industry_name, leader_rank, leader_score, source_as_of
       FROM l3_leader_pool_members WHERE stock_code LIKE '002008%' LIMIT 5"""
):
    print(dict(r))

print("\n=== focus / risk if exists ===")
for q in [
    "SELECT * FROM low_value_risk_snapshots WHERE stock_code LIKE '002008%' ORDER BY source_as_of DESC LIMIT 1",
]:
    try:
        row = conn.execute(q).fetchone()
        if row:
            d = dict(row)
            for k in list(d.keys())[:15]:
                print(k, d[k])
    except Exception as exc:
        print("skip", exc)

conn.close()

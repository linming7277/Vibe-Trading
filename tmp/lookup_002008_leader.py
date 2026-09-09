"""Lookup 002008 leader / pool status and schema."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
print("db", db, "exists", db.exists())
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
print("relevant tables:")
for t in tabs:
    if any(k in t.lower() for k in ["pool", "leader", "focus", "l3", "value", "member", "low", "industry"]):
        print(" ", t)

print("\n=== company_low_value_leader_pool ===")
try:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(company_low_value_leader_pool)")]
    print("cols", cols)
    for r in conn.execute(
        "SELECT * FROM company_low_value_leader_pool WHERE stock_code LIKE '002008%'"
    ):
        print(json.dumps(dict(r), ensure_ascii=False, default=str))
except Exception as exc:
    print("err", exc)

# discover l3-like tables
for t in tabs:
    if "l3" in t.lower() or "leader" in t.lower():
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            if any("stock" in c.lower() for c in cols):
                print(f"\n=== {t} ===")
                print("cols", cols)
                q = f"SELECT * FROM {t} WHERE stock_code LIKE '002008%' LIMIT 10"
                rows = list(conn.execute(q))
                if not rows:
                    # try alternate
                    for c in cols:
                        if "code" in c.lower():
                            q2 = f"SELECT * FROM {t} WHERE {c} LIKE '002008%' LIMIT 5"
                            try:
                                rows = list(conn.execute(q2))
                            except Exception:
                                rows = []
                            if rows:
                                break
                for r in rows:
                    print(json.dumps(dict(r), ensure_ascii=False, default=str))
                if not rows:
                    print("(no 002008 rows)")
        except Exception as exc:
            print(t, exc)

# industry peers for laser equipment
print("\n=== laser industry peers in low value pool ===")
try:
    for r in conn.execute(
        """SELECT stock_code, company_name, industry_name, pool_status, valuation_status,
                  leader_score, source_as_of, entry_level, leader_rank
           FROM company_low_value_leader_pool
           WHERE industry_name LIKE '%激光%' OR stock_code LIKE '002008%'
           ORDER BY leader_score DESC NULLS LAST"""
    ):
        print(json.dumps(dict(r), ensure_ascii=False, default=str))
except Exception as exc:
    print("peer err", exc)
    try:
        for r in conn.execute(
            """SELECT stock_code, company_name, industry_name, pool_status, valuation_status,
                      leader_score, source_as_of, entry_level
               FROM company_low_value_leader_pool
               WHERE industry_name LIKE '%激光%' OR stock_code LIKE '002008%'
               ORDER BY leader_score DESC"""
        ):
            print(json.dumps(dict(r), ensure_ascii=False, default=str))
    except Exception as exc2:
        print("peer err2", exc2)

conn.close()

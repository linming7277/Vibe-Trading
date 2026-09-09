# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

# latest pool for industry 881316
print("=== latest laser leaders in value_level3_leaders ===")
row = conn.execute(
    """SELECT run_id, as_of, leader_formula_version
       FROM value_level3_leaders
       WHERE level3_code='881316.SH'
       ORDER BY as_of DESC, created_at DESC LIMIT 1"""
).fetchone()
print(dict(row) if row else None)
if row:
    for r in conn.execute(
        """SELECT stock_code, stock_name, leader_rank, leader_score, eligibility_status,
                  leader_formula_version, component_scores_json
           FROM value_level3_leaders
           WHERE run_id=? AND level3_code='881316.SH'
           ORDER BY leader_rank ASC NULLS LAST, leader_score DESC""",
        (row["run_id"],),
    ):
        d = dict(r)
        comps = json.loads(d.pop("component_scores_json") or "{}")
        d["components"] = comps
        print(json.dumps(d, ensure_ascii=False))

print("\n=== latest l3_leader_pool_members for laser ===")
pool = conn.execute(
    """SELECT pool_id, MAX(last_seen_at) AS last_seen
       FROM l3_leader_pool_members WHERE level3_code='881316.SH'
       GROUP BY pool_id ORDER BY last_seen DESC LIMIT 1"""
).fetchone()
print(dict(pool) if pool else None)
if pool:
    for r in conn.execute(
        """SELECT stock_code, stock_name, leader_rank, leader_score, eligibility_status,
                  lifecycle_status, leader_formula_version
           FROM l3_leader_pool_members
           WHERE pool_id=? AND level3_code='881316.SH'
           ORDER BY leader_rank""",
        (pool["pool_id"],),
    ):
        print(json.dumps(dict(r), ensure_ascii=False))

print("\n=== formula versions in recent runs ===")
for r in conn.execute(
    """SELECT as_of, leader_formula_version, COUNT(*) AS n
       FROM value_level3_leaders
       GROUP BY as_of, leader_formula_version
       ORDER BY as_of DESC LIMIT 15"""
):
    print(dict(r))

print("\n=== low value historical valuation change for 002008 ===")
for r in conn.execute(
    """SELECT source_as_of, pool_status,
              json_extract(payload_json, '$.historical_valuation_status') AS hist,
              json_extract(payload_json, '$.valuation_status') AS val,
              json_extract(payload_json, '$.leader_rank') AS rank,
              json_extract(payload_json, '$.leader_score') AS score,
              json_extract(payload_json, '$.current_price') AS price,
              json_extract(payload_json, '$.fair_value_mid') AS fv_mid
       FROM company_low_value_leader_pool_snapshots
       WHERE stock_code='002008.SZ'
       ORDER BY source_as_of"""
):
    print(dict(r))

print("\n=== current pool row key fields ===")
r = conn.execute(
    """SELECT stock_code, company_name, industry_name, leader_rank, leader_score,
              valuation_status, historical_valuation_status, entry_level, pool_status,
              current_price, fair_value_mid, source_as_of, updated_at,
              json_extract(metadata_json, '$.leader_formula_version') AS formula
       FROM company_low_value_leader_pool WHERE stock_code='002008.SZ'"""
).fetchone()
print(json.dumps(dict(r), ensure_ascii=False, default=str))

print("\n=== focus cursor if any ===")
tabs = [x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
for t in tabs:
    if "focus" in t.lower() or "cursor" in t.lower() or "brief" in t.lower():
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
        if any("stock" in c.lower() for c in cols):
            try:
                rows = list(conn.execute(f"SELECT * FROM {t} WHERE stock_code LIKE '002008%' LIMIT 3"))
                if rows:
                    print(t, "->", len(rows))
                    print(json.dumps(dict(rows[0]), ensure_ascii=False, default=str)[:500])
            except Exception as exc:
                pass

conn.close()

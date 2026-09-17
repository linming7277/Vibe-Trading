# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("=== 2026-09-04 V3 laser leaders ===")
row = conn.execute(
    """SELECT run_id, as_of, leader_formula_version, created_at
       FROM value_level3_leaders
       WHERE level3_code='881316.SH' AND leader_formula_version='value-leader-v3.0.0'
       ORDER BY created_at DESC LIMIT 1"""
).fetchone()
print(dict(row) if row else None)
if row:
    for r in conn.execute(
        """SELECT stock_code, stock_name, leader_rank, leader_score, eligibility_status,
                  component_scores_json
           FROM value_level3_leaders
           WHERE run_id=? AND level3_code='881316.SH'
           ORDER BY CASE WHEN leader_rank IS NULL THEN 999 ELSE leader_rank END, leader_score DESC""",
        (row["run_id"],),
    ):
        d = dict(r)
        d["components"] = json.loads(d.pop("component_scores_json") or "{}")
        print(json.dumps(d, ensure_ascii=False))

print("\n=== which run is production pool using ===")
r = conn.execute(
    """SELECT source_pool_id, source_as_of,
              json_extract(metadata_json,'$.leader_formula_version') AS formula
       FROM company_low_value_leader_pool WHERE stock_code='002008.SZ'"""
).fetchone()
print(dict(r))
members = list(conn.execute(
    """SELECT stock_code, stock_name, leader_rank, leader_score, leader_formula_version
       FROM l3_leader_pool_members WHERE pool_id=? ORDER BY leader_rank""",
    (r["source_pool_id"],),
))
print("pool members count", len(members))
for m in members[:5]:
    print(json.dumps(dict(m), ensure_ascii=False))

conn.close()

# -*- coding: utf-8 -*-
"""Audit: production formula vs V2/V3 top2 diff across all industries."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row

print("=== value_level3_leader_runs schema ===")
print([c[1] for c in conn.execute("PRAGMA table_info(value_level3_leader_runs)")])
for r in conn.execute(
    "SELECT * FROM value_level3_leader_runs ORDER BY rowid DESC LIMIT 8"
):
    d = {k: r[k] for k in r.keys() if k != "summary_json"}
    print(json.dumps(d, ensure_ascii=False, default=str))

print("\n=== formula versions by as_of (latest first) ===")
for r in conn.execute(
    """SELECT as_of, leader_formula_version, COUNT(DISTINCT level3_code) AS industries,
              COUNT(*) AS rows, MIN(created_at) AS first_at, MAX(created_at) AS last_at
       FROM value_level3_leaders
       GROUP BY as_of, leader_formula_version
       ORDER BY as_of DESC, last_at DESC
       LIMIT 20"""
):
    print(dict(r))

print("\n=== production low-value pool formula mix ===")
for r in conn.execute(
    """SELECT json_extract(metadata_json,'$.leader_formula_version') AS formula,
              COUNT(*) AS n, MAX(source_as_of) AS max_as_of
       FROM company_low_value_leader_pool
       WHERE pool_status='ACTIVE'
       GROUP BY 1"""
):
    print(dict(r))

print("\n=== latest pool members formula ===")
pool = conn.execute(
    """SELECT pool_id, COUNT(*) n, MAX(last_seen_at) last_seen,
              MAX(leader_formula_version) formula
       FROM l3_leader_pool_members GROUP BY pool_id
       ORDER BY last_seen DESC LIMIT 5"""
).fetchall()
for r in pool:
    print(dict(r))

# Pick best comparable V2 and V3 runs on same as_of if possible
print("\n=== find paired V2/V3 as_of ===")
for r in conn.execute(
    """SELECT as_of,
              SUM(CASE WHEN leader_formula_version LIKE 'value-leader-v2%' THEN 1 ELSE 0 END) AS v2,
              SUM(CASE WHEN leader_formula_version='value-leader-v3.0.0' THEN 1 ELSE 0 END) AS v3
       FROM value_level3_leaders
       GROUP BY as_of HAVING v2>0 AND v3>0
       ORDER BY as_of DESC"""
):
    print(dict(r))

conn.close()

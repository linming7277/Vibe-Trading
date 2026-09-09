# -*- coding: utf-8 -*-
from __future__ import annotations
import json, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

conn = sqlite3.connect(str(Path.home() / ".vibe-trading" / "research.db"))
conn.row_factory = sqlite3.Row
pid = "l3pool_ebfa35d4e17c426b"

print("=== lifecycle x formula (all members) ===")
for r in conn.execute(
    """SELECT lifecycle_status, leader_formula_version, COUNT(*) n
       FROM l3_leader_pool_members WHERE pool_id=?
       GROUP BY 1,2 ORDER BY 1,2""",
    (pid,),
):
    print(dict(r))

print("\n=== ACTIVE only formula ===")
for r in conn.execute(
    """SELECT leader_formula_version, COUNT(*) n
       FROM l3_leader_pool_members
       WHERE pool_id=? AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')
       GROUP BY 1""",
    (pid,),
):
    print(dict(r))

print("\n=== ACTIVE top2 industries formula purity ===")
ind = defaultdict(set)
for r in conn.execute(
    """SELECT level3_code, leader_formula_version
       FROM l3_leader_pool_members
       WHERE pool_id=? AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')
         AND leader_rank IS NOT NULL AND leader_rank<=2""",
    (pid,),
):
    ind[r["level3_code"]].add(r["leader_formula_version"])
print(Counter("+".join(sorted(s)) for s in ind.values()))
print("active top2 industries", len(ind))
print("active top2 members", sum(
    1 for _ in conn.execute(
        """SELECT 1 FROM l3_leader_pool_members
           WHERE pool_id=? AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')
             AND leader_rank<=2""",
        (pid,),
    )
))

# count LEADER_LEFT that are size-quality flips: sample
print("\n=== sample LEADER_LEFT (exited under V3) ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, level3_name, previous_rank, current_rank, payload_json
       FROM l3_leader_pool_events WHERE pool_id=? AND event_type='LEADER_LEFT' LIMIT 15""",
    (pid,),
):
    print(json.dumps(dict(r), ensure_ascii=False)[:300])

print("\n=== sample LEADER_ENTERED ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, level3_name, previous_rank, current_rank
       FROM l3_leader_pool_events WHERE pool_id=? AND event_type='LEADER_ENTERED'""",
    (pid,),
):
    print(dict(r))

print("\n=== sample LEADER_REENTERED ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, level3_name, previous_rank, current_rank
       FROM l3_leader_pool_events WHERE pool_id=? AND event_type='LEADER_REENTERED' LIMIT 15""",
    (pid,),
):
    print(dict(r))

# 7 quality-insufficient top2 — who are they?
v3_run = conn.execute(
    """SELECT run_id FROM value_level3_leaders
       WHERE as_of='2026-09-07' AND leader_formula_version='value-leader-v3.0.0'
       ORDER BY created_at DESC LIMIT 1"""
).fetchone()[0]
print("\n=== V3 top2 quality-insufficient (kept rank, not in pool) ===")
for r in conn.execute(
    """SELECT level3_name, stock_code, stock_name, leader_rank, leader_score,
              eligibility_status, eligibility_reasons_json
       FROM value_level3_leaders
       WHERE run_id=? AND leader_rank<=2 AND eligibility_status!='eligible'""",
    (v3_run,),
):
    print(json.dumps(dict(r), ensure_ascii=False))

conn.close()

# -*- coding: utf-8 -*-
"""Force-rematerialize current industry leader pool with size-only Top2 membership."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"d:/AI/hzstock/agent")))

from src.level3_leaders.store import Level3LeaderStore

RESEARCH_DB = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(RESEARCH_DB))
conn.row_factory = sqlite3.Row

# Prefer the V3 run that backs the current active pool; else latest V3 run.
current = conn.execute(
    """SELECT pool_id FROM l3_leader_pool_members
       WHERE lifecycle_status IN ('NEW','ACTIVE','REENTERED')
       GROUP BY pool_id ORDER BY MAX(last_seen_at) DESC LIMIT 1"""
).fetchone()
source_run = None
if current:
    row = conn.execute(
        "SELECT source_leader_run_id, as_of FROM l3_leader_pool_runs WHERE id=?",
        (current["pool_id"],),
    ).fetchone()
    if row:
        source_run = row["source_leader_run_id"]
        print("current pool", current["pool_id"], "source", source_run, "as_of", row["as_of"])

v3 = conn.execute(
    """SELECT run_id, as_of, COUNT(*) n,
              SUM(CASE WHEN leader_rank<=2 AND leader_rank IS NOT NULL THEN 1 ELSE 0 END) top2,
              SUM(CASE WHEN leader_rank<=2 AND eligibility_status!='eligible' THEN 1 ELSE 0 END) top2_quality_gap
       FROM value_level3_leaders
       WHERE leader_formula_version='value-leader-v3.0.0'
       GROUP BY run_id, as_of
       ORDER BY as_of DESC, MAX(created_at) DESC LIMIT 3"""
).fetchall()
print("recent v3 runs:")
for r in v3:
    print(dict(r))

# Use current pool's source if it is V3; else newest V3
chosen = source_run
if chosen:
    formula = conn.execute(
        "SELECT leader_formula_version FROM value_level3_leaders WHERE run_id=? LIMIT 1",
        (chosen,),
    ).fetchone()
    if not formula or formula[0] != "value-leader-v3.0.0":
        chosen = v3[0]["run_id"] if v3 else None
else:
    chosen = v3[0]["run_id"] if v3 else None

if not chosen:
    raise SystemExit("no V3 run found")

before = conn.execute(
    """SELECT COUNT(*) FROM value_level3_leaders
       WHERE run_id=? AND leader_rank IS NOT NULL AND leader_rank<=2
         AND eligibility_status!='eligible'""",
    (chosen,),
).fetchone()[0]
print("quality-insufficient size top2 in run:", before)
conn.close()

store = Level3LeaderStore(RESEARCH_DB)
try:
    pool, created = store.materialize_pool(chosen, force=True)
    active = [
        m for m in pool.get("members") or []
        if m.get("lifecycle_status") in {"NEW", "ACTIVE", "REENTERED"}
    ]
    changxin = [m for m in active if m.get("stock_code") == "688825.SH"]
    print(json.dumps({
        "created": created,
        "pool_id": pool["id"],
        "as_of": pool.get("as_of"),
        "source_leader_run_id": pool.get("source_leader_run_id"),
        "active_members": len(active),
        "changxin": changxin[0] if changxin else None,
        "quality_gap_in_active": sum(
            1 for m in active if m.get("eligibility_status") != "eligible"
        ),
    }, ensure_ascii=False, indent=2, default=str))
finally:
    store.close()

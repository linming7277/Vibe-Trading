# -*- coding: utf-8 -*-
import json
import sqlite3
from pathlib import Path

conn = sqlite3.connect(str(Path.home() / ".vibe-trading" / "research.db"))
conn.row_factory = sqlite3.Row

print("=== search 长鑫 / 688825 in leader runs ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, level3_name, leader_rank, leader_score,
              eligibility_status, eligibility_reasons_json, leader_formula_version,
              run_id, as_of
       FROM value_level3_leaders
       WHERE stock_code LIKE '688825%' OR stock_name LIKE '%长鑫%'
       ORDER BY as_of DESC, created_at DESC LIMIT 12"""
):
    print(json.dumps(dict(r), ensure_ascii=False))

pool = conn.execute(
    """SELECT pool_id FROM l3_leader_pool_members
       WHERE lifecycle_status IN ('NEW','ACTIVE','REENTERED')
       GROUP BY pool_id ORDER BY MAX(last_seen_at) DESC LIMIT 1"""
).fetchone()[0]
print("\npool", pool)

print("\n=== any 长鑫 in this pool ===")
rows = list(conn.execute(
    """SELECT stock_code, stock_name, level3_name, leader_rank, leader_score,
              eligibility_status, lifecycle_status, leader_formula_version
       FROM l3_leader_pool_members
       WHERE pool_id=? AND (stock_code LIKE '688825%' OR stock_name LIKE '%长鑫%')""",
    (pool,),
))
print(rows or "(absent from pool entirely)")

print("\n=== 集成电路设计 members currently in pool ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, leader_rank, leader_score, lifecycle_status,
              eligibility_status, leader_formula_version
       FROM l3_leader_pool_members
       WHERE pool_id=? AND level3_name='集成电路设计'
       ORDER BY CASE WHEN leader_rank IS NULL THEN 99 ELSE leader_rank END""",
    (pool,),
):
    print(json.dumps(dict(r), ensure_ascii=False))

run = conn.execute(
    """SELECT run_id FROM value_level3_leaders
       WHERE as_of='2026-09-07' AND leader_formula_version='value-leader-v3.0.0'
       ORDER BY created_at DESC LIMIT 1"""
).fetchone()[0]
print("\n=== V3 run", run, "集成电路设计 top ranks ===")
for r in conn.execute(
    """SELECT stock_code, stock_name, leader_rank, leader_score, eligibility_status,
              eligibility_reasons_json, coverage, component_scores_json, raw_features_json
       FROM value_level3_leaders
       WHERE run_id=? AND level3_name='集成电路设计'
         AND leader_rank IS NOT NULL
       ORDER BY leader_rank LIMIT 6""",
    (run,),
):
    d = dict(r)
    d["components"] = json.loads(d.pop("component_scores_json") or "{}")
    d["reasons"] = json.loads(d.pop("eligibility_reasons_json") or "[]")
    raw = json.loads(d.pop("raw_features_json") or "{}")
    d["raw_scale"] = {
        k: raw.get(k) for k in ("market_cap", "revenue", "net_profit", "roe", "pe", "pb")
    }
    print(json.dumps(d, ensure_ascii=False))

conn.close()

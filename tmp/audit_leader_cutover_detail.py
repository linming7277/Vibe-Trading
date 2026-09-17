# -*- coding: utf-8 -*-
"""Clarify production pool V3 cutover completeness."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

conn = sqlite3.connect(str(Path.home() / ".vibe-trading" / "research.db"))
conn.row_factory = sqlite3.Row

pool_id = conn.execute(
    "SELECT source_pool_id, source_as_of FROM company_low_value_leader_pool WHERE stock_code='002008.SZ'"
).fetchone()
print("low value source", dict(pool_id))

# latest pool by last_seen
latest = conn.execute(
    """SELECT pool_id, MAX(last_seen_at) ls, COUNT(*) n,
              SUM(CASE WHEN leader_formula_version='value-leader-v3.0.0' THEN 1 ELSE 0 END) v3,
              SUM(CASE WHEN leader_formula_version LIKE 'value-leader-v2%' THEN 1 ELSE 0 END) v2
       FROM l3_leader_pool_members GROUP BY pool_id ORDER BY ls DESC LIMIT 5"""
).fetchall()
print("latest pools:")
for r in latest:
    print(dict(r))

pid = latest[0]["pool_id"]
print("\nusing pool", pid)

# per-industry formula consistency in pool top2
ind_formula = defaultdict(set)
ind_members = defaultdict(list)
for r in conn.execute(
    """SELECT level3_code, level3_name, stock_code, stock_name, leader_rank,
              leader_score, leader_formula_version, eligibility_status, lifecycle_status
       FROM l3_leader_pool_members
       WHERE pool_id=? AND leader_rank IS NOT NULL AND leader_rank<=2""",
    (pid,),
):
    ind_formula[r["level3_code"]].add(r["leader_formula_version"])
    ind_members[r["level3_code"]].append(dict(r))

formula_mix = Counter()
for code, formulas in ind_formula.items():
    key = "+".join(sorted(formulas))
    formula_mix[key] += 1
print("\nindustry top2 formula mix:", dict(formula_mix))
print("industries with top2:", len(ind_members))

# low value pool: how many of ACTIVE are in current V3 top2?
lv = list(conn.execute(
    """SELECT stock_code, company_name, industry_code, industry_name, leader_rank,
              leader_score, source_as_of,
              json_extract(metadata_json,'$.leader_formula_version') AS meta_formula,
              pool_status
       FROM company_low_value_leader_pool WHERE pool_status='ACTIVE'"""
))
print("active low value", len(lv))
meta = Counter(r["meta_formula"] for r in lv)
print("low value meta formula", dict(meta))

# Compare ACTIVE low-value membership against V3 top2 set from latest v3 run
v3_run = conn.execute(
    """SELECT run_id FROM value_level3_leaders
       WHERE as_of='2026-09-07' AND leader_formula_version='value-leader-v3.0.0'
       ORDER BY created_at DESC LIMIT 1"""
).fetchone()["run_id"]
v3_top2 = set()
v3_by_ind = defaultdict(set)
for r in conn.execute(
    """SELECT level3_code, stock_code FROM value_level3_leaders
       WHERE run_id=? AND leader_rank IS NOT NULL AND leader_rank<=2""",
    (v3_run,),
):
    v3_top2.add(r["stock_code"])
    v3_by_ind[r["level3_code"]].add(r["stock_code"])

in_v3 = sum(1 for r in lv if r["stock_code"] in v3_top2)
out_v3 = [r for r in lv if r["stock_code"] not in v3_top2]
print(f"\nactive LV in V3 top2: {in_v3}/{len(lv)}")
print(f"active LV NOT in V3 top2: {len(out_v3)}")
print("examples not in V3 top2:")
for r in out_v3[:20]:
    ind_set = v3_by_ind.get(r["industry_code"], set())
    print(json.dumps({
        "code": r["stock_code"], "name": r["company_name"],
        "industry": r["industry_name"], "rank": r["leader_rank"],
        "meta_formula": r["meta_formula"],
        "v3_top2_same_industry": sorted(ind_set),
    }, ensure_ascii=False))

# pool events on 09-07: entered/exited due to formula?
print("\n=== LEADER_ENTERED / EXITED on latest pool ===")
for r in conn.execute(
    """SELECT event_type, COUNT(*) n FROM l3_leader_pool_events
       WHERE pool_id=? GROUP BY event_type""",
    (pid,),
):
    print(dict(r))

# quality insufficient in v3 run
for r in conn.execute(
    """SELECT eligibility_status, COUNT(*) n,
              SUM(CASE WHEN leader_rank IS NOT NULL AND leader_rank<=2 THEN 1 ELSE 0 END) top2
       FROM value_level3_leaders WHERE run_id=? GROUP BY eligibility_status""",
    (v3_run,),
):
    print("v3 eligibility", dict(r))

# how many top2 have QUALITY_DATA_INSUFFICIENT
qi = conn.execute(
    """SELECT COUNT(*) FROM value_level3_leaders
       WHERE run_id=? AND leader_rank<=2 AND eligibility_status!='eligible'""",
    (v3_run,),
).fetchone()[0]
print("v3 top2 with non-eligible quality:", qi)

# materialize: are non-eligible top2 in pool?
non_elig_in_pool = conn.execute(
    """SELECT COUNT(*) FROM l3_leader_pool_members
       WHERE pool_id=? AND leader_rank<=2 AND eligibility_status!='eligible'""",
    (pid,),
).fetchone()[0]
print("pool top2 non-eligible:", non_elig_in_pool)

conn.close()

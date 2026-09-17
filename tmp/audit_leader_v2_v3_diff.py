# -*- coding: utf-8 -*-
"""Full-market top2 diff: production V2 vs shadow V3, and cutover check."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

OUT = Path(r"d:/AI/hzstock/tmp/leader_logic_audit_report.json")
db = Path.home() / ".vibe-trading" / "research.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row


def pick_run(as_of: str, formula: str) -> str | None:
    row = conn.execute(
        """SELECT run_id, COUNT(*) n, MAX(created_at) created_at
           FROM value_level3_leaders
           WHERE as_of=? AND leader_formula_version=?
           GROUP BY run_id ORDER BY created_at DESC LIMIT 1""",
        (as_of, formula),
    ).fetchone()
    return row["run_id"] if row else None


def top2_by_industry(run_id: str) -> dict[str, dict]:
    """Return industry -> {name, top2:[(code,name,rank,score)], all ranked top2 only}."""
    out: dict[str, dict] = {}
    for r in conn.execute(
        """SELECT level3_code, level3_name, stock_code, stock_name,
                  leader_rank, leader_score, eligibility_status
           FROM value_level3_leaders
           WHERE run_id=? AND leader_rank IS NOT NULL AND leader_rank <= 2
           ORDER BY level3_code, leader_rank""",
        (run_id,),
    ):
        code = r["level3_code"]
        bucket = out.setdefault(code, {"level3_name": r["level3_name"], "top2": []})
        bucket["top2"].append({
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "rank": r["leader_rank"],
            "score": r["leader_score"],
            "eligibility": r["eligibility_status"],
        })
    return out


def top2_set(info: dict) -> frozenset[str]:
    return frozenset(x["stock_code"] for x in info.get("top2", []))


# formula presence
formula_rows = [dict(r) for r in conn.execute(
    """SELECT as_of, leader_formula_version AS formula, COUNT(DISTINCT run_id) runs,
              COUNT(DISTINCT level3_code) industries, COUNT(*) rows,
              MAX(created_at) last_at
       FROM value_level3_leaders
       GROUP BY as_of, leader_formula_version
       ORDER BY as_of DESC, last_at DESC
       LIMIT 25"""
)]

# production pool
prod = dict(conn.execute(
    """SELECT source_as_of, source_pool_id,
              json_extract(metadata_json,'$.leader_formula_version') AS formula
       FROM company_low_value_leader_pool WHERE stock_code='002008.SZ'"""
).fetchone())

pool_formula = [dict(r) for r in conn.execute(
    """SELECT leader_formula_version AS formula, COUNT(*) n
       FROM l3_leader_pool_members WHERE pool_id=?
       GROUP BY 1""",
    (prod["source_pool_id"],),
)]

# Prefer same as_of pair
pair_as_of = None
for r in conn.execute(
    """SELECT as_of,
              SUM(CASE WHEN leader_formula_version LIKE 'value-leader-v2%' THEN 1 ELSE 0 END) v2,
              SUM(CASE WHEN leader_formula_version='value-leader-v3.0.0' THEN 1 ELSE 0 END) v3
       FROM value_level3_leaders
       GROUP BY as_of HAVING v2>0 AND v3>0
       ORDER BY as_of DESC LIMIT 1"""
):
    pair_as_of = r["as_of"]

# On 09-04 there may be v2.0 and v2.1 and v3 — pick latest v2.* and v3
v2_formula = conn.execute(
    """SELECT leader_formula_version FROM value_level3_leaders
       WHERE as_of=? AND leader_formula_version LIKE 'value-leader-v2%'
       ORDER BY created_at DESC LIMIT 1""",
    (pair_as_of,),
).fetchone()
v2_formula = v2_formula[0] if v2_formula else None
v3_formula = "value-leader-v3.0.0"

v2_run = pick_run(pair_as_of, v2_formula) if pair_as_of and v2_formula else None
v3_run = pick_run(pair_as_of, v3_formula) if pair_as_of else None

v2 = top2_by_industry(v2_run) if v2_run else {}
v3 = top2_by_industry(v3_run) if v3_run else {}

all_industries = sorted(set(v2) | set(v3))
unchanged = []
changed = []
only_v2 = []
only_v3 = []
rank_swap_same_set = []

for ind in all_industries:
    a = v2.get(ind)
    b = v3.get(ind)
    if a and not b:
        only_v2.append(ind)
        continue
    if b and not a:
        only_v3.append(ind)
        continue
    sa, sb = top2_set(a), top2_set(b)
    if sa == sb:
        # check rank order
        order_a = [x["stock_code"] for x in sorted(a["top2"], key=lambda x: x["rank"])]
        order_b = [x["stock_code"] for x in sorted(b["top2"], key=lambda x: x["rank"])]
        if order_a == order_b:
            unchanged.append(ind)
        else:
            rank_swap_same_set.append({
                "level3_code": ind,
                "level3_name": a["level3_name"],
                "v2": a["top2"],
                "v3": b["top2"],
            })
    else:
        changed.append({
            "level3_code": ind,
            "level3_name": a["level3_name"],
            "v2_codes": sorted(sa),
            "v3_codes": sorted(sb),
            "entered": sorted(sb - sa),
            "exited": sorted(sa - sb),
            "v2": a["top2"],
            "v3": b["top2"],
        })

# name lookup
names = {}
for r in conn.execute("SELECT stock_code, company_name FROM company_low_value_leader_pool"):
    names[r["stock_code"]] = r["company_name"]
for bucket in list(v2.values()) + list(v3.values()):
    for x in bucket["top2"]:
        names[x["stock_code"]] = x["stock_name"]

# annotate changed with names
for item in changed:
    item["entered_named"] = [f"{c} {names.get(c, '')}" for c in item["entered"]]
    item["exited_named"] = [f"{c} {names.get(c, '')}" for c in item["exited"]]

# production still V2? compare latest production pool top2 vs V3 on pair as_of
prod_top2 = defaultdict(list)
for r in conn.execute(
    """SELECT level3_code, level3_name, stock_code, stock_name, leader_rank, leader_score,
              leader_formula_version
       FROM l3_leader_pool_members
       WHERE pool_id=? AND leader_rank IS NOT NULL AND leader_rank <= 2
       ORDER BY level3_code, leader_rank""",
    (prod["source_pool_id"],),
):
    prod_top2[r["level3_code"]].append(dict(r))

prod_vs_v3_changed = 0
prod_vs_v3_examples = []
for ind, members in prod_top2.items():
    sa = frozenset(x["stock_code"] for x in members)
    sb = top2_set(v3.get(ind, {"top2": []}))
    if not sb:
        continue
    if sa != sb:
        prod_vs_v3_changed += 1
        if len(prod_vs_v3_examples) < 25:
            prod_vs_v3_examples.append({
                "level3_code": ind,
                "level3_name": members[0]["level3_name"],
                "prod_v2": [f"{x['stock_code']} {x['stock_name']}#{x['leader_rank']}" for x in members],
                "v3": [f"{x['stock_code']} {x['stock_name']}#{x['rank']}" for x in v3[ind]["top2"]],
                "entered": sorted(sb - sa),
                "exited": sorted(sa - sb),
            })

# laser check
laser = {
    "prod": prod_top2.get("881316.SH"),
    "v2_run": v2.get("881316.SH"),
    "v3_run": v3.get("881316.SH"),
}

report = {
    "verdict": {
        "code_uses": "value-leader-v3.0.0 (level3_leaders/service.py + value_line.py)",
        "production_pool_formula": pool_formula,
        "production_as_of": prod["source_as_of"],
        "production_cutover": "NOT_CUT_OVER" if any(
            (x.get("formula") or "").startswith("value-leader-v2") for x in pool_formula
        ) else "CUT_OVER_OR_MIXED",
        "frontend_copy": "ValueLeaderPool still describes V2 '六维加权排序' flow",
    },
    "formula_timeline": formula_rows,
    "comparison": {
        "as_of": pair_as_of,
        "v2_formula": v2_formula,
        "v2_run": v2_run,
        "v3_formula": v3_formula,
        "v3_run": v3_run,
        "industry_count_v2": len(v2),
        "industry_count_v3": len(v3),
        "top2_set_unchanged": len(unchanged),
        "top2_set_changed": len(changed),
        "rank_order_swapped_same_set": len(rank_swap_same_set),
        "only_in_v2": len(only_v2),
        "only_in_v3": len(only_v3),
        "change_rate_pct": round(100 * len(changed) / max(len(all_industries), 1), 1),
    },
    "production_vs_v3": {
        "industries_with_prod_top2": len(prod_top2),
        "top2_set_changed_vs_v3": prod_vs_v3_changed,
        "examples": prod_vs_v3_examples,
    },
    "changed_examples": changed[:40],
    "rank_swap_examples": rank_swap_same_set[:20],
    "laser_881316": laser,
}

OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", OUT)
print(json.dumps({
    "verdict": report["verdict"],
    "comparison": report["comparison"],
    "production_vs_v3_changed": prod_vs_v3_changed,
    "laser": laser,
    "changed_sample": [
        {
            "name": x["level3_name"],
            "exited": x["exited_named"],
            "entered": x["entered_named"],
        }
        for x in changed[:15]
    ],
}, ensure_ascii=False, indent=2))
conn.close()

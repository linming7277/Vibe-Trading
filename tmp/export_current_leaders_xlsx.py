# -*- coding: utf-8 -*-
"""Export current ACTIVE V3 industry leaders (Top2) to Excel."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB = Path.home() / ".vibe-trading" / "research.db"
OUT_DIR = Path(r"d:/AI/hzstock/tmp")
OUT_DIR.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row

# Latest pool by last_seen among ACTIVE members
pool = conn.execute(
    """SELECT pool_id, MAX(last_seen_at) AS last_seen
       FROM l3_leader_pool_members
       WHERE lifecycle_status IN ('NEW','ACTIVE','REENTERED')
       GROUP BY pool_id
       ORDER BY last_seen DESC
       LIMIT 1"""
).fetchone()
pool_id = pool["pool_id"]
as_of_row = conn.execute(
    """SELECT as_of, completed_at, source_leader_run_id, formula_version
       FROM l3_leader_pool_runs WHERE id=?""",
    (pool_id,),
).fetchone()
# schema may differ
if as_of_row is None:
    cols = [c[1] for c in conn.execute("PRAGMA table_info(l3_leader_pool_runs)")]
    as_of_row = conn.execute(f"SELECT * FROM l3_leader_pool_runs WHERE id=?", (pool_id,)).fetchone()
    pool_meta = dict(as_of_row) if as_of_row else {"id": pool_id}
else:
    pool_meta = dict(as_of_row)
    pool_meta["id"] = pool_id

leaders = list(conn.execute(
    """SELECT level1_code, level1_name, level2_code, level2_name,
              level3_code, level3_name,
              stock_code, stock_name,
              leader_rank, leader_score, leader_formula_version,
              coverage, eligibility_status, lifecycle_status,
              component_scores_json, first_entered_at, last_seen_at
       FROM l3_leader_pool_members
       WHERE pool_id=?
         AND lifecycle_status IN ('NEW','ACTIVE','REENTERED')
         AND leader_rank IS NOT NULL
         AND leader_rank <= 2
       ORDER BY level1_code, level2_code, level3_code, leader_rank, stock_code""",
    (pool_id,),
))

# Low-value overlay
lv = {
    r["stock_code"]: dict(r)
    for r in conn.execute(
        """SELECT stock_code, pool_status, valuation_status, historical_valuation_status,
                  entry_level, current_price, fair_value_low, fair_value_mid, fair_value_high,
                  source_as_of AS low_value_as_of
           FROM company_low_value_leader_pool"""
    )
}

rows = []
for r in leaders:
    comps = json.loads(r["component_scores_json"] or "{}")
    lv_row = lv.get(r["stock_code"]) or {}
    rows.append({
        "一级行业代码": r["level1_code"],
        "一级行业": r["level1_name"],
        "二级行业代码": r["level2_code"],
        "二级行业": r["level2_name"],
        "三级行业代码": r["level3_code"],
        "三级行业": r["level3_name"],
        "股票代码": r["stock_code"],
        "股票名称": r["stock_name"],
        "龙头排名": r["leader_rank"],
        "质量分": r["leader_score"],
        "规模地位分": comps.get("industry_position"),
        "盈利能力": comps.get("profitability"),
        "成长稳定性": comps.get("growth_stability"),
        "现金流质量": comps.get("cash_flow"),
        "同行相对估值": comps.get("valuation"),
        "财务稳健与波动": comps.get("governance_risk"),
        "覆盖率": r["coverage"],
        "资格状态": r["eligibility_status"],
        "生命周期": r["lifecycle_status"],
        "公式版本": r["leader_formula_version"],
        "首次进入": r["first_entered_at"],
        "最近可见": r["last_seen_at"],
        "低估池状态": lv_row.get("pool_status") or "",
        "估值标签": lv_row.get("valuation_status") or "",
        "历史估值标签": lv_row.get("historical_valuation_status") or "",
        "买入档": lv_row.get("entry_level") or "",
        "现价": lv_row.get("current_price"),
        "合理价值低": lv_row.get("fair_value_low"),
        "合理价值中": lv_row.get("fair_value_mid"),
        "合理价值高": lv_row.get("fair_value_high"),
        "低估池as_of": lv_row.get("low_value_as_of") or "",
    })

df = pd.DataFrame(rows)

# Industry summary: one row per industry with #1/#2
by_ind = {}
for row in rows:
    key = row["三级行业代码"]
    bucket = by_ind.setdefault(key, {
        "一级行业": row["一级行业"],
        "二级行业": row["二级行业"],
        "三级行业代码": row["三级行业代码"],
        "三级行业": row["三级行业"],
        "第1名代码": "", "第1名名称": "", "第1名质量分": None,
        "第2名代码": "", "第2名名称": "", "第2名质量分": None,
        "公式版本": row["公式版本"],
    })
    if row["龙头排名"] == 1:
        bucket["第1名代码"] = row["股票代码"]
        bucket["第1名名称"] = row["股票名称"]
        bucket["第1名质量分"] = row["质量分"]
    elif row["龙头排名"] == 2:
        bucket["第2名代码"] = row["股票代码"]
        bucket["第2名名称"] = row["股票名称"]
        bucket["第2名质量分"] = row["质量分"]

summary_df = pd.DataFrame(list(by_ind.values())).sort_values(
    ["一级行业", "二级行业", "三级行业代码"]
)

stamp = datetime.now().strftime("%Y%m%d_%H%M")
as_of = str(pool_meta.get("as_of") or pool_meta.get("source_as_of") or "unknown")
out = OUT_DIR / f"当前行业龙头_V3_Top2_{as_of}_{stamp}.xlsx"

meta_df = pd.DataFrame([
    {"字段": "pool_id", "值": pool_id},
    {"字段": "as_of", "值": as_of},
    {"字段": "formula", "值": "value-leader-v3.0.0"},
    {"字段": "说明", "值": "两段式：规模定行业前2排名；质量分为第二阶段评分"},
    {"字段": "活跃龙头家数", "值": len(df)},
    {"字段": "覆盖三级行业数", "值": df["三级行业代码"].nunique()},
    {"字段": "导出时间", "值": datetime.now().isoformat(timespec="seconds")},
    {"字段": "数据来源", "值": str(DB)},
])

with pd.ExcelWriter(out, engine="openpyxl") as writer:
    meta_df.to_excel(writer, sheet_name="说明", index=False)
    summary_df.to_excel(writer, sheet_name="行业前2一览", index=False)
    df.to_excel(writer, sheet_name="龙头明细", index=False)
    # only those also in low-value active pool
    lv_active = df[df["低估池状态"] == "ACTIVE"].copy()
    lv_active.to_excel(writer, sheet_name="低估池交集", index=False)

conn.close()
print(out)
print(f"leaders={len(df)} industries={df['三级行业代码'].nunique()} low_value_overlap={len(lv_active)}")

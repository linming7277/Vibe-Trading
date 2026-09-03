# -*- coding: utf-8 -*-
"""Phase B2: watchpoint data-gap category detail (frozen batch path, snapshot copies)."""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAP = sorted(HERE.glob("runtime-snapshot-*"))[-1]
os.environ["VIBE_TRADING_HOME"] = str(SNAP)
sys.path.insert(0, str(HERE.parents[1] / "agent"))

from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.value_watchpoints import get_value_watchpoint_projection_service

pool = LowValueLeaderPoolRepository()
codes = [str(i["stock_code"]) for i in pool.active("CN")]
pool.close()
codes += [c for c in ("600460.SH", "002371.SZ") if c not in set(codes)]

t0 = time.perf_counter()
batch = get_value_watchpoint_projection_service().get_watchpoints_batch("CN", codes)
gap_cats: Counter = Counter()
gap_sources: Counter = Counter()
need: Counter = Counter()
per = {}
for code, wp in batch.items():
    cats = [str(g.get("category")) for g in (wp.get("data_gaps") or [])]
    srcs = [str(g.get("source_module")) for g in (wp.get("data_gaps") or [])]
    gap_cats.update(cats)
    gap_sources.update(srcs)
    need[str(wp.get("suggested_research_need"))] += 1
    per[code] = {"gap_categories": sorted(set(cats)), "need": wp.get("suggested_research_need"),
                 "top_categories": [str(w.get("category")) for w in (wp.get("top_watchpoints") or [])]}

out = HERE / "phase_b2_wp_gaps.json"
out.write_text(json.dumps({
    "seconds": round(time.perf_counter() - t0, 2),
    "gap_categories": dict(gap_cats),
    "gap_source_modules": dict(gap_sources),
    "suggested_research_need": dict(need),
    "per_company": per,
}, ensure_ascii=False, indent=1), encoding="utf-8")
print("seconds:", round(time.perf_counter() - t0, 2))
print("gap cats:", dict(gap_cats))
print("sources:", dict(gap_sources))
print("need:", dict(need))

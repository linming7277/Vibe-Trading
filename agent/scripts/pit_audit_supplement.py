"""Supplementary read-only audit: missing historical valuation reasons + thesis versions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

RUNTIME = Path.home() / ".vibe-trading"
r = sqlite3.connect(f"file:{(RUNTIME / 'research.db').as_posix()}?mode=ro", uri=True)
r.row_factory = sqlite3.Row
t = sqlite3.connect(f"file:{(RUNTIME / 'tdx_data.db').as_posix()}?mode=ro", uri=True)
t.row_factory = sqlite3.Row

DATES = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]

all_codes: set[str] = set()
for day in DATES:
    rows = r.execute(
        """SELECT stock_code FROM company_low_value_leader_pool_snapshots
           WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE'""",
        (day,),
    ).fetchall()
    all_codes |= {str(x[0]) for x in rows}
print("union low value codes across dates:", len(all_codes))

missing: set[str] = set()
for day in DATES:
    rows = t.execute(
        "SELECT stock_code FROM historical_valuation_series WHERE market='CN' AND trade_date<=? GROUP BY stock_code",
        (day,),
    ).fetchall()
    have = {str(x[0]) for x in rows}
    for day_codes in [
        {str(x[0]) for x in r.execute(
            """SELECT stock_code FROM company_low_value_leader_pool_snapshots
               WHERE market='CN' AND source_as_of=? AND pool_status='ACTIVE'""", (day,)).fetchall()}
    ]:
        missing |= day_codes - have
print("union missing historical valuation:", len(missing), sorted(missing))

report = {}
for code in sorted(missing):
    bars = t.execute(
        """SELECT COUNT(*) AS n, MIN(trade_date) AS f, MAX(trade_date) AS l FROM adjusted_daily_bars
           WHERE market='CN' AND stock_code=? AND adjustment_type='front'""",
        (code,),
    ).fetchone()
    klines = t.execute(
        "SELECT COUNT(*) AS n FROM records WHERE dataset='klines' AND record_key LIKE ?",
        (f"%{code}%",),
    ).fetchone()
    fin = r.execute(
        "SELECT COUNT(*) AS n FROM company_financial_analysis_snapshots WHERE stock_code=?",
        (code,),
    ).fetchone()
    cov = t.execute(
        "SELECT bar_count, coverage_status, last_date, error FROM adjusted_daily_bar_coverage WHERE market='CN' AND stock_code=? AND adjustment_type='front'",
        (code,),
    ).fetchone()
    hvcov = t.execute(
        "SELECT * FROM historical_valuation_coverage WHERE market='CN' AND stock_code=?",
        (code,),
    ).fetchone()
    report[code] = {
        "adjusted_bars": dict(bars) if bars else None,
        "klines_records": int(klines["n"]) if klines else 0,
        "financial_snapshots": int(fin["n"]) if fin else 0,
        "bar_coverage": dict(cov) if cov else None,
        "hv_coverage": dict(hvcov) if hvcov else None,
    }
print(json.dumps(report, ensure_ascii=False, indent=1, default=str))

# thesis version distribution + any version>1
ver = r.execute(
    "SELECT version, COUNT(*) AS n FROM company_theses GROUP BY version ORDER BY version"
).fetchall()
print("thesis versions:", [(int(x[0]), int(x[1])) for x in ver])
auth = r.execute(
    "SELECT authority_status, COUNT(*) AS n FROM company_theses GROUP BY authority_status"
).fetchall()
print("authority:", [(str(x[0]), int(x[1])) for x in auth])
status = r.execute("SELECT status, COUNT(*) AS n FROM company_theses GROUP BY status").fetchall()
print("status:", [(str(x[0]), int(x[1])) for x in status])

# 09-01 active pool metadata check (valuation_quality present?)
row = r.execute(
    """SELECT metadata_json FROM company_low_value_leader_pool
       WHERE market='CN' AND source_as_of='2026-09-01' AND pool_status='ACTIVE' LIMIT 1"""
).fetchone()
if row:
    meta = json.loads(row[0] or "{}")
    print("09-01 active metadata keys:", sorted(meta.keys()))
    print("09-01 valuation_quality:", json.dumps(meta.get("valuation_quality"), ensure_ascii=False))
    print("09-01 peer_comparables:", json.dumps((meta.get("data_quality") or {}).get("peer_comparables"), ensure_ascii=False))

# how many 09-01 ACTIVE rows carry valuation_quality
n_vq = r.execute(
    """SELECT COUNT(*) AS n FROM company_low_value_leader_pool
       WHERE market='CN' AND source_as_of='2026-09-01' AND pool_status='ACTIVE'
         AND metadata_json LIKE '%valuation_quality%'"""
).fetchone()
print("09-01 ACTIVE rows with valuation_quality:", int(n_vq["n"]), "/ 202")

r.close()
t.close()

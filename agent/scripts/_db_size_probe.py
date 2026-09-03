"""Quick probe of production database sizes and row counts."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

HOME = Path(os.environ["USERPROFILE"]) / ".vibe-trading"


def probe_db(path: Path) -> None:
    print(f"=== {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MB) ===")
    conn = sqlite3.connect(str(path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    print(f"tables: {len(tables)}")
    candidates = [
        "records", "snapshot_records", "adjusted_daily_bars",
        "company_financial_analysis_snapshots", "value_level3_leaders",
        "value_strategy_state_cursors", "company_theses", "disclosure_materials",
        "moat_evidence", "business_research_snapshots", "cio_reports",
        "value_level3_leader_runs", "l3_leader_pool_members",
    ]
    for table in candidates:
        if table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count:,}")
    if "records" in tables:
        print("  records by dataset:")
        rows = conn.execute(
            "SELECT dataset, COUNT(*), SUM(LENGTH(payload_json)) "
            "FROM records GROUP BY dataset ORDER BY COUNT(*) DESC LIMIT 20"
        ).fetchall()
        for dataset, count, payload in rows:
            mb = (payload or 0) / 1024 / 1024
            print(f"    {dataset}: {count:,} rows, payload ~{mb:.1f} MB")
    if "adjusted_daily_bars" in tables:
        row = conn.execute(
            "SELECT COUNT(DISTINCT stock_code), COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM adjusted_daily_bars"
        ).fetchone()
        print(f"  adjusted_daily_bars: {row[1]:,} bars, {row[0]:,} symbols, {row[2]}..{row[3]}")
    conn.close()


def folder_size(path: Path, label: str) -> None:
    if not path.exists():
        return
    files = [f for f in path.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    print(f"=== {label} ===")
    print(f"  files: {len(files):,}")
    print(f"  size: {total / 1024 / 1024:.1f} MB")


def main() -> None:
    probe_db(HOME / "research.db")
    probe_db(HOME / "tdx_data.db")
    folder_size(HOME / "disclosures", "disclosures/")
    folder_size(HOME / "backups", "backups/")


if __name__ == "__main__":
    main()

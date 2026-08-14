"""Backfill A/H daily history into the point-in-time Parquet warehouse."""

from __future__ import annotations

import argparse
from datetime import date

from src.strategy_engines.history import MultiSourceHistoryAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("CN", "HK"), required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    args = parser.parse_args()
    records = MultiSourceHistoryAdapter().backfill_ohlcv(
        market=args.market, codes=args.codes, start_date=args.start, end_date=args.end,
    )
    print(f"wrote {len(records)} immutable partitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

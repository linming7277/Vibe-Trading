"""Import an official/PIT CSV into the immutable strategy history warehouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.strategy_engines.history import OfficialCsvImportAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--market", choices=("CN", "HK"), required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--data-as-of-column", default="data_as_of")
    parser.add_argument("--available-at-column", default="available_at")
    args = parser.parse_args()
    records = OfficialCsvImportAdapter().import_file(
        path=args.path,
        market=args.market,
        dataset=args.dataset,
        provider=args.provider,
        data_as_of_column=args.data_as_of_column,
        available_at_column=args.available_at_column,
    )
    print(f"wrote {len(records)} immutable PIT partitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

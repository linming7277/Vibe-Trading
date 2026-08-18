"""Build persisted business tracks within each TDX terminal industry."""

from __future__ import annotations

import argparse
import json

from src.fine_tracks.service import FineTrackService


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Value Line Fine Track V2")
    parser.add_argument("--industry", help="Only classify one TDX terminal 881xxx.SH industry")
    parser.add_argument("--batch-size", type=int, default=50, help="Companies per same-industry model call")
    parser.add_argument("--mode", choices=("database", "agent"), default="database",
                        help="database uses existing TDX business text only; agent uses the configured cloud Provider")
    args = parser.parse_args()
    service = FineTrackService(batch_size=args.batch_size)
    try:
        if args.industry:
            result = (service.classify_industry_from_database(args.industry)
                      if args.mode == "database" else service.classify_industry(args.industry))
        else:
            result = (service.classify_all_from_database()
                      if args.mode == "database" else service.classify_all_industries())
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") != "FAILED" and not result.get("failed") else 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())

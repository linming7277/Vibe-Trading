"""Explicitly run existing Business Research for one company.

This is intentionally single-company and opt-in.  It neither creates a
Company Thesis nor extracts Thesis Evidence.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import sys

from src.business_research.service import BusinessResearchService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stock_code")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug-stack-after", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps({"phase": "initializing", "stock_code": args.stock_code.upper()}, ensure_ascii=False), flush=True)
    if args.debug_stack_after > 0:
        faulthandler.dump_traceback_later(args.debug_stack_after, repeat=False, file=sys.stderr)
    service = BusinessResearchService()
    try:
        print(json.dumps({"phase": "running_existing_business_research"}, ensure_ascii=False), flush=True)
        result = service.analyze(args.stock_code, force=args.force)
        analysis = result.get("analysis") or {}
        print(json.dumps({
            "stock_code": result.get("stock_code"),
            "analysis_status": result.get("analysis_status"),
            "traceability_status": result.get("traceability_status"),
            "quality_status": (analysis.get("analysis_metadata") or {}).get("quality_status"),
            "claim_count": len(analysis.get("claims") or []),
            "agent_error": result.get("agent_error"),
        }, ensure_ascii=False), flush=True)
        return 0 if result.get("analysis_status") == "COMPLETED" else 1
    finally:
        faulthandler.cancel_dump_traceback_later()
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())

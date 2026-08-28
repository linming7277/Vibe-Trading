"""Run the bounded risk-data preparation worker outside page and chat requests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.risk_research_preparation import get_risk_research_preparation_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare risk research materials for ACTIVE low-value leaders only.")
    parser.add_argument("--as-of", dest="source_as_of", help="PIT research date, YYYY-MM-DD")
    args = parser.parse_args()
    result = get_risk_research_preparation_service().prepare_current_active_low_value_pool(
        source_as_of=args.source_as_of,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

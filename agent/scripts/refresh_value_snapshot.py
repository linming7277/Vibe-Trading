"""Refresh one atomic Value Line market snapshot, scores, and Level-3 leaders."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.level3_leaders.service import Level3IndustryLeaderService  # noqa: E402
from src.strategy_engines.value_line import ValueLineService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="trading date, YYYY-MM-DD")
    parser.add_argument("--skip-leaders", action="store_true", help="refresh only market data and Value scores")
    args = parser.parse_args()

    value = ValueLineService()
    try:
        market = value.refresh_market_history(args.as_of)
        print(json.dumps({"step": "market", "result": market}, ensure_ascii=False), flush=True)
        if market.get("market_data_status") != "COMPLETE":
            print("Refusing to score an incomplete market snapshot.", file=sys.stderr, flush=True)
            return 2
        scores = value.refresh_scores(args.as_of)
        print(json.dumps({"step": "scores", "result": scores}, ensure_ascii=False), flush=True)
    finally:
        value.close()

    if args.skip_leaders:
        return 0
    leaders = Level3IndustryLeaderService()
    try:
        result = leaders.build_level3_leaders(args.as_of, force=True)
        print(json.dumps({"step": "level3_leaders", "result": result}, ensure_ascii=False), flush=True)
    finally:
        leaders.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

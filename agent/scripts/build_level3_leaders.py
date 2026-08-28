"""Build persisted Leader V2 rankings for all terminal TDX industries."""

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
    os.execv(
        str(VENV_PYTHON),
        [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.level3_leaders.service import Level3IndustryLeaderService  # noqa: E402
from src.tdx_data.service import get_tdx_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="PIT date (YYYY-MM-DD); defaults to latest published close snapshot")
    parser.add_argument("--force", action="store_true", help="rebuild an existing idempotent snapshot")
    args = parser.parse_args()
    service = Level3IndustryLeaderService()
    try:
        close_snapshot = get_tdx_service().store.active_snapshot()
        as_of = args.as_of or (close_snapshot or {}).get("market_date")
        if not as_of:
            parser.error("no published close snapshot; pass --as-of")
        result = service.build_level3_leaders(str(as_of), force=args.force)
        statistics = dict(result["statistics"])
        statistics.pop("industry_stats", None)
        print(json.dumps({
            "run_id": result["id"], "status": result["status"], "as_of": result["as_of"],
            "formula_version": result["formula_version"], "idempotent_reuse": result["idempotent_reuse"],
            "statistics": statistics,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())

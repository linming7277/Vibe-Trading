"""Refresh TongDaXin daily-K cache for one Value Line trading date."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.strategy_engines.value_line import ValueLineService  # noqa: E402
from src.tdx_data.service import get_tdx_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    value = ValueLineService()
    try:
        snapshot = value.data_store.memberships_as_of(args.as_of)
        symbols = sorted({row["symbol"] for row in snapshot["items"] if str(row["symbol"]).endswith(".SH")})
    finally:
        value.close()
    client = get_tdx_service().client
    try:
        for index in range(0, len(symbols), args.batch_size):
            batch = symbols[index:index + args.batch_size]
            client.call("refresh_kline", stock_list=batch, period="1d")
            print(f"{min(index + len(batch), len(symbols))}/{len(symbols)}", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""历史表现查询脚本（Phase 3 §56）：--mode shadow|official|all --start --end。0 LLM / 0 网络。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.macro_forecast.performance import get_macro_forecast_performance


def main() -> int:
    parser = argparse.ArgumentParser(description="预测历史表现聚合")
    parser.add_argument("--mode", default="shadow", choices=["shadow", "official", "all"])
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    run_mode = None if args.mode == "all" else args.mode.upper()
    report = get_macro_forecast_performance(
        start_date=args.start, end_date=args.end, run_mode=run_mode)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                  encoding="utf-8")
        print(f"saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

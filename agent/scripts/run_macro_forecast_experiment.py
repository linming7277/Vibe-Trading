"""宏观预测前向实验运行器（xcm-cny-forward-v1）。

用法：
  python scripts/run_macro_forecast_experiment.py --dry-run                 # 0 LLM 资格检查
  python scripts/run_macro_forecast_experiment.py --target 20260909 --dry-run
  python scripts/run_macro_forecast_experiment.py --shadow-pair             # 真实 A/B（≤2 logical calls）
  python scripts/run_macro_forecast_experiment.py --shadow-pair --target 20260909
  python scripts/run_macro_forecast_experiment.py --performance             # 只读聚合（0 LLM/0 网络）

规则：SHADOW-only；每日 ≤2 logical calls（A/B 各一）；同 (target, arm,
fingerprint) 重复运行 REUSED 不重调；CNY 不可见或世界未就绪当日不计入比较。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.macro_forecast.experiment import (
    EXPERIMENT_ID,
    get_macro_forecast_experiment_performance,
    run_experiment_pair,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="宏观预测前向 A/B 实验运行器")
    parser.add_argument("--target", default=None, help="目标交易日 YYYYMMDD；缺省自动解析")
    parser.add_argument("--dry-run", action="store_true", help="只做资格检查，0 LLM")
    parser.add_argument("--shadow-pair", action="store_true", help="执行 A/B shadow pair")
    parser.add_argument("--performance", action="store_true", help="只读实验聚合")
    args = parser.parse_args()

    if args.performance:
        print(json.dumps(get_macro_forecast_experiment_performance(EXPERIMENT_ID),
                         ensure_ascii=False, indent=1))
        return 0
    if args.dry_run or not args.shadow_pair:
        result = run_experiment_pair(target_date=args.target, dry_run=True)
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        return 0 if result.get("status") in {"DRY_RUN", "WORLD_NOT_READY",
                                             "CNY_NOT_VISIBLE"} else 1
    result = run_experiment_pair(target_date=args.target, dry_run=False)
    printable = {key: value for key, value in result.items()}
    print(json.dumps(printable, ensure_ascii=False, indent=1, default=str))
    return 0 if result.get("status") in {"PAIRED", "CNY_NOT_VISIBLE",
                                         "WORLD_NOT_READY", "REUSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

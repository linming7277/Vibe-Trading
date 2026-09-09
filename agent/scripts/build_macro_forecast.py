"""构建下一交易日联合预测（预测引擎 V1 §43-§46）。

用法：
  python scripts/build_macro_forecast.py --target 20260908 --dry-run
  python scripts/build_macro_forecast.py --target 20260908 --shadow
  python scripts/build_macro_forecast.py --target 20260908 --official   # 须人工批准
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.forecast_service import (
    get_latest_macro_forecast, run_macro_forecast,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="下一交易日大盘与行业联合预测")
    parser.add_argument("--target", default=None, help="目标交易日 YYYYMMDD；缺省自动解析下一交易日")
    parser.add_argument("--mode", default="dry-run", choices=["dry-run", "shadow", "draft", "official"])
    parser.add_argument("--dry-run", action="store_true", help="等价 --mode dry-run")
    parser.add_argument("--shadow", action="store_true", help="等价 --mode shadow（真实模型调用，留档不计正式）")
    parser.add_argument("--official", action="store_true", help="等价 --mode official（须人工批准+全部 Gate）")
    parser.add_argument("--out", default=None, help="将 narrative_md 另存路径")
    parser.add_argument("--model-role", default=None, help="覆盖模型角色（agent_model_configs）")
    parser.add_argument("--disable-thinking", action="store_true", help="GLM 关闭深度思考（extra_body）")
    args = parser.parse_args()

    mode = "official" if args.official else ("shadow" if args.shadow else ("dry-run" if args.dry_run else args.mode))

    result = run_macro_forecast(
        target_date=args.target, mode=mode,
        model_role=args.model_role or "macro_policy",
        model_extra_body={"thinking": {"type": "disabled"}} if args.disable_thinking else None,
    )
    printable = {key: value for key, value in result.items()
                 if key not in {"payload", "row", "engine", "prep", "bundle", "macro_context", "industry_rows"}}
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))

    if result.get("status") in {"SHADOW", "OFFICIAL", "DRAFT", "ABSTAINED", "MODEL_FAILED", "INVALID_OUTPUT", "REUSED"}:
        row = result.get("row") or {}
        print("\n=== 留档 ===")
        print(f"id={row.get('id')} status={row.get('status')} direction={row.get('market_direction')} "
              f"requests={row.get('actual_requests')} retries={row.get('retry_count')} "
              f"latency_ms={row.get('model_latency_ms')}")
        narrative = (result.get("payload") or {}).get("_narrative") or row.get("narrative_md")
        if narrative:
            print("\n--- narrative ---")
            print(narrative[:1200])
        if args.out and narrative:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(narrative, encoding="utf-8")
            print(f"\nnarrative 已另存: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

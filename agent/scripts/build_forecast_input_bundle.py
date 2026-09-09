"""构建并保存一份真实的预测输入包（任务卡1 §6；经授权的真实包）。

用法：
  python scripts/build_forecast_input_bundle.py --target 20260908
  python scripts/build_forecast_input_bundle.py            # 自动取下一可判定交易日

读取：research.db（forecast_index_bars / macro_series / forecast_instruments）+
本地交易日历（可选 TDX 实时日历增强）。0 模型、0 外部网络（TDX 只读除外）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.service import load_trading_days, prepare_input_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="构建预测输入包")
    parser.add_argument("--target", default=None, help="目标交易日 YYYYMMDD；缺省自动解析")
    parser.add_argument("--live-calendar", action="store_true", help="合并 TDX 实时日历（只读）")
    parser.add_argument("--no-save", action="store_true", help="只构建不落库")
    parser.add_argument("--out", default=None, help="将 payload 另存 JSON 的路径")
    args = parser.parse_args()

    trading_days = load_trading_days()
    if args.live_calendar:
        from src.tdx_data.client import TdxClient

        client = TdxClient()
        try:
            trading_days = load_trading_days(tdx_client=client)
        finally:
            client.close()
    print(f"日历：{len(trading_days)} 天，最新 {trading_days[-1] if trading_days else '-'}")

    result = prepare_input_bundle(
        target_date=args.target, trading_days=trading_days, save=not args.no_save,
    )
    printable = {key: value for key, value in result.items() if key != "payload"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if result.get("status") == "BUILT" and args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(result["payload"], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"payload 已另存: {args.out}")
    return 0 if result.get("status") in {"BUILT", "CALENDAR_UNAVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

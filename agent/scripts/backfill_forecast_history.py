"""回补预测输入所需的指数/板块历史日线（任务卡1 §5）。

范围：PRIMARY_BENCHMARK + 8 个背景指数（各 >=500 日）+ 128 个 881xxx 行业
（各 >=250 日）。来源 TDX 本地（refresh_kline 预热 + get_market_data 批量），
写入 research.db forecast_index_bars，first_observed 留档以区分 PIT 状态。
未收盘当日的 K 线一律丢弃；重复执行只补缺不改写。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.bars import ForecastBarStore, collect_history
from src.macro_forecast.registry import (
    PRIMARY_BENCHMARK, REFERENCE_INDEXES, REGISTRY_VERSION, InstrumentRegistryStore,
)
from src.tdx_data.client import TdxClient


def main() -> int:
    parser = argparse.ArgumentParser(description="回补预测输入指数/板块日线")
    parser.add_argument("--research-db", default=str(get_runtime_root() / "research.db"))
    parser.add_argument("--end-date", default="20260904", help="读取截止日（最后一个已完成交易日）")
    parser.add_argument("--index-count", type=int, default=520)
    parser.add_argument("--industry-count", type=int, default=260)
    args = parser.parse_args()

    registry = InstrumentRegistryStore(Path(args.research_db))
    store = ForecastBarStore(Path(args.research_db))
    try:
        industries = registry.industry_targets(registry_version=REGISTRY_VERSION)
        if not industries:
            print("[ERROR] 注册表尚未冻结；先执行 scripts/freeze_forecast_universe.py")
            return 2
        index_codes = [PRIMARY_BENCHMARK] + [code for code, _ in REFERENCE_INDEXES]
        print(f"目标：{len(index_codes)} 指数 × {args.index_count} 日；{len(industries)} 行业 × {args.industry_count} 日")

        client = TdxClient()
        started = time.monotonic()

        def progress(done: int, total: int, label: str) -> None:
            print(f"  [{done}/{total}] {label}", flush=True)

        print("== 阶段1：指数历史 ==")
        rows = collect_history(client, index_codes, end_date=args.end_date, count=args.index_count, progress=progress)
        written = store.write_bars(rows)
        print(f"指数写入: {json.dumps(written, ensure_ascii=False)}")

        print("== 阶段2：行业历史 ==")
        industry_codes = [row["code"] for row in industries]
        rows = collect_history(client, industry_codes, end_date=args.end_date, count=args.industry_count, progress=progress)
        written = store.write_bars(rows)
        print(f"行业写入: {json.dumps(written, ensure_ascii=False)}")

        client.close()
        report = store.coverage_report(registry_rows=registry.load(registry_version=REGISTRY_VERSION))
        summary = {
            "instruments_with_bars": report["instruments_with_bars"],
            "history_target_total": report["history_target_total"],
            "history_target_met": report["history_target_met"],
            "unmet": report["history_target_unmet"][:20],
            "elapsed_min": round((time.monotonic() - started) / 60, 1),
        }
        print(f"覆盖: {json.dumps(summary, ensure_ascii=False)}")
        return 0
    finally:
        registry.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

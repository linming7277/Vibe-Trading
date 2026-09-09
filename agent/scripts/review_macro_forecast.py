"""收盘复盘脚本（Phase 3 §55）：--forecast-id / --target，--dry-run / --evaluate。

dry-run 只检查 qualified close/bars 完整性与预计可评价数量，不写库。
evaluate：确定性评价并幂等保存（UNIQUE forecast_id+outcome 版本）。
0 LLM / 0 网络。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.paths import get_runtime_root
from src.macro_forecast.outcome import (
    BENCHMARK_SYMBOL, OUTCOME_FORMULA_VERSION, evaluate_forecast_outcome, persist_outcome,
)
from src.macro_forecast.review_render import render_review


def _find_forecasts(db_path: Path, target: str | None) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if target:
            rows = conn.execute(
                "SELECT id, target_trade_date, run_mode, status FROM macro_market_forecasts "
                "WHERE target_trade_date=? ORDER BY created_at", (target,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, target_trade_date, run_mode, status FROM macro_market_forecasts "
                "ORDER BY target_trade_date DESC LIMIT 5",
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _bars_ready(db_path: Path, target: str) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        benchmark = conn.execute(
            "SELECT COUNT(*) FROM forecast_index_bars WHERE code=? AND trade_date=?",
            (BENCHMARK_SYMBOL, target),
        ).fetchone()[0]
        industries = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM forecast_index_bars WHERE trade_date=?", (target,),
        ).fetchone()[0]
        return {"benchmark_t_bar": benchmark, "codes_with_t_bar": industries}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="预测收盘复盘（0 LLM）")
    parser.add_argument("--forecast-id", default=None)
    parser.add_argument("--target", default=None, help="目标日 YYYY-MM-DD/YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    research_db = get_runtime_root() / "research.db"
    target = args.target.replace("-", "") if args.target else None
    target_iso = f"{target[:4]}-{target[4:6]}-{target[6:8]}" if target else None

    if not args.forecast_id:
        forecasts = _find_forecasts(research_db, target_iso)
        print(json.dumps({"candidate_forecasts": forecasts}, ensure_ascii=False, indent=2))
        if not forecasts:
            return 2
        if not target and forecasts:
            target_iso = forecasts[0]["target_trade_date"]
            target = target_iso.replace("-", "")
        if len(forecasts) == 1 or args.forecast_id is None:
            args.forecast_id = forecasts[0]["id"]

    if args.dry_run:
        readiness = _bars_ready(research_db, target or "")
        print(json.dumps({
            "forecast_id": args.forecast_id, "target": target_iso,
            "outcome_formula_version": OUTCOME_FORMULA_VERSION,
            "bars_readiness": readiness,
            "would_evaluate": readiness["benchmark_t_bar"] > 0,
        }, ensure_ascii=False, indent=2))
        return 0

    result = evaluate_forecast_outcome(args.forecast_id)
    report = {"status": result.get("status"), "written": result.get("written", 0)}
    if args.evaluate and result.get("status") in {"EVALUATED", "NOT_EVALUABLE", "PENDING"}:
        saved = persist_outcome(result)
        report["save"] = saved
    outcome = result.get("outcome")
    if outcome:
        report["market_evaluation"] = outcome.get("market_evaluation")
        report["actual_market_class"] = outcome.get("actual_market_class")
        report["actual_return"] = outcome.get("actual_return")
        report["industry_hit_rate"] = outcome.get("industry_hit_rate")
        if args.out and result.get("status") in {"EVALUATED", "REUSED"}:
            conn = sqlite3.connect(f"file:{research_db.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM macro_market_forecasts WHERE id=?", (args.forecast_id,),
            ).fetchone()
            conn.close()
            narrative = render_review(outcome, dict(row) if row else {})
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(narrative, encoding="utf-8")
            report["narrative_saved"] = args.out
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

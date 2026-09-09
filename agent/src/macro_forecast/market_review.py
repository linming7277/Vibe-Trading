"""今日市场复盘：确定性计算（V28 §七-§十）。

数据只来自 forecast_index_bars（不建第二套行情）；缺失指数显示"暂无可靠数据"
不阻塞；0 LLM / 0 网络。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.macro_forecast.outcome import BENCHMARK_SYMBOL, classify_market_return

MARKET_REVIEW_VERSION = "market-review-v1.0.0"

# §八：老板版指数（对照 registry REFERENCE）；缺哪个显"暂无可靠数据"。
DISPLAY_INDEXES: tuple[tuple[str, str], ...] = (
    (BENCHMARK_SYMBOL, "沪深300"),
    ("999999.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
)

# §九：行业强弱从 128 行业目标（881xxx）确定性计算。
INDUSTRY_TOP_N = 5


def _day(value: object) -> str:
    text = str(value or "").replace("-", "")[:8]
    return text if len(text) == 8 and text.isdigit() else ""


def _closes(db: Path, code: str, end_day: str, count: int) -> list[tuple[str, float]]:
    """返回升序 (trade_date, close)；[-1]=end_day（或其前最近交易日）。"""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT trade_date, close FROM forecast_index_bars WHERE code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT ?",
            (code, end_day, count),
        ).fetchall()
    finally:
        conn.close()
    pairs = [(str(row[0]), float(row[1])) for row in rows if row[1] is not None]
    pairs.reverse()
    return pairs


def _window_return(closes: list[tuple[str, float]], window: int) -> float | None:
    if len(closes) <= window or closes[-(window + 1)][1] <= 0:
        return None
    return closes[-1][1] / closes[-(window + 1)][1] - 1


def build_market_review(
    *,
    as_of: str,
    previous_date: str,
    research_db: Any = None,
    registry_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """report_date=as_of 的今日市场复盘（相对基准=沪深300，P=previous_date）。"""
    db = Path(research_db or (get_runtime_root() / "research.db"))
    end = _day(as_of)
    benchmark_closes = _closes(db, BENCHMARK_SYMBOL, end, 25)
    r_market_1d = benchmark_closes[-1][1] / benchmark_closes[-2][1] - 1 if len(benchmark_closes) >= 2 and benchmark_closes[-2][1] > 0 else None

    indices = []
    for code, name in DISPLAY_INDEXES:
        closes = _closes(db, code, end, 25)
        r1 = _window_return(closes, 1)
        r5 = _window_return(closes, 5)
        if r1 is None:
            indices.append({"code": code, "name": name, "ret_1d": None, "ret_5d": None,
                            "status": "NO_DATA"})
            continue
        indices.append({"code": code, "name": name, "ret_1d": round(r1, 12),
                        "ret_5d": round(r5, 12) if r5 is not None else None, "status": "READY"})

    # 行业强弱：今日涨跌与相对沪深300；只统计 history_target 行业（881xxx 活跃）
    if registry_rows is None:
        from src.macro_forecast.registry import REGISTRY_VERSION, InstrumentRegistryStore

        registry = InstrumentRegistryStore(db)
        try:
            registry_rows = registry.load(registry_version=REGISTRY_VERSION)
        finally:
            registry.close()
    industry_codes = [row["code"] for row in registry_rows
                      if row.get("role") == "INDUSTRY_TARGET" and row.get("status") == "ACTIVE"]
    rows: list[dict[str, Any]] = []
    names = {row["code"]: row.get("name_zh") for row in registry_rows if row.get("code")}
    for code in industry_codes:
        closes = _closes(db, code, end, 3)  # 升序：[-1]=今日，[-2]=前一交易日
        if len(closes) < 2:
            continue
        today_pair, prev_pair = closes[-1], closes[-2]
        if today_pair[0] != end or prev_pair[1] <= 0:
            continue
        r_industry = today_pair[1] / prev_pair[1] - 1
        if r_market_1d is None:
            continue
        rows.append({"code": code, "name": names.get(code) or code,
                     "ret_1d": round(r_industry, 12),
                     "rr": round(r_industry - r_market_1d, 12)})
    rows.sort(key=lambda item: (-item["rr"], item["code"]))
    strong = rows[:INDUSTRY_TOP_N]
    weak = list(reversed(rows[-INDUSTRY_TOP_N:])) if len(rows) >= INDUSTRY_TOP_N else []

    market_class = classify_market_return(r_market_1d) if r_market_1d is not None else None
    summary = None
    if r_market_1d is not None and strong and weak:
        strong_names = "、".join(item["name"] for item in strong[:2])
        weak_names = "、".join(item["name"] for item in weak[:2])
        direction = "上涨" if r_market_1d > 0 else ("下跌" if r_market_1d < 0 else "持平")
        summary = (f"沪深300今日{direction}{abs(r_market_1d) * 100:.2f}%，"
                   f"{strong_names}方向相对占优，{weak_names}相对落后。")
    return {
        "market_review_version": MARKET_REVIEW_VERSION,
        "as_of": f"{end[:4]}-{end[4:6]}-{end[6:8]}",
        "benchmark": {"code": BENCHMARK_SYMBOL, "ret_1d": round(r_market_1d, 12) if r_market_1d is not None else None,
                      "market_class": market_class},
        "indices": indices,
        "strong_industries": strong,
        "weak_industries": weak,
        "industry_evaluated": len(rows),
        "one_liner": summary,
    }

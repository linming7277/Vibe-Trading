"""前瞻宽度行 P2（2026-09-09）：20日新高 N 家 + 涨停 M 家 + SHADOW 前缀。

夹具写 tmp `.day`（21 根收盘）+ tmp 事件表；不连飞书、0 LLM。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.investment_research_supervisor.next_session_outlook import (
    build_next_session_outlook,
    count_20d_new_highs,
)

AS_OF = "2026-09-08"
PREV_INT = 20260907
_DAY = __import__("struct").Struct("<IIIIIfII")


class DayHome:
    """tmp tdx_home：写 sh/sz .day 文件（[(date_int, close, amount)]）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        for exchange in ("sh", "sz"):
            (root / "vipdoc" / exchange / "lday").mkdir(parents=True, exist_ok=True)

    def write_day(self, code: str, closes: list[float], amount: float = 200_000_000.0) -> None:
        exchange = "sh" if code.endswith(".SH") else "sz"
        dates = [20260710 + i for i in range(len(closes))]  # 连续占位日期即可
        dates[-1] = 20260908
        path = self.root / "vipdoc" / exchange / "lday" / f"{exchange}{code[:6]}.day"
        with open(path, "wb") as handle:
            for date, close in zip(dates, closes):
                packed = int(round(close * 100))
                handle.write(_DAY.pack(date, packed, packed, packed, packed,
                                       float(amount), 1000, 0))


def test_20d_new_highs_counts_two_of_three(tmp_path: Path) -> None:
    home = DayHome(tmp_path)
    home.write_day("600001.SH", [10.0] * 20 + [12.0])  # 创新高
    home.write_day("600002.SH", [10.0] * 20 + [15.0])  # 创新高
    home.write_day("600003.SH", [10.0] * 20 + [8.0])   # 未创新高
    assert count_20d_new_highs(AS_OF, tdx_home=home.root) == 2


def test_outlook_breadth_line_with_shadow(tmp_path: Path) -> None:
    home = DayHome(tmp_path)
    home.write_day("600001.SH", [10.0] * 20 + [12.0])
    home.write_day("600002.SH", [10.0] * 20 + [15.0])
    home.write_day("600003.SH", [10.0] * 20 + [8.0])
    research = tmp_path / "research.db"
    conn = sqlite3.connect(str(research))
    conn.executescript("""
        CREATE TABLE macro_market_forecasts (
            id TEXT PRIMARY KEY, target_trade_date TEXT, run_mode TEXT, status TEXT,
            market_direction TEXT, structured_payload_json TEXT DEFAULT '{}',
            created_at TEXT);
        CREATE TABLE company_action_events (id TEXT PRIMARY KEY);
    """)
    conn.commit()
    conn.close()
    result = build_next_session_outlook(
        AS_OF, forecast={"status": "SHADOW", "direction": "WEAKER", "id": "f1",
                         "target_trade_date": "2026-09-09", "model_summary": ""},
        new_highs=2, limit_up_count=5,
        tdx_home=home.root, tdx_db_path=tmp_path / "tdx_data.db",
        research_db_path=research)
    breadth = result["breadth_line"]
    assert breadth.startswith("SHADOW｜")
    assert "20日新高 2 家" in breadth
    assert "涨停 5 家" in breadth
    assert "20日新高 数据不足" not in breadth
    assert result["breadth_new_highs"] == 2 and result["breadth_limit_ups"] == 5
    assert len(result["text"].splitlines()) <= 12  # 总前瞻 ≤12 行


def test_missing_20d_k_bars_reports_insufficient(tmp_path: Path) -> None:
    home = DayHome(tmp_path)  # 无任何 .day 文件
    research = tmp_path / "research.db"
    conn = sqlite3.connect(str(research))
    conn.executescript("CREATE TABLE macro_market_forecasts (id TEXT PRIMARY KEY);")
    conn.commit()
    conn.close()
    result = build_next_session_outlook(
        AS_OF, forecast=None, tdx_home=home.root, tdx_db_path=tmp_path / "tdx_data.db",
        research_db_path=research)
    assert "20日新高 数据不足" in result["breadth_line"]


def test_line_never_contains_trading_terms(tmp_path: Path) -> None:
    home = DayHome(tmp_path)
    home.write_day("600001.SH", [10.0] * 20 + [12.0])
    research = tmp_path / "research.db"
    conn = sqlite3.connect(str(research))
    conn.executescript("CREATE TABLE macro_market_forecasts (id TEXT PRIMARY KEY);")
    conn.commit()
    conn.close()
    result = build_next_session_outlook(
        AS_OF, forecast=None, tdx_home=home.root, tdx_db_path=tmp_path / "tdx_data.db",
        research_db_path=research)
    for term in ("买入", "卖出", "加仓", "减仓", "追涨", "打板"):
        assert term not in result["breadth_line"]

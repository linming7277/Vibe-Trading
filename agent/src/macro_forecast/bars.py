"""指数/板块日线历史：TDX 采集 + PIT 留档（任务卡1；主规格 §5.1 §6.1 §7.1 §7.2）。

采集路径与 Value 线一致：refresh_kline 小批量预热本地缓存，再 get_market_data
批量读取。历史回补的 K 线 first_observed=采集日 → FORWARD_OBSERVED；此后每日
收盘当日采集的 K 线 first_observed==trade_date → STRICT_PIT_ELIGIBLE（供后续
前向预测累计严格样本）。

写入约束：
- 只追加/补缺，不改写既有行的价格内容；first_observed_date 首次写入后不变；
- 未收盘当日的 K 线一律丢弃（now <= 当日15:00 Asia/Shanghai）；
- 停牌/无成交 K 线（volume 与 amount 均为 0）保留原文值，由特征层过滤。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable

from src.config.paths import get_runtime_root
from src.macro_forecast.contracts import SHANGHAI

SH_MARKET_CLOSE = clock_time(15, 0)

# TDX 本地日线路径别名（与 value_market_history.py TDX_BENCHMARK_ALIAS 同源口径）。
TDX_SYMBOL_ALIASES: dict[str, str] = {
    "000985.SH": "000985.SZ",  # 中证全指本地文件在深市命名空间
}

BAR_SOURCE = "TongDaXin"


def _now_shanghai() -> datetime:
    return datetime.now(tz=SHANGHAI)


def _normalize_day(value: object) -> str:
    text = str(value or "").strip().replace("-", "")[:8]
    return text if len(text) == 8 and text.isdigit() else ""


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def tdx_payload_rows(payload: dict[str, Any], symbols: list[str]) -> list[dict[str, Any]]:
    """get_market_data 返回的 field->{index:{symbol:val}} 结构 → 行记录。

    与 ValueMarketHistoryService._tdx_rows 同构，但保留 open/high/low 全字段、
    成交量单位为股、成交额按 TDX 日线万元×10000 折算为元。
    """
    fields = {key.lower(): rows for key, rows in (payload or {}).items() if isinstance(rows, list)}
    by_date: dict[str, dict[str, dict[str, float | None]]] = {}
    for field, rows in fields.items():
        for raw in rows:
            timestamp = str(raw.get("index") or "")[:10]
            if len(timestamp) != 10:
                continue
            for symbol in symbols:
                value = _number(raw.get(symbol))
                if value is not None:
                    by_date.setdefault(timestamp, {}).setdefault(symbol, {})[field] = value
    result: list[dict[str, Any]] = []
    for trade_date, securities in by_date.items():
        for symbol, values in securities.items():
            close = values.get("close")
            if close is None or close <= 0:
                continue
            amount = values.get("amount")
            result.append({
                "code": symbol,
                "trade_date": trade_date.replace("-", ""),
                "open": values.get("open"),
                "high": values.get("high"),
                "low": values.get("low"),
                "close": close,
                "volume": values.get("volume"),
                "amount": amount * 10_000 if amount is not None else None,
            })
    return result


def filter_completed_sessions(rows: list[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """丢弃不完整/异常日期的 K 线：未来日一律丢弃；当日须已过 15:00 收盘。"""
    current = (now or _now_shanghai()).astimezone(SHANGHAI)
    today_key = current.strftime("%Y%m%d")
    market_closed_today = current.time() > SH_MARKET_CLOSE
    kept: list[dict[str, Any]] = []
    for row in rows:
        day = _normalize_day(row.get("trade_date"))
        if not day or day > today_key:
            continue
        if day == today_key and not market_closed_today:
            continue  # 今日未收盘，K 线不完整
        row = dict(row)
        row["trade_date"] = day
        kept.append(row)
    return kept


class ForecastBarStore:
    """research.db forecast_index_bars：指数/板块日线，含 first_observed PIT 标记。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS forecast_index_bars (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL NOT NULL,
                    volume REAL, amount REAL,
                    source TEXT NOT NULL,
                    first_observed_date TEXT NOT NULL,
                    pit_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (code, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_forecast_bars_code ON forecast_index_bars(code, trade_date DESC);
            """)

    def close(self) -> None:
        self._conn.close()

    def write_bars(self, rows: list[dict[str, Any]], *, fetched_at: datetime | None = None) -> dict[str, int]:
        """只补缺：已存在 (code, trade_date) 的行不改写（追加版本语义，§6.3）。"""
        now = fetched_at or _now_shanghai()
        now_text = now.astimezone(SHANGHAI).isoformat()
        today = now.astimezone(SHANGHAI).strftime("%Y%m%d")
        inserted = updated = 0
        with self._conn:
            for row in rows:
                existing = self._conn.execute(
                    "SELECT first_observed_date, pit_status, close, volume, amount FROM forecast_index_bars WHERE code=? AND trade_date=?",
                    (row["code"], row["trade_date"]),
                ).fetchone()
                if existing:
                    # 收盘价缺失时补内容；否则保持首次留档（不改写历史价格）。
                    if existing["close"] is None and row.get("close") is not None:
                        self._conn.execute(
                            "UPDATE forecast_index_bars SET open=?, high=?, low=?, close=?, volume=?, amount=?, fetched_at=? WHERE code=? AND trade_date=?",
                            (row.get("open"), row.get("high"), row.get("low"), row.get("close"),
                             row.get("volume"), row.get("amount"), now_text, row["code"], row["trade_date"]),
                        )
                        updated += 1
                    continue
                first_observed = today
                pit_status = "STRICT_PIT_ELIGIBLE" if first_observed == row["trade_date"] else "FORWARD_OBSERVED"
                self._conn.execute(
                    """INSERT INTO forecast_index_bars(
                        code, trade_date, open, high, low, close, volume, amount,
                        source, first_observed_date, pit_status, fetched_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["code"], row["trade_date"], row.get("open"), row.get("high"), row.get("low"),
                     row["close"], row.get("volume"), row.get("amount"), BAR_SOURCE,
                     first_observed, pit_status, now_text),
                )
                inserted += 1
        return {"inserted": inserted, "updated": updated, "skipped_existing": len(rows) - inserted - updated}

    def read_bars(self, codes: list[str], *, end_date: str, count: int = 500) -> dict[str, list[dict[str, Any]]]:
        requested = sorted(set(str(code).upper() for code in codes))
        result: dict[str, list[dict[str, Any]]] = {code: [] for code in requested}
        if not requested:
            return result
        for code in requested:
            rows = self._conn.execute(
                "SELECT * FROM forecast_index_bars WHERE code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
                (code, _normalize_day(end_date) or "99999999", int(count)),
            ).fetchall()
            result[code] = [dict(row) for row in reversed(rows)]
        return result

    def coverage_report(self, *, registry_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        rows = self._conn.execute(
            """SELECT code, COUNT(*) AS bars, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date,
                      SUM(CASE WHEN pit_status='STRICT_PIT_ELIGIBLE' THEN 1 ELSE 0 END) AS strict_rows
               FROM forecast_index_bars GROUP BY code"""
        ).fetchall()
        by_code = {row["code"]: dict(row) for row in rows}
        report: dict[str, Any] = {"instruments_with_bars": len(by_code), "by_code": by_code}
        if registry_rows:
            targets = [row for row in registry_rows if row.get("history_target_days")]
            met = [
                row for row in targets
                if by_code.get(row["code"], {}).get("bars", 0) >= int(row["history_target_days"])
            ]
            report["history_target_total"] = len(targets)
            report["history_target_met"] = len(met)
            report["history_target_unmet"] = [
                {"code": row["code"], "name_zh": row["name_zh"],
                 "target": int(row["history_target_days"]), "have": by_code.get(row["code"], {}).get("bars", 0)}
                for row in targets if by_code.get(row["code"], {}).get("bars", 0) < int(row["history_target_days"])
            ]
        return report


def collect_history(
    client: Any,
    codes: list[str],
    *,
    end_date: str,
    count: int,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """refresh_kline 预热 + get_market_data 批量读取（批量上限沿用 Value 线经验）。"""
    requested = sorted({str(code).upper() for code in codes})
    alias_map = {TDX_SYMBOL_ALIASES.get(code, code): code for code in requested}
    tdx_codes = list(alias_map.keys())

    refresh_batch = 20
    for offset in range(0, len(tdx_codes), refresh_batch):
        batch = tdx_codes[offset:offset + refresh_batch]
        client.call("refresh_kline", stock_list=batch, period="1d")
        if progress:
            progress(min(offset + len(batch), len(tdx_codes)), len(tdx_codes), "预热板块/指数日线缓存")

    rows: list[dict[str, Any]] = []
    read_batch = 100
    for offset in range(0, len(tdx_codes), read_batch):
        batch = tdx_codes[offset:offset + read_batch]
        payload = client.call(
            "get_market_data",
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=batch, period="1d",
            end_time=_normalize_day(end_date) or "", count=int(count),
            dividend_type="none", fill_data=False,
        ) or {}
        for row in tdx_payload_rows(payload, batch):
            canonical = alias_map.get(row["code"], row["code"])
            row["code"] = canonical
            rows.append(row)
        if progress:
            progress(min(offset + len(batch), len(tdx_codes)), len(tdx_codes), "读取指数/板块日线")
    return filter_completed_sessions(rows)

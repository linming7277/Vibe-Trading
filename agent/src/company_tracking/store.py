"""老板的跟踪清单存储：A 档自动跟踪 + B/C 档人工纳入，软删除保留历史。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS company_tracking_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL DEFAULT 'CN',
    stock_code TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    added_date TEXT,
    added_tier TEXT NOT NULL DEFAULT '',
    added_price REAL,
    added_reasons_json TEXT NOT NULL DEFAULT '[]',
    added_cautions_json TEXT NOT NULL DEFAULT '[]',
    entry_snapshot_json TEXT NOT NULL DEFAULT '{}',
    added_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    removed_date TEXT,
    removed_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CompanyTrackingStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = str(db_path or (get_runtime_root() / "research.db"))
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(TRACKING_TABLE_SQL)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_tracking_list)")}
        if "source" not in columns:
            self._conn.execute("ALTER TABLE company_tracking_list ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
        if "added_date" not in columns:
            self._conn.execute("ALTER TABLE company_tracking_list ADD COLUMN added_date TEXT")
        if "added_cautions_json" not in columns:
            self._conn.execute("ALTER TABLE company_tracking_list ADD COLUMN added_cautions_json TEXT NOT NULL DEFAULT '[]'")
        if "entry_snapshot_json" not in columns:
            self._conn.execute("ALTER TABLE company_tracking_list ADD COLUMN entry_snapshot_json TEXT NOT NULL DEFAULT '{}'")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, stock_code: str) -> dict[str, Any] | None:
        with self._conn:
            row = self._conn.execute(
                "SELECT * FROM company_tracking_list WHERE stock_code=?", (stock_code.upper(),)
            ).fetchone()
        return dict(row) if row else None

    def add(self, *, market: str, stock_code: str, company_name: str, source: str,
            tier: str, reasons: list[str], price: float | None, note: str = "",
            added_date: str | None = None, now: datetime | None = None,
            cautions: list[str] | None = None, entry_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        now_dt = now or datetime.now().astimezone()
        stamp = now_dt.isoformat(timespec="seconds")
        added_date = (added_date or now_dt.date().isoformat())[:10]
        with self._conn:
            previous = self._conn.execute(
                "SELECT status FROM company_tracking_list WHERE stock_code=?",
                (stock_code.upper(),),
            ).fetchone()
            self._conn.execute(
                """INSERT INTO company_tracking_list(
                       market, stock_code, company_name, source, added_date, added_tier, added_price,
                       added_reasons_json, added_cautions_json, entry_snapshot_json,
                       added_note, status, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
                   ON CONFLICT(stock_code) DO UPDATE SET
                     market=excluded.market, company_name=excluded.company_name,
                     source=excluded.source, status='active', removed_date=NULL,
                     removed_note='', added_date=excluded.added_date,
                     added_tier=excluded.added_tier,
                     added_price=excluded.added_price,
                     added_reasons_json=excluded.added_reasons_json,
                     added_cautions_json=excluded.added_cautions_json,
                     entry_snapshot_json=excluded.entry_snapshot_json,
                     added_note=excluded.added_note, updated_at=excluded.updated_at""",
                (market.upper(), stock_code.upper(), company_name.strip(), source, added_date, tier, price,
                 json.dumps(reasons, ensure_ascii=False),
                 json.dumps(cautions or [], ensure_ascii=False),
                 json.dumps(entry_snapshot or {}, ensure_ascii=False),
                 note, stamp, stamp),
            )
        reactivated = bool(previous and previous["status"] == "removed")
        row = self.get(stock_code) or {}
        return {**row, "reactivated": reactivated}

    def remove(self, stock_code: str, *, note: str = "", now: datetime | None = None) -> bool:
        now_dt = now or datetime.now().astimezone()
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE company_tracking_list SET status='removed', removed_date=?, removed_note=?, updated_at=? "
                "WHERE stock_code=? AND status='active'",
                (now_dt.date().isoformat(), note, now_dt.isoformat(timespec="seconds"), stock_code.upper()),
            )
        return cursor.rowcount > 0

    def auto_remove_leavers(self, keep_codes: set[str], *, note: str, now: datetime | None = None) -> int:
        """把不在 keep 集合内的 auto 来源活跃行标记移出；manual 行不受影响。"""
        now_dt = now or datetime.now().astimezone()
        stamp = now_dt.isoformat(timespec="seconds")
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE company_tracking_list SET status='removed', removed_date=?, removed_note=?, updated_at=? "
                "WHERE status='active' AND source='auto' AND stock_code NOT IN "
                f"({','.join('?' for _ in keep_codes)})",  # noqa: S608 - 集合来自服务层白名单
                (now_dt.date().isoformat(), note, stamp, *[c.upper() for c in keep_codes]),
            )
        return cursor.rowcount

    def list_active(self) -> list[dict[str, Any]]:
        with self._conn:
            rows = self._conn.execute(
                "SELECT * FROM company_tracking_list WHERE status='active' ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["added_reasons"] = json.loads(item.pop("added_reasons_json") or "[]")
            except (TypeError, ValueError):
                item["added_reasons"] = []
            item.pop("added_reasons_json", None)
            try:
                item["added_cautions"] = json.loads(item.pop("added_cautions_json") or "[]")
            except (KeyError, TypeError, ValueError):
                item["added_cautions"] = []
            try:
                item["entry_snapshot"] = json.loads(item.pop("entry_snapshot_json") or "{}")
            except (KeyError, TypeError, ValueError):
                item["entry_snapshot"] = {}
            result.append(item)
        return result

    def tracked_codes(self) -> set[str]:
        with self._conn:
            rows = self._conn.execute(
                "SELECT stock_code FROM company_tracking_list WHERE status='active'"
            ).fetchall()
        return {str(row[0]).upper() for row in rows}

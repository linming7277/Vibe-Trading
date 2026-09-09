"""Cross-market macro series persistence on the existing ``macro_series`` table.

零表结构变更（M1-B §三）：source identity 走 ``metadata_json`` companion，
PIT 复用既有 (release_date, fetched_at) 列与索引；幂等靠主键
(series_id, observation_date, release_date, vintage_id) + ``DO NOTHING``——
首次捕获的证据属性（含 HISTORICAL_BACKFILL 标记）不被后续重抓覆盖。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from src.macro_data.identity import MACRO_SERIES_IDS, ALL_IDENTITIES
from src.macro_data.freshness import evaluate_freshness

_UPSERIES_WHITELIST = frozenset(MACRO_SERIES_IDS)


class CrossMarketStore:
    """读写 research.db 的 macro_series（仅限本模块注册的 series_id）。"""

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- write -------------------------------------------------------------

    def upsert_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        """幂等写入。首次捕获优先：同主键重复抓取 DO NOTHING（0 duplicate，
        且不会把 HISTORICAL_BACKFILL 翻写成 FORWARD_CAPTURED）。"""
        payloads: list[tuple[Any, ...]] = []
        for row in rows:
            series_id = str(row["series_id"])
            if series_id not in _UPSERIES_WHITELIST:
                raise ValueError(f"series_id {series_id!r} 未在跨市场身份目录注册")
            identity = ALL_IDENTITIES[series_id]
            metadata = dict(row.get("metadata") or {})
            metadata.setdefault("source_id", identity.source_id)
            payloads.append((
                series_id,
                str(row["observation_date"]),
                str(row["release_date"]),
                str(row["vintage_id"]),
                row.get("value"),
                identity.unit,
                identity.source_id,
                str(row.get("source_url") or ""),
                str(row.get("release_status") or "RELEASED"),
                str(row["fetched_at"]),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ))
        if not payloads:
            return 0
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._conn.executemany(
                    """INSERT INTO macro_series(
                           series_id, observation_date, release_date, vintage_id,
                           value, unit, source, source_url, release_status,
                           fetched_at, metadata_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(series_id, observation_date, release_date, vintage_id)
                       DO NOTHING""",
                    payloads,
                )
                inserted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return inserted

    # -- read --------------------------------------------------------------

    def read_rows(self, series_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        placeholders = ""
        params: tuple[str, ...] = ()
        if series_ids is not None:
            ids = sorted(set(series_ids))
            if not ids:
                return []
            placeholders = f" WHERE series_id IN ({','.join('?' for _ in ids)})"
            params = tuple(ids)
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM macro_series" + placeholders + " ORDER BY series_id, observation_date",
                params,
            )
            rows = []
            for raw in cursor.fetchall():
                item = dict(raw)
                try:
                    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
                except (TypeError, ValueError):
                    item["metadata"] = {}
                rows.append(item)
        return rows

    # -- ops ---------------------------------------------------------------

    def health_summary(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """只读运维视图（§二十四）：每序列最新观测/抓取/滞后/状态。不进任何日报。"""
        now = now or datetime.now().astimezone()
        rows = self.read_rows()
        by_series: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_series.setdefault(str(row["series_id"]), []).append(row)
        summary: list[dict[str, Any]] = []
        for series_id, identity in ALL_IDENTITIES.items():
            series_rows = by_series.get(series_id) or []
            entry: dict[str, Any] = {
                "source": identity.source_id,
                "series_id": series_id,
                "frequency": identity.frequency,
                "status": "MISSING",
                "latest_observation": None,
                "latest_capture": None,
                "lag": None,
                "error": None,
            }
            if series_rows:
                latest = max(series_rows, key=lambda item: str(item["observation_date"]))
                entry["latest_observation"] = str(latest["observation_date"])
                entry["latest_capture"] = str(latest["fetched_at"])
                obs_day = date.fromisoformat(str(latest["observation_date"])[:10])
                entry["status"] = evaluate_freshness(
                    series_id, obs_day, now_date=now.date(),
                )
                entry["lag"] = (now.date() - obs_day).days
            summary.append(entry)
        return summary

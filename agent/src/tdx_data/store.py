"""SQLite cache used by the TongDaXin data bridge."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.config.paths import get_runtime_root


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class TdxDataStore:
    """Small generic cache with atomic per-dataset replacement."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or (get_runtime_root() / "tdx_data.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS module_state (
                    module TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'never',
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT,
                    updated_at TEXT,
                    last_success_at TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    dataset TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(dataset, record_key)
                );
                CREATE INDEX IF NOT EXISTS idx_tdx_records_category
                    ON records(dataset, category, name);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    module TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tdx_jobs_created ON jobs(created_at DESC);
                CREATE TABLE IF NOT EXISTS formula_scans (
                    id TEXT PRIMARY KEY,
                    formula_type INTEGER NOT NULL,
                    formula_code TEXT NOT NULL,
                    formula_args TEXT NOT NULL DEFAULT '',
                    universe TEXT NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '[]',
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_formula_scans_created ON formula_scans(created_at DESC);
                """
            )
            module_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(module_state)")}
            if "last_success_at" not in module_columns:
                self._conn.execute("ALTER TABLE module_state ADD COLUMN last_success_at TEXT")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ensure_modules(self, modules: Iterable[str]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO module_state(module) VALUES(?)",
                [(module,) for module in modules],
            )
            self._conn.commit()

    def set_module_state(self, module: str, **values: Any) -> None:
        allowed = {
            "status", "progress", "total", "item_count", "message", "error",
            "metadata_json", "started_at", "updated_at", "last_success_at",
        }
        fields = [key for key in values if key in allowed]
        if not fields:
            return
        payload = dict(values)
        if "metadata_json" in payload and not isinstance(payload["metadata_json"], str):
            payload["metadata_json"] = _json(payload["metadata_json"])
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO module_state(module) VALUES(?)", (module,))
            assignments = ", ".join(f"{field}=?" for field in fields)
            self._conn.execute(
                f"UPDATE module_state SET {assignments} WHERE module=?",  # noqa: S608 - fixed allowlist
                [payload[field] for field in fields] + [module],
            )
            self._conn.commit()

    def module_states(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM module_state ORDER BY rowid").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            result.append(item)
        return result

    def replace_dataset(self, dataset: str, rows: Iterable[dict[str, Any]]) -> int:
        now = utc_now()
        materialized = list(rows)
        values = [
            (
                dataset,
                str(row["key"]),
                str(row.get("category", "")),
                str(row.get("name", "")),
                _json(row.get("payload", row)),
                now,
            )
            for row in materialized
        ]
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM records WHERE dataset=?", (dataset,))
                self._conn.executemany(
                    "INSERT INTO records(dataset,record_key,category,name,payload_json,updated_at) VALUES(?,?,?,?,?,?)",
                    values,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(values)

    def upsert_records(self, dataset: str, rows: Iterable[dict[str, Any]]) -> int:
        now = utc_now()
        materialized = list(rows)
        values = [
            (
                dataset,
                str(row["key"]),
                str(row.get("category", "")),
                str(row.get("name", "")),
                _json(row.get("payload", row)),
                now,
            )
            for row in materialized
        ]
        with self._lock:
            self._conn.executemany(
                """INSERT INTO records(dataset,record_key,category,name,payload_json,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(dataset,record_key) DO UPDATE SET
                     category=excluded.category,name=excluded.name,
                     payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                values,
            )
            self._conn.commit()
        return len(values)

    def list_records(
        self,
        dataset: str,
        *,
        category: str | None = None,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["dataset=?"]
        args: list[Any] = [dataset]
        if category:
            where.append("category=?")
            args.append(category)
        if query:
            where.append("(record_key LIKE ? OR name LIKE ?)")
            needle = f"%{query}%"
            args.extend([needle, needle])
        clause = " AND ".join(where)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM records WHERE {clause}", args).fetchone()[0]  # noqa: S608
            rows = self._conn.execute(
                f"SELECT * FROM records WHERE {clause} ORDER BY category,name,record_key LIMIT ? OFFSET ?",  # noqa: S608
                args + [limit, offset],
            ).fetchall()
        return {"dataset": dataset, "total": total, "items": [self._decode_record(row) for row in rows]}

    def get_record(self, dataset: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE dataset=? AND record_key=?", (dataset, key)
            ).fetchone()
        return self._decode_record(row) if row else None

    def count(self, dataset: str) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM records WHERE dataset=?", (dataset,)).fetchone()[0])

    @staticmethod
    def _decode_record(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            payload = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        item["key"] = item.pop("record_key")
        item["payload"] = payload
        return item

    def create_job(self, job_id: str, module: str) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(id,module,status,created_at) VALUES(?,?,?,?)",
                (job_id, module, "queued", now),
            )
            self._conn.commit()
        return self.get_job(job_id) or {}

    def update_job(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "progress", "total", "message", "error", "started_at", "completed_at"}
        fields = [key for key in values if key in allowed]
        if not fields:
            return
        with self._lock:
            assignments = ", ".join(f"{field}=?" for field in fields)
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",  # noqa: S608 - fixed allowlist
                [values[field] for field in fields] + [job_id],
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def create_formula_scan(self, scan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """INSERT INTO formula_scans(
                       id,formula_type,formula_code,formula_args,universe,period,status,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    scan_id, int(payload["formula_type"]), str(payload["formula_code"]),
                    str(payload.get("formula_args", "")), str(payload.get("universe", "all")),
                    str(payload.get("period", "1d")), "queued", utc_now(),
                ),
            )
            self._conn.commit()
        return self.get_formula_scan(scan_id) or {}

    def update_formula_scan(self, scan_id: str, **values: Any) -> None:
        allowed = {"status", "progress", "total", "result_json", "message", "error", "completed_at"}
        fields = [key for key in values if key in allowed]
        if not fields:
            return
        payload = dict(values)
        if "result_json" in payload and not isinstance(payload["result_json"], str):
            payload["result_json"] = _json(payload["result_json"])
        with self._lock:
            assignments = ", ".join(f"{field}=?" for field in fields)
            self._conn.execute(
                f"UPDATE formula_scans SET {assignments} WHERE id=?",  # noqa: S608 - fixed allowlist
                [payload[field] for field in fields] + [scan_id],
            )
            self._conn.commit()

    def get_formula_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM formula_scans WHERE id=?", (scan_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        try:
            value["results"] = json.loads(value.pop("result_json"))
        except (TypeError, json.JSONDecodeError):
            value["results"] = []
        return value

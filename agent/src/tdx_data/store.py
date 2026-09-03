"""SQLite cache used by the TongDaXin data bridge."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
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
        self._write_context = threading.local()
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
                    snapshot_id TEXT,
                    PRIMARY KEY(dataset, record_key)
                );
                CREATE INDEX IF NOT EXISTS idx_tdx_records_category
                    ON records(dataset, category, name);
                -- Refresh workers write here first.  The public ``records``
                -- cache is changed only after the whole refresh profile has
                -- passed, so consumers never observe a half-refreshed cache.
                CREATE TABLE IF NOT EXISTS snapshot_records (
                    snapshot_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, dataset, record_key)
                );
                CREATE INDEX IF NOT EXISTS idx_tdx_snapshot_records_lookup
                    ON snapshot_records(snapshot_id, dataset, category, name);
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
                CREATE TABLE IF NOT EXISTS refresh_runs (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    market TEXT NOT NULL,
                    market_date TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    modules_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_refresh_runs_profile_date
                    ON refresh_runs(profile, market, market_date, created_at DESC);
                CREATE TABLE IF NOT EXISTS dataset_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    refresh_run_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    market TEXT NOT NULL,
                    market_date TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    coverage REAL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    expected_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    UNIQUE(snapshot_id, dataset)
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_snapshots_lookup
                    ON dataset_snapshots(snapshot_id, dataset, status);
                -- Long-lived, company-level daily history for Value Line price
                -- research.  It is deliberately separate from the generic
                -- ``klines`` records cache (which only keeps the last ad-hoc
                -- response) and from the cross-sectional parquet warehouse.
                CREATE TABLE IF NOT EXISTS adjusted_daily_bars (
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL,
                    amount REAL,
                    adjustment_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    PRIMARY KEY(market, stock_code, trade_date, adjustment_type)
                );
                CREATE INDEX IF NOT EXISTS idx_adjusted_daily_bars_lookup
                    ON adjusted_daily_bars(market, stock_code, adjustment_type, trade_date);
                CREATE TABLE IF NOT EXISTS adjusted_daily_bar_coverage (
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    adjustment_type TEXT NOT NULL,
                    bar_count INTEGER NOT NULL DEFAULT 0,
                    first_date TEXT,
                    last_date TEXT,
                    coverage_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(market, stock_code, adjustment_type)
                );
                -- Durable progress for the deliberately explicit Current L3
                -- history backfill.  Per-company rows make an interrupted run
                -- resumable without re-fetching completed companies.
                CREATE TABLE IF NOT EXISTS adjusted_daily_bar_backfill_runs (
                    id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    target_as_of TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    last_stock_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS adjusted_daily_bar_backfill_items (
                    run_id TEXT NOT NULL,
                    item_offset INTEGER NOT NULL,
                    stock_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    bar_count INTEGER NOT NULL DEFAULT 0,
                    coverage_status TEXT NOT NULL DEFAULT 'INSUFFICIENT',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, stock_code),
                    UNIQUE(run_id, item_offset)
                );
                CREATE INDEX IF NOT EXISTS idx_adjusted_bar_backfill_pending
                    ON adjusted_daily_bar_backfill_items(run_id, status, item_offset);
                -- PIT-rebuilt company valuation history.  ``close`` is the
                -- source's unadjusted close so per-share accounting values and
                -- cash dividends are not mixed with a forward-adjusted price.
                CREATE TABLE IF NOT EXISTS historical_valuation_series (
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    pe_ttm REAL,
                    pb_mrq REAL,
                    dividend_yield REAL,
                    market_cap REAL,
                    financial_data_as_of TEXT,
                    financial_source_id TEXT NOT NULL DEFAULT '',
                    price_source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    quality_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(market, stock_code, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_historical_valuation_lookup
                    ON historical_valuation_series(market, stock_code, trade_date);
                CREATE TABLE IF NOT EXISTS historical_valuation_coverage (
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    first_date TEXT,
                    last_date TEXT,
                    pe_count INTEGER NOT NULL DEFAULT 0,
                    pb_count INTEGER NOT NULL DEFAULT 0,
                    dividend_yield_count INTEGER NOT NULL DEFAULT 0,
                    coverage_status TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(market, stock_code)
                );
                -- Durable, operator-triggered initial backfill for the
                -- Current L3 pool. No scheduler consumes these runs.
                CREATE TABLE IF NOT EXISTS historical_valuation_backfill_runs (
                    id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    target_as_of TEXT NOT NULL,
                    batch_size INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    last_stock_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS historical_valuation_backfill_items (
                    run_id TEXT NOT NULL,
                    item_offset INTEGER NOT NULL,
                    stock_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    pe_count INTEGER NOT NULL DEFAULT 0,
                    pb_count INTEGER NOT NULL DEFAULT 0,
                    dividend_yield_count INTEGER NOT NULL DEFAULT 0,
                    coverage_status TEXT NOT NULL DEFAULT 'INSUFFICIENT',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, stock_code),
                    UNIQUE(run_id, item_offset)
                );
                CREATE INDEX IF NOT EXISTS idx_historical_valuation_backfill_pending
                    ON historical_valuation_backfill_items(run_id, status, item_offset);
                CREATE TABLE IF NOT EXISTS refresh_locks (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refresh_automation (
                    market TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_profile TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    next_run_at TEXT,
                    updated_at TEXT NOT NULL
                );
                -- Point-in-time market-close qualification (PIT remediation
                -- V1).  A view, not a copy: the status stays derivable for
                -- every historical date from immutable run/dataset records
                -- and is never backfilled or mutated retroactively.
                CREATE VIEW IF NOT EXISTS v_market_close_qualifications AS
                SELECT
                    rr.market,
                    rr.market_date,
                    rr.snapshot_id,
                    rr.status AS run_status,
                    rr.completed_at,
                    (SELECT ds.status FROM dataset_snapshots ds
                      WHERE ds.snapshot_id = rr.snapshot_id AND ds.dataset = 'quotes') AS quotes_status,
                    (SELECT ds.item_count FROM dataset_snapshots ds
                      WHERE ds.snapshot_id = rr.snapshot_id AND ds.dataset = 'quotes') AS quotes_item_count,
                    CASE
                        WHEN rr.status = 'completed'
                         AND (SELECT ds.status FROM dataset_snapshots ds
                               WHERE ds.snapshot_id = rr.snapshot_id AND ds.dataset = 'quotes') = 'ready'
                         AND COALESCE((SELECT ds.item_count FROM dataset_snapshots ds
                               WHERE ds.snapshot_id = rr.snapshot_id AND ds.dataset = 'quotes'), 0) >= 5000
                        THEN 'QUALIFIED'
                        WHEN rr.status = 'completed' THEN 'PARTIAL'
                        ELSE 'FAILED'
                    END AS qualification,
                    rr.rowid AS run_rowid
                FROM refresh_runs rr
                WHERE rr.profile = 'market_close';
                """
            )
            module_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(module_state)")}
            if "last_success_at" not in module_columns:
                self._conn.execute("ALTER TABLE module_state ADD COLUMN last_success_at TEXT")
            record_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(records)")}
            if "snapshot_id" not in record_columns:
                self._conn.execute("ALTER TABLE records ADD COLUMN snapshot_id TEXT")
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

    @contextmanager
    def snapshot_context(self, snapshot_id: str | None):
        """Attach one immutable refresh snapshot to all writes on this worker thread."""
        previous = getattr(self._write_context, "snapshot_id", None)
        self._write_context.snapshot_id = snapshot_id
        try:
            yield
        finally:
            self._write_context.snapshot_id = previous

    def _write_snapshot_id(self, explicit: str | None = None) -> str | None:
        return explicit if explicit is not None else getattr(self._write_context, "snapshot_id", None)

    def _read_snapshot_id(self, explicit: str | None = None) -> str | None:
        """Use staged data while a refresh worker is building its snapshot."""
        return explicit if explicit is not None else getattr(self._write_context, "snapshot_id", None)

    @staticmethod
    def _record_values(dataset: str, rows: Iterable[dict[str, Any]], now: str, snapshot_id: str | None = None) -> list[tuple[Any, ...]]:
        return [
            (
                *((snapshot_id,) if snapshot_id is not None else ()),
                dataset,
                str(row["key"]),
                str(row.get("category", "")),
                str(row.get("name", "")),
                _json(row.get("payload", row)),
                now,
                *((snapshot_id,) if snapshot_id is None else ()),
            )
            for row in rows
        ]

    def replace_dataset(self, dataset: str, rows: Iterable[dict[str, Any]], *, snapshot_id: str | None = None) -> int:
        now = utc_now()
        snapshot = self._write_snapshot_id(snapshot_id)
        materialized = list(rows)
        values = self._record_values(dataset, materialized, now, snapshot)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if snapshot:
                    self._conn.execute("DELETE FROM snapshot_records WHERE snapshot_id=? AND dataset=?", (snapshot, dataset))
                    self._conn.executemany(
                        "INSERT INTO snapshot_records(snapshot_id,dataset,record_key,category,name,payload_json,updated_at) VALUES(?,?,?,?,?,?,?)",
                        values,
                    )
                else:
                    self._conn.execute("DELETE FROM records WHERE dataset=?", (dataset,))
                    self._conn.executemany(
                        "INSERT INTO records(dataset,record_key,category,name,payload_json,updated_at,snapshot_id) VALUES(?,?,?,?,?,?,?)",
                        values,
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(values)

    def upsert_records(self, dataset: str, rows: Iterable[dict[str, Any]], *, snapshot_id: str | None = None) -> int:
        now = utc_now()
        snapshot = self._write_snapshot_id(snapshot_id)
        materialized = list(rows)
        values = self._record_values(dataset, materialized, now, snapshot)
        with self._lock:
            if snapshot:
                self._conn.executemany(
                    """INSERT INTO snapshot_records(snapshot_id,dataset,record_key,category,name,payload_json,updated_at)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(snapshot_id,dataset,record_key) DO UPDATE SET
                         category=excluded.category,name=excluded.name,
                         payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                    values,
                )
            else:
                self._conn.executemany(
                    """INSERT INTO records(dataset,record_key,category,name,payload_json,updated_at,snapshot_id)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(dataset,record_key) DO UPDATE SET
                         category=excluded.category,name=excluded.name,
                         payload_json=excluded.payload_json,updated_at=excluded.updated_at,
                         snapshot_id=excluded.snapshot_id""",
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
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self._read_snapshot_id(snapshot_id)
        table = "snapshot_records" if snapshot else "records"
        where = ["dataset=?"]
        args: list[Any] = [dataset]
        if snapshot:
            where.insert(0, "snapshot_id=?")
            args.insert(0, snapshot)
        if category:
            where.append("category=?")
            args.append(category)
        if query:
            where.append("(record_key LIKE ? OR name LIKE ?)")
            needle = f"%{query}%"
            args.extend([needle, needle])
        clause = " AND ".join(where)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {clause}", args).fetchone()[0]  # noqa: S608
            rows = self._conn.execute(
                f"SELECT * FROM {table} WHERE {clause} ORDER BY category,name,record_key LIMIT ? OFFSET ?",  # noqa: S608
                args + [limit, offset],
            ).fetchall()
        return {"dataset": dataset, "total": total, "items": [self._decode_record(row) for row in rows]}

    def get_record(self, dataset: str, key: str, *, snapshot_id: str | None = None) -> dict[str, Any] | None:
        snapshot = self._read_snapshot_id(snapshot_id)
        with self._lock:
            if snapshot:
                row = self._conn.execute(
                    "SELECT * FROM snapshot_records WHERE snapshot_id=? AND dataset=? AND record_key=?", (snapshot, dataset, key)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM records WHERE dataset=? AND record_key=?", (dataset, key)
                ).fetchone()
        return self._decode_record(row) if row else None

    def count(self, dataset: str, *, snapshot_id: str | None = None) -> int:
        snapshot = self._read_snapshot_id(snapshot_id)
        with self._lock:
            if snapshot:
                return int(self._conn.execute("SELECT COUNT(*) FROM snapshot_records WHERE snapshot_id=? AND dataset=?", (snapshot, dataset)).fetchone()[0])
            return int(self._conn.execute("SELECT COUNT(*) FROM records WHERE dataset=?", (dataset,)).fetchone()[0])

    @staticmethod
    def _daily_bar_coverage_status(count: int) -> str:
        if count >= 250:
            return "READY"
        if count >= 60:
            return "PARTIAL"
        return "INSUFFICIENT"

    def upsert_adjusted_daily_bars(self, rows: Iterable[dict[str, Any]]) -> int:
        """Idempotently persist normalized front-adjusted daily bars.

        Exact repeats leave rows untouched.  A provider correction with a new
        source hash is accepted and refreshes its provenance without creating a
        duplicate date.
        """
        materialized = list(rows)
        if not materialized:
            return 0
        values = [
            (
                str(row["market"]), str(row["stock_code"]), str(row["trade_date"]),
                float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
                row.get("volume"), row.get("amount"), str(row["adjustment_type"]),
                str(row["source"]), str(row["source_version"]), str(row["fetched_at"]), str(row["source_hash"]),
            )
            for row in materialized
        ]
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                """INSERT INTO adjusted_daily_bars(
                     market,stock_code,trade_date,open,high,low,close,volume,amount,
                     adjustment_type,source,source_version,fetched_at,source_hash
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market,stock_code,trade_date,adjustment_type) DO UPDATE SET
                     open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                     volume=excluded.volume,amount=excluded.amount,source=excluded.source,
                     source_version=excluded.source_version,fetched_at=excluded.fetched_at,
                     source_hash=excluded.source_hash
                   WHERE adjusted_daily_bars.source_hash<>excluded.source_hash""",
                values,
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def refresh_adjusted_daily_bar_coverage(
        self, market: str, stock_code: str, *, adjustment_type: str = "front",
        source: str = "TongDaXin", source_version: str = "get_market_data/front/v1",
        fetched_at: str | None = None, source_hash: str = "", error: str = "",
    ) -> dict[str, Any]:
        """Recompute one company's coverage from stored bars, never from wall clock."""
        market, stock_code = str(market).upper(), str(stock_code).upper()
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS bar_count, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date
                   FROM adjusted_daily_bars WHERE market=? AND stock_code=? AND adjustment_type=?""",
                (market, stock_code, adjustment_type),
            ).fetchone()
            count = int(row["bar_count"] if row else 0)
            payload = {
                "market": market, "stock_code": stock_code, "adjustment_type": adjustment_type,
                "bar_count": count, "first_date": row["first_date"] if row else None,
                "last_date": row["last_date"] if row else None,
                "coverage_status": self._daily_bar_coverage_status(count), "source": source,
                "source_version": source_version, "fetched_at": fetched_at or utc_now(),
                "source_hash": source_hash, "error": error,
            }
            self._conn.execute(
                """INSERT INTO adjusted_daily_bar_coverage(
                     market,stock_code,adjustment_type,bar_count,first_date,last_date,coverage_status,
                     source,source_version,fetched_at,source_hash,error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market,stock_code,adjustment_type) DO UPDATE SET
                     bar_count=excluded.bar_count,first_date=excluded.first_date,last_date=excluded.last_date,
                     coverage_status=excluded.coverage_status,source=excluded.source,
                     source_version=excluded.source_version,fetched_at=excluded.fetched_at,
                     source_hash=excluded.source_hash,error=excluded.error""",
                tuple(payload.values()),
            )
            self._conn.commit()
        return payload

    def adjusted_daily_bar_status(self, market: str, stock_code: str, *, adjustment_type: str = "front") -> dict[str, Any]:
        market, stock_code = str(market).upper(), str(stock_code).upper()
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM adjusted_daily_bar_coverage
                   WHERE market=? AND stock_code=? AND adjustment_type=?""",
                (market, stock_code, adjustment_type),
            ).fetchone()
        if row:
            return dict(row)
        # A status read must remain read-only.  Coverage is written only by an
        # explicit refresh; an unseen company is honestly INSUFFICIENT.
        return {
            "market": market, "stock_code": stock_code, "adjustment_type": adjustment_type,
            "bar_count": 0, "first_date": None, "last_date": None, "coverage_status": "INSUFFICIENT",
            "source": "TongDaXin", "source_version": "get_market_data/front/v1",
            "fetched_at": None, "source_hash": "", "error": "not_cached",
        }

    def adjusted_daily_bar_status_as_of(
        self, market: str, stock_code: str, *, adjustment_type: str = "front", as_of: str,
    ) -> dict[str, Any]:
        """Coverage metadata scoped to ``trade_date <= as_of`` only.

        The persisted coverage row is a mutable latest-state projection; an
        as-of read must never surface a ``last_date`` newer than the requested
        business date.  This recomputes count/first/last/status from the same
        stored bars the reader would select, read-only.
        """
        market, stock_code = str(market).upper(), str(stock_code).upper()
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS bar_count, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date
                   FROM adjusted_daily_bars
                   WHERE market=? AND stock_code=? AND adjustment_type=? AND trade_date<=?""",
                (market, stock_code, adjustment_type, str(as_of)[:10]),
            ).fetchone()
        count = int(row["bar_count"] if row else 0)
        return {
            "market": market, "stock_code": stock_code, "adjustment_type": adjustment_type,
            "bar_count": count,
            "first_date": row["first_date"] if row else None,
            "last_date": row["last_date"] if row else None,
            "coverage_status": self._daily_bar_coverage_status(count),
            "as_of": str(as_of)[:10],
        }

    def get_adjusted_daily_bars(
        self, market: str, stock_code: str, *, adjustment_type: str = "front", as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        market, stock_code = str(market).upper(), str(stock_code).upper()
        where = ["market=?", "stock_code=?", "adjustment_type=?"]
        args: list[Any] = [market, stock_code, adjustment_type]
        if as_of:
            where.append("trade_date<=?")
            args.append(str(as_of)[:10])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM adjusted_daily_bars WHERE {' AND '.join(where)} ORDER BY trade_date", args,  # noqa: S608
            ).fetchall()
        return [dict(row) for row in rows]

    def create_adjusted_daily_bar_backfill_run(
        self, *, run_id: str, pool_id: str, market: str, target_as_of: str, batch_size: int, symbols: list[str],
    ) -> dict[str, Any]:
        now = utc_now()
        normalized = [str(symbol).upper() for symbol in symbols]
        with self._lock:
            self._conn.execute(
                """INSERT INTO adjusted_daily_bar_backfill_runs(
                     id,pool_id,market,target_as_of,batch_size,total_count,status,last_stock_code,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, pool_id, market, target_as_of, int(batch_size), len(normalized), "RUNNING", None, now, now),
            )
            self._conn.executemany(
                """INSERT INTO adjusted_daily_bar_backfill_items(
                     run_id,item_offset,stock_code,status,updated_at
                   ) VALUES(?,?,?,?,?)""",
                [(run_id, offset, symbol, "PENDING", now) for offset, symbol in enumerate(normalized)],
            )
            self._conn.commit()
        return self.adjusted_daily_bar_backfill_run(run_id) or {}

    def adjusted_daily_bar_backfill_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM adjusted_daily_bar_backfill_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def adjusted_daily_bar_backfill_items(
        self, run_id: str, *, statuses: tuple[str, ...] = ("PENDING",), offset: int = 0, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM adjusted_daily_bar_backfill_items
                    WHERE run_id=? AND status IN ({placeholders}) AND item_offset>=?
                    ORDER BY item_offset LIMIT ?""",  # noqa: S608
                [run_id, *statuses, int(offset), int(limit)],
            ).fetchall()
        return [dict(row) for row in rows]

    def update_adjusted_daily_bar_backfill_item(
        self, run_id: str, stock_code: str, *, status: str, bar_count: int, coverage_status: str,
        error_type: str = "", error_summary: str = "", increment_attempts: bool = True,
    ) -> None:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """UPDATE adjusted_daily_bar_backfill_items SET
                     status=?, attempts=attempts+?, bar_count=?, coverage_status=?, error_type=?,
                     error_summary=?, updated_at=? WHERE run_id=? AND stock_code=?""",
                (status, 1 if increment_attempts else 0, int(bar_count), coverage_status,
                 error_type[:100], error_summary[:1000], now, run_id, stock_code),
            )
            self._conn.execute(
                """UPDATE adjusted_daily_bar_backfill_runs SET last_stock_code=?,updated_at=? WHERE id=?""",
                (stock_code, now, run_id),
            )
            self._conn.commit()

    def reset_adjusted_daily_bar_backfill_failures(self, run_id: str) -> int:
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE adjusted_daily_bar_backfill_items
                   SET status='PENDING',error_type='',error_summary='',updated_at=?
                   WHERE run_id=? AND status='FAILED'""",
                (now, run_id),
            )
            self._conn.execute(
                "UPDATE adjusted_daily_bar_backfill_runs SET status='RUNNING',completed_at=NULL,updated_at=? WHERE id=?",
                (now, run_id),
            )
            self._conn.commit()
        return int(cursor.rowcount)

    def adjusted_daily_bar_backfill_summary(self, run_id: str) -> dict[str, Any]:
        run = self.adjusted_daily_bar_backfill_run(run_id)
        if not run:
            raise KeyError(f"daily bar backfill run not found: {run_id}")
        with self._lock:
            rows = self._conn.execute(
                """SELECT status,COUNT(*) AS count FROM adjusted_daily_bar_backfill_items
                   WHERE run_id=? GROUP BY status""", (run_id,),
            ).fetchall()
            next_row = self._conn.execute(
                """SELECT MIN(item_offset) AS next_offset FROM adjusted_daily_bar_backfill_items
                   WHERE run_id=? AND status='PENDING'""", (run_id,),
            ).fetchone()
            failures = self._conn.execute(
                """SELECT stock_code,error_type,error_summary,attempts FROM adjusted_daily_bar_backfill_items
                   WHERE run_id=? AND status='FAILED' ORDER BY item_offset""", (run_id,),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        total = int(run["total_count"])
        ready = counts.get("READY", 0)
        return {
            **run,
            "processed": total - counts.get("PENDING", 0), "ready": ready, "partial": counts.get("PARTIAL", 0),
            "insufficient": counts.get("INSUFFICIENT", 0), "failed": counts.get("FAILED", 0),
            "skipped": counts.get("SKIPPED", 0), "pending": counts.get("PENDING", 0),
            "ready_coverage_rate": round(ready / total * 100, 2) if total else 0.0,
            "next_offset": int(next_row["next_offset"]) if next_row and next_row["next_offset"] is not None else total,
            "failures": [dict(row) for row in failures],
        }

    def finalize_adjusted_daily_bar_backfill_run(self, run_id: str) -> dict[str, Any]:
        summary = self.adjusted_daily_bar_backfill_summary(run_id)
        if summary["pending"]:
            status, completed_at = "RUNNING", None
        elif summary["failed"]:
            status, completed_at = "COMPLETED_WITH_FAILURES", utc_now()
        else:
            status, completed_at = "COMPLETED", utc_now()
        with self._lock:
            self._conn.execute(
                "UPDATE adjusted_daily_bar_backfill_runs SET status=?,updated_at=?,completed_at=? WHERE id=?",
                (status, utc_now(), completed_at, run_id),
            )
            self._conn.commit()
        return self.adjusted_daily_bar_backfill_summary(run_id)

    @staticmethod
    def _historical_valuation_coverage_status(pe_count: int, pb_count: int) -> str:
        # PE and PB are the primary self-valuation measures.  Dividend yield is
        # optional because not every company has a distributable cash history.
        usable = max(pe_count, pb_count)
        if usable >= 750:
            return "READY"
        if usable >= 250:
            return "PARTIAL"
        return "INSUFFICIENT"

    def upsert_historical_valuation_series(self, rows: Iterable[dict[str, Any]]) -> int:
        materialized = list(rows)
        if not materialized:
            return 0
        values = [
            (
                str(row["market"]), str(row["stock_code"]), str(row["trade_date"]), float(row["close"]),
                row.get("pe_ttm"), row.get("pb_mrq"), row.get("dividend_yield"), row.get("market_cap"),
                row.get("financial_data_as_of"), str(row.get("financial_source_id") or ""),
                str(row["price_source_id"]), str(row["source_type"]), str(row["source_hash"]),
                str(row["quality_status"]), str(row["created_at"]),
            ) for row in materialized
        ]
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                """INSERT INTO historical_valuation_series(
                     market,stock_code,trade_date,close,pe_ttm,pb_mrq,dividend_yield,market_cap,
                     financial_data_as_of,financial_source_id,price_source_id,source_type,source_hash,
                     quality_status,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market,stock_code,trade_date) DO UPDATE SET
                     close=excluded.close,pe_ttm=excluded.pe_ttm,pb_mrq=excluded.pb_mrq,
                     dividend_yield=excluded.dividend_yield,market_cap=excluded.market_cap,
                     financial_data_as_of=excluded.financial_data_as_of,
                     financial_source_id=excluded.financial_source_id,
                     price_source_id=excluded.price_source_id,source_type=excluded.source_type,
                     source_hash=excluded.source_hash,quality_status=excluded.quality_status,
                     created_at=excluded.created_at
                   WHERE historical_valuation_series.source_hash<>excluded.source_hash""",
                values,
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def refresh_historical_valuation_coverage(
        self, market: str, stock_code: str, *, last_error: str = "",
    ) -> dict[str, Any]:
        market, stock_code = str(market).upper(), str(stock_code).upper()
        with self._lock:
            row = self._conn.execute(
                """SELECT MIN(trade_date) AS first_date,MAX(trade_date) AS last_date,
                          SUM(CASE WHEN pe_ttm>0 THEN 1 ELSE 0 END) AS pe_count,
                          SUM(CASE WHEN pb_mrq>0 THEN 1 ELSE 0 END) AS pb_count,
                          SUM(CASE WHEN dividend_yield>0 THEN 1 ELSE 0 END) AS dividend_yield_count
                   FROM historical_valuation_series WHERE market=? AND stock_code=?""",
                (market, stock_code),
            ).fetchone()
            pe_count, pb_count = int((row or {})["pe_count"] or 0), int((row or {})["pb_count"] or 0)
            payload = {
                "market": market, "stock_code": stock_code,
                "first_date": row["first_date"] if row else None, "last_date": row["last_date"] if row else None,
                "pe_count": pe_count, "pb_count": pb_count,
                "dividend_yield_count": int((row or {})["dividend_yield_count"] or 0),
                "coverage_status": self._historical_valuation_coverage_status(pe_count, pb_count),
                "last_error": last_error[:1000], "updated_at": utc_now(),
            }
            self._conn.execute(
                """INSERT INTO historical_valuation_coverage(
                     market,stock_code,first_date,last_date,pe_count,pb_count,dividend_yield_count,
                     coverage_status,last_error,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market,stock_code) DO UPDATE SET
                     first_date=excluded.first_date,last_date=excluded.last_date,pe_count=excluded.pe_count,
                     pb_count=excluded.pb_count,dividend_yield_count=excluded.dividend_yield_count,
                     coverage_status=excluded.coverage_status,last_error=excluded.last_error,
                     updated_at=excluded.updated_at""",
                tuple(payload.values()),
            )
            self._conn.commit()
        return payload

    def historical_valuation_coverage(self, market: str, stock_code: str) -> dict[str, Any]:
        market, stock_code = str(market).upper(), str(stock_code).upper()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM historical_valuation_coverage WHERE market=? AND stock_code=?", (market, stock_code),
            ).fetchone()
        if row:
            return dict(row)
        return {
            "market": market, "stock_code": stock_code, "first_date": None, "last_date": None,
            "pe_count": 0, "pb_count": 0, "dividend_yield_count": 0,
            "coverage_status": "INSUFFICIENT", "last_error": "not_cached", "updated_at": None,
        }

    def get_historical_valuation_series(
        self, market: str, stock_code: str, *, as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        market, stock_code = str(market).upper(), str(stock_code).upper()
        where, args = ["market=?", "stock_code=?"], [market, stock_code]
        if as_of:
            where.append("trade_date<=?")
            args.append(str(as_of)[:10])
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM historical_valuation_series WHERE {' AND '.join(where)} ORDER BY trade_date", args,  # noqa: S608
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Historical valuation Current-L3 backfill state. These are explicit,
    # durable operator runs and not part of the data scheduler.

    def create_historical_valuation_backfill_run(
        self, *, run_id: str, pool_id: str, market: str, target_as_of: str, batch_size: int, symbols: list[str],
    ) -> dict[str, Any]:
        now = utc_now()
        normalized = [str(symbol).upper() for symbol in symbols]
        with self._lock:
            self._conn.execute(
                """INSERT INTO historical_valuation_backfill_runs(
                     id,pool_id,market,target_as_of,batch_size,total_count,status,last_stock_code,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, pool_id, market, target_as_of, int(batch_size), len(normalized), "RUNNING", None, now, now),
            )
            self._conn.executemany(
                """INSERT INTO historical_valuation_backfill_items(
                     run_id,item_offset,stock_code,status,updated_at
                   ) VALUES(?,?,?,?,?)""",
                [(run_id, offset, symbol, "PENDING", now) for offset, symbol in enumerate(normalized)],
            )
            self._conn.commit()
        return self.historical_valuation_backfill_run(run_id) or {}

    def historical_valuation_backfill_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM historical_valuation_backfill_runs WHERE id=?", (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def historical_valuation_backfill_items(
        self, run_id: str, *, statuses: tuple[str, ...] = ("PENDING",), offset: int = 0, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM historical_valuation_backfill_items
                    WHERE run_id=? AND status IN ({placeholders}) AND item_offset>=?
                    ORDER BY item_offset LIMIT ?""",  # noqa: S608
                [run_id, *statuses, int(offset), int(limit)],
            ).fetchall()
        return [dict(row) for row in rows]

    def update_historical_valuation_backfill_item(
        self, run_id: str, stock_code: str, *, status: str, pe_count: int, pb_count: int,
        dividend_yield_count: int, coverage_status: str, error_type: str = "", error_summary: str = "",
        increment_attempts: bool = True,
    ) -> None:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """UPDATE historical_valuation_backfill_items SET
                     status=?, attempts=attempts+?, pe_count=?, pb_count=?, dividend_yield_count=?,
                     coverage_status=?, error_type=?, error_summary=?, updated_at=?
                   WHERE run_id=? AND stock_code=?""",
                (status, 1 if increment_attempts else 0, int(pe_count), int(pb_count), int(dividend_yield_count),
                 coverage_status, error_type[:100], error_summary[:1000], now, run_id, stock_code),
            )
            self._conn.execute(
                "UPDATE historical_valuation_backfill_runs SET last_stock_code=?,updated_at=? WHERE id=?",
                (stock_code, now, run_id),
            )
            self._conn.commit()

    def reset_historical_valuation_backfill_failures(self, run_id: str) -> int:
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE historical_valuation_backfill_items
                   SET status='PENDING',error_type='',error_summary='',updated_at=?
                   WHERE run_id=? AND status='FAILED'""",
                (now, run_id),
            )
            self._conn.execute(
                """UPDATE historical_valuation_backfill_runs
                   SET status='RUNNING',completed_at=NULL,updated_at=? WHERE id=?""",
                (now, run_id),
            )
            self._conn.commit()
        return int(cursor.rowcount)

    def historical_valuation_backfill_summary(self, run_id: str) -> dict[str, Any]:
        run = self.historical_valuation_backfill_run(run_id)
        if not run:
            raise KeyError(f"historical valuation backfill run not found: {run_id}")
        with self._lock:
            rows = self._conn.execute(
                """SELECT status,COUNT(*) AS count FROM historical_valuation_backfill_items
                   WHERE run_id=? GROUP BY status""", (run_id,),
            ).fetchall()
            next_row = self._conn.execute(
                """SELECT MIN(item_offset) AS next_offset FROM historical_valuation_backfill_items
                   WHERE run_id=? AND status='PENDING'""", (run_id,),
            ).fetchone()
            failures = self._conn.execute(
                """SELECT stock_code,error_type,error_summary,attempts FROM historical_valuation_backfill_items
                   WHERE run_id=? AND status='FAILED' ORDER BY item_offset""", (run_id,),
            ).fetchall()
            items = self._conn.execute(
                """SELECT item_offset,stock_code,status,pe_count,pb_count,dividend_yield_count,coverage_status
                   FROM historical_valuation_backfill_items WHERE run_id=? ORDER BY item_offset""", (run_id,),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        materialized = [dict(row) for row in items]
        total = int(run["total_count"])
        ready, partial = counts.get("READY", 0), counts.get("PARTIAL", 0)
        completed = [row for row in materialized if row["status"] in {"READY", "PARTIAL", "SKIPPED"}]
        # Existing READY rows skipped by an idempotent run are usable too.
        usable = ready + partial + counts.get("SKIPPED", 0)
        return {
            **run,
            "processed": total - counts.get("PENDING", 0), "ready": ready, "partial": partial,
            "insufficient": counts.get("INSUFFICIENT", 0), "failed": counts.get("FAILED", 0),
            "skipped": counts.get("SKIPPED", 0), "pending": counts.get("PENDING", 0),
            "usable_coverage_rate": round(usable / total * 100, 2) if total else 0.0,
            "pe_usable": sum(int(row["pe_count"] or 0) >= 250 for row in completed),
            "pb_usable": sum(int(row["pb_count"] or 0) >= 250 for row in completed),
            "dividend_usable": sum(int(row["dividend_yield_count"] or 0) >= 250 for row in completed),
            "next_offset": int(next_row["next_offset"]) if next_row and next_row["next_offset"] is not None else total,
            "failures": [dict(row) for row in failures], "items": materialized,
        }

    def finalize_historical_valuation_backfill_run(self, run_id: str) -> dict[str, Any]:
        summary = self.historical_valuation_backfill_summary(run_id)
        if summary["pending"]:
            status, completed_at = "RUNNING", None
        elif summary["failed"]:
            status, completed_at = "COMPLETED_WITH_FAILURES", utc_now()
        else:
            status, completed_at = "COMPLETED", utc_now()
        with self._lock:
            self._conn.execute(
                "UPDATE historical_valuation_backfill_runs SET status=?,updated_at=?,completed_at=? WHERE id=?",
                (status, utc_now(), completed_at, run_id),
            )
            self._conn.commit()
        return self.historical_valuation_backfill_summary(run_id)

    def publish_snapshot(self, snapshot_id: str) -> list[str]:
        """Atomically promote a fully built snapshot to the public cache."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT DISTINCT dataset FROM snapshot_records WHERE snapshot_id=? ORDER BY dataset", (snapshot_id,)
                ).fetchall()
                datasets = [str(row["dataset"]) for row in rows]
                for dataset in datasets:
                    self._conn.execute("DELETE FROM records WHERE dataset=?", (dataset,))
                    self._conn.execute(
                        """INSERT INTO records(dataset,record_key,category,name,payload_json,updated_at,snapshot_id)
                           SELECT dataset,record_key,category,name,payload_json,updated_at,snapshot_id
                           FROM snapshot_records WHERE snapshot_id=? AND dataset=?""",
                        (snapshot_id, dataset),
                    )
                self._conn.commit()
                return datasets
            except Exception:
                self._conn.rollback()
                raise

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

    # ------------------------------------------------------------------
    # Refresh pipeline state.  ``jobs`` remains the compatible UI record;
    # these tables describe the immutable data version behind that job.

    def create_refresh_run(
        self,
        run_id: str,
        *,
        profile: str,
        market: str,
        market_date: str,
        snapshot_id: str,
        modules: Iterable[str],
        retry_count: int = 0,
    ) -> dict[str, Any]:
        now = utc_now()
        module_list = list(modules)
        with self._lock:
            self._conn.execute(
                """INSERT INTO refresh_runs(
                       id,profile,market,market_date,snapshot_id,modules_json,status,total,retry_count,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, profile, market, market_date, snapshot_id, _json(module_list), "queued", len(module_list), retry_count, now),
            )
            self._conn.commit()
        return self.get_refresh_run(run_id) or {}

    def update_refresh_run(self, run_id: str, **values: Any) -> None:
        allowed = {"status", "progress", "total", "message", "error", "retry_count", "started_at", "completed_at"}
        fields = [key for key in values if key in allowed]
        if not fields:
            return
        with self._lock:
            assignments = ", ".join(f"{field}=?" for field in fields)
            self._conn.execute(
                f"UPDATE refresh_runs SET {assignments} WHERE id=?",  # noqa: S608 - fixed allowlist
                [values[field] for field in fields] + [run_id],
            )
            self._conn.commit()

    def get_refresh_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM refresh_runs WHERE id=?", (run_id,)).fetchone()
        return self._decode_refresh_run(row) if row else None

    def latest_refresh_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM refresh_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode_refresh_run(row) for row in rows]

    def latest_refresh_run(self, profile: str, market: str, market_date: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM refresh_runs WHERE profile=? AND market=? AND market_date=?
                   ORDER BY created_at DESC LIMIT 1""",
                (profile, market, market_date),
            ).fetchone()
        return self._decode_refresh_run(row) if row else None

    def latest_refresh_run_for_market(self, profile: str, market: str) -> dict[str, Any] | None:
        """Return the latest run regardless of its local market date."""
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM refresh_runs WHERE profile=? AND market=?
                   ORDER BY created_at DESC LIMIT 1""",
                (profile, market),
            ).fetchone()
        return self._decode_refresh_run(row) if row else None

    def refresh_attempt_count(self, profile: str, market: str, market_date: str) -> int:
        with self._lock:
            value = self._conn.execute(
                "SELECT COUNT(*) FROM refresh_runs WHERE profile=? AND market=? AND market_date=?",
                (profile, market, market_date),
            ).fetchone()[0]
        return int(value)

    def market_close_qualifications(self, market: str = "CN", *, limit: int = 60) -> list[dict[str, Any]]:
        """Read-only QUALIFIED/PARTIAL/FAILED derivation per market date."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM v_market_close_qualifications
                   WHERE market=? ORDER BY market_date DESC, run_rowid DESC LIMIT ?""",
                (market.upper(), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def market_close_qualification(self, market_date: str, market: str = "CN") -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM v_market_close_qualifications
                   WHERE market=? AND market_date=? ORDER BY run_rowid DESC LIMIT 1""",
                (market.upper(), str(market_date)[:10]),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _decode_refresh_run(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        try:
            value["modules"] = json.loads(value.pop("modules_json"))
        except (TypeError, json.JSONDecodeError):
            value["modules"] = []
        return value

    def record_dataset_snapshot(
        self,
        *,
        snapshot_id: str,
        refresh_run_id: str,
        dataset: str,
        market: str,
        market_date: str,
        source: str,
        coverage: float | None,
        item_count: int,
        expected_count: int,
        status: str,
        error: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO dataset_snapshots(
                       snapshot_id,refresh_run_id,dataset,market,market_date,available_at,source,
                       coverage,item_count,expected_count,missing_count,status,error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(snapshot_id,dataset) DO UPDATE SET
                     available_at=excluded.available_at,coverage=excluded.coverage,item_count=excluded.item_count,
                     expected_count=excluded.expected_count,missing_count=excluded.missing_count,
                     status=excluded.status,error=excluded.error""",
                (
                    snapshot_id, refresh_run_id, dataset, market, market_date, utc_now(), source,
                    coverage, item_count, expected_count, max(0, expected_count - item_count), status, error,
                ),
            )
            self._conn.commit()

    def dataset_snapshots(self, snapshot_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if snapshot_id:
                rows = self._conn.execute(
                    "SELECT * FROM dataset_snapshots WHERE snapshot_id=? ORDER BY dataset", (snapshot_id,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM dataset_snapshots ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def latest_snapshot_record_for_market_date(
        self,
        dataset: str,
        key: str,
        *,
        market: str,
        market_date: str,
    ) -> dict[str, Any] | None:
        """Return the newest published record for one immutable market date.

        The public ``records`` cache intentionally contains only the latest
        value.  Point-in-time research must instead read the matching refresh
        snapshot when it needs a quote from an earlier trading day.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT sr.*
                   FROM snapshot_records AS sr
                   JOIN refresh_runs AS rr ON rr.snapshot_id=sr.snapshot_id
                   JOIN dataset_snapshots AS ds
                     ON ds.snapshot_id=sr.snapshot_id AND ds.dataset=sr.dataset
                   WHERE sr.dataset=? AND sr.record_key=?
                     AND rr.market=? AND rr.market_date=? AND rr.status='completed'
                     AND ds.status='ready'
                   ORDER BY sr.updated_at DESC
                   LIMIT 1""",
                (dataset, key, market.upper(), market_date),
            ).fetchone()
        return self._decode_record(row) if row else None

    def active_snapshot(self, *, market: str = "CN", market_date: str | None = None, profile: str = "market_close") -> dict[str, Any] | None:
        where = ["profile=?", "market=?", "status='completed'"]
        args: list[Any] = [profile, market]
        if market_date:
            where.append("market_date=?")
            args.append(market_date)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM refresh_runs WHERE {' AND '.join(where)} ORDER BY completed_at DESC LIMIT 1",  # noqa: S608
                args,
            ).fetchone()
        if not row:
            return None
        value = self._decode_refresh_run(row)
        value["datasets"] = self.dataset_snapshots(value["snapshot_id"])
        return value

    def completed_snapshots(
        self,
        *,
        market: str = "CN",
        profile: str = "market_close",
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        """Return recent completed refresh snapshots in newest-market-date order.

        Callers that need a quality-gated close must still validate every item.
        This deliberately keeps an incomplete or pre-close run visible so a
        service can fall back to the most recent *qualified* close instead of
        treating that incomplete run as the only historical choice.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM refresh_runs
                   WHERE profile=? AND market=? AND status='completed'
                   ORDER BY market_date DESC, completed_at DESC
                   LIMIT ?""",
                (profile, market.upper(), max(1, int(limit))),
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            value = self._decode_refresh_run(row)
            value["datasets"] = self.dataset_snapshots(value["snapshot_id"])
            values.append(value)
        return values

    def acquire_refresh_lock(self, name: str, owner: str, *, expires_at: str, now_value: str | None = None) -> bool:
        now = now_value or utc_now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._conn.execute("SELECT owner,expires_at FROM refresh_locks WHERE name=?", (name,)).fetchone()
                if current and current["owner"] != owner and str(current["expires_at"]) > now:
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    """INSERT INTO refresh_locks(name,owner,acquired_at,expires_at) VALUES(?,?,?,?)
                       ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,acquired_at=excluded.acquired_at,expires_at=excluded.expires_at""",
                    (name, owner, now, expires_at),
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def extend_refresh_lock(self, name: str, owner: str, *, expires_at: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE refresh_locks SET expires_at=? WHERE name=? AND owner=?", (expires_at, name, owner)
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def release_refresh_lock(self, name: str, owner: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM refresh_locks WHERE name=? AND owner=?", (name, owner))
            self._conn.commit()

    def refresh_lock(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM refresh_locks WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def get_refresh_automation(self, market: str = "CN") -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO refresh_automation(market,updated_at) VALUES(?,?)", (market, utc_now())
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM refresh_automation WHERE market=?", (market,)).fetchone()
        value = dict(row) if row else {"market": market, "enabled": 1}
        value["enabled"] = bool(value.get("enabled"))
        return value

    def update_refresh_automation(self, market: str = "CN", **values: Any) -> dict[str, Any]:
        allowed = {"enabled", "last_profile", "last_status", "last_error", "next_run_at"}
        fields = [key for key in values if key in allowed]
        self.get_refresh_automation(market)
        if not fields:
            return self.get_refresh_automation(market)
        with self._lock:
            assignments = ", ".join(f"{field}=?" for field in fields)
            self._conn.execute(
                f"UPDATE refresh_automation SET {assignments},updated_at=? WHERE market=?",  # noqa: S608
                [int(values[field]) if field == "enabled" else values[field] for field in fields] + [utc_now(), market],
            )
            self._conn.commit()
        return self.get_refresh_automation(market)

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

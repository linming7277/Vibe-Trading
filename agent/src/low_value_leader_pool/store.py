"""SQLite repository for low-value leader pool entry and exit periods."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class LowValueLeaderPoolRepository:
    """Owns the independent automatic pool; it never changes leader state."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_low_value_leader_pool (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    industry_code TEXT NOT NULL,
                    industry_name TEXT NOT NULL,
                    leader_rank INTEGER NOT NULL,
                    leader_score REAL NOT NULL,
                    current_price REAL,
                    fair_value_low REAL,
                    fair_value_mid REAL,
                    fair_value_high REAL,
                    valuation_status TEXT NOT NULL,
                    historical_valuation_status TEXT,
                    support_status TEXT,
                    support_zone_low REAL,
                    support_zone_high REAL,
                    entry_level TEXT,
                    pool_status TEXT NOT NULL CHECK(pool_status IN ('ACTIVE','REMOVED')),
                    source_pool_id TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    entered_at TEXT NOT NULL,
                    removed_at TEXT,
                    updated_at TEXT NOT NULL,
                    enter_reason TEXT NOT NULL,
                    remove_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_low_value_leader_active
                    ON company_low_value_leader_pool(market, stock_code)
                    WHERE pool_status='ACTIVE';
                CREATE INDEX IF NOT EXISTS idx_low_value_leader_current
                    ON company_low_value_leader_pool(pool_status, source_as_of DESC, leader_score DESC);
                CREATE INDEX IF NOT EXISTS idx_low_value_leader_history
                    ON company_low_value_leader_pool(market, stock_code, entered_at DESC);

                -- The active pool is intentionally a current-state projection.
                -- Before advancing it to a newer research date, retain an
                -- immutable copy so a resumed EOD run never rewrites the
                -- previous day's audited result.
                CREATE TABLE IF NOT EXISTS company_low_value_leader_pool_snapshots (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    source_pool_id TEXT NOT NULL,
                    pool_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    UNIQUE(market, stock_code, source_as_of, source_pool_id)
                );
                CREATE INDEX IF NOT EXISTS idx_low_value_leader_pool_snapshots_asof
                    ON company_low_value_leader_pool_snapshots(source_as_of DESC, stock_code);

                CREATE TABLE IF NOT EXISTS low_value_leader_events (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    industry_code TEXT,
                    industry_name TEXT,
                    event_type TEXT NOT NULL CHECK(event_type IN ('ENTER_LOW_VALUE','EXIT_LOW_VALUE')),
                    before_status TEXT,
                    after_status TEXT NOT NULL,
                    current_price REAL,
                    fair_value_mid REAL,
                    valuation_status TEXT,
                    event_date TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_low_value_leader_event
                    ON low_value_leader_events(market, stock_code, event_type, source_as_of);
                CREATE INDEX IF NOT EXISTS idx_low_value_leader_event_recent
                    ON low_value_leader_events(event_date DESC, event_type, stock_code);

                -- A successful pool evaluation can legitimately produce zero
                -- ACTIVE members and zero events.  Keep one tiny, durable
                -- completion marker so the EOD scheduler can distinguish that
                -- result from a refresh that was never attempted.
                CREATE TABLE IF NOT EXISTS low_value_leader_pool_refreshes (
                    source_as_of TEXT NOT NULL,
                    source_pool_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('COMPLETED','PARTIAL','FAILED')),
                    active_count INTEGER NOT NULL DEFAULT 0,
                    entered_count INTEGER NOT NULL DEFAULT 0,
                    stayed_count INTEGER NOT NULL DEFAULT 0,
                    removed_count INTEGER NOT NULL DEFAULT 0,
                    event_entered_count INTEGER NOT NULL DEFAULT 0,
                    event_exited_count INTEGER NOT NULL DEFAULT 0,
                    errors_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_as_of, source_pool_id)
                );
                CREATE INDEX IF NOT EXISTS idx_low_value_leader_pool_refreshes_recent
                    ON low_value_leader_pool_refreshes(source_as_of DESC, status);
                """
            )
            pool_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_low_value_leader_pool)")}
            if "support_zone_low" not in pool_columns:
                self._conn.execute("ALTER TABLE company_low_value_leader_pool ADD COLUMN support_zone_low REAL")
            if "support_zone_high" not in pool_columns:
                self._conn.execute("ALTER TABLE company_low_value_leader_pool ADD COLUMN support_zone_high REAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = _loads(item.pop("metadata_json"), {})
        return item

    def active(self, market: str = "CN") -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM company_low_value_leader_pool
               WHERE market=? AND pool_status='ACTIVE'
               ORDER BY CASE valuation_status WHEN 'DEEPLY_UNDERVALUED' THEN 0 ELSE 1 END,
                        leader_score DESC, stock_code""",
            (market.upper(),),
        ).fetchall()
        return [self._row(row) for row in rows]

    def snapshots_for_as_of(self, source_as_of: str, market: str = "CN") -> list[dict[str, Any]]:
        """Read the immutable pool materialized for one research date."""
        normalized_market = market.upper()
        with self._lock:
            archived_rows = self._conn.execute(
                """SELECT payload_json FROM company_low_value_leader_pool_snapshots
                   WHERE market=? AND source_as_of=? AND pool_status='ACTIVE'""",
                (normalized_market, source_as_of),
            ).fetchall()
            current_rows = self._conn.execute(
                """SELECT * FROM company_low_value_leader_pool
                   WHERE market=? AND source_as_of=? AND pool_status='ACTIVE'""",
                (normalized_market, source_as_of),
            ).fetchall()
        by_code = {
            str(item.get("stock_code") or ""): item
            for item in (_loads(row[0], {}) for row in archived_rows)
            if isinstance(item, dict)
        }
        for row in current_rows:
            item = self._row(row)
            by_code[str(item.get("stock_code") or "")] = item
        return sorted(
            by_code.values(),
            key=lambda item: (
                0 if item.get("valuation_status") == "DEEPLY_UNDERVALUED" else 1,
                -float(item.get("leader_score") or 0),
                str(item.get("stock_code") or ""),
            ),
        )

    def active_map(self, market: str = "CN") -> dict[str, dict[str, Any]]:
        return {item["stock_code"]: item for item in self.active(market)}

    def refresh_status(self, *, source_as_of: str, source_pool_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM low_value_leader_pool_refreshes
               WHERE source_as_of=? AND source_pool_id=?""",
            (source_as_of, source_pool_id),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["errors"] = _loads(item.pop("errors_json"), [])
        return item

    def record_refresh(
        self,
        *,
        source_as_of: str,
        source_pool_id: str,
        status: str,
        active_count: int,
        changes: dict[str, int],
        errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Persist completion even when a valid run produces zero events."""
        timestamp = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO low_value_leader_pool_refreshes(
                    source_as_of,source_pool_id,status,active_count,entered_count,stayed_count,removed_count,
                    event_entered_count,event_exited_count,errors_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_as_of,source_pool_id) DO UPDATE SET
                    status=excluded.status,active_count=excluded.active_count,entered_count=excluded.entered_count,
                    stayed_count=excluded.stayed_count,removed_count=excluded.removed_count,
                    event_entered_count=excluded.event_entered_count,event_exited_count=excluded.event_exited_count,
                    errors_json=excluded.errors_json,updated_at=excluded.updated_at""",
                (
                    source_as_of, source_pool_id, status, max(0, int(active_count)),
                    int(changes.get("entered") or 0), int(changes.get("stayed") or 0), int(changes.get("removed") or 0),
                    int(changes.get("event_entered") or 0), int(changes.get("event_exited") or 0),
                    json.dumps(errors, ensure_ascii=False, sort_keys=True), timestamp, timestamp,
                ),
            )
        return self.refresh_status(source_as_of=source_as_of, source_pool_id=source_pool_id) or {}

    def history(self, market: str = "CN", stock_code: str | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        args: list[Any] = [market.upper()]
        where = ["market=?"]
        if stock_code:
            where.append("stock_code=?")
            args.append(stock_code.upper())
        args.append(max(1, min(int(limit), 500)))
        rows = self._conn.execute(
            f"""SELECT * FROM company_low_value_leader_pool WHERE {' AND '.join(where)}
                ORDER BY entered_at DESC, stock_code LIMIT ?""",  # noqa: S608
            tuple(args),
        ).fetchall()
        return [self._row(row) for row in rows]

    def events(self, market: str = "CN", *, limit: int = 100, event_date: str | None = None) -> list[dict[str, Any]]:
        args: list[Any] = [market.upper()]
        where = ["market=?"]
        if event_date:
            where.append("event_date=?")
            args.append(event_date)
        args.append(max(1, min(int(limit), 500)))
        rows = self._conn.execute(
            f"""SELECT * FROM low_value_leader_events WHERE {' AND '.join(where)}
                ORDER BY event_date DESC,
                         CASE event_type WHEN 'ENTER_LOW_VALUE' THEN 0 ELSE 1 END,
                         stock_code
                LIMIT ?""",  # noqa: S608
            tuple(args),
        ).fetchall()
        return [self._row(row) for row in rows]

    def event_summary(self, market: str = "CN", *, limit: int = 20, event_date: str | None = None) -> dict[str, Any]:
        normalized_market = market.upper()
        if event_date:
            latest_date = event_date
        else:
            row = self._conn.execute(
                "SELECT MAX(event_date) AS event_date FROM low_value_leader_events WHERE market=?",
                (normalized_market,),
            ).fetchone()
            latest_date = row["event_date"] if row and row["event_date"] else None
        summary = {"entered": 0, "exited": 0}
        if latest_date:
            counts = self._conn.execute(
                """SELECT event_type, COUNT(*) AS count FROM low_value_leader_events
                   WHERE market=? AND event_date=? GROUP BY event_type""",
                (normalized_market, latest_date),
            ).fetchall()
            for count in counts:
                if count["event_type"] == "ENTER_LOW_VALUE":
                    summary["entered"] = int(count["count"])
                elif count["event_type"] == "EXIT_LOW_VALUE":
                    summary["exited"] = int(count["count"])
        items = self.events(normalized_market, limit=limit, event_date=latest_date)
        return {"event_date": latest_date, "entered": summary["entered"], "exited": summary["exited"], "items": items, "total": len(items)}

    def _previous_status(self, market: str, stock_code: str) -> str | None:
        row = self._conn.execute(
            """SELECT valuation_status FROM company_low_value_leader_pool
               WHERE market=? AND stock_code=? ORDER BY COALESCE(removed_at, updated_at) DESC LIMIT 1""",
            (market, stock_code),
        ).fetchone()
        return str(row["valuation_status"]) if row and row["valuation_status"] else None

    def _insert_event(
        self,
        *,
        event_type: str,
        before_status: str | None,
        after_status: str,
        item: dict[str, Any],
        source_as_of: str,
        reason: str,
        timestamp: str,
    ) -> bool:
        cursor = self._conn.execute(
            """INSERT OR IGNORE INTO low_value_leader_events(
               id,market,stock_code,company_name,industry_code,industry_name,event_type,before_status,after_status,
               current_price,fair_value_mid,valuation_status,event_date,source_as_of,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"lvevent_{uuid.uuid4().hex[:20]}", str(item.get("market") or "CN"), str(item["stock_code"]),
                str(item.get("company_name") or item["stock_code"]), item.get("industry_code"), item.get("industry_name"),
                event_type, before_status, after_status, item.get("current_price"), item.get("fair_value_mid"),
                item.get("valuation_status"), source_as_of, source_as_of,
                json.dumps({"reason": reason, "source_pool_id": item.get("source_pool_id"), "metadata": item.get("metadata") or {}}, ensure_ascii=False, sort_keys=True),
                timestamp,
            ),
        )
        return cursor.rowcount == 1

    def create_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        record_id = f"lvpool_{uuid.uuid4().hex[:20]}"
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_low_value_leader_pool(
                   id,market,stock_code,company_name,industry_code,industry_name,leader_rank,leader_score,
                   current_price,fair_value_low,fair_value_mid,fair_value_high,valuation_status,
                   historical_valuation_status,support_status,support_zone_low,support_zone_high,entry_level,pool_status,source_pool_id,
                   source_as_of,entered_at,updated_at,enter_reason,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?,?,?,?,?)""",
                (
                    record_id, item["market"], item["stock_code"], item["company_name"],
                    item["industry_code"], item["industry_name"], item["leader_rank"], item["leader_score"],
                    item.get("current_price"), item.get("fair_value_low"), item.get("fair_value_mid"), item.get("fair_value_high"),
                    item["valuation_status"], item.get("historical_valuation_status"), item.get("support_status"),
                    item.get("support_zone_low"), item.get("support_zone_high"), item.get("entry_level"),
                    item["source_pool_id"], item["source_as_of"], timestamp, timestamp,
                    item["enter_reason"], json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        return self.history(item["market"], item["stock_code"], limit=1)[0]

    def update_active(self, record_id: str, item: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE company_low_value_leader_pool SET
                   company_name=?,industry_code=?,industry_name=?,leader_rank=?,leader_score=?,
                   current_price=?,fair_value_low=?,fair_value_mid=?,fair_value_high=?,valuation_status=?,
                   historical_valuation_status=?,support_status=?,support_zone_low=?,support_zone_high=?,entry_level=?,source_pool_id=?,source_as_of=?,
                   updated_at=?,metadata_json=?
                   WHERE id=? AND pool_status='ACTIVE'""",
                (
                    item["company_name"], item["industry_code"], item["industry_name"], item["leader_rank"], item["leader_score"],
                    item.get("current_price"), item.get("fair_value_low"), item.get("fair_value_mid"), item.get("fair_value_high"),
                    item["valuation_status"], item.get("historical_valuation_status"), item.get("support_status"),
                    item.get("support_zone_low"), item.get("support_zone_high"), item.get("entry_level"),
                    item["source_pool_id"], item["source_as_of"], _now(),
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True), record_id,
                ),
            )

    def mark_removed(self, record_id: str, *, reason: str, source_pool_id: str, source_as_of: str, valuation_status: str | None = None) -> None:
        timestamp = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE company_low_value_leader_pool SET pool_status='REMOVED',removed_at=?,updated_at=?,
                   remove_reason=?,source_pool_id=?,source_as_of=?,valuation_status=COALESCE(?,valuation_status)
                   WHERE id=? AND pool_status='ACTIVE'""",
                (timestamp, timestamp, reason, source_pool_id, source_as_of, valuation_status, record_id),
            )

    def synchronize_refresh(
        self,
        *,
        eligible: dict[str, dict[str, Any]],
        current_codes: set[str],
        evaluated: dict[str, str],
        error_codes: set[str],
        source_pool_id: str,
        source_as_of: str,
        remove_reason: Any,
    ) -> dict[str, int]:
        """Atomically synchronize one completed Focus Pool evaluation."""
        timestamp = _now()
        entered = stayed = removed = event_entered = event_exited = 0
        with self._lock, self._conn:
            active_rows = self._conn.execute(
                "SELECT * FROM company_low_value_leader_pool WHERE market='CN' AND pool_status='ACTIVE'"
            ).fetchall()
            active = {str(row["stock_code"]): row for row in active_rows}

            def archive_prior_projection(row: sqlite3.Row) -> None:
                """Keep a daily audit copy before changing an ACTIVE row."""
                if str(row["source_as_of"]) == source_as_of and str(row["source_pool_id"]) == source_pool_id:
                    return
                archived = self._row(row)
                self._conn.execute(
                    """INSERT OR IGNORE INTO company_low_value_leader_pool_snapshots(
                        id,market,stock_code,source_as_of,source_pool_id,pool_status,payload_json,archived_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        f"lvpoolsnap_{uuid.uuid4().hex[:20]}", str(row["market"]), str(row["stock_code"]),
                        str(row["source_as_of"]), str(row["source_pool_id"]), str(row["pool_status"]),
                        json.dumps(archived, ensure_ascii=False, sort_keys=True), timestamp,
                    ),
                )
            for code, item in eligible.items():
                row = active.get(code)
                if row:
                    # Preserve the prior materialized day before this current
                    # projection advances its source date.  This is not an
                    # entry/exit event: the company can remain continuously
                    # eligible while its daily snapshot changes.
                    archive_prior_projection(row)
                    self._conn.execute(
                        """UPDATE company_low_value_leader_pool SET
                           company_name=?,industry_code=?,industry_name=?,leader_rank=?,leader_score=?,
                           current_price=?,fair_value_low=?,fair_value_mid=?,fair_value_high=?,valuation_status=?,
                           historical_valuation_status=?,support_status=?,support_zone_low=?,support_zone_high=?,entry_level=?,source_pool_id=?,source_as_of=?,
                           updated_at=?,metadata_json=? WHERE id=? AND pool_status='ACTIVE'""",
                        (
                            item["company_name"], item["industry_code"], item["industry_name"], item["leader_rank"], item["leader_score"],
                            item.get("current_price"), item.get("fair_value_low"), item.get("fair_value_mid"), item.get("fair_value_high"),
                            item["valuation_status"], item.get("historical_valuation_status"), item.get("support_status"),
                            item.get("support_zone_low"), item.get("support_zone_high"), item.get("entry_level"),
                            item["source_pool_id"], item["source_as_of"], timestamp,
                            json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True), row["id"],
                        ),
                    )
                    stayed += 1
                    continue
                before_status = self._previous_status(str(item["market"]), code)
                self._conn.execute(
                    """INSERT INTO company_low_value_leader_pool(
                       id,market,stock_code,company_name,industry_code,industry_name,leader_rank,leader_score,
                       current_price,fair_value_low,fair_value_mid,fair_value_high,valuation_status,
                       historical_valuation_status,support_status,support_zone_low,support_zone_high,entry_level,pool_status,source_pool_id,
                       source_as_of,entered_at,updated_at,enter_reason,metadata_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?,?,?,?,?)""",
                    (
                        f"lvpool_{uuid.uuid4().hex[:20]}", item["market"], item["stock_code"], item["company_name"],
                        item["industry_code"], item["industry_name"], item["leader_rank"], item["leader_score"],
                        item.get("current_price"), item.get("fair_value_low"), item.get("fair_value_mid"), item.get("fair_value_high"),
                        item["valuation_status"], item.get("historical_valuation_status"), item.get("support_status"),
                        item.get("support_zone_low"), item.get("support_zone_high"), item.get("entry_level"),
                        item["source_pool_id"], item["source_as_of"], timestamp, timestamp, item["enter_reason"],
                        json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                entered += 1
                if self._insert_event(
                    event_type="ENTER_LOW_VALUE", before_status=before_status, after_status=str(item["valuation_status"]),
                    item=item, source_as_of=source_as_of, reason="PRICE_ENTERED_LOW_VALUE", timestamp=timestamp,
                ):
                    event_entered += 1
            for code, row in active.items():
                if code in eligible or code in error_codes:
                    continue
                archive_prior_projection(row)
                if code not in current_codes:
                    reason, valuation_status, after_status = "NO_LONGER_LEADER", None, "NO_LONGER_LEADER"
                else:
                    valuation_status = evaluated.get(code, "INSUFFICIENT_DATA")
                    reason = str(remove_reason(valuation_status))
                    after_status = valuation_status
                self._conn.execute(
                    """UPDATE company_low_value_leader_pool SET pool_status='REMOVED',removed_at=?,updated_at=?,
                       remove_reason=?,source_pool_id=?,source_as_of=?,valuation_status=COALESCE(?,valuation_status)
                       WHERE id=? AND pool_status='ACTIVE'""",
                    (timestamp, timestamp, reason, source_pool_id, source_as_of, valuation_status, row["id"]),
                )
                removed += 1
                if after_status in {"FAIR", "OVERVALUED", "DEEPLY_OVERVALUED", "NO_LONGER_LEADER"} and self._insert_event(
                    event_type="EXIT_LOW_VALUE", before_status=str(row["valuation_status"]), after_status=after_status,
                    item=self._row(row), source_as_of=source_as_of, reason=reason, timestamp=timestamp,
                ):
                    event_exited += 1
        return {"entered": entered, "stayed": stayed, "removed": removed, "event_entered": event_entered, "event_exited": event_exited}

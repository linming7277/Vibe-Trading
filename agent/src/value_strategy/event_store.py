"""Durable cursor and event persistence for Value Strategy State transitions."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class ValueStrategyEventRepository:
    """Owns event persistence; one evaluation is committed as one transaction."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        event_insert_hook: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self.event_insert_hook = event_insert_hook

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _cursor(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["state"] = _loads(result.pop("state_json", None), {})
        return result

    @staticmethod
    def _event(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("before_state_json", "after_state_json", "reasons_json", "cautions_json", "source_refs_json"):
            result[key.removesuffix("_json")] = _loads(result.pop(key, None), [] if key in {"reasons_json", "cautions_json", "source_refs_json"} else {})
        return result

    def get_cursor(self, market: str, stock_code: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM value_strategy_state_cursors WHERE market=? AND stock_code=?",
                (market, stock_code),
            ).fetchone()
        return self._cursor(row)

    def latest_cursor_research_as_of(self, market: str = "CN") -> str | None:
        """Newest research date any cursor has advanced to, for fast-path staleness checks."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(research_as_of) FROM value_strategy_state_cursors WHERE market=?", (market,),
            ).fetchone()
        value = str((row or [None])[0] or "")[:10]
        return value or None

    def cursor_scope(self, market: str = "CN") -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT stock_code FROM value_strategy_state_cursors WHERE market=? AND current_eligibility='IN_VALUE_SCOPE' ORDER BY stock_code",
                (market,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def cursor_count(self, market: str = "CN") -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM value_strategy_state_cursors WHERE market=?", (market,),
            ).fetchone()[0])

    def persist_evaluation(self, cursor: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
        """Insert all transition events and advance the cursor atomically."""
        now = _now()
        created: list[str] = []
        columns = (
            "market", "stock_code", "current_eligibility", "current_priority", "current_primary_action",
            "current_risk", "current_value_trap", "current_thesis_status", "current_thesis_authority",
            "current_leader_scope", "current_valuation_reliability", "current_price_attention",
            "current_review_pressure", "state_fingerprint", "research_as_of", "market_price_as_of",
            "state_json", "event_history_start_at", "last_checked_at", "created_at", "updated_at",
        )
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                for index, event in enumerate(events):
                    if self.event_insert_hook:
                        self.event_insert_hook(index, event)
                    values = (
                        event["id"], event["event_key"], event["market"], event["stock_code"],
                        event["event_type"], event["category"], event["severity"], event.get("direction"),
                        event.get("before_value"), event.get("after_value"), _dumps(event["before_state"]),
                        _dumps(event["after_state"]), event["primary_reason"], _dumps(event["reasons"]),
                        _dumps(event["cautions"]), event["trigger_dimension"], _dumps(event["source_refs"]),
                        event["transition_batch_id"], "OPEN", event.get("research_as_of"), event["occurred_at"],
                        None, None, now, now,
                    )
                    result = self._conn.execute(
                        """INSERT OR IGNORE INTO value_strategy_state_events(
                        id,event_key,market,stock_code,event_type,category,severity,direction,before_value,
                        after_value,before_state_json,after_state_json,primary_reason,reasons_json,cautions_json,
                        trigger_dimension,source_refs_json,transition_batch_id,status,research_as_of,occurred_at,
                        acknowledged_at,closed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values,
                    )
                    if result.rowcount:
                        created.append(event["id"])
                cursor_values = []
                for key in columns:
                    value = cursor.get(key)
                    if key == "state_json":
                        value = _dumps(cursor["state"])
                    elif key in {"last_checked_at", "updated_at"}:
                        value = now
                    elif key == "created_at":
                        value = cursor.get(key) or now
                    cursor_values.append(value)
                updates = ",".join(f"{key}=excluded.{key}" for key in columns if key not in {"market", "stock_code", "created_at", "event_history_start_at"})
                self._conn.execute(
                    f"INSERT INTO value_strategy_state_cursors({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                    f"ON CONFLICT(market,stock_code) DO UPDATE SET {updates}",
                    tuple(cursor_values),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return created

    def list_events(
        self, *, market: str | None = None, stock_code: str | None = None,
        status: str | None = None, event_type: str | None = None,
        since: str | None = None, research_as_of: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses, values = [], []
        for column, value in (("market", market), ("stock_code", stock_code), ("status", status), ("event_type", event_type)):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        if since:
            clauses.append("occurred_at>=?")
            values.append(since)
        if research_as_of:
            clauses.append("research_as_of=?")
            values.append(research_as_of)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM value_strategy_state_events{where} ORDER BY occurred_at DESC,id DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [self._event(row) for row in rows if row is not None]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM value_strategy_state_events WHERE id=?", (event_id,)).fetchone()
        return self._event(row)

    def transition_lifecycle(self, event_id: str, target: str) -> dict[str, Any] | None:
        if target not in {"ACKNOWLEDGED", "CLOSED"}:
            raise ValueError("unsupported lifecycle target")
        now = _now()
        with self._lock, self._conn:
            row = self._conn.execute("SELECT status FROM value_strategy_state_events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                return None
            current = str(row[0])
            if target == "ACKNOWLEDGED":
                if current == "CLOSED":
                    raise ValueError("已关闭事件不能再确认")
                if current == "OPEN":
                    self._conn.execute(
                        "UPDATE value_strategy_state_events SET status='ACKNOWLEDGED',acknowledged_at=?,updated_at=? WHERE id=?",
                        (now, now, event_id),
                    )
            elif current != "CLOSED":
                self._conn.execute(
                    "UPDATE value_strategy_state_events SET status='CLOSED',closed_at=?,updated_at=? WHERE id=?",
                    (now, now, event_id),
                )
        return self.get_event(event_id)

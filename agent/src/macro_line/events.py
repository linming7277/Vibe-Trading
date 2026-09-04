"""Macro event store: detects Regime/axis tier changes between snapshots.

Independent table (NOT value_strategy_state_events) per macro-line V1 plan §1.1.
Events are tier-crossing only — same-tier score wiggle never fires (plan §1.2).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

FORMULA_VERSION = "macro-line-events-v1.0.0"

_AXIS_LABELS = {
    "growth": "经济增长",
    "inflation": "通胀",
    "liquidity": "流动性",
    "credit": "信用",
    "financial_conditions": "金融条件",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _axis_tier(score: Any) -> str:
    """Same thresholds as the /macro page (plan §Phase1 alignment)."""
    if score is None:
        return "资料不足"
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "资料不足"
    if value >= 60:
        return "偏暖"
    if value <= 40:
        return "偏冷"
    return "中性"


class MacroEventStore:
    """SQLite-backed macro event store; independent from strategy events."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS macro_events (
                    id TEXT PRIMARY KEY,
                    research_as_of TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('MACRO_REGIME_CHANGED','MACRO_AXIS_CHANGED')),
                    axis_key TEXT,
                    from_value TEXT NOT NULL,
                    to_value TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL DEFAULT 'DAILY_DIGEST',
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    formula_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_macro_events_date ON macro_events(research_as_of DESC, created_at DESC);
            """)

    def close(self) -> None:
        self._conn.close()

    def latest_snapshot_before(self, as_of: str) -> dict[str, Any] | None:
        import json as _json

        row = self._conn.execute(
            "SELECT * FROM macro_snapshots WHERE as_of<? AND status IN ('ready','partial') ORDER BY as_of DESC LIMIT 1",
            (as_of,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("axes_json", "states_json"):
            raw = result.pop(key, None)
            parsed_key = key.removesuffix("_json")
            try:
                result[parsed_key] = _json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                result[parsed_key] = {}
        return result

    def diff_and_record(self, current: dict[str, Any]) -> list[dict[str, Any]]:
        """Compare current snapshot axes/regime vs the previous one; record events.

        Returns the list of events created (may be empty — that is the norm).
        """
        research_as_of = str(current.get("as_of") or "")
        if not research_as_of:
            return []
        # Idempotent: if events already exist for this date, don't duplicate.
        existing = self.open_events_for_date(research_as_of)
        if existing:
            return []
        previous = self.latest_snapshot_before(research_as_of)
        events: list[dict[str, Any]] = []

        current_regime = str(current.get("regime") or "")
        previous_regime = str((previous or {}).get("regime") or "")
        if previous_regime and current_regime and previous_regime != current_regime:
            events.append(self._insert(
                research_as_of, "MACRO_REGIME_CHANGED", None, previous_regime, current_regime,
            ))

        current_axes = dict(current.get("axes") or {})
        previous_axes = dict((previous or {}).get("axes") or {})
        for key, label in _AXIS_LABELS.items():
            cur_tier = _axis_tier(current_axes.get(key))
            prev_tier = _axis_tier(previous_axes.get(key))
            if cur_tier != prev_tier:
                events.append(self._insert(
                    research_as_of, "MACRO_AXIS_CHANGED", key, prev_tier, cur_tier,
                ))
        return events

    def _insert(self, research_as_of: str, event_type: str, axis_key: str | None,
                from_value: str, to_value: str) -> dict[str, Any]:
        event_id = f"macro_evt_{uuid.uuid4().hex[:20]}"
        with self._conn:
            self._conn.execute(
                """INSERT INTO macro_events(
                    id, research_as_of, event_type, axis_key, from_value, to_value,
                    delivery_mode, status, formula_version, created_at
                ) VALUES(?,?,?,?,?,?, 'DAILY_DIGEST', 'OPEN', ?, ?)""",
                (event_id, research_as_of, event_type, axis_key, from_value, to_value,
                 FORMULA_VERSION, _now()),
            )
        return {
            "id": event_id, "research_as_of": research_as_of, "event_type": event_type,
            "axis_key": axis_key, "from_value": from_value, "to_value": to_value,
            "delivery_mode": "DAILY_DIGEST", "status": "OPEN",
        }

    def open_events_for_date(self, research_as_of: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM macro_events WHERE research_as_of=? ORDER BY created_at",
            (research_as_of,),
        ).fetchall()
        return [dict(row) for row in rows]

    def events_for_summary(self, research_as_of: str) -> list[dict[str, Any]]:
        """OPEN events for the given research day (SPEC §4.3 / M1-09)."""
        rows = self._conn.execute(
            "SELECT * FROM macro_events WHERE research_as_of=? AND status='OPEN' ORDER BY created_at",
            (research_as_of,),
        ).fetchall()
        return [dict(row) for row in rows]

    def undigested_events(self, before_as_of: str) -> list[dict[str, Any]]:
        """OPEN events at or before the given date (SPEC §4.3: same-day visible)."""
        rows = self._conn.execute(
            "SELECT * FROM macro_events WHERE research_as_of<=? AND status='OPEN' ORDER BY research_as_of DESC, created_at DESC LIMIT 10",
            (before_as_of,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close_events(self, research_as_of: str) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE macro_events SET status='CLOSED' WHERE research_as_of<=? AND status='OPEN'",
                (research_as_of,),
            )
            return cursor.rowcount


def event_to_chinese(event: dict[str, Any]) -> str:
    """Human-readable event description for Daily Brief / Hermes (no trading words)."""
    if event["event_type"] == "MACRO_REGIME_CHANGED":
        return f"宏观环境由「{event['from_value']}」变为「{event['to_value']}」"
    label = _AXIS_LABELS.get(str(event.get("axis_key") or ""), str(event.get("axis_key") or ""))
    return f"{label}由「{event['from_value']}」变为「{event['to_value']}」"

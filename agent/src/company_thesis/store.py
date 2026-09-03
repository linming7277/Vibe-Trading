"""SQLite repository for immutable Company Thesis versions."""

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


def _loads(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _input_hash(payload: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class CompanyThesisRepository:
    """Owns thesis reads and append-only version writes in ``research.db``."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_theses)")}
            if "authority_status" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN authority_status TEXT")
                # Historical authority cannot be inferred safely.  Legacy is
                # intentionally distinct from human confirmation.
                self._conn.execute("UPDATE company_theses SET authority_status='LEGACY_UNVERIFIED' WHERE authority_status IS NULL")
            if "source_draft_id" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN source_draft_id TEXT")
            # PIT remediation: a thesis is replay-usable only from the day the
            # system actually reached that conclusion.  ``valid_from`` is the
            # creation day (fact, not the evidence's older source_data_as_of);
            # ``valid_to`` closes a superseded version.  Factual backfill only.
            if "valid_from" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN valid_from TEXT")
                self._conn.execute(
                    "UPDATE company_theses SET valid_from=substr(created_at,1,10) WHERE valid_from IS NULL",
                )
            if "valid_to" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN valid_to TEXT")
            if "supporting_conditions_json" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN supporting_conditions_json TEXT NOT NULL DEFAULT '[]'")
            if "key_metrics_to_monitor_json" not in columns:
                self._conn.execute("ALTER TABLE company_theses ADD COLUMN key_metrics_to_monitor_json TEXT NOT NULL DEFAULT '[]'")
            # One immutable lifecycle event per thesis version: the system's own
            # record that this conclusion existed as of ``created_at``.  The
            # pre-existing company_thesis_history only records v2+ transitions,
            # so creation had no history carrier at all until now.
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_thesis_lifecycle_events (
                    event_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    thesis_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    authority_status TEXT,
                    confidence TEXT,
                    source_data_as_of TEXT,
                    valid_from TEXT,
                    valid_to TEXT,
                    change_type TEXT NOT NULL,
                    input_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(market, stock_code, thesis_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_thesis_lifecycle_company
                    ON company_thesis_lifecycle_events(market, stock_code, valid_from);
                """
            )
            self._safe_copy_conditions_from_source_drafts()

    def _safe_copy_conditions_from_source_drafts(self) -> None:
        """Copy draft assumptions/metrics only when source_draft_id still points at that draft.

        Does not invent conditions.  Skips rows that already have either JSON list.
        """
        drafts_exist = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_thesis_drafts'",
        ).fetchone()
        if not drafts_exist:
            return
        rows = self._conn.execute(
            """SELECT thesis_id, source_draft_id, supporting_conditions_json, key_metrics_to_monitor_json
               FROM company_theses WHERE source_draft_id IS NOT NULL AND TRIM(source_draft_id) != ''""",
        ).fetchall()
        for row in rows:
            supporting = _loads(row["supporting_conditions_json"])
            metrics = _loads(row["key_metrics_to_monitor_json"])
            if supporting or metrics:
                continue
            draft = self._conn.execute(
                """SELECT draft_id, key_assumptions_json, key_metrics_to_monitor_json, confirmed_thesis_id
                   FROM company_thesis_drafts WHERE draft_id=?""",
                (row["source_draft_id"],),
            ).fetchone()
            if draft is None:
                continue
            confirmed = str(draft["confirmed_thesis_id"] or "")
            if confirmed and confirmed != row["thesis_id"]:
                continue
            assumptions = _loads(draft["key_assumptions_json"])
            draft_metrics = _loads(draft["key_metrics_to_monitor_json"])
            if not assumptions and not draft_metrics:
                continue
            self._conn.execute(
                """UPDATE company_theses
                   SET supporting_conditions_json=?, key_metrics_to_monitor_json=?
                   WHERE thesis_id=?""",
                (
                    json.dumps(assumptions, ensure_ascii=False, sort_keys=True),
                    json.dumps(draft_metrics, ensure_ascii=False, sort_keys=True),
                    row["thesis_id"],
                ),
            )

    def backfill_lifecycle_events(self) -> dict[str, int]:
        """Factual migration backfill: one event per pre-existing thesis version.

        ``valid_from`` uses the row's own ``created_at`` day — the earliest day
        the system could have acted on that conclusion — never the (older)
        ``source_data_as_of`` of its evidence.  Idempotent via UNIQUE.
        """
        with self._lock, self._conn:
            rows = self._conn.execute(
                """SELECT market,stock_code,thesis_id,version,status,authority_status,confidence,
                          source_data_as_of,valid_from,valid_to,created_at,
                          title,core_thesis,invalid_conditions_json
                   FROM company_theses""",
            ).fetchall()
            inserted = 0
            for row in rows:
                payload = {
                    "title": row["title"], "core_thesis": row["core_thesis"], "status": row["status"],
                    "confidence": row["confidence"], "source_data_as_of": row["source_data_as_of"],
                    "authority_status": row["authority_status"],
                    "invalid_conditions": _loads(row["invalid_conditions_json"]),
                }
                cursor = self._conn.execute(
                    """INSERT OR IGNORE INTO company_thesis_lifecycle_events(
                        event_id,market,stock_code,thesis_id,version,status,authority_status,confidence,
                        source_data_as_of,valid_from,valid_to,change_type,input_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"theslife_{uuid.uuid4().hex[:20]}", row["market"], row["stock_code"],
                        row["thesis_id"], int(row["version"]), row["status"], row["authority_status"],
                        row["confidence"], row["source_data_as_of"],
                        row["valid_from"] or str(row["created_at"])[:10], row["valid_to"],
                        "MIGRATION_BACKFILL", _input_hash(payload), row["created_at"],
                    ),
                )
                inserted += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            return {"thesis_versions": len(rows), "events_written": inserted}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["is_current"] = bool(item["is_current"])
        item["invalid_conditions"] = _loads(item.pop("invalid_conditions_json"))
        item["supporting_conditions"] = _loads(item.pop("supporting_conditions_json", None))
        item["key_metrics_to_monitor"] = _loads(item.pop("key_metrics_to_monitor_json", None))
        return item

    def get_current_thesis(self, market: str, stock_code: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                """SELECT * FROM company_theses WHERE market=? AND stock_code=? AND is_current=1
                   ORDER BY version DESC LIMIT 1""",
                (market, stock_code),
            ).fetchone())

    def list_thesis_versions(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_theses WHERE market=? AND stock_code=?
                   ORDER BY version DESC""", (market, stock_code),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def get_thesis_by_id(self, thesis_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_theses WHERE thesis_id=?", (thesis_id,),
            ).fetchone())

    def create_initial_thesis(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert version 1 only; existing versions must use ``create_new_version``."""
        market, stock_code = payload["market"], payload["stock_code"]
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT 1 FROM company_theses WHERE market=? AND stock_code=? LIMIT 1",
                    (market, stock_code),
                ).fetchone()
                if existing:
                    raise ValueError("current thesis already exists; create new version instead")
                created = self._insert(payload, version=1, is_current=True)
                self._conn.commit()
                return created
            except Exception:
                self._conn.rollback()
                raise

    def create_new_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                created = self._create_new_version_in_transaction(payload)
                self._conn.commit()
                return created
            except Exception:
                self._conn.rollback()
                raise

    def _create_new_version_in_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the next version using the caller's already-open transaction."""
        market, stock_code = payload["market"], payload["stock_code"]
        current = self._conn.execute(
            "SELECT * FROM company_theses WHERE market=? AND stock_code=? AND is_current=1",
            (market, stock_code),
        ).fetchone()
        if not current:
            raise KeyError("current thesis does not exist; create initial thesis first")
        previous = self._row(current) or {}
        next_version = int(previous["version"]) + 1
        actor = str(payload.get("updated_by") or payload.get("created_by") or "").upper()
        if str(previous.get("authority_status") or "") == "HUMAN_CONFIRMED" and actor != "HUMAN":
            payload["invalid_conditions"] = list(previous.get("invalid_conditions") or [])
            payload["supporting_conditions"] = list(previous.get("supporting_conditions") or [])
            payload["key_metrics_to_monitor"] = list(previous.get("key_metrics_to_monitor") or [])
        else:
            if payload.get("supporting_conditions") is None:
                payload["supporting_conditions"] = list(previous.get("supporting_conditions") or [])
            if payload.get("key_metrics_to_monitor") is None:
                payload["key_metrics_to_monitor"] = list(previous.get("key_metrics_to_monitor") or [])
        evidence_ids = self._validate_history_evidence_ids(payload.get("evidence_ids") or [], previous)
        self._conn.execute(
            "UPDATE company_theses SET is_current=0 WHERE market=? AND stock_code=? AND is_current=1",
            (market, stock_code),
        )
        created = self._insert(payload, version=next_version, is_current=True)
        # Close the superseded version on the day the replacement took effect.
        self._conn.execute(
            "UPDATE company_theses SET valid_to=? WHERE thesis_id=?",
            (created.get("valid_from"), previous["thesis_id"]),
        )
        self._insert_history(previous, created, payload, evidence_ids)
        self._conn.execute(
            "UPDATE company_thesis_reviews SET is_stale=1 WHERE thesis_id=? AND is_stale=0",
            (previous["thesis_id"],),
        )
        return created

    def _validate_history_evidence_ids(self, evidence_ids: list[str], previous: dict[str, Any]) -> list[str]:
        """Capture only evidence explicitly attached to the version being replaced."""
        normalized: list[str] = []
        for raw_id in evidence_ids:
            evidence_id = str(raw_id or "").strip()
            if not evidence_id or evidence_id in normalized:
                continue
            row = self._conn.execute(
                """SELECT thesis_id,market,stock_code FROM company_thesis_evidence WHERE evidence_id=?""",
                (evidence_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"history evidence not found: {evidence_id}")
            if (row["thesis_id"], row["market"], row["stock_code"]) != (
                previous["thesis_id"], previous["market"], previous["stock_code"],
            ):
                raise ValueError("history evidence must belong to the previous thesis version")
            normalized.append(evidence_id)
        return normalized

    def _insert_history(self, previous: dict[str, Any], created: dict[str, Any],
                        payload: dict[str, Any], evidence_ids: list[str]) -> None:
        timestamp = _now()
        actor = payload["updated_by"]
        trigger_type = payload.get("history_trigger_type") or (
            "MANUAL" if actor == "HUMAN" else "SYSTEM" if actor == "SYSTEM" else "AGENT"
        )
        self._conn.execute(
            """INSERT INTO company_thesis_history(
                history_id,market,stock_code,from_thesis_id,to_thesis_id,from_version,to_version,
                old_status,new_status,old_confidence,new_confidence,change_type,change_reason,
                trigger_type,trigger_ref,evidence_ids_json,created_by,created_at,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"thesis_history_{uuid.uuid4().hex[:20]}", previous["market"], previous["stock_code"],
                previous["thesis_id"], created["thesis_id"], previous["version"], created["version"],
                previous["status"], created["status"], previous["confidence"], created["confidence"],
                "VERSION_CREATED", payload["change_reason"], trigger_type, payload.get("trigger_ref"),
                json.dumps(evidence_ids, ensure_ascii=False), actor, timestamp,
                json.dumps(payload.get("history_metadata") or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _insert(self, payload: dict[str, Any], *, version: int, is_current: bool) -> dict[str, Any]:
        thesis_id, timestamp = f"thesis_{uuid.uuid4().hex[:20]}", _now()
        # The conclusion exists from its creation day onward.  This is the
        # replay gate: evidence may legitimately be older (source_data_as_of),
        # but the system could not have acted on the conclusion earlier.
        valid_from = str(payload.get("valid_from") or timestamp[:10])
        self._conn.execute(
            """INSERT INTO company_theses(
                thesis_id,market,stock_code,title,core_thesis,status,confidence,
                invalid_conditions_json,change_reason,version,is_current,created_at,updated_at,
                created_by,updated_by,source_data_as_of,authority_status,source_draft_id,valid_from,
                supporting_conditions_json,key_metrics_to_monitor_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                thesis_id, payload["market"], payload["stock_code"], payload["title"],
                payload["core_thesis"], payload["status"], payload["confidence"],
                json.dumps(payload["invalid_conditions"], ensure_ascii=False, sort_keys=True),
                payload.get("change_reason"), version, int(is_current), timestamp, timestamp,
                payload["created_by"], payload["updated_by"], payload.get("source_data_as_of"),
                payload.get("authority_status", "LEGACY_UNVERIFIED"), payload.get("source_draft_id"),
                valid_from,
                json.dumps(payload.get("supporting_conditions") or [], ensure_ascii=False, sort_keys=True),
                json.dumps(payload.get("key_metrics_to_monitor") or [], ensure_ascii=False, sort_keys=True),
            ),
        )
        self._insert_lifecycle_event(dict(payload), thesis_id=thesis_id, version=version,
                                     valid_from=valid_from, created_at=timestamp,
                                     change_type="CREATE" if version == 1 else "VERSION_CREATED")
        return self.get_thesis_by_id(thesis_id) or {}

    def _insert_lifecycle_event(self, payload: dict[str, Any], *, thesis_id: str, version: int,
                                valid_from: str, created_at: str, change_type: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO company_thesis_lifecycle_events(
                event_id,market,stock_code,thesis_id,version,status,authority_status,confidence,
                source_data_as_of,valid_from,valid_to,change_type,input_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"theslife_{uuid.uuid4().hex[:20]}", payload["market"], payload["stock_code"],
                thesis_id, int(version), payload["status"], payload.get("authority_status"),
                payload["confidence"], payload.get("source_data_as_of"), valid_from, None,
                change_type,
                _input_hash({
                    "title": payload.get("title"), "core_thesis": payload.get("core_thesis"),
                    "status": payload["status"], "confidence": payload["confidence"],
                    "source_data_as_of": payload.get("source_data_as_of"),
                    "authority_status": payload.get("authority_status"),
                    "invalid_conditions": payload.get("invalid_conditions") or [],
                    "supporting_conditions": payload.get("supporting_conditions") or [],
                    "key_metrics_to_monitor": payload.get("key_metrics_to_monitor") or [],
                }),
                created_at,
            ),
        )

    def thesis_as_of(self, market: str, stock_code: str, replay_as_of: str) -> dict[str, Any] | None:
        """PIT-safe thesis selection for a replay date.

        Usable requires BOTH gates: the conclusion existed (``valid_from``)
        and its evidence was visible (``source_data_as_of``) on or before the
        replay date.  An older ``source_data_as_of`` alone must not leak a
        conclusion the system had not yet reached.
        """
        day = str(replay_as_of)[:10]
        row = self._conn.execute(
            """SELECT * FROM company_theses
               WHERE market=? AND stock_code=?
                 AND COALESCE(valid_from, substr(created_at,1,10)) <= ?
                 AND COALESCE(source_data_as_of, substr(created_at,1,10)) <= ?
                 AND (valid_to IS NULL OR valid_to >= ?)
               ORDER BY COALESCE(valid_from, substr(created_at,1,10)) DESC, version DESC
               LIMIT 1""",
            (market, stock_code, day, day, day),
        ).fetchone()
        return self._row(row)

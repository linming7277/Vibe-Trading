"""SQLite persistence for calculation profiles, track snapshots and research monitoring."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


MODEL_KEYS = ("policy_cycle", "economic_cycle", "liquidity", "earnings_climate")
DEFAULT_PROFILES = (
    ("profile_policy", "政策与产业周期（Legacy）", "single", {"policy_cycle": 1.0}),
    ("profile_economic", "经济周期（Legacy）", "single", {"economic_cycle": 1.0}),
    ("profile_liquidity", "流动性（Legacy）", "single", {"liquidity": 1.0}),
    ("profile_earnings", "盈利景气（Legacy）", "single", {"earnings_climate": 1.0}),
    ("profile_balanced", "均衡组合（Legacy）", "composite", {key: 0.25 for key in MODEL_KEYS}),
    ("profile_value_line_v2", "价值线 V2 标准方案", "composite", {key: 0.25 for key in MODEL_KEYS}),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def normalize_weights(weights: dict[str, Any]) -> dict[str, float]:
    unknown = set(weights) - set(MODEL_KEYS)
    if unknown:
        raise ValueError(f"unknown value model(s): {', '.join(sorted(unknown))}")
    cleaned = {key: float(value) for key, value in weights.items() if float(value) > 0}
    if not cleaned:
        raise ValueError("at least one model weight must be positive")
    total = sum(cleaned.values())
    return {key: round(value / total, 8) for key, value in cleaned.items()}


class ValueWorkspaceStore:
    JSON_COLUMNS = {
        "model_weights_json": "model_weights", "component_scores_json": "component_scores",
        "quality_flags_json": "quality_flags", "valuation_json": "valuation",
        "conditions_json": "conditions", "channels_json": "channels", "payload_json": "payload",
        "diff_json": "diff", "missing_fields_json": "missing_fields",
        "sources_json": "sources", "evidence_ids_json": "evidence_ids",
        "rules_json": "rules", "inputs_json": "inputs", "reasons_json": "reasons",
    }

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        initializer = ResearchWorkspaceStore(self.db_path, seed=False)
        initializer.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_db(self) -> None:
        with self._lock:
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(engine_runs)")}
            if "profile_id" not in columns:
                self._conn.execute("ALTER TABLE engine_runs ADD COLUMN profile_id TEXT")
            if "profile_version" not in columns:
                self._conn.execute("ALTER TABLE engine_runs ADD COLUMN profile_version INTEGER")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS value_calculation_profiles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
                    model_weights_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    is_default INTEGER NOT NULL DEFAULT 0, is_builtin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS value_tracks (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, profile_id TEXT NOT NULL,
                    track_id TEXT NOT NULL, track_name TEXT NOT NULL, category TEXT NOT NULL,
                    base_score REAL, coverage REAL NOT NULL, rank INTEGER NOT NULL,
                    component_scores_json TEXT NOT NULL, quality_flags_json TEXT NOT NULL,
                    source_status TEXT NOT NULL, data_as_of TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(engine_run_id, track_id),
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_value_tracks_run_rank ON value_tracks(engine_run_id, rank);
                CREATE TABLE IF NOT EXISTS value_track_leaders (
                    id TEXT PRIMARY KEY, engine_run_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, name TEXT NOT NULL, leader_type TEXT NOT NULL,
                    base_score REAL, coverage REAL NOT NULL, rank INTEGER NOT NULL,
                    component_scores_json TEXT NOT NULL, quality_flags_json TEXT NOT NULL,
                    research_status TEXT NOT NULL DEFAULT 'idle', created_at TEXT NOT NULL,
                    UNIQUE(engine_run_id, track_id, symbol),
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_value_leaders_track ON value_track_leaders(engine_run_id, track_id, rank);
                CREATE TABLE IF NOT EXISTS company_research_batches (
                    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    engine_run_id TEXT NOT NULL, profile_id TEXT NOT NULL, track_id TEXT NOT NULL,
                    status TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    template_version TEXT NOT NULL, concurrency INTEGER NOT NULL,
                    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                    FOREIGN KEY(engine_run_id) REFERENCES engine_runs(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS company_research_jobs (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    dossier_id TEXT, report_id TEXT, valuation_status TEXT NOT NULL DEFAULT 'queued',
                    valuation_json TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, symbol), FOREIGN KEY(batch_id) REFERENCES company_research_batches(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_company_research_jobs_batch ON company_research_jobs(batch_id, status);
                CREATE TABLE IF NOT EXISTS value_entry_monitors (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT NOT NULL,
                    engine_run_id TEXT NOT NULL, track_id TEXT NOT NULL, research_job_id TEXT NOT NULL,
                    status TEXT NOT NULL, conditions_json TEXT NOT NULL, channels_json TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL, last_checked_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(research_job_id) REFERENCES company_research_jobs(id) ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_value_monitors_status ON value_entry_monitors(status, symbol);
                CREATE TABLE IF NOT EXISTS value_monitor_events (
                    id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
                    message TEXT NOT NULL, payload_json TEXT NOT NULL, triggered_at TEXT NOT NULL,
                    acknowledged_at TEXT, FOREIGN KEY(monitor_id) REFERENCES value_entry_monitors(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL, channel TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', attempted_at TEXT NOT NULL,
                    UNIQUE(event_id, channel), FOREIGN KEY(event_id) REFERENCES value_monitor_events(id) ON DELETE CASCADE
                );
                """
            )
            monitor_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(value_entry_monitors)")}
            for column, declaration in (
                ("universe_id", "TEXT"),
                ("position_state", "TEXT NOT NULL DEFAULT 'watching'"),
                ("signal_state", "TEXT NOT NULL DEFAULT 'watching'"),
                ("risk_preset", "TEXT NOT NULL DEFAULT 'balanced'"),
                ("thesis_invalidated", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in monitor_columns:
                    self._conn.execute(f"ALTER TABLE value_entry_monitors ADD COLUMN {column} {declaration}")
            event_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(value_monitor_events)")}
            for column, declaration in (
                ("evaluation_id", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'open'"),
                ("acknowledgement_note", "TEXT NOT NULL DEFAULT ''"),
                ("resolved_at", "TEXT"),
            ):
                if column not in event_columns:
                    self._conn.execute(f"ALTER TABLE value_monitor_events ADD COLUMN {column} {declaration}")
            timestamp = now()
            self._conn.execute(
                """INSERT OR IGNORE INTO value_research_automation(
                       id,enabled,timezone,run_time,max_retries,retry_minutes,updated_at
                   ) VALUES('default',0,'Asia/Shanghai','16:45',3,20,?)""",
                (timestamp,),
            )
            for profile_id, name, mode, weights in DEFAULT_PROFILES:
                self._conn.execute(
                    """INSERT OR IGNORE INTO value_calculation_profiles
                       (id,name,mode,model_weights_json,version,is_default,is_builtin,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (profile_id, name, mode, json.dumps(weights), 1, int(profile_id == "profile_value_line_v2"), 1, timestamp, timestamp),
                )
            # Preserve V1 profiles and historical references, while making the
            # versioned V2 formula package the starting point for new work.
            self._conn.execute("UPDATE value_calculation_profiles SET is_default=0 WHERE id<>?", ("profile_value_line_v2",))
            self._conn.execute(
                "UPDATE value_calculation_profiles SET is_default=1,name=?,updated_at=? WHERE id=?",
                ("价值线 V2 标准方案", timestamp, "profile_value_line_v2"),
            )
            legacy_names = {
                profile_id: name for profile_id, name, _mode, _weights in DEFAULT_PROFILES
                if profile_id != "profile_value_line_v2"
            }
            self._conn.executemany(
                "UPDATE value_calculation_profiles SET name=? WHERE id=? AND is_builtin=1",
                [(name, profile_id) for profile_id, name in legacy_names.items()],
            )
            self._conn.commit()

    @classmethod
    def row(cls, raw: sqlite3.Row | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        item = dict(raw)
        for column, public in cls.JSON_COLUMNS.items():
            if column in item:
                value = item.pop(column)
                try:
                    item[public] = json.loads(value or "{}")
                except json.JSONDecodeError:
                    item[public] = {}
        for field in ("is_default", "is_builtin", "cancel_requested", "enabled", "thesis_invalidated"):
            if field in item:
                item[field] = bool(item[field])
        return item

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM value_calculation_profiles ORDER BY is_default DESC,is_builtin DESC,updated_at DESC").fetchall()
        return [self.row(value) or {} for value in rows]

    def get_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        if not profile_id:
            raw = self._conn.execute("SELECT * FROM value_calculation_profiles ORDER BY is_default DESC,updated_at DESC LIMIT 1").fetchone()
        else:
            raw = self._conn.execute("SELECT * FROM value_calculation_profiles WHERE id=?", (profile_id,)).fetchone()
        return self.row(raw)

    def save_profile(self, *, name: str, mode: str, weights: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        if mode not in {"single", "composite"}:
            raise ValueError("mode must be single or composite")
        normalized = normalize_weights(weights)
        if mode == "single" and len(normalized) != 1:
            raise ValueError("single profile must select exactly one model")
        timestamp = now()
        with self._lock:
            if profile_id:
                current = self.get_profile(profile_id)
                if not current:
                    raise KeyError("calculation profile not found")
                self._conn.execute(
                    "UPDATE value_calculation_profiles SET name=?,mode=?,model_weights_json=?,version=version+1,updated_at=? WHERE id=?",
                    (name.strip(), mode, json.dumps(normalized), timestamp, profile_id),
                )
            else:
                profile_id = new_id("profile")
                self._conn.execute(
                    "INSERT INTO value_calculation_profiles VALUES(?,?,?,?,?,?,?,?,?)",
                    (profile_id, name.strip(), mode, json.dumps(normalized), 1, 0, 0, timestamp, timestamp),
                )
            self._conn.commit()
        return self.get_profile(profile_id) or {}

    def delete_profile(self, profile_id: str) -> None:
        profile = self.get_profile(profile_id)
        if not profile:
            raise KeyError("calculation profile not found")
        if profile["is_builtin"]:
            raise ValueError("built-in profiles cannot be deleted")
        used = self._conn.execute("SELECT 1 FROM engine_runs WHERE profile_id=? LIMIT 1", (profile_id,)).fetchone()
        if used:
            raise ValueError("profile is referenced by a historical run")
        self._conn.execute("DELETE FROM value_calculation_profiles WHERE id=?", (profile_id,))
        self._conn.commit()

    def set_run_profile(self, run_id: str, profile: dict[str, Any]) -> None:
        self._conn.execute("UPDATE engine_runs SET profile_id=?,profile_version=? WHERE id=?", (profile["id"], profile["version"], run_id))
        self._conn.commit()

    def replace_tracks(self, run_id: str, profile_id: str, tracks: list[dict[str, Any]]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM value_track_leaders WHERE engine_run_id=?", (run_id,))
            self._conn.execute("DELETE FROM value_tracks WHERE engine_run_id=?", (run_id,))
            for item in tracks:
                self._conn.execute(
                    "INSERT INTO value_tracks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id("track"), run_id, profile_id, item["track_id"], item["track_name"], item["category"],
                     item.get("base_score"), item["coverage"], item["rank"], json.dumps(item["component_scores"]),
                     json.dumps(item.get("quality_flags", [])), item["source_status"], item["data_as_of"], now()),
                )
                for leader in item.get("leaders", []):
                    self._conn.execute(
                        "INSERT INTO value_track_leaders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id("leader"), run_id, item["track_id"], leader["symbol"], leader["name"], leader["leader_type"],
                         leader.get("base_score"), leader["coverage"], leader["rank"], json.dumps(leader["component_scores"]),
                         json.dumps(leader.get("quality_flags", [])), "idle", now()),
                    )
            self._conn.commit()

    def list_tracks(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM value_tracks WHERE engine_run_id=? ORDER BY rank", (run_id,)).fetchall()
        return [self.row(value) or {} for value in rows]

    def list_leaders(self, run_id: str, track_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM value_track_leaders WHERE engine_run_id=? AND track_id=? ORDER BY rank", (run_id, track_id)).fetchall()
        return [self.row(value) or {} for value in rows]

    def create_universe(
        self, *, idempotency_key: str, run_id: str, profile_id: str,
        candidate_limit: int, leader_limit: int, data_as_of: str,
        formula_version: str, members: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        existing = self._conn.execute(
            "SELECT id FROM value_research_universes WHERE idempotency_key=?", (idempotency_key,),
        ).fetchone()
        if existing:
            return self.get_universe(existing["id"]) or {}, False
        universe_id, timestamp = new_id("universe"), now()
        track_count = len({item["track_id"] for item in members})
        company_count = len({item["symbol"] for item in members})
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """INSERT INTO value_research_universes(
                           id,idempotency_key,engine_run_id,profile_id,candidate_limit,leader_limit,
                           status,data_as_of,formula_version,track_count,membership_count,company_count,
                           created_at,activated_at,archived_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (universe_id, idempotency_key, run_id, profile_id, candidate_limit, leader_limit,
                     "draft", data_as_of, formula_version, track_count, len(members), company_count,
                     timestamp, None, None),
                )
                self._conn.executemany(
                    """INSERT INTO value_research_universe_members(
                           id,universe_id,track_id,track_name,track_rank,symbol,name,leader_rank,
                           leader_type,leader_score,leader_coverage,inclusion_reason,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(
                        new_id("membership"), universe_id, item["track_id"], item["track_name"],
                        item["track_rank"], item["symbol"], item["name"], item["leader_rank"],
                        item["leader_type"], item.get("leader_score"), item["leader_coverage"],
                        item["inclusion_reason"], timestamp,
                    ) for item in members],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_universe(universe_id) or {}, True

    def get_universe(self, universe_id: str) -> dict[str, Any] | None:
        universe = self.row(self._conn.execute(
            "SELECT * FROM value_research_universes WHERE id=?", (universe_id,),
        ).fetchone())
        if not universe:
            return None
        members = [self.row(row) or {} for row in self._conn.execute(
            """SELECT * FROM value_research_universe_members
               WHERE universe_id=? ORDER BY track_rank,leader_rank,symbol""", (universe_id,),
        ).fetchall()]
        universe["members"] = members
        companies: dict[str, dict[str, Any]] = {}
        for member in members:
            company = companies.setdefault(member["symbol"], {
                "symbol": member["symbol"], "name": member["name"], "memberships": [],
            })
            company["memberships"].append(member)
        universe["companies"] = list(companies.values())
        latest_run = self._conn.execute(
            "SELECT id FROM company_incremental_runs WHERE universe_id=? ORDER BY created_at DESC LIMIT 1",
            (universe_id,),
        ).fetchone()
        universe["latest_operation"] = self.get_incremental_run(latest_run["id"]) if latest_run else None
        return universe

    def list_universes(self, profile_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if profile_id:
            rows = self._conn.execute(
                "SELECT id FROM value_research_universes WHERE profile_id=? ORDER BY created_at DESC LIMIT ?",
                (profile_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM value_research_universes ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self.get_universe(row["id"]) or {} for row in rows]

    def activate_universe(self, universe_id: str) -> dict[str, Any]:
        universe = self.get_universe(universe_id)
        if not universe:
            raise KeyError("research universe not found")
        if universe["status"] not in {"ready", "active"}:
            raise ValueError("research universe must finish bootstrap before activation")
        timestamp = now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """UPDATE value_research_universes SET status='archived',archived_at=?
                       WHERE profile_id=? AND status='active' AND id<>?""",
                    (timestamp, universe["profile_id"], universe_id),
                )
                self._conn.execute(
                    "UPDATE value_research_universes SET status='active',activated_at=?,archived_at=NULL WHERE id=?",
                    (timestamp, universe_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_universe(universe_id) or {}

    def active_universes(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id FROM value_research_universes WHERE status='active' ORDER BY activated_at DESC",
        ).fetchall()
        return [self.get_universe(row["id"]) or {} for row in rows]

    def update_universe_status(self, universe_id: str, status: str) -> None:
        cursor = self._conn.execute(
            "UPDATE value_research_universes SET status=? WHERE id=?", (status, universe_id),
        )
        if cursor.rowcount != 1:
            raise KeyError("research universe not found")
        self._conn.commit()

    def create_incremental_run(
        self, *, universe_id: str, run_kind: str, trigger_kind: str, as_of: str,
        companies: list[dict[str, Any]], retry_token: str = "",
    ) -> tuple[dict[str, Any], bool]:
        key = f"{universe_id}:{run_kind}:{as_of}:{retry_token or 'base'}"
        existing = self._conn.execute(
            "SELECT id FROM company_incremental_runs WHERE idempotency_key=?", (key,),
        ).fetchone()
        if existing:
            return self.get_incremental_run(existing["id"]) or {}, False
        run_id, timestamp = new_id("valueop"), now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """INSERT INTO company_incremental_runs(
                           id,idempotency_key,universe_id,run_kind,trigger_kind,as_of,status,total,
                           completed,failed,coverage,cancel_requested,message,created_at,started_at,completed_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, key, universe_id, run_kind, trigger_kind, as_of, "queued", len(companies),
                     0, 0, 0, 0, "", timestamp, None, None),
                )
                self._conn.executemany(
                    """INSERT INTO company_incremental_jobs(
                           id,run_id,symbol,name,primary_track_id,status,stage,attempts,message,
                           snapshot_id,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(
                        new_id("valuejob"), run_id, item["symbol"], item.get("name") or item["symbol"],
                        item.get("primary_track_id") or "", "queued", "facts", 0, "", None,
                        timestamp, timestamp,
                    ) for item in companies],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get_incremental_run(run_id) or {}, True

    def get_incremental_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.row(self._conn.execute(
            "SELECT * FROM company_incremental_runs WHERE id=?", (run_id,),
        ).fetchone())
        if run:
            run["jobs"] = [self.row(row) or {} for row in self._conn.execute(
                "SELECT * FROM company_incremental_jobs WHERE run_id=? ORDER BY symbol", (run_id,),
            ).fetchall()]
        return run

    def list_incremental_runs(self, universe_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if universe_id:
            rows = self._conn.execute(
                "SELECT id FROM company_incremental_runs WHERE universe_id=? ORDER BY created_at DESC LIMIT ?",
                (universe_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM company_incremental_runs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [self.get_incremental_run(row["id"]) or {} for row in rows]

    def update_incremental_run(self, run_id: str, **fields: Any) -> None:
        allowed = {"status", "completed", "failed", "coverage", "cancel_requested", "message", "started_at", "completed_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if values:
            with self._lock:
                self._conn.execute(
                    f"UPDATE company_incremental_runs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
                    (*values.values(), run_id),
                )
                self._conn.commit()

    def update_incremental_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "stage", "attempts", "message", "snapshot_id"}
        values = {key: value for key, value in fields.items() if key in allowed}
        values["updated_at"] = now()
        with self._lock:
            self._conn.execute(
                f"UPDATE company_incremental_jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?",
                (*values.values(), job_id),
            )
            self._conn.commit()

    def refresh_incremental_progress(self, run_id: str) -> dict[str, Any]:
        """Persist live progress after every company job, not only at run end."""
        with self._lock:
            counts = self._conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status IN ('completed','partial') THEN 1 ELSE 0 END) AS completed,
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                   FROM company_incremental_jobs WHERE run_id=?""",
                (run_id,),
            ).fetchone()
            if not counts or not counts["total"]:
                raise KeyError("incremental run not found")
            total = int(counts["total"])
            completed = int(counts["completed"] or 0)
            failed = int(counts["failed"] or 0)
            self._conn.execute(
                "UPDATE company_incremental_runs SET completed=?,failed=?,coverage=? WHERE id=?",
                (completed, failed, completed / total, run_id),
            )
            self._conn.commit()
        return self.get_incremental_run(run_id) or {}

    def retry_incremental_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_incremental_run(run_id)
        if not run:
            raise KeyError("incremental run not found")
        # A normal company snapshot is bounded to 45 seconds.  Anything still
        # marked running after one minute is safe to recover after a restart.
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=1)
        retryable = [job for job in run["jobs"] if job["status"] == "failed"]
        for job in run["jobs"]:
            if job["status"] != "running":
                continue
            try:
                updated_at = datetime.fromisoformat(str(job["updated_at"]))
            except (TypeError, ValueError):
                updated_at = datetime.min.replace(tzinfo=timezone.utc)
            if updated_at <= stale_before:
                retryable.append(job)
        if not retryable:
            raise ValueError("no failed incremental jobs to retry")
        timestamp = now()
        with self._lock:
            for job in retryable:
                self._conn.execute(
                    """UPDATE company_incremental_jobs
                       SET status='queued',stage='facts',message='',updated_at=? WHERE id=?""",
                    (timestamp, job["id"]),
                )
            self._conn.execute(
                """UPDATE company_incremental_runs
                   SET status='queued',failed=0,cancel_requested=0,message='',completed_at=NULL WHERE id=?""",
                (run_id,),
            )
            self._conn.commit()
        self.refresh_incremental_progress(run_id)
        return self.get_incremental_run(run_id) or {}

    def upsert_evidence(self, evidence: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._conn.execute(
            """SELECT * FROM company_research_evidence
               WHERE source=? AND source_id=? AND content_hash=?""",
            (evidence["source"], evidence["source_id"], evidence["content_hash"]),
        ).fetchone()
        if existing:
            return self.row(existing) or {}, False
        evidence_id = new_id("evidence")
        self._conn.execute(
            """INSERT INTO company_research_evidence(
                   id,symbol,evidence_type,source,source_id,data_as_of,published_at,fetched_at,
                   content_hash,payload_json,status
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (evidence_id, evidence["symbol"], evidence["evidence_type"], evidence["source"],
             evidence["source_id"], evidence["data_as_of"], evidence.get("published_at"),
             evidence.get("fetched_at") or now(), evidence["content_hash"],
             json.dumps(evidence.get("payload", {}), ensure_ascii=False, sort_keys=True),
             evidence.get("status", "ready")),
        )
        self._conn.commit()
        return self.row(self._conn.execute(
            "SELECT * FROM company_research_evidence WHERE id=?", (evidence_id,),
        ).fetchone()) or {}, True

    def latest_snapshot(self, universe_id: str, symbol: str) -> dict[str, Any] | None:
        return self.row(self._conn.execute(
            """SELECT * FROM company_research_snapshots
               WHERE universe_id=? AND symbol=? ORDER BY version DESC LIMIT 1""",
            (universe_id, symbol),
        ).fetchone())

    def save_snapshot(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._conn.execute(
            """SELECT * FROM company_research_snapshots
               WHERE universe_id=? AND symbol=? AND source_hash=?""",
            (snapshot["universe_id"], snapshot["symbol"], snapshot["source_hash"]),
        ).fetchone()
        if existing:
            return self.row(existing) or {}, False
        previous = self.latest_snapshot(snapshot["universe_id"], snapshot["symbol"])
        version = int(previous["version"] if previous else 0) + 1
        snapshot_id = new_id("snapshot")
        self._conn.execute(
            """INSERT INTO company_research_snapshots(
                   id,universe_id,symbol,version,data_as_of,status,completeness,source_hash,
                   payload_json,diff_json,missing_fields_json,sources_json,evidence_ids_json,
                   dossier_id,report_id,previous_snapshot_id,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, snapshot["universe_id"], snapshot["symbol"], version,
             snapshot["data_as_of"], snapshot["status"], snapshot["completeness"], snapshot["source_hash"],
             json.dumps(snapshot.get("payload", {}), ensure_ascii=False, sort_keys=True),
             json.dumps(snapshot.get("diff", {}), ensure_ascii=False, sort_keys=True),
             json.dumps(snapshot.get("missing_fields", []), ensure_ascii=False),
             json.dumps(snapshot.get("sources", []), ensure_ascii=False),
             json.dumps(snapshot.get("evidence_ids", []), ensure_ascii=False),
             snapshot.get("dossier_id"), snapshot.get("report_id"),
             previous.get("id") if previous else None, now()),
        )
        self._conn.commit()
        return self.row(self._conn.execute(
            "SELECT * FROM company_research_snapshots WHERE id=?", (snapshot_id,),
        ).fetchone()) or {}, True

    def company_archive(self, symbol: str) -> dict[str, Any]:
        memberships = [self.row(row) or {} for row in self._conn.execute(
            """SELECT m.*,u.profile_id,u.engine_run_id,u.data_as_of AS universe_as_of,u.status AS universe_status
               FROM value_research_universe_members m
               JOIN value_research_universes u ON u.id=m.universe_id
               WHERE m.symbol=? ORDER BY u.created_at DESC,m.track_rank,m.leader_rank""", (symbol,),
        ).fetchall()]
        snapshots = [self.row(row) or {} for row in self._conn.execute(
            "SELECT * FROM company_research_snapshots WHERE symbol=? ORDER BY created_at DESC", (symbol,),
        ).fetchall()]
        evidence = [self.row(row) or {} for row in self._conn.execute(
            "SELECT * FROM company_research_evidence WHERE symbol=? ORDER BY data_as_of DESC", (symbol,),
        ).fetchall()]
        monitors = [self.row(row) or {} for row in self._conn.execute(
            "SELECT * FROM value_entry_monitors WHERE symbol=? ORDER BY created_at DESC", (symbol,),
        ).fetchall()]
        events: list[dict[str, Any]] = []
        for monitor in monitors:
            rows = self._conn.execute(
                "SELECT * FROM value_monitor_events WHERE monitor_id=? ORDER BY triggered_at DESC", (monitor["id"],),
            ).fetchall()
            events.extend(self.row(row) or {} for row in rows)
        return {
            "symbol": symbol, "memberships": memberships, "snapshots": snapshots,
            "evidence": evidence, "monitors": monitors,
            "events": sorted(events, key=lambda item: item.get("triggered_at") or "", reverse=True),
        }

    def save_signal_evaluation(self, evaluation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self._conn.execute(
            """SELECT * FROM value_signal_evaluations
               WHERE monitor_id=? AND input_hash=? AND rule_version=?""",
            (evaluation["monitor_id"], evaluation["input_hash"], evaluation["rule_version"]),
        ).fetchone()
        if existing:
            return self.row(existing) or {}, False
        evaluation_id = new_id("evaluation")
        self._conn.execute(
            """INSERT INTO value_signal_evaluations(
                   id,monitor_id,snapshot_id,as_of,signal_state,rule_version,input_hash,
                   rules_json,inputs_json,reasons_json,missing_fields_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (evaluation_id, evaluation["monitor_id"], evaluation.get("snapshot_id"), evaluation["as_of"],
             evaluation["signal_state"], evaluation["rule_version"], evaluation["input_hash"],
             json.dumps(evaluation.get("rules", {}), ensure_ascii=False, sort_keys=True),
             json.dumps(evaluation.get("inputs", {}), ensure_ascii=False, sort_keys=True),
             json.dumps(evaluation.get("reasons", []), ensure_ascii=False),
             json.dumps(evaluation.get("missing_fields", []), ensure_ascii=False), now()),
        )
        self._conn.execute(
            "UPDATE value_entry_monitors SET signal_state=?,last_checked_at=?,updated_at=? WHERE id=?",
            (evaluation["signal_state"], now(), now(), evaluation["monitor_id"]),
        )
        self._conn.commit()
        return self.row(self._conn.execute(
            "SELECT * FROM value_signal_evaluations WHERE id=?", (evaluation_id,),
        ).fetchone()) or {}, True

    def latest_signal_evaluation(self, monitor_id: str) -> dict[str, Any] | None:
        return self.row(self._conn.execute(
            "SELECT * FROM value_signal_evaluations WHERE monitor_id=? ORDER BY created_at DESC LIMIT 1",
            (monitor_id,),
        ).fetchone())

    def list_signal_evaluations(
        self, *, signal_state: str | None = None, symbol: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if signal_state:
            clauses.append("e.signal_state=?")
            args.append(signal_state)
        if symbol:
            clauses.append("m.symbol=?")
            args.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"""SELECT e.*,m.symbol,m.name,m.position_state
                FROM value_signal_evaluations e JOIN value_entry_monitors m ON m.id=e.monitor_id
                {where} ORDER BY e.created_at DESC LIMIT ?""",
            (*args, limit),
        ).fetchall()
        return [self.row(row) or {} for row in rows]

    def get_automation(self) -> dict[str, Any]:
        return self.row(self._conn.execute(
            "SELECT * FROM value_research_automation WHERE id='default'",
        ).fetchone()) or {}

    def update_automation(self, **fields: Any) -> dict[str, Any]:
        allowed = {"enabled", "next_run_at", "last_run_id", "last_status", "last_error", "lock_owner", "lock_until"}
        values = {key: int(value) if key == "enabled" else value for key, value in fields.items() if key in allowed}
        values["updated_at"] = now()
        self._conn.execute(
            f"UPDATE value_research_automation SET {','.join(f'{key}=?' for key in values)} WHERE id='default'",
            tuple(values.values()),
        )
        self._conn.commit()
        return self.get_automation()

    def acquire_automation_lock(self, owner: str, *, until: str, now_value: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT lock_owner,lock_until FROM value_research_automation WHERE id='default'",
                ).fetchone()
                if row and row["lock_owner"] and str(row["lock_until"] or "") > now_value and row["lock_owner"] != owner:
                    self._conn.rollback()
                    return False
                self._conn.execute(
                    "UPDATE value_research_automation SET lock_owner=?,lock_until=?,updated_at=? WHERE id='default'",
                    (owner, until, now()),
                )
                self._conn.commit()
                return True
            except Exception:
                self._conn.rollback()
                raise

    def release_automation_lock(self, owner: str) -> None:
        self._conn.execute(
            """UPDATE value_research_automation SET lock_owner=NULL,lock_until=NULL,updated_at=?
               WHERE id='default' AND lock_owner=?""", (now(), owner),
        )
        self._conn.commit()

    def create_batch(self, *, run_id: str, profile_id: str, track_id: str, companies: list[dict[str, str]], template_version: str, concurrency: int) -> tuple[dict[str, Any], bool]:
        symbols = sorted({company["symbol"] for company in companies})
        key = f"{run_id}:{track_id}:{','.join(symbols)}:{template_version}"
        existing = self._conn.execute("SELECT * FROM company_research_batches WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return self.get_batch(existing["id"]) or {}, False
        batch_id, timestamp = new_id("batch"), now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO company_research_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, key, run_id, profile_id, track_id, "queued", len(symbols), 0, 0, 0, template_version, concurrency, timestamp, None, None),
            )
            names = {item["symbol"]: item.get("name") or item["symbol"] for item in companies}
            for symbol in symbols:
                self._conn.execute(
                    "INSERT INTO company_research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id("research"), batch_id, symbol, names[symbol], "queued", "facts", "", None, None, "queued", "{}", 0, timestamp, timestamp),
                )
            self._conn.execute("UPDATE value_track_leaders SET research_status='queued' WHERE engine_run_id=? AND track_id=? AND symbol IN (%s)" % ",".join("?" for _ in symbols), (run_id, track_id, *symbols))
            self._conn.commit()
        return self.get_batch(batch_id) or {}, True

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.row(self._conn.execute("SELECT * FROM company_research_batches WHERE id=?", (batch_id,)).fetchone())
        if batch:
            rows = self._conn.execute("SELECT * FROM company_research_jobs WHERE batch_id=? ORDER BY created_at,symbol", (batch_id,)).fetchall()
            batch["jobs"] = [self.row(value) or {} for value in rows]
        return batch

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT id FROM company_research_batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.get_batch(row["id"]) or {} for row in rows]

    def update_batch(self, batch_id: str, **fields: Any) -> None:
        allowed = {"status", "completed", "failed", "cancel_requested", "started_at", "completed_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        self._conn.execute(f"UPDATE company_research_batches SET {','.join(f'{key}=?' for key in values)} WHERE id=?", (*values.values(), batch_id))
        self._conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "stage", "message", "dossier_id", "report_id", "valuation_status", "valuation_json", "attempts"}
        values = {key: (json.dumps(value) if key == "valuation_json" else value) for key, value in fields.items() if key in allowed}
        values["updated_at"] = now()
        self._conn.execute(f"UPDATE company_research_jobs SET {','.join(f'{key}=?' for key in values)} WHERE id=?", (*values.values(), job_id))
        self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.row(self._conn.execute("SELECT * FROM company_research_jobs WHERE id=?", (job_id,)).fetchone())

    def reviewed_job_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM company_research_jobs
               WHERE symbol=? AND status IN ('partial','completed')
               ORDER BY updated_at DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        return self.row(row)

    def create_monitor(
        self, *, job_id: str, conditions: dict[str, Any], channels: list[str],
        position_state: str = "watching", universe_id: str | None = None,
        risk_preset: str = "balanced",
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        if not job or job["status"] not in {"partial", "completed"}:
            raise ValueError("only reviewed completed or partial research can be monitored")
        existing = self._conn.execute(
            "SELECT * FROM value_entry_monitors WHERE research_job_id=? AND status!='closed' ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if existing:
            return self.row(existing) or {}
        batch = self._conn.execute("SELECT * FROM company_research_batches WHERE id=?", (job["batch_id"],)).fetchone()
        monitor_id, timestamp = new_id("monitor"), now()
        self._conn.execute(
            """INSERT INTO value_entry_monitors(
                   id,symbol,name,engine_run_id,track_id,research_job_id,status,
                   conditions_json,channels_json,confirmed_at,last_checked_at,created_at,updated_at,
                   universe_id,position_state,signal_state,risk_preset,thesis_invalidated
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (monitor_id, job["symbol"], job["name"], batch["engine_run_id"], batch["track_id"], job_id, "active",
             json.dumps(conditions), json.dumps(channels), timestamp, None, timestamp, timestamp,
             universe_id, position_state, "watching", risk_preset, 0),
        )
        self._conn.commit()
        return self.get_monitor(monitor_id) or {}

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        return self.row(self._conn.execute("SELECT * FROM value_entry_monitors WHERE id=?", (monitor_id,)).fetchone())

    def list_monitors(self) -> list[dict[str, Any]]:
        return [self.row(value) or {} for value in self._conn.execute("SELECT * FROM value_entry_monitors ORDER BY updated_at DESC").fetchall()]

    def update_monitor(self, monitor_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "conditions", "channels", "position_state", "risk_preset", "thesis_invalidated"}
        values = {f"{key}_json" if key in {"conditions", "channels"} else key: json.dumps(value) if key in {"conditions", "channels"} else value for key, value in fields.items() if key in allowed}
        values["updated_at"] = now()
        cursor = self._conn.execute(f"UPDATE value_entry_monitors SET {','.join(f'{key}=?' for key in values)} WHERE id=?", (*values.values(), monitor_id))
        if cursor.rowcount != 1:
            raise KeyError("monitor not found")
        self._conn.commit()
        return self.get_monitor(monitor_id) or {}

    def add_event(self, *, monitor_id: str, event_key: str, event_type: str, severity: str, title: str, message: str, payload: dict[str, Any], channels: list[str]) -> dict[str, Any]:
        existing = self._conn.execute("SELECT * FROM value_monitor_events WHERE event_key=?", (event_key,)).fetchone()
        if existing:
            return self.row(existing) or {}
        event_id, timestamp = new_id("event"), now()
        self._conn.execute(
            """INSERT INTO value_monitor_events(
                   id,monitor_id,event_key,event_type,severity,title,message,payload_json,
                   triggered_at,acknowledged_at,evaluation_id,status,acknowledgement_note,resolved_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, monitor_id, event_key, event_type, severity, title, message,
             json.dumps(payload), timestamp, None, payload.get("evaluation_id"), "open", "", None),
        )
        requested = set(channels)
        for channel in ("in_app", "feishu", "weixin"):
            status = "sent" if channel == "in_app" else "pending" if channel in requested else "skipped"
            self._conn.execute("INSERT INTO notification_deliveries VALUES(?,?,?,?,?,?)", (new_id("delivery"), event_id, channel, status, "", timestamp))
        self._conn.commit()
        return self.row(self._conn.execute("SELECT * FROM value_monitor_events WHERE id=?", (event_id,)).fetchone()) or {}

    def list_events(
        self, limit: int = 200, *, event_type: str | None = None, status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if event_type:
            clauses.append("event_type=?")
            args.append(event_type)
        if status:
            clauses.append("status=?")
            args.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        events = [self.row(value) or {} for value in self._conn.execute(
            f"SELECT * FROM value_monitor_events {where} ORDER BY triggered_at DESC LIMIT ?", (*args, limit),
        ).fetchall()]
        for event in events:
            event["deliveries"] = [dict(value) for value in self._conn.execute("SELECT * FROM notification_deliveries WHERE event_id=? ORDER BY channel", (event["id"],)).fetchall()]
        return events

    def acknowledge_event(self, event_id: str, *, status: str = "acknowledged", note: str = "") -> dict[str, Any]:
        if status not in {"acknowledged", "closed"}:
            raise ValueError("event status must be acknowledged or closed")
        timestamp = now()
        cursor = self._conn.execute(
            """UPDATE value_monitor_events SET status=?,acknowledged_at=?,acknowledgement_note=?,resolved_at=?
               WHERE id=?""",
            (status, timestamp, note[:2000], timestamp if status == "closed" else None, event_id),
        )
        if cursor.rowcount != 1:
            raise KeyError("monitor event not found")
        self._conn.commit()
        event = self.row(self._conn.execute(
            "SELECT * FROM value_monitor_events WHERE id=?", (event_id,),
        ).fetchone()) or {}
        event["deliveries"] = [dict(value) for value in self._conn.execute(
            "SELECT * FROM notification_deliveries WHERE event_id=? ORDER BY channel", (event_id,),
        ).fetchall()]
        return event

    def update_delivery(self, event_id: str, channel: str, *, status: str, error: str = "") -> None:
        self._conn.execute(
            "UPDATE notification_deliveries SET status=?,error=?,attempted_at=? WHERE event_id=? AND channel=?",
            (status, error[:1000], now(), event_id, channel),
        )
        self._conn.commit()

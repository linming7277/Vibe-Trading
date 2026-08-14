"""SQLite persistence for calculation profiles, track snapshots and research monitoring."""

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
            timestamp = now()
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
        for field in ("is_default", "is_builtin", "cancel_requested"):
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

    def create_monitor(self, *, job_id: str, conditions: dict[str, Any], channels: list[str]) -> dict[str, Any]:
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
            "INSERT INTO value_entry_monitors VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (monitor_id, job["symbol"], job["name"], batch["engine_run_id"], batch["track_id"], job_id, "active",
             json.dumps(conditions), json.dumps(channels), timestamp, None, timestamp, timestamp),
        )
        self._conn.commit()
        return self.get_monitor(monitor_id) or {}

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        return self.row(self._conn.execute("SELECT * FROM value_entry_monitors WHERE id=?", (monitor_id,)).fetchone())

    def list_monitors(self) -> list[dict[str, Any]]:
        return [self.row(value) or {} for value in self._conn.execute("SELECT * FROM value_entry_monitors ORDER BY updated_at DESC").fetchall()]

    def update_monitor(self, monitor_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "conditions", "channels"}
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
        self._conn.execute("INSERT INTO value_monitor_events VALUES(?,?,?,?,?,?,?,?,?,?)", (event_id, monitor_id, event_key, event_type, severity, title, message, json.dumps(payload), timestamp, None))
        requested = set(channels)
        for channel in ("in_app", "feishu", "weixin"):
            status = "sent" if channel == "in_app" else "pending" if channel in requested else "skipped"
            self._conn.execute("INSERT INTO notification_deliveries VALUES(?,?,?,?,?,?)", (new_id("delivery"), event_id, channel, status, "", timestamp))
        self._conn.commit()
        return self.row(self._conn.execute("SELECT * FROM value_monitor_events WHERE id=?", (event_id,)).fetchone()) or {}

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        events = [self.row(value) or {} for value in self._conn.execute("SELECT * FROM value_monitor_events ORDER BY triggered_at DESC LIMIT ?", (limit,)).fetchall()]
        for event in events:
            event["deliveries"] = [dict(value) for value in self._conn.execute("SELECT * FROM notification_deliveries WHERE event_id=? ORDER BY channel", (event["id"],)).fetchall()]
        return events

    def update_delivery(self, event_id: str, channel: str, *, status: str, error: str = "") -> None:
        self._conn.execute(
            "UPDATE notification_deliveries SET status=?,error=?,attempted_at=? WHERE event_id=? AND channel=?",
            (status, error[:1000], now(), event_id, channel),
        )
        self._conn.commit()

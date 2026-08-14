"""Persistence primitives for Value Line V2 data and refresh jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class ValueDataStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or (get_runtime_root() / "research.db"))
        initializer = ResearchWorkspaceStore(self.path, seed=False)
        initializer.close()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def replace_macro_series(self, series_ids: Iterable[str], rows: list[dict[str, Any]]) -> None:
        identifiers = sorted(set(series_ids))
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                if identifiers:
                    placeholders = ",".join("?" for _ in identifiers)
                    self._conn.execute(f"DELETE FROM macro_series WHERE series_id IN ({placeholders})", identifiers)  # noqa: S608
                self._conn.executemany(
                    """INSERT INTO macro_series(
                           series_id,observation_date,release_date,vintage_id,value,unit,source,
                           source_url,release_status,fetched_at,metadata_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    [(
                        row["series_id"], row["observation_date"], row["release_date"], row["vintage_id"],
                        row.get("value"), row.get("unit", ""), row["source"], row.get("source_url", ""),
                        row.get("release_status", "first_observed_only"), row["fetched_at"],
                        dumps(row.get("metadata", {})),
                    ) for row in rows],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def macro_series_as_of(self, as_of: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM macro_series WHERE release_date<=?
                   ORDER BY series_id,observation_date,release_date,vintage_id""", (as_of,),
            ).fetchall()
        return [self._decode(row, ("metadata_json",)) for row in rows]

    def save_macro_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """INSERT INTO macro_snapshots(
                       id,as_of,formula_version,regime,score,coverage,confidence,status,
                       axes_json,states_json,missing_fields_json,sources_json,provenance_key,created_at,
                       axis_coverage,series_coverage,series_count,series_total,
                       release_verified_coverage,first_observed_count,missing_series_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(provenance_key) DO NOTHING""",
                (
                    snapshot["id"], snapshot["as_of"], snapshot["formula_version"], snapshot["regime"],
                    snapshot.get("score"), snapshot["coverage"], snapshot["confidence"], snapshot["status"],
                    dumps(snapshot["axes"]), dumps(snapshot["states"]), dumps(snapshot["missing_fields"]),
                    dumps(snapshot["sources"]), snapshot["provenance_key"], snapshot["created_at"],
                    snapshot.get("axis_coverage", snapshot["coverage"]), snapshot.get("series_coverage", 0),
                    snapshot.get("series_count", 0), snapshot.get("series_total", 0),
                    snapshot.get("release_verified_coverage", 0), snapshot.get("first_observed_count", 0),
                    dumps(snapshot.get("missing_series", [])),
                ),
            )
            self._conn.commit()
        return self.get_macro_snapshot(snapshot["as_of"]) or snapshot

    def get_macro_snapshot(self, as_of: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            if as_of:
                row = self._conn.execute(
                    "SELECT * FROM macro_snapshots WHERE as_of<=? ORDER BY as_of DESC,created_at DESC LIMIT 1", (as_of,),
                ).fetchone()
            else:
                row = self._conn.execute("SELECT * FROM macro_snapshots ORDER BY as_of DESC,created_at DESC LIMIT 1").fetchone()
        return self._decode(
            row,
            ("axes_json", "states_json", "missing_fields_json", "sources_json", "missing_series_json"),
        ) if row else None

    def replace_membership_snapshot(self, as_of: str, rows: list[dict[str, Any]]) -> int:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM sector_membership_snapshots WHERE as_of=?", (as_of,))
                self._conn.executemany(
                    """INSERT INTO sector_membership_snapshots(
                           as_of,sector_code,sector_name,symbol,source,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    [(as_of, row["sector_code"], row["sector_name"], row["symbol"], row["source"], now()) for row in rows],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(rows)

    def memberships_as_of(self, as_of: str) -> dict[str, Any]:
        with self._lock:
            first = self._conn.execute("SELECT MIN(as_of) FROM sector_membership_snapshots").fetchone()[0]
            if not first or as_of < first:
                return {"status": "membership_history_unavailable", "first_as_of": first, "items": []}
            snapshot_date = self._conn.execute(
                "SELECT MAX(as_of) FROM sector_membership_snapshots WHERE as_of<=?", (as_of,),
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT * FROM sector_membership_snapshots WHERE as_of=? ORDER BY sector_code,symbol", (snapshot_date,),
            ).fetchall()
        return {"status": "ready", "as_of": snapshot_date, "first_as_of": first, "items": [dict(row) for row in rows]}

    def upsert_policy_events(self, events: list[dict[str, Any]], classifications: list[dict[str, Any]]) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(
                    """INSERT INTO policy_events(
                           id,document_number,title,normalized_url,content_hash,source,published_at,
                           fetched_at,etag,last_modified,status,content_text,metadata_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         document_number=excluded.document_number,title=excluded.title,
                         source=excluded.source,published_at=excluded.published_at,
                         fetched_at=excluded.fetched_at,etag=excluded.etag,
                         last_modified=excluded.last_modified,status=excluded.status,
                         content_text=excluded.content_text,metadata_json=excluded.metadata_json""",
                    [(
                        row["id"], row.get("document_number", ""), row["title"], row["normalized_url"],
                        row["content_hash"], row["source"], row.get("published_at"), row["fetched_at"],
                        row.get("etag", ""), row.get("last_modified", ""), row["status"],
                        row.get("content_text", ""), dumps(row.get("metadata", {})),
                    ) for row in events],
                )
                self._conn.executemany(
                    """INSERT INTO policy_classifications(
                           id,event_id,industry_code,industry_name,direction,strength,sensitivity,horizon_days,
                           evidence,confidence,classifier_version,status,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id,industry_code,classifier_version) DO UPDATE SET
                         direction=excluded.direction,strength=excluded.strength,
                         sensitivity=excluded.sensitivity,
                         horizon_days=excluded.horizon_days,evidence=excluded.evidence,
                         confidence=excluded.confidence,status=excluded.status,created_at=excluded.created_at""",
                    [(
                        row["id"], row["event_id"], row["industry_code"], row["industry_name"],
                        row["direction"], row["strength"], row.get("sensitivity", 1.0),
                        row["horizon_days"], row["evidence"],
                        row["confidence"], row["classifier_version"], row["status"], row["created_at"],
                    ) for row in classifications],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def find_policy_event(
        self, *, document_number: str = "", normalized_url: str = "", content_hash: str = "",
    ) -> dict[str, Any] | None:
        clauses: list[str] = []
        args: list[str] = []
        for column, value in (
            ("document_number", document_number), ("normalized_url", normalized_url), ("content_hash", content_hash),
        ):
            if value:
                clauses.append(f"{column}=?")
                args.append(value)
        if not clauses:
            return None
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM policy_events WHERE {' OR '.join(clauses)} ORDER BY fetched_at DESC LIMIT 1",  # noqa: S608
                args,
            ).fetchone()
        return self._decode(row, ("metadata_json",)) if row else None

    def policy_request_headers(self, normalized_url: str) -> dict[str, str]:
        existing = self.find_policy_event(normalized_url=normalized_url)
        if not existing:
            return {}
        headers: dict[str, str] = {}
        if existing.get("etag"):
            headers["If-None-Match"] = str(existing["etag"])
        if existing.get("last_modified"):
            headers["If-Modified-Since"] = str(existing["last_modified"])
        return headers

    def policies(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clause, args = ("WHERE e.status=?", [status]) if status else ("", [])
        with self._lock:
            events = self._conn.execute(
                f"SELECT e.* FROM policy_events e {clause} ORDER BY COALESCE(e.published_at,e.fetched_at) DESC LIMIT ?",  # noqa: S608
                (*args, max(1, min(limit, 500))),
            ).fetchall()
            result = []
            for event in events:
                item = self._decode(event, ("metadata_json",))
                rows = self._conn.execute(
                    "SELECT * FROM policy_classifications WHERE event_id=? ORDER BY confidence DESC,industry_code", (item["id"],),
                ).fetchall()
                item["classifications"] = [dict(row) for row in rows]
                result.append(item)
        return result

    def create_job(self, job_id: str, modules: list[str], as_of: str) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                "INSERT INTO value_refresh_jobs(id,modules_json,as_of,status,total,created_at) VALUES(?,?,?,?,?,?)",
                (job_id, dumps(modules), as_of, "queued", len(modules), now()),
            )
            self._conn.commit()
        return self.get_job(job_id) or {}

    def update_job(self, job_id: str, **values: Any) -> None:
        json_fields = {"results": "results_json", "errors": "errors_json"}
        allowed = {"status", "current_module", "progress", "total", "started_at", "completed_at", *json_fields}
        payload = {json_fields.get(key, key): dumps(value) if key in json_fields else value for key, value in values.items() if key in allowed}
        if not payload:
            return
        with self._lock:
            self._conn.execute(
                f"UPDATE value_refresh_jobs SET {','.join(f'{key}=?' for key in payload)} WHERE id=?",  # noqa: S608
                (*payload.values(), job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM value_refresh_jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row, ("modules_json", "results_json", "errors_json")) if row else None

    def recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM value_refresh_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode(row, ("modules_json", "results_json", "errors_json")) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row, json_fields: tuple[str, ...]) -> dict[str, Any]:
        item = dict(row)
        for field in json_fields:
            raw = item.pop(field, None)
            key = field.removesuffix("_json")
            try:
                item[key] = json.loads(raw or "[]")
            except (TypeError, json.JSONDecodeError):
                item[key] = []
        return item

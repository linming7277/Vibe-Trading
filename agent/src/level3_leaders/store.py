"""SQLite snapshots for terminal-industry Leader V1."""

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


class Level3LeaderStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["statistics"] = _loads(item.pop("statistics_json"), {})
        return item

    @staticmethod
    def _leader(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["component_scores"] = _loads(item.pop("component_scores_json"), {})
        item["eligibility_reasons"] = _loads(item.pop("eligibility_reasons_json"), [])
        item["metric_applicability_notes"] = _loads(item.pop("metric_notes_json"), [])
        item["raw_features"] = _loads(item.pop("raw_features_json"), {})
        return item

    def completed_run(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM value_level3_leader_runs WHERE idempotency_key=? AND status='COMPLETED'",
            (idempotency_key,),
        ).fetchone()
        return self._run(row) if row else None

    def start_run(self, *, idempotency_key: str, as_of: str, catalog_as_of: str,
                  formula_version: str) -> dict[str, Any]:
        prior = self._conn.execute(
            "SELECT id FROM value_level3_leader_runs WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        run_id = str(prior[0]) if prior else f"level3_{uuid.uuid4().hex[:16]}"
        with self._lock, self._conn:
            if prior:
                self._conn.execute(
                    "UPDATE value_level3_leader_runs SET status='RUNNING',statistics_json='{}',error='',completed_at=NULL WHERE id=?",
                    (run_id,),
                )
                self._conn.execute("DELETE FROM value_level3_leaders WHERE run_id=?", (run_id,))
            else:
                self._conn.execute(
                    """INSERT INTO value_level3_leader_runs(
                       id,idempotency_key,as_of,catalog_as_of,formula_version,status,created_at
                       ) VALUES(?,?,?,?,?,'RUNNING',?)""",
                    (run_id, idempotency_key, as_of, catalog_as_of, formula_version, _now()),
                )
        return self.get_run(run_id)

    def finish_run(self, run_id: str, *, rows: list[dict[str, Any]], statistics: dict[str, Any]) -> dict[str, Any]:
        created_at = _now()
        values = [(
            f"l3lead_{uuid.uuid4().hex[:20]}", run_id, row["as_of"],
            row["level1_code"], row["level1_name"], row["level2_code"], row["level2_name"],
            row["level3_code"], row["level3_name"], row["stock_code"], row["stock_name"],
            row.get("leader_rank"), row.get("leader_score"), row["leader_formula_version"],
            json.dumps(row.get("component_scores") or {}, ensure_ascii=False, sort_keys=True),
            float(row.get("coverage") or 0), row["eligibility_status"],
            json.dumps(row.get("eligibility_reasons") or [], ensure_ascii=False),
            json.dumps(row.get("metric_applicability_notes") or [], ensure_ascii=False),
            json.dumps(row.get("raw_features") or {}, ensure_ascii=False, sort_keys=True),
            str(row.get("provenance_key") or ""), created_at,
        ) for row in rows]
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO value_level3_leaders(
                   id,run_id,as_of,level1_code,level1_name,level2_code,level2_name,
                   level3_code,level3_name,stock_code,stock_name,leader_rank,leader_score,
                   leader_formula_version,component_scores_json,coverage,eligibility_status,
                   eligibility_reasons_json,metric_notes_json,raw_features_json,provenance_key,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            self._conn.execute(
                "UPDATE value_level3_leader_runs SET status='COMPLETED',statistics_json=?,completed_at=? WHERE id=?",
                (json.dumps(statistics, ensure_ascii=False, sort_keys=True), created_at, run_id),
            )
        return self.get_run(run_id)

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE value_level3_leader_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",
                (error, _now(), run_id),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM value_level3_leader_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        return self._run(row)

    def latest_run(self, as_of: str | None = None) -> dict[str, Any] | None:
        if as_of:
            row = self._conn.execute(
                "SELECT * FROM value_level3_leader_runs WHERE status='COMPLETED' AND as_of=? ORDER BY completed_at DESC LIMIT 1",
                (as_of,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM value_level3_leader_runs WHERE status='COMPLETED' ORDER BY as_of DESC,completed_at DESC LIMIT 1"
            ).fetchone()
        return self._run(row) if row else None

    def industry_rows(self, run_id: str, industry_code: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM value_level3_leaders WHERE run_id=? AND level3_code=?
               ORDER BY CASE WHEN leader_rank IS NULL THEN 1 ELSE 0 END,leader_rank,stock_code""",
            (run_id, industry_code),
        ).fetchall()
        return [self._leader(row) for row in rows]

    def all_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM value_level3_leaders WHERE run_id=?
               ORDER BY level3_code,CASE WHEN leader_rank IS NULL THEN 1 ELSE 0 END,leader_rank,stock_code""",
            (run_id,),
        ).fetchall()
        return [self._leader(row) for row in rows]

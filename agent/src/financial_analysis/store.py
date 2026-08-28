"""Persistence for Financial Analyst V1 snapshots."""

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


class FinancialAnalysisStore:
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
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_financial_chat_entries (
                    id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    source_snapshot_id TEXT,
                    source_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_financial_chat_entries_company_time
                    ON company_financial_chat_entries(stock_code, created_at DESC);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _snapshot(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        for target, source, fallback in (
            ("identity", "identity_json", {}), ("history", "history_json", []),
            ("feature", "feature_json", {}), ("forecast", "forecast_json", {}),
            ("analysis", "analysis_payload_json", None), ("data_gaps", "data_gaps_json", []),
        ):
            item[target] = _loads(item.pop(source), fallback)
        return item

    def latest_leader(self, stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
        clauses = ["l.stock_code=?", "l.eligibility_status='eligible'"]
        args: list[Any] = [stock_code.upper()]
        if as_of:
            clauses.append("r.as_of<=?")
            args.append(as_of)
        row = self._conn.execute(
            f"""SELECT l.* FROM value_level3_leaders l
                JOIN value_level3_leader_runs r ON r.id=l.run_id
                WHERE {' AND '.join(clauses)} AND r.status='COMPLETED'
                ORDER BY r.as_of DESC,r.completed_at DESC,l.leader_rank ASC LIMIT 1""",
            args,
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["component_scores"] = _loads(item.pop("component_scores_json"), {})
        item["metric_applicability_notes"] = _loads(item.pop("metric_notes_json"), [])
        item["eligibility_reasons"] = _loads(item.pop("eligibility_reasons_json"), [])
        item["raw_features"] = _loads(item.pop("raw_features_json"), {})
        return item

    def by_source_hash(self, stock_code: str, source_hash: str) -> dict[str, Any] | None:
        return self._snapshot(self._conn.execute(
            "SELECT * FROM company_financial_analysis_snapshots WHERE stock_code=? AND source_hash=?",
            (stock_code.upper(), source_hash),
        ).fetchone())

    def latest(self, stock_code: str, as_of: str | None = None) -> dict[str, Any] | None:
        if as_of:
            row = self._conn.execute(
                """SELECT * FROM company_financial_analysis_snapshots
                   WHERE stock_code=? AND as_of<=? ORDER BY as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
                (stock_code.upper(), as_of),
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT * FROM company_financial_analysis_snapshots
                   WHERE stock_code=? ORDER BY as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
                (stock_code.upper(),),
            ).fetchone()
        return self._snapshot(row)

    def recent(self, stock_code: str, *, as_of: str | None = None, limit: int = 2) -> list[dict[str, Any]]:
        """Read the most recent persisted snapshots without preparing new data.

        Risk Research uses this only to compare two already-completed deterministic
        forecast snapshots.  Keeping the query here prevents the read-only risk
        path from accidentally invoking FinancialAnalysisService.prepare().
        """
        clauses = ["stock_code=?"]
        args: list[Any] = [stock_code.upper()]
        if as_of:
            clauses.append("as_of<=?")
            args.append(as_of)
        args.append(max(1, min(int(limit), 20)))
        rows = self._conn.execute(
            f"""SELECT * FROM company_financial_analysis_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY as_of DESC,created_at DESC,rowid DESC LIMIT ?""",
            args,
        ).fetchall()
        return [self._snapshot(row) or {} for row in rows]

    def latest_completed(self, stock_code: str) -> dict[str, Any] | None:
        """Return the newest successfully completed Financial Agent snapshot only."""
        row = self._conn.execute(
            """SELECT * FROM company_financial_analysis_snapshots
               WHERE stock_code=? AND analysis_status='COMPLETED'
               ORDER BY as_of DESC,created_at DESC,rowid DESC LIMIT 1""",
            (stock_code.upper(),),
        ).fetchone()
        return self._snapshot(row)

    def save_python_snapshot(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self.by_source_hash(payload["stock_code"], payload["source_hash"])
        if existing:
            return existing, False
        snapshot_id, timestamp = f"financial_{uuid.uuid4().hex[:20]}", _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_financial_analysis_snapshots(
                   id,stock_code,stock_name,as_of,historical_cutoff,financial_feature_version,
                   forecast_version,feature_status,forecast_status,analysis_status,
                   agent_provider,agent_model,identity_json,history_json,feature_json,forecast_json,
                   analysis_payload_json,data_gaps_json,source_hash,agent_error,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id, payload["stock_code"].upper(), payload["stock_name"], payload["as_of"],
                    payload["historical_cutoff"], payload["financial_feature_version"], payload["forecast_version"],
                    payload["feature_status"], payload["forecast_status"], payload["analysis_status"],
                    payload.get("agent_provider"), payload.get("agent_model"),
                    json.dumps(payload.get("identity") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("history") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("feature") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("forecast") or {}, ensure_ascii=False, sort_keys=True),
                    None, json.dumps(payload.get("data_gaps") or [], ensure_ascii=False),
                    payload["source_hash"], "", timestamp, timestamp,
                ),
            )
        return self.latest(payload["stock_code"], payload["as_of"]) or {}, True

    def update_agent_result(self, snapshot_id: str, *, status: str, provider: str | None,
                            model: str | None, analysis: dict[str, Any] | None = None,
                            error: str = "") -> dict[str, Any]:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE company_financial_analysis_snapshots
                   SET analysis_status=?,agent_provider=?,agent_model=?,analysis_payload_json=?,
                       agent_error=?,updated_at=? WHERE id=?""",
                (status, provider, model, json.dumps(analysis, ensure_ascii=False, sort_keys=True) if analysis is not None else None,
                 error, _now(), snapshot_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(snapshot_id)
        return self._snapshot(self._conn.execute(
            "SELECT * FROM company_financial_analysis_snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()) or {}

    def append_chat_entry(self, *, stock_code: str, stock_name: str, role: str, content: str,
                          source_snapshot_id: str | None, source_hash: str | None) -> dict[str, Any]:
        if role not in {"user", "assistant"}:
            raise ValueError("invalid financial chat role")
        entry = {
            "id": f"financial_chat_{uuid.uuid4().hex[:20]}", "stock_code": stock_code.upper(),
            "stock_name": stock_name, "role": role, "content": content,
            "source_snapshot_id": source_snapshot_id, "source_hash": source_hash, "created_at": _now(),
        }
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_financial_chat_entries(
                    id,stock_code,stock_name,role,content,source_snapshot_id,source_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                tuple(entry.values()),
            )
        return entry

    def list_chat_entries(self, stock_code: str, *, limit: int = 40) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM company_financial_chat_entries WHERE stock_code=?
               ORDER BY created_at DESC,rowid DESC LIMIT ?""",
            (stock_code.upper(), max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

"""Read repository for immutable Company Thesis version-change history."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class CompanyThesisHistoryRepository:
    """Query immutable history rows; writes occur atomically with version creation."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
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
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["evidence_ids"] = _loads(item.pop("evidence_ids_json"), [])
        item["metadata"] = _loads(item.pop("metadata_json"), {})
        return item

    def get_history_by_id(self, history_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_thesis_history WHERE history_id=?", (history_id,),
            ).fetchone())

    def get_history_for_to_thesis(self, thesis_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(
                "SELECT * FROM company_thesis_history WHERE to_thesis_id=?", (thesis_id,),
            ).fetchone())

    def list_history_for_company(self, market: str, stock_code: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_history WHERE market=? AND stock_code=?
                   ORDER BY to_version DESC,created_at DESC""", (market, stock_code),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def list_history_for_thesis(self, thesis_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM company_thesis_history
                   WHERE from_thesis_id=? OR to_thesis_id=? ORDER BY created_at DESC""",
                (thesis_id, thesis_id),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

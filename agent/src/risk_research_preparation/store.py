"""Durable preparation-state projection for current low-value leaders."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


class RiskResearchPreparationRepository:
    """Owns state only; collecting source data is owned by the service."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_risk_research_preparation (
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    research_as_of TEXT NOT NULL,
                    company_name TEXT NOT NULL DEFAULT '',
                    financial_status TEXT NOT NULL,
                    business_profile_status TEXT NOT NULL,
                    business_research_status TEXT NOT NULL,
                    disclosure_status TEXT NOT NULL,
                    thesis_status TEXT NOT NULL,
                    overall_status TEXT NOT NULL CHECK(overall_status IN ('READY','PARTIAL','MISSING','FAILED')),
                    missing_capabilities_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, stock_code, research_as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_risk_research_preparation_current
                    ON company_risk_research_preparation(research_as_of DESC, overall_status, stock_code);
                """
            )
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(company_risk_research_preparation)")}
            for name, definition in {"draft_status": "TEXT", "validation_status": "TEXT", "provisional_thesis_status": "TEXT", "provisional_thesis_id": "TEXT"}.items():
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE company_risk_research_preparation ADD COLUMN {name} {definition}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["missing_capabilities"] = _loads(item.pop("missing_capabilities_json"), [])
        item["metadata"] = _loads(item.pop("metadata_json"), {})
        return item

    def upsert(self, item: dict[str, Any]) -> dict[str, Any]:
        values = {
            "market": str(item["market"]).upper(),
            "stock_code": str(item["stock_code"]).upper(),
            "research_as_of": str(item["research_as_of"])[:10],
            "company_name": str(item.get("company_name") or ""),
            "financial_status": str(item.get("financial_status") or "MISSING"),
            "business_profile_status": str(item.get("business_profile_status") or "MISSING"),
            "business_research_status": str(item.get("business_research_status") or "MISSING"),
            "disclosure_status": str(item.get("disclosure_status") or "MISSING"),
            "thesis_status": str(item.get("thesis_status") or "MISSING"),
            "overall_status": str(item.get("overall_status") or "MISSING"),
            "missing_capabilities_json": json.dumps(item.get("missing_capabilities") or [], ensure_ascii=False, sort_keys=True),
            "last_error": str(item.get("last_error") or "")[:2000],
            "metadata_json": json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
            "updated_at": _now(),
            "draft_status": item.get("draft_status"), "validation_status": item.get("validation_status"),
            "provisional_thesis_status": item.get("provisional_thesis_status"), "provisional_thesis_id": item.get("provisional_thesis_id"),
        }
        columns = tuple(values)
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO company_risk_research_preparation({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)}) "
                f"ON CONFLICT(market,stock_code,research_as_of) DO UPDATE SET "
                + ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"market", "stock_code", "research_as_of"}),
                tuple(values[column] for column in columns),
            )
        return self.get(values["market"], values["stock_code"], values["research_as_of"]) or {}

    def get(self, market: str, stock_code: str, research_as_of: str) -> dict[str, Any] | None:
        return self._row(self._conn.execute(
            """SELECT * FROM company_risk_research_preparation
               WHERE market=? AND stock_code=? AND research_as_of=?""",
            (market.upper(), stock_code.upper(), str(research_as_of)[:10]),
        ).fetchone())

    def latest_on_or_before(self, market: str, stock_code: str, on_or_before: str) -> dict[str, Any] | None:
        """Latest point-in-time preparation dated on or before the research day.

        Same-day preparation is scheduled asynchronously and may still be
        materializing when a projection runs; a preparation snapshot from an
        earlier date is legitimate PIT knowledge for the later research day.
        """
        return self._row(self._conn.execute(
            """SELECT * FROM company_risk_research_preparation
               WHERE market=? AND stock_code=? AND research_as_of<=?
               ORDER BY research_as_of DESC LIMIT 1""",
            (market.upper(), stock_code.upper(), str(on_or_before)[:10]),
        ).fetchone())

    def list_for_as_of(self, research_as_of: str | None = None, *, market: str = "CN") -> list[dict[str, Any]]:
        params: list[Any] = [market.upper()]
        clause = "market=?"
        if research_as_of:
            clause += " AND research_as_of=?"
            params.append(str(research_as_of)[:10])
        rows = self._conn.execute(
            f"SELECT * FROM company_risk_research_preparation WHERE {clause} "
            "ORDER BY research_as_of DESC, overall_status, stock_code",
            params,
        ).fetchall()
        return [self._row(row) or {} for row in rows]

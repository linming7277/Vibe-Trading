"""Durable, append-preserving storage for structured moat evidence."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
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


class MoatEvidenceStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_moat_evidence (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    research_as_of TEXT NOT NULL,
                    moat_dimension TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    normalized_claim TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    supports_or_challenges TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_document_id TEXT,
                    source_material_id TEXT,
                    source_url TEXT NOT NULL DEFAULT '',
                    report_date TEXT,
                    announcement_date TEXT,
                    page_number INTEGER,
                    source_quote_ref TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL DEFAULT '',
                    value_json TEXT NOT NULL DEFAULT '{}',
                    confidence TEXT NOT NULL,
                    data_quality TEXT NOT NULL,
                    extractor_version TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_moat_evidence_company
                    ON company_moat_evidence(market,stock_code,announcement_date DESC,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_moat_evidence_filter
                    ON company_moat_evidence(stock_code,moat_dimension,evidence_type,status);
                CREATE INDEX IF NOT EXISTS idx_moat_evidence_fact
                    ON company_moat_evidence(stock_code,moat_dimension,evidence_type,fact_key,status);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["value"] = _loads(item.pop("value_json"), {})
        return item

    def save(self, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Save once per source fact; newer same-fact disclosures supersede old ones."""
        existing = self._conn.execute(
            "SELECT * FROM company_moat_evidence WHERE fingerprint=?", (item["fingerprint"],),
        ).fetchone()
        if existing:
            return self._row(existing), False
        evidence_id, timestamp = f"moat_ev_{uuid.uuid4().hex[:20]}", _now()
        columns = (
            "fingerprint", "market", "stock_code", "research_as_of", "moat_dimension", "evidence_type",
            "claim", "normalized_claim", "summary", "supports_or_challenges", "source_type", "source_document_id",
            "source_material_id", "source_url", "report_date", "announcement_date", "page_number", "source_quote_ref",
            "source_hash", "value_json", "confidence", "data_quality", "extractor_version", "fact_key", "status",
        )
        values = [
            item.get(key) if key != "value_json" else json.dumps(item.get("value") or {}, ensure_ascii=False, sort_keys=True)
            for key in columns
        ]
        with self._lock, self._conn:
            # A revised annual/quarterly filing on the same fact key supersedes
            # the earlier active source.  The old row remains available for PIT
            # audit and historical review.
            if item.get("source_document_id") and item.get("announcement_date"):
                self._conn.execute(
                    """UPDATE company_moat_evidence SET status='SUPERSEDED',updated_at=?
                       WHERE market=? AND stock_code=? AND moat_dimension=? AND evidence_type=? AND fact_key=?
                         AND status='ACTIVE' AND source_document_id<>?
                         AND COALESCE(announcement_date,'')<=?""",
                    (timestamp, item["market"], item["stock_code"], item["moat_dimension"], item["evidence_type"],
                     item["fact_key"], item["source_document_id"], item["announcement_date"]),
                )
            self._conn.execute(
                f"INSERT INTO company_moat_evidence(id,{','.join(columns)},created_at,updated_at) "
                f"VALUES(?,{','.join('?' for _ in columns)},?,?)",
                (evidence_id, *values, timestamp, timestamp),
            )
        row = self._conn.execute("SELECT * FROM company_moat_evidence WHERE id=?", (evidence_id,)).fetchone()
        return self._row(row), True

    def list(
        self, market: str, stock_code: str, *, as_of: str | None = None, dimension: str | None = None,
        evidence_type: str | None = None, active: bool | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["market=?", "stock_code=?"]
        values: list[Any] = [market, stock_code]
        if as_of:
            clauses.append("COALESCE(announcement_date,research_as_of)<=?")
            values.append(str(as_of)[:10])
        if dimension:
            clauses.append("moat_dimension=?")
            values.append(dimension.upper())
        if evidence_type:
            clauses.append("evidence_type=?")
            values.append(evidence_type.upper())
        if active is True:
            clauses.append("status='ACTIVE'")
        elif active is False:
            clauses.append("status='SUPERSEDED'")
        rows = self._conn.execute(
            f"SELECT * FROM company_moat_evidence WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(announcement_date,research_as_of) DESC,created_at DESC",
            values,
        ).fetchall()
        return [self._row(row) for row in rows]

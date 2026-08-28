"""Durable, source-first storage for official company disclosures."""

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


def _stock_code(value: str) -> str:
    """CNINFO is keyed by the six-digit security code, unlike Value Line."""
    return str(value or "").strip().upper().split(".")[0]


class DisclosureMaterialStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_disclosure_documents (
                    id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    org_id TEXT NOT NULL,
                    announcement_id TEXT NOT NULL UNIQUE,
                    report_kind TEXT NOT NULL,
                    report_period TEXT,
                    announcement_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    pdf_path TEXT,
                    pdf_sha256 TEXT,
                    text_path TEXT,
                    text_sha256 TEXT,
                    page_count INTEGER,
                    extraction_status TEXT NOT NULL,
                    extraction_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_company_disclosure_time
                    ON company_disclosure_documents(stock_code,announcement_date DESC,report_kind);
                CREATE TABLE IF NOT EXISTS company_disclosure_materials (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    material_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    excerpts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(document_id,material_type),
                    FOREIGN KEY(document_id) REFERENCES company_disclosure_documents(id)
                );
                CREATE INDEX IF NOT EXISTS idx_company_disclosure_material_type
                    ON company_disclosure_materials(stock_code,material_type,status);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _document(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def save_document(self, item: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        existing = self._conn.execute(
            "SELECT id FROM company_disclosure_documents WHERE announcement_id=?", (item["announcement_id"],),
        ).fetchone()
        document_id = str(existing[0]) if existing else f"disclosure_{uuid.uuid4().hex[:20]}"
        columns = (
            "stock_code", "company_name", "org_id", "announcement_id", "report_kind", "report_period",
            "announcement_date", "title", "source_url", "pdf_path", "pdf_sha256", "text_path", "text_sha256",
            "page_count", "extraction_status", "extraction_error",
        )
        values = [item.get(column) for column in columns]
        with self._lock, self._conn:
            if existing:
                self._conn.execute(
                    f"UPDATE company_disclosure_documents SET {','.join(f'{key}=?' for key in columns)},updated_at=? WHERE id=?",
                    (*values, timestamp, document_id),
                )
            else:
                self._conn.execute(
                    f"INSERT INTO company_disclosure_documents(id,{','.join(columns)},created_at,updated_at) "
                    f"VALUES(?,{','.join('?' for _ in columns)},?,?)",
                    (document_id, *values, timestamp, timestamp),
                )
        return self.get_document(document_id) or {}

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self._document(self._conn.execute(
            "SELECT * FROM company_disclosure_documents WHERE id=?", (document_id,),
        ).fetchone())

    def get_document_by_announcement(self, announcement_id: str) -> dict[str, Any] | None:
        """Return a prior collection result so a scheduled sync can reuse it."""
        return self._document(self._conn.execute(
            "SELECT * FROM company_disclosure_documents WHERE announcement_id=?", (str(announcement_id),),
        ).fetchone())

    def list_documents(self, stock_code: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        clauses, values = ["stock_code=?"], [_stock_code(stock_code)]
        if as_of:
            clauses.append("announcement_date<=?")
            values.append(as_of)
        rows = self._conn.execute(
            f"SELECT * FROM company_disclosure_documents WHERE {' AND '.join(clauses)} "
            "ORDER BY announcement_date DESC,report_kind,title",
            values,
        ).fetchall()
        return [self._document(row) or {} for row in rows]

    def save_materials(self, document_id: str, stock_code: str, materials: list[dict[str, Any]]) -> None:
        timestamp = _now()
        with self._lock, self._conn:
            for item in materials:
                prior = self._conn.execute(
                    "SELECT id FROM company_disclosure_materials WHERE document_id=? AND material_type=?",
                    (document_id, item["material_type"]),
                ).fetchone()
                material_id = str(prior[0]) if prior else f"disclosure_material_{uuid.uuid4().hex[:20]}"
                values = (
                    item["status"], json.dumps(item.get("keywords") or [], ensure_ascii=False),
                    json.dumps(item.get("excerpts") or [], ensure_ascii=False), timestamp,
                )
                if prior:
                    self._conn.execute(
                        "UPDATE company_disclosure_materials SET status=?,keywords_json=?,excerpts_json=?,updated_at=? WHERE id=?",
                        (*values, material_id),
                    )
                else:
                    self._conn.execute(
                        "INSERT INTO company_disclosure_materials("
                        "id,document_id,stock_code,material_type,status,keywords_json,excerpts_json,created_at,updated_at"
                        ") VALUES(?,?,?,?,?,?,?,?,?)",
                        (material_id, document_id, _stock_code(stock_code), item["material_type"], item["status"],
                         json.dumps(item.get("keywords") or [], ensure_ascii=False),
                         json.dumps(item.get("excerpts") or [], ensure_ascii=False), timestamp, timestamp),
                    )

    def list_materials(self, stock_code: str, *, as_of: str | None = None) -> list[dict[str, Any]]:
        clauses, values = ["m.stock_code=?"], [_stock_code(stock_code)]
        if as_of:
            clauses.append("d.announcement_date<=?")
            values.append(as_of)
        rows = self._conn.execute(
            f"SELECT m.*,d.announcement_id,d.announcement_date,d.report_period,d.report_kind,d.title,d.source_url,d.text_sha256 "
            f"FROM company_disclosure_materials m JOIN company_disclosure_documents d ON d.id=m.document_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY d.announcement_date DESC,m.material_type",
            values,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["keywords"] = _loads(item.pop("keywords_json"), [])
            item["excerpts"] = _loads(item.pop("excerpts_json"), [])
            result.append(item)
        return result

"""Persistence for CIO reports: one report row + per-section rows (plan §13)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CioReportStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(get_runtime_root()) / "research.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_cio_research_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    research_as_of TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'READY',
                    overall_freshness TEXT NOT NULL DEFAULT 'UNKNOWN',
                    input_fingerprint TEXT NOT NULL,
                    financial_hash TEXT, business_hash TEXT, valuation_hash TEXT,
                    risk_hash TEXT, leader_hash TEXT, moat_hash TEXT, capital_hash TEXT,
                    thesis_hash TEXT, focus_hash TEXT,
                    structured_payload_json TEXT NOT NULL DEFAULT '{}',
                    narrative_report_md TEXT NOT NULL DEFAULT '',
                    synthesis_source TEXT NOT NULL DEFAULT 'TEMPLATE',
                    formula_version TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    previous_report_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_cio_reports_lookup
                    ON company_cio_research_reports(market, stock_code, research_as_of DESC);
                CREATE TABLE IF NOT EXISTS company_cio_report_sections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL REFERENCES company_cio_research_reports(id),
                    section_type TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL DEFAULT '',
                    freshness_status TEXT NOT NULL DEFAULT 'FRESH',
                    structured_payload_json TEXT NOT NULL DEFAULT '{}',
                    narrative_md TEXT NOT NULL DEFAULT '',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(report_id, section_type)
                );
                """
            )

    def latest_report(self, market: str, stock_code: str, *, as_of: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM company_cio_research_reports WHERE market=? AND stock_code=?"
        params: list[Any] = [market, stock_code.upper()]
        if as_of:
            sql += " AND research_as_of<=?"
            params.append(str(as_of)[:10])
        sql += " ORDER BY research_as_of DESC, id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        if row is None:
            return None
        columns = [c[0] for c in self._conn.execute(
            "SELECT * FROM company_cio_research_reports LIMIT 0").description]
        report = dict(zip(columns, row))
        report["sections"] = self.sections_for(report["id"])
        return report

    def sections_for(self, report_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM company_cio_report_sections WHERE report_id=? ORDER BY id",
                (report_id,),
            ).fetchall()
        columns = [c[0] for c in self._conn.execute(
            "SELECT * FROM company_cio_report_sections LIMIT 0").description]
        result = []
        for row in rows:
            section = dict(zip(columns, row))
            section["structured_payload"] = json.loads(section.pop("structured_payload_json") or "{}")
            section["source_refs"] = json.loads(section.pop("source_refs_json") or "[]")
            result.append(section)
        return result

    def save_report(
        self, *, market: str, stock_code: str, research_as_of: str, overall_freshness: str,
        input_fingerprint: str, module_hashes: dict[str, str], sections: list[dict[str, Any]],
        narrative_report_md: str, synthesis_source: str, formula_version: str,
        prompt_version: str, model_version: str, previous_report_id: int | None,
    ) -> dict[str, Any]:
        existing = self.latest_report(market, stock_code, as_of=research_as_of)
        if existing and str(existing.get("input_fingerprint") or "") == input_fingerprint:
            if (str(existing.get("narrative_report_md") or "") == narrative_report_md
                    and str(existing.get("synthesis_source") or "") == synthesis_source):
                return {**existing, "idempotent_reuse": True}
            # Same research inputs, better synthesis (e.g. TEMPLATE_FALLBACK
            # recovered via an explicit full-report retry): update the
            # delivery layer in place instead of discarding the recovery.
            now = _utc_now()
            with self._lock, self._conn:
                self._conn.execute(
                    """UPDATE company_cio_research_reports
                       SET narrative_report_md=?, synthesis_source=?, model_version=?,
                           prompt_version=?, updated_at=?
                       WHERE id=?""",
                    (narrative_report_md, synthesis_source, model_version,
                     prompt_version, now, existing["id"]),
                )
            refreshed = self.latest_report(market, stock_code, as_of=research_as_of) or {}
            return {**refreshed, "idempotent_reuse": False, "synthesis_recovered": True}
        now = _utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT INTO company_cio_research_reports(
                       market, stock_code, research_as_of, status, overall_freshness,
                       input_fingerprint, financial_hash, business_hash, valuation_hash,
                       risk_hash, leader_hash, moat_hash, capital_hash, thesis_hash, focus_hash,
                       structured_payload_json, narrative_report_md, synthesis_source,
                       formula_version, prompt_version, model_version, created_at, updated_at,
                       previous_report_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market, stock_code.upper(), str(research_as_of)[:10], "READY", overall_freshness,
                    input_fingerprint,
                    module_hashes.get("financial"), module_hashes.get("business"),
                    module_hashes.get("valuation"), module_hashes.get("risk"),
                    module_hashes.get("leader"), module_hashes.get("moat"),
                    module_hashes.get("capital_allocation"), module_hashes.get("thesis"),
                    module_hashes.get("focus"),
                    json.dumps({"sections": [s["section_type"] for s in sections]},
                               ensure_ascii=False),
                    narrative_report_md, synthesis_source, formula_version, prompt_version,
                    model_version, now, now, previous_report_id,
                ),
            )
            report_id = int(cursor.lastrowid)
            for section in sections:
                self._conn.execute(
                    """INSERT INTO company_cio_report_sections(
                           report_id, section_type, input_fingerprint, freshness_status,
                           structured_payload_json, narrative_md, source_refs_json,
                           created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        report_id, section["section_type"],
                        str(section.get("input_fingerprint") or ""),
                        str(section.get("freshness_status") or "FRESH"),
                        json.dumps(section.get("structured_payload") or {}, ensure_ascii=False,
                                   sort_keys=True, default=str),
                        str(section.get("narrative_md") or ""),
                        json.dumps(section.get("source_refs") or [], ensure_ascii=False,
                                   default=str),
                        now, now,
                    ),
                )
        report = self.latest_report(market, stock_code, as_of=research_as_of) or {}
        return {**report, "idempotent_reuse": False}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

"""Business Driver Evidence store — persists structured facts extracted from
CNINFO annual/semi-annual report PDFs (already persisted as text).

V1 dimensions: SEGMENT_REVENUE, SEGMENT_MARGIN, REGIONAL_MIX, PRODUCT_VOLUME,
CUSTOMER, CAPEX_PROJECT.  Evidence types follow the Moat Evidence discipline.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

BUSINESS_DRIVER_EVIDENCE_VERSION = "business-driver-evidence-v1.0.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BusinessDriverEvidenceStore:
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
                CREATE TABLE IF NOT EXISTS company_business_driver_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    raw_name TEXT,
                    canonical_name TEXT,
                    mapping_confidence TEXT DEFAULT 'LOW',
                    period TEXT,
                    announcement_date TEXT,
                    evidence_type TEXT NOT NULL,
                    value REAL,
                    value_secondary REAL,
                    unit TEXT,
                    revenue REAL,
                    revenue_share REAL,
                    revenue_yoy REAL,
                    cost REAL,
                    gross_margin REAL,
                    gross_margin_yoy TEXT,
                    production_volume REAL,
                    sales_volume REAL,
                    inventory_volume REAL,
                    customer_rank INTEGER,
                    customer_share REAL,
                    project_name TEXT,
                    project_status TEXT,
                    planned_investment REAL,
                    actual_investment REAL,
                    source_document_id TEXT,
                    source_page INTEGER,
                    source_text TEXT,
                    source_hash TEXT,
                    fingerprint TEXT NOT NULL,
                    comparability_status TEXT DEFAULT 'UNKNOWN',
                    extractor_version TEXT NOT NULL,
                    status TEXT DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    UNIQUE(stock_code, dimension, fact_key, period, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_bde_lookup
                    ON company_business_driver_evidence(stock_code, dimension, period DESC);
                """
            )

    def save(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Insert one evidence row; returns the stored row with `created` flag."""
        fingerprint = evidence.get("fingerprint") or self._fingerprint(evidence)
        now = _utc_now()
        fields = {
            "market": evidence.get("market", "CN"),
            "stock_code": evidence.get("stock_code", "").upper(),
            "dimension": evidence.get("dimension", ""),
            "fact_key": evidence.get("fact_key", ""),
            "raw_name": evidence.get("raw_name"),
            "canonical_name": evidence.get("canonical_name") or evidence.get("raw_name"),
            "mapping_confidence": evidence.get("mapping_confidence", "LOW"),
            "period": evidence.get("period"),
            "announcement_date": evidence.get("announcement_date"),
            "evidence_type": evidence.get("evidence_type", "DISCLOSED_FACT"),
            "value": evidence.get("value"),
            "value_secondary": evidence.get("value_secondary"),
            "unit": evidence.get("unit"),
            "revenue": evidence.get("revenue"),
            "revenue_share": evidence.get("revenue_share"),
            "revenue_yoy": evidence.get("revenue_yoy"),
            "cost": evidence.get("cost"),
            "gross_margin": evidence.get("gross_margin"),
            "gross_margin_yoy": evidence.get("gross_margin_yoy"),
            "production_volume": evidence.get("production_volume"),
            "sales_volume": evidence.get("sales_volume"),
            "inventory_volume": evidence.get("inventory_volume"),
            "customer_rank": evidence.get("customer_rank"),
            "customer_share": evidence.get("customer_share"),
            "project_name": evidence.get("project_name"),
            "project_status": evidence.get("project_status"),
            "planned_investment": evidence.get("planned_investment"),
            "actual_investment": evidence.get("actual_investment"),
            "source_document_id": evidence.get("source_document_id"),
            "source_page": evidence.get("source_page"),
            "source_text": str(evidence.get("source_text") or "")[:500],
            "source_hash": evidence.get("source_hash"),
            "comparability_status": evidence.get("comparability_status", "UNKNOWN"),
            "extractor_version": evidence.get("extractor_version", BUSINESS_DRIVER_EVIDENCE_VERSION),
            "status": "ACTIVE",
        }
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM company_business_driver_evidence WHERE fingerprint=?",
                (fingerprint,)).fetchone()
            if existing:
                return {"id": existing[0], "fingerprint": fingerprint, "created": False}
            columns = list(fields.keys()) + ["fingerprint", "created_at"]
            values = list(fields.values()) + [fingerprint, now]
            placeholders = ",".join("?" * len(columns))
            cursor = self._conn.execute(
                f"INSERT INTO company_business_driver_evidence({','.join(columns)}) VALUES ({placeholders})",
                values)
            return {"id": cursor.lastrowid, "fingerprint": fingerprint, "created": True}

    def list_evidence(self, stock_code: str, *, dimension: str | None = None,
                      period: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM company_business_driver_evidence WHERE stock_code=? AND status='ACTIVE'"
        params: list[Any] = [stock_code.upper()]
        if dimension:
            sql += " AND dimension=?"
            params.append(dimension)
        if period:
            sql += " AND period=?"
            params.append(period)
        sql += " ORDER BY period DESC, id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        columns = [c[0] for c in self._conn.execute(
            "SELECT * FROM company_business_driver_evidence LIMIT 0").description]
        return [dict(zip(columns, row)) for row in rows]

    @staticmethod
    def _fingerprint(evidence: dict[str, Any]) -> str:
        payload = {
            k: evidence.get(k)
            for k in ("stock_code", "dimension", "fact_key", "period",
                       "source_document_id", "source_page", "raw_name")
        }
        payload["v"] = evidence.get("extractor_version", BUSINESS_DRIVER_EVIDENCE_VERSION)
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

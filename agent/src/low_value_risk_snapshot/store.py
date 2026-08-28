"""Durable list-level projections of the read-only RiskResearchService output."""

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


class LowValueRiskSnapshotRepository:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path, seed=False)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS company_low_value_risk_snapshots (
                    id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    overall_risk TEXT NOT NULL,
                    value_trap_risk TEXT NOT NULL,
                    material_risk_count INTEGER NOT NULL,
                    high_risk_count INTEGER NOT NULL,
                    medium_risk_count INTEGER NOT NULL,
                    top_risk_types_json TEXT NOT NULL DEFAULT '[]',
                    risk_summary TEXT NOT NULL,
                    financial_status TEXT NOT NULL,
                    business_status TEXT NOT NULL,
                    thesis_status TEXT NOT NULL,
                    formula_version TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(market, stock_code, source_as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_low_value_risk_snapshot_lookup
                    ON company_low_value_risk_snapshots(market, stock_code, source_as_of DESC);
            """)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["top_risk_types"] = _loads(item.pop("top_risk_types_json"), [])
        return item

    def get(self, market: str, stock_code: str, source_as_of: str) -> dict[str, Any] | None:
        return self._row(self._conn.execute(
            """SELECT * FROM company_low_value_risk_snapshots
               WHERE market=? AND stock_code=? AND source_as_of=?""",
            (market.upper(), stock_code.upper(), source_as_of),
        ).fetchone())

    def save(self, item: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO company_low_value_risk_snapshots(
                    id,market,stock_code,source_as_of,overall_risk,value_trap_risk,material_risk_count,
                    high_risk_count,medium_risk_count,top_risk_types_json,risk_summary,financial_status,
                    business_status,thesis_status,formula_version,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market,stock_code,source_as_of) DO UPDATE SET
                    overall_risk=excluded.overall_risk,value_trap_risk=excluded.value_trap_risk,
                    material_risk_count=excluded.material_risk_count,high_risk_count=excluded.high_risk_count,
                    medium_risk_count=excluded.medium_risk_count,top_risk_types_json=excluded.top_risk_types_json,
                    risk_summary=excluded.risk_summary,financial_status=excluded.financial_status,
                    business_status=excluded.business_status,thesis_status=excluded.thesis_status,
                    formula_version=excluded.formula_version,error=excluded.error,updated_at=excluded.updated_at""",
                (
                    f"lvrisk_{uuid.uuid4().hex[:20]}", item["market"].upper(), item["stock_code"].upper(), item["source_as_of"],
                    item["overall_risk"], item["value_trap_risk"], item["material_risk_count"], item["high_risk_count"],
                    item["medium_risk_count"], json.dumps(item.get("top_risk_types") or [], ensure_ascii=False),
                    item["risk_summary"], item["financial_status"], item["business_status"], item["thesis_status"],
                    item["formula_version"], item.get("error") or "", timestamp, timestamp,
                ),
            )
        return self.get(item["market"], item["stock_code"], item["source_as_of"]) or {}

    def attach_to_pool_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach only a same-as-of snapshot. Never calculates on an API GET."""
        output: list[dict[str, Any]] = []
        for item in items:
            source_as_of = str(item.get("source_as_of") or "")
            snapshot = self.get(str(item.get("market") or "CN"), str(item.get("stock_code") or ""), source_as_of) if source_as_of else None
            output.append({**item, "risk_overall": (snapshot or {}).get("overall_risk", "UNKNOWN"),
                           "value_trap_risk": (snapshot or {}).get("value_trap_risk", "UNKNOWN"),
                           "material_risk_count": int((snapshot or {}).get("material_risk_count") or 0),
                           "top_risk_types": list((snapshot or {}).get("top_risk_types") or []),
                           "risk_summary": (snapshot or {}).get("risk_summary", "风险快照尚未生成，暂无法完整判断。"),
                           "risk_as_of": (snapshot or {}).get("source_as_of"),
                           "risk_snapshot_status": "READY" if snapshot and not snapshot.get("error") else "UNKNOWN"})
        return output

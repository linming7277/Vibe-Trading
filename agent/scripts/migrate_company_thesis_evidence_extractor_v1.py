#!/usr/bin/env python3
"""Idempotently install Company Thesis deterministic evidence extraction V1."""

from __future__ import annotations

import argparse
import json
import sqlite3

from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.config.paths import get_runtime_root


def inventory(db_path: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(company_thesis_evidence)")]
        indexes = [row[1] for row in conn.execute("PRAGMA index_list(company_thesis_evidence)")]
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return {
            "research_db": db_path,
            "schema_version": int(version[0]) if version else None,
            "evidence_fingerprint_column": "evidence_fingerprint" in columns,
            "active_fingerprint_index": "idx_company_thesis_evidence_active_fingerprint" in indexes,
            "evidence_count": conn.execute("SELECT COUNT(*) FROM company_thesis_evidence").fetchone()[0],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = str(get_runtime_root() / "research.db")
    before = inventory(db_path)
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "before": before}, ensure_ascii=False, indent=2))
        return 0
    repository = CompanyThesisEvidenceRepository()
    repository.close()
    print(json.dumps({"mode": "apply", "before": before, "after": inventory(db_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

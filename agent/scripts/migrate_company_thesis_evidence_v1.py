#!/usr/bin/env python3
"""Idempotently install Company Thesis Evidence V1 in research.db."""

from __future__ import annotations

import argparse
import json
import sqlite3

from src.company_thesis.evidence_store import CompanyThesisEvidenceRepository
from src.config.paths import get_runtime_root


def inventory(db_path: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_thesis_evidence'"
        ).fetchone() is not None
        count = conn.execute("SELECT COUNT(*) FROM company_thesis_evidence").fetchone()[0] if exists else 0
        return {"research_db": db_path, "company_thesis_evidence_exists": exists,
                "company_thesis_evidence_count": count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="create evidence table and indexes")
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

#!/usr/bin/env python3
"""Idempotently install Company Thesis Review Apply V1 fields in research.db."""

from __future__ import annotations

import argparse
import json
import sqlite3

from src.company_thesis.review_store import CompanyThesisReviewRepository
from src.config.paths import get_runtime_root


def inventory(db_path: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(company_thesis_reviews)")]
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return {"research_db": db_path, "schema_version": int(version[0]) if version else None,
                "review_columns": columns, "review_count": conn.execute("SELECT COUNT(*) FROM company_thesis_reviews").fetchone()[0]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = str(get_runtime_root() / "research.db")
    before = inventory(db_path)
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "before": before}, ensure_ascii=False, indent=2))
        return 0
    repository = CompanyThesisReviewRepository()
    repository.close()
    print(json.dumps({"mode": "apply", "before": before, "after": inventory(db_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

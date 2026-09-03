"""Verify that read-only Watchpoint projection creates no tables or events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.config.paths import get_runtime_root
from src.value_watchpoints import get_value_watchpoint_projection_service


def main() -> None:
    connection = sqlite3.connect(str(get_runtime_root() / "research.db"))
    before_events = connection.execute(
        "SELECT COUNT(*) FROM value_strategy_state_events"
    ).fetchone()[0]
    before_cursors = connection.execute(
        "SELECT COUNT(*), MAX(updated_at) FROM value_strategy_state_cursors"
    ).fetchone()
    table_names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    get_value_watchpoint_projection_service().get_watchpoints("CN", "600460.SH")

    after_events = connection.execute(
        "SELECT COUNT(*) FROM value_strategy_state_events"
    ).fetchone()[0]
    after_cursors = connection.execute(
        "SELECT COUNT(*), MAX(updated_at) FROM value_strategy_state_cursors"
    ).fetchone()
    connection.close()
    report = {
        "strategy_events_before": before_events,
        "strategy_events_after": after_events,
        "strategy_cursors_before": list(before_cursors),
        "strategy_cursors_after": list(after_cursors),
        "company_research_watchpoints_table": "company_research_watchpoints" in table_names,
        "watchpoint_events_table": "watchpoint_events" in table_names,
    }
    destination = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "value-strategy"
        / "_watchpoint_invariance_audit.json"
    )
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

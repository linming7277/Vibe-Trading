"""Macro EOD validation: refresh, brief rebuild (with/without changes), close events."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from src.investment_research_supervisor import get_investment_research_daily_brief_service
from src.macro_line import get_macro_line_summary, refresh_macro_line
from src.macro_line.events import MacroEventStore

DB = Path.home() / ".vibe-trading" / "research.db"


def _print_macro_summary(as_of: str) -> None:
    summary = get_macro_line_summary(as_of)
    print(f"=== get_macro_line_summary {as_of} ===")
    print(json.dumps({
        "available": summary.get("available"),
        "changed": summary.get("changed"),
        "changes": summary.get("changes"),
        "text_preview": (summary.get("text") or "")[:280],
    }, ensure_ascii=False, indent=2))


def _rebuild_brief(as_of: str) -> dict:
    conn = sqlite3.connect(str(DB))
    conn.execute("DELETE FROM investment_research_daily_briefs WHERE research_as_of = ?", (as_of,))
    conn.commit()
    conn.close()

    brief = get_investment_research_daily_brief_service().build(research_as_of=as_of)
    record = brief.brief or {}
    payload = record.get("brief_payload") or record
    macro_env = payload.get("macro_environment") or {}

    event_store = MacroEventStore()
    try:
        event_store.close_events(as_of)
    finally:
        event_store.close()

    result = {
        "status": brief.status,
        "reused": brief.reused,
        "macro_available": macro_env.get("available"),
        "macro_changed": macro_env.get("changed"),
        "macro_changes": macro_env.get("changes"),
        "macro_text_preview": (macro_env.get("text") or "")[:280],
    }
    print(f"=== brief rebuild {as_of} ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    for as_of in ("2026-09-03", "2026-09-04"):
        macro = refresh_macro_line(as_of)
        print(f"=== refresh_macro_line {as_of} ===")
        print(json.dumps(
            {k: macro.get(k) for k in ("status", "regime", "event_count", "freshness")},
            ensure_ascii=False,
            indent=2,
            default=str,
        ))
        _print_macro_summary(as_of)

    _rebuild_brief("2026-09-03")
    changed_brief = _rebuild_brief("2026-09-04")

    conn = sqlite3.connect(str(DB))
    open_events = conn.execute(
        "SELECT COUNT(*) FROM macro_events WHERE research_as_of='2026-09-04' AND status='OPEN'",
    ).fetchone()[0]
    conn.close()
    print(f"=== 09-04 OPEN events after close === {open_events}")

    if changed_brief.get("macro_changed"):
        joined = "；".join(changed_brief.get("macro_changes") or [])
        assert "由「" in joined and "」变为「" in joined, "expected from→to style change text"


if __name__ == "__main__":
    main()

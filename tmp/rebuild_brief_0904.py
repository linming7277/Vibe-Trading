"""Rebuild the 2026-09-04 Daily Brief and resend the Feishu card."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from src.investment_research_supervisor import (
    get_daily_brief_notification_service,
    get_investment_research_daily_brief_service,
)

AS_OF = "2026-09-04"


def main() -> None:
    conn = sqlite3.connect(str(Path.home() / ".vibe-trading" / "research.db"))
    conn.execute("DELETE FROM investment_research_daily_briefs WHERE research_as_of = ?", (AS_OF,))
    conn.commit()
    conn.close()

    service = get_investment_research_daily_brief_service()
    built = service.build(research_as_of=AS_OF)
    payload = (built.brief or {}).get("brief_payload") or {}
    watchlist = list(payload.get("executive_watchlist") or [])
    print(json.dumps({
        "build_status": built.status,
        "reused": built.reused,
        "basis": payload.get("executive_watchlist_basis"),
        "watchlist_count": len(watchlist),
        "watchlist": [
            {"code": item.get("stock_code"), "name": item.get("company_name")}
            for item in watchlist
        ],
        "macro_changed": (payload.get("macro_environment") or {}).get("changed"),
        "macro_preview": str((payload.get("macro_environment") or {}).get("text") or "")[:180],
    }, ensure_ascii=False, indent=2))
    if built.status != "READY" or not watchlist:
        raise SystemExit("brief rebuild did not produce a Focus watchlist")

    notify = get_daily_brief_notification_service().notify(research_as_of=AS_OF)
    print(json.dumps({
        "notify_status": notify.get("status"),
        "covers_low_value": notify.get("covers_low_value"),
        "delivery_status": (notify.get("delivery") or {}).get("status"),
        "message_id": (notify.get("delivery") or {}).get("message_id"),
        "error": notify.get("error") or (notify.get("delivery") or {}).get("error"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

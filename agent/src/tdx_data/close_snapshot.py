"""Shared provenance checks for A-share end-of-day snapshots."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
# The continuous session ends at 15:00. Keep a short buffer so a run started
# during the closing auction cannot be labelled as the final market close.
CLOSE_SNAPSHOT_NOT_BEFORE = time(15, 5)


def close_snapshot_provenance_error(snapshot: dict[str, Any] | None) -> str | None:
    """Return why a snapshot cannot represent its stated market day's close."""
    if not snapshot:
        return "尚无收盘数据快照"
    market_date = str(snapshot.get("market_date") or "")[:10]
    completed_at = _as_shanghai(snapshot.get("completed_at"))
    if not market_date:
        return "收盘快照缺少市场日期"
    if completed_at is None:
        return "收盘快照缺少完成时间"
    if completed_at.date().isoformat() != market_date:
        return "收盘快照完成日期与市场日期不一致"
    if completed_at.time().replace(tzinfo=None) < CLOSE_SNAPSHOT_NOT_BEFORE:
        return "收盘快照生成时间早于 15:05，不能作为收盘数据"
    return None


def is_qualified_close_snapshot(snapshot: dict[str, Any] | None) -> bool:
    return close_snapshot_provenance_error(snapshot) is None


def _as_shanghai(value: object) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=SHANGHAI)
        return parsed.astimezone(SHANGHAI)
    except (TypeError, ValueError):
        return None

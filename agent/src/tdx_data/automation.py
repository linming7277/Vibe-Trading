"""Independent, bounded scheduler for the A-share data refresh pipeline.

The scheduler intentionally triggers profile-sized refreshes instead of the
legacy ``all`` job.  It never runs missed jobs immediately after a restart:
the next eligible slot is used, while the previous successful snapshot remains
visible as stale to downstream strategy schedulers.
"""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .close_snapshot import is_qualified_close_snapshot
from .service import PROFILE_META, get_tdx_service
from .store import TdxDataStore


SHANGHAI = ZoneInfo("Asia/Shanghai")
_RETRY_DELAY = timedelta(minutes=10)
_MAX_RETRIES = 3


def _next_weekday(local: datetime) -> datetime:
    candidate = local
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def next_refresh_slot(current: datetime | None = None) -> datetime:
    """Return the next known CN refresh slot without backfilling missed work."""
    local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    dates = [local.date() + timedelta(days=offset) for offset in range(8)]
    slots = (time(9, 20), time(9, 25), time(15, 30), time(16, 0), time(20, 30))
    for date_value in dates:
        if date_value.weekday() >= 5:
            continue
        for slot in slots:
            candidate = datetime.combine(date_value, slot, SHANGHAI)
            if candidate > local:
                return candidate
    return _next_weekday(local + timedelta(days=1))


def due_profiles(current: datetime) -> list[str]:
    """Profiles eligible in the current minute, in dependency order."""
    local = current.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return []
    clock = local.time().replace(second=0, microsecond=0, tzinfo=None)
    if clock == time(9, 20):
        return ["reference_daily"]
    if (time(9, 25) <= clock <= time(11, 30) or time(13, 0) <= clock <= time(15, 0)) and local.minute % 5 == 0:
        return ["market_intraday"]
    if clock == time(16, 0):
        return ["market_close"]
    if clock == time(15, 30):
        return ["reference_daily"]
    if clock == time(20, 30):
        return ["history_nightly"]
    if local.weekday() == 0 and clock == time(20, 45):
        return ["fundamental_weekly"]
    return []


class DataRefreshScheduler:
    """Owns wall-clock data collection; strategies only consume snapshots."""

    def __init__(self, *, store: TdxDataStore | None = None) -> None:
        self.store = store or TdxDataStore()
        self._owns_store = store is None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="data-refresh-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        if self._owns_store:
            self.store.close()

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        value = self.store.get_refresh_automation("CN")
        value.update({
            "running": bool(self._thread and self._thread.is_alive()),
            "next_run_at": value.get("next_run_at") or next_refresh_slot().isoformat(),
            "profiles": [{"code": code, **meta} for code, meta in PROFILE_META.items() if code != "all"],
        })
        return value

    def set_enabled(self, enabled: bool) -> dict:
        value = self.store.update_refresh_automation("CN", enabled=enabled)
        if enabled:
            value = self.store.update_refresh_automation("CN", next_run_at=next_refresh_slot().isoformat(), last_error="")
            self.wake()
        return value

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # A failed scheduler tick must never kill the API process.
                pass
            self._wake.wait(20)
            self._wake.clear()

    def tick(self, current: datetime | None = None) -> None:
        local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        automation = self.store.get_refresh_automation("CN")
        self.store.update_refresh_automation("CN", next_run_at=next_refresh_slot(local).isoformat())
        if not automation.get("enabled"):
            return
        for profile in due_profiles(local):
            if not self._eligible(profile, local):
                continue
            try:
                get_tdx_service().start_update(profile)
                self.store.update_refresh_automation("CN", last_profile=profile, last_status="started", last_error="")
            except RuntimeError as exc:
                # Another process owns the named DB lock.  This is expected on
                # a desktop restart or a manual update, not a scheduler fault.
                self.store.update_refresh_automation("CN", last_profile=profile, last_status="waiting_lock", last_error=str(exc))

    def _eligible(self, profile: str, local: datetime) -> bool:
        date_value = local.date().isoformat()
        latest = self.store.latest_refresh_run(profile, "CN", date_value)
        if latest is None:
            return True
        status = str(latest.get("status") or "")
        if status in {"queued", "running", "completed"}:
            # A manually triggered market-close run may complete before the
            # market closes. It must not suppress the scheduled post-close run.
            if profile == "market_close" and status == "completed" and not is_qualified_close_snapshot(latest):
                return True
            # Intraday profiles are intentionally allowed every five minutes.
            if profile != "market_intraday":
                return False
            created = _parse_time(latest.get("created_at"))
            return created is None or local - created >= timedelta(minutes=4)
        if status in {"failed", "partial"}:
            created = _parse_time(latest.get("completed_at") or latest.get("created_at"))
            return int(latest.get("retry_count") or 0) < _MAX_RETRIES and created is not None and local - created >= _RETRY_DELAY
        return True


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.astimezone(SHANGHAI) if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)
    except (TypeError, ValueError):
        return None


_scheduler: DataRefreshScheduler | None = None


def get_data_refresh_scheduler() -> DataRefreshScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = DataRefreshScheduler()
    return _scheduler


def start_data_refresh_scheduler() -> None:
    get_data_refresh_scheduler().start()


def stop_data_refresh_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None

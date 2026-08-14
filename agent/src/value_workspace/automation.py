"""Dedicated, opt-in close scheduler for the A-share value research universe."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.tdx_data.service import get_tdx_service

from .service import ValueWorkspaceService
from .store import ValueWorkspaceStore


SHANGHAI = ZoneInfo("Asia/Shanghai")


def next_value_run(current: datetime | None = None) -> datetime:
    local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    candidate = datetime.combine(local.date(), time(16, 45), SHANGHAI)
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _is_data_ready(universes: list[dict], as_of: str) -> tuple[bool, str]:
    company = next((item for universe in universes for item in universe.get("companies", [])), None)
    if not company:
        return False, "活动研究宇宙为空"
    overview = get_tdx_service().security_overview(company["symbol"])
    if not overview:
        return False, "通达信行情不可用"
    quote = overview.get("quote") or {}
    if not quote.get("price") or bool((overview.get("cache") or {}).get("stale")):
        return False, "通达信收盘行情尚未就绪"
    data_date = str(overview.get("as_of") or "")[:10]
    if data_date and data_date < as_of:
        return False, f"行情日期仍为 {data_date}"
    return True, ""


class ValueResearchScheduler:
    def __init__(self) -> None:
        self.owner = f"value-scheduler:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._retry_counts: dict[str, int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="value-research-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # State and the next successful tick remain available even if
                # a vendor bridge fails unexpectedly.
                pass
            self._wake.wait(30)
            self._wake.clear()

    def tick(self, current: datetime | None = None) -> None:
        local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        store = ValueWorkspaceStore()
        try:
            automation = store.get_automation()
            if not automation.get("enabled"):
                store.update_automation(next_run_at=None)
                return
            due = next_value_run(local - timedelta(days=1) if local.time().replace(tzinfo=None) >= time(16, 45) else local)
            # The subtraction above makes today's 16:45 the due point after it
            # has passed; before 16:45 next_value_run returns today's point.
            if due.date() < local.date() or due.weekday() >= 5:
                due = next_value_run(local)
            store.update_automation(next_run_at=due.isoformat())
            if local < due or local.weekday() >= 5:
                return
            as_of = local.date().isoformat()
            last_run = store.get_incremental_run(str(automation.get("last_run_id") or "")) if automation.get("last_run_id") else None
            if last_run and last_run.get("as_of") == as_of and last_run.get("status") in {"completed", "partial"}:
                store.update_automation(next_run_at=next_value_run(local + timedelta(minutes=1)).isoformat())
                return
            lock_now = datetime.now(timezone.utc)
            lock_until = (lock_now + timedelta(hours=2)).isoformat()
            if not store.acquire_automation_lock(self.owner, until=lock_until, now_value=lock_now.isoformat()):
                return
            try:
                universes = store.active_universes()
                if not universes:
                    store.update_automation(last_status="skipped", last_error="没有活动研究宇宙")
                    return
                ready, reason = _is_data_ready(universes, as_of)
                retries = self._retry_counts.get(as_of, 0)
                if not ready and retries < int(automation.get("max_retries") or 3):
                    self._retry_counts[as_of] = retries + 1
                    retry_at = local + timedelta(minutes=int(automation.get("retry_minutes") or 20))
                    store.update_automation(next_run_at=retry_at.isoformat(), last_status="waiting_data", last_error=reason)
                    return
                if not ready:
                    events = self._emit_stale_events(store, as_of, reason)
                    self._deliver(events)
                    store.update_automation(last_status="stale", last_error=reason)
                    return
                last_run_id = None
                final_status = "completed"
                all_events: list[dict] = []
                for universe in universes:
                    service = ValueWorkspaceService(store)
                    operation, created = service.create_operation(
                        universe["id"], run_kind="incremental", as_of=as_of, trigger_kind="scheduled",
                    )
                    last_run_id = operation["id"]
                    if created or operation["status"] in {"queued", "partial", "failed"}:
                        result = service.run_operation(operation["id"])
                    else:
                        result = operation
                    all_events.extend(result.get("generated_events") or [])
                    if result.get("status") != "completed":
                        final_status = "partial"
                self._deliver(all_events)
                self._retry_counts.pop(as_of, None)
                store.update_automation(
                    last_run_id=last_run_id, last_status=final_status, last_error="",
                    next_run_at=next_value_run(local + timedelta(minutes=1)).isoformat(),
                )
            finally:
                store.release_automation_lock(self.owner)
        finally:
            store.close()

    @staticmethod
    def _emit_stale_events(store: ValueWorkspaceStore, as_of: str, reason: str) -> list[dict]:
        events = []
        for monitor in store.list_monitors():
            if monitor["status"] != "active" or not monitor.get("universe_id"):
                continue
            events.append(store.add_event(
                monitor_id=monitor["id"], event_key=f"{monitor['id']}:stale:{as_of}",
                event_type="stale", severity="warning", title=f"{monitor['name']} · 数据过期",
                message=reason, payload={
                    "symbol": monitor["symbol"], "data_as_of": as_of,
                    "rule_version": "value-automation-v1.0.0",
                }, channels=monitor["channels"],
            ))
        return events

    @staticmethod
    def _deliver(events: list[dict]) -> None:
        if not events:
            return
        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        try:
            runtime = host._get_channel_runtime() if host else None
        except Exception:
            runtime = None
        service = ValueWorkspaceService()
        try:
            asyncio.run(service.deliver_notifications(events, getattr(runtime, "manager", None)))
        finally:
            service.close()


_scheduler: ValueResearchScheduler | None = None


def get_value_research_scheduler() -> ValueResearchScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ValueResearchScheduler()
    return _scheduler


def start_value_research_scheduler() -> None:
    get_value_research_scheduler().start()


def stop_value_research_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.stop()

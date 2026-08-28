"""Close scheduler for the single Value Line L3 -> Top2 -> research path."""

from __future__ import annotations

import os
import threading
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.adjusted_daily_bars.service import get_adjusted_daily_bar_service
from src.financial_analysis.service import get_financial_analysis_service
from src.level3_leaders.service import Level3IndustryLeaderService
from src.level3_leaders.store import Level3LeaderStore
from src.tdx_data.service import get_tdx_service


SHANGHAI = ZoneInfo("Asia/Shanghai")


def next_value_run(current: datetime | None = None) -> datetime:
    local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    candidate = datetime.combine(local.date(), time(16, 45), SHANGHAI)
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class ValueResearchScheduler:
    """Consume one published close snapshot and refresh the immutable L3 pool."""

    def __init__(self) -> None:
        self.owner = f"value-l3-scheduler:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._retry_counts: dict[str, int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="value-l3-scheduler", daemon=True)
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
                pass
            self._wake.wait(30)
            self._wake.clear()

    @staticmethod
    def _due(local: datetime) -> datetime:
        today = datetime.combine(local.date(), time(16, 45), SHANGHAI)
        return today if local >= today and local.weekday() < 5 else next_value_run(local)

    def tick(self, current: datetime | None = None) -> None:
        local = (current or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        store = Level3LeaderStore()
        try:
            automation = store.get_automation()
            if not automation.get("enabled"):
                store.update_automation(next_run_at=None)
                return
            # ``waiting_data`` and ``stale`` are persisted operational states.
            # Honour their retry deadline before calculating today's ordinary
            # 16:45 run; otherwise the 30-second polling loop burns all retry
            # attempts while the close refresh is still in flight.
            deferred_until = self._parse_scheduled_time(automation.get("next_run_at"))
            if (
                str(automation.get("last_status") or "") in {"waiting_data", "stale"}
                and deferred_until is not None
                and local < deferred_until
            ):
                return
            due = self._due(local)
            store.update_automation(next_run_at=due.isoformat())
            if local < due or local.weekday() >= 5:
                return
            self._run_latest_completed(store, automation=automation, local=local, allow_refresh_request=True)
        finally:
            store.close()

    @staticmethod
    def _parse_scheduled_time(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
            return parsed.astimezone(SHANGHAI) if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError):
            return None

    def recover_latest_completed(self) -> dict:
        """Run the same stage-resume path for the latest qualified close.

        This is an operations recovery entry point, not a separate refresh
        implementation.  It never invents a natural-day as_of: the target is
        always the latest published qualified market_close date.
        """
        local = datetime.now(SHANGHAI)
        store = Level3LeaderStore()
        try:
            automation = store.get_automation()
            if not automation.get("enabled"):
                return {"status": "DISABLED"}
            return self._run_latest_completed(store, automation=automation, local=local, allow_refresh_request=False)
        finally:
            store.close()

    @staticmethod
    def _stage_summary(stages: dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in stages.items())

    @staticmethod
    def _financial_ready(pool: dict) -> bool:
        states = [item for item in pool.get("research_states", []) if item.get("lifecycle_status") != "OUT_OF_TOP2"]
        return bool(states) and all(str(item.get("research_status") or "") == "READY" for item in states)

    @staticmethod
    def _pool_stage_ready(service: object, *, as_of: str, pool_id: str) -> bool:
        repository = getattr(service, "repository")
        marker = repository.refresh_status(source_as_of=as_of, source_pool_id=pool_id)
        if not marker or marker.get("status") != "COMPLETED":
            return False
        return all(
            str(item.get("source_as_of") or "") == as_of and str(item.get("source_pool_id") or "") == pool_id
            for item in repository.active("CN")
        )

    _DAILY_BAR_BACKFILL_LIMIT = 30

    def _ensure_low_value_daily_bars(self, focus_service: object, *, as_of: str) -> dict:
        """Backfill front-adjusted daily bars for active low-value members.

        The bar cache is materialized per company, but the historical backfill
        only covered one frozen L3 snapshot; members entering the pool later
        had no bars and therefore no support zones.  The repair is bounded
        per run and fail-soft — a bar-cache gap must never block the core
        chain.  Members entering the pool today are picked up by the next
        run, so a brand-new entrant shows support data at most one day late.
        """
        summary: dict = {"active_members": 0, "missing": 0, "backfilled": [], "failed": [], "remaining": 0}
        try:
            repository = getattr(focus_service, "repository", None)
            members = list(repository.active("CN")) if repository is not None else []
        except Exception:
            members = []
        summary["active_members"] = len(members)
        if not members:
            return summary
        try:
            bar_service = get_adjusted_daily_bar_service()
            missing: list[str] = []
            for member in members:
                code = str(member.get("stock_code") or "").upper()
                if not code or code in missing:
                    continue
                try:
                    status = bar_service.status("CN", code)
                except Exception:
                    missing.append(code)
                    continue
                if not status.get("last_date") or str(status.get("coverage_status") or "INSUFFICIENT") == "INSUFFICIENT":
                    missing.append(code)
            summary["missing"] = len(missing)
            for code in missing[: self._DAILY_BAR_BACKFILL_LIMIT]:
                try:
                    result = bar_service.refresh_company("CN", code, as_of=as_of)
                except Exception as exc:  # one company must not sink the batch
                    summary["failed"].append({"stock_code": code, "error": f"{type(exc).__name__}: {exc}"[:160]})
                    continue
                if str(result.get("coverage_status") or "") in {"READY", "PARTIAL"}:
                    summary["backfilled"].append(code)
                else:
                    summary["failed"].append({
                        "stock_code": code,
                        "error": str(result.get("error") or "coverage still insufficient")[:160],
                    })
                time_module.sleep(0.15)
            summary["remaining"] = max(0, len(missing) - self._DAILY_BAR_BACKFILL_LIMIT)
        except Exception:
            summary["failed"].append({"stock_code": "*", "error": "daily bar backfill step failed"})
        return summary

    def _resolve_latest_target(self, tdx: object, *, natural_date: str) -> tuple[bool, str, dict | None]:
        resolver = getattr(tdx, "latest_qualified_close_snapshot", None)
        if callable(resolver):
            return resolver()
        # Compatibility for focused tests and older provider wrappers.  The
        # production TdxDataService always supplies the resolver above.
        return tdx.close_snapshot_ready(natural_date)

    def _run_latest_completed(self, store: Level3LeaderStore, *, automation: dict, local: datetime,
                              allow_refresh_request: bool) -> dict:
        tdx = get_tdx_service()
        ready, reason, snapshot = self._resolve_latest_target(tdx, natural_date=local.date().isoformat())
        if not ready or not snapshot:
            natural_date = local.date().isoformat()
            retries = self._retry_counts.get(natural_date, 0)
            if allow_refresh_request and retries < int(automation.get("max_retries") or 3):
                try:
                    tdx.start_update("market_close")
                except RuntimeError:
                    pass
            if retries < int(automation.get("max_retries") or 3):
                self._retry_counts[natural_date] = retries + 1
                store.update_automation(
                    last_status="waiting_data", last_error=reason,
                    next_run_at=(local + timedelta(minutes=int(automation.get("retry_minutes") or 20))).isoformat(),
                )
                return {"status": "WAITING_DATA", "reason": reason}
            store.update_automation(
                last_status="stale", last_error=reason,
                next_run_at=next_value_run(local + timedelta(minutes=1)).isoformat(),
            )
            return {"status": "STALE", "reason": reason}

        as_of = str(snapshot.get("market_date") or "")[:10]
        if not as_of:
            store.update_automation(last_status="stale", last_error="qualified market_close has no market_date")
            return {"status": "STALE", "reason": "qualified market_close has no market_date"}

        lock_now = datetime.now(timezone.utc)
        if not store.acquire_automation_lock(
            self.owner, until=(lock_now + timedelta(hours=4)).isoformat(), now_value=lock_now.isoformat(),
        ):
            return {"status": "LOCKED", "as_of": as_of}
        try:
            stages = {"MARKET_READY": "READY", "L3_READY": "PENDING", "FINANCIAL_READY": "PENDING",
                      "LOW_VALUE_DAILY_BAR_READY": "PENDING",
                      "LOW_VALUE_POOL_READY": "PENDING", "LOW_VALUE_EVENTS_READY": "PENDING",
                      "RISK_SNAPSHOT_READY": "PENDING", "DAILY_RESEARCH_BRIEF_READY": "PENDING",
                      "LOW_VALUE_NOTIFICATION_READY": "PENDING"}
            pool = store.pool_for_as_of(as_of)
            if pool:
                stages["L3_READY"] = "REUSED"
            else:
                leader_service = Level3IndustryLeaderService(store=store)
                try:
                    result = leader_service.build_level3_leaders(as_of)
                    pool = result["leader_pool"]
                finally:
                    leader_service.value_line.close()
                stages["L3_READY"] = "READY"

            if not pool:
                raise RuntimeError("L3 pool was not materialized")
            if self._financial_ready(pool):
                stages["FINANCIAL_READY"] = "REUSED"
            else:
                research = self._prepare_incremental(pool)
                pool = store.pool_for_as_of(as_of) or pool
                if research["failed"] or not self._financial_ready(pool):
                    stages["FINANCIAL_READY"] = "PARTIAL"
                    return self._record_partial(store, pool=pool, as_of=as_of, stages=stages,
                                                message=f"financial prepare incomplete: {research['failed']} failed")
                stages["FINANCIAL_READY"] = "READY"

            from src.low_value_leader_notifications import get_low_value_leader_notification_service
            notification_service = get_low_value_leader_notification_service()
            notification_service.prepare_activation()

            from src.low_value_leader_pool import get_low_value_leader_pool_service
            focus_service = get_low_value_leader_pool_service()
            bar_summary = self._ensure_low_value_daily_bars(focus_service, as_of=as_of)
            stages["LOW_VALUE_DAILY_BAR_READY"] = (
                "REUSED" if bar_summary["missing"] == 0
                else "PARTIAL" if bar_summary["failed"] or bar_summary["remaining"]
                else "READY"
            )
            if self._pool_stage_ready(focus_service, as_of=as_of, pool_id=str(pool["id"])):
                stages["LOW_VALUE_POOL_READY"] = "REUSED"
                # Zero events are a valid completed pool synchronization; the
                # same durable pool-refresh marker is the event-stage proof.
                stages["LOW_VALUE_EVENTS_READY"] = "REUSED"
            else:
                try:
                    focus = focus_service.refresh_low_value_leader_pool(as_of=as_of)
                except Exception as exc:
                    stages["LOW_VALUE_POOL_READY"] = "FAILED"
                    return self._record_partial(store, pool=pool, as_of=as_of, stages=stages,
                                                message=f"low-value pool refresh failed: {type(exc).__name__}: {exc}")
                if focus.get("status") != "COMPLETED":
                    stages["LOW_VALUE_POOL_READY"] = "PARTIAL"
                    return self._record_partial(store, pool=pool, as_of=as_of, stages=stages,
                                                message="low-value pool refresh partial")
                stages["LOW_VALUE_POOL_READY"] = "READY"
                stages["LOW_VALUE_EVENTS_READY"] = "READY"

            from src.low_value_risk_snapshot import get_low_value_pool_risk_snapshot_service
            risk_service = get_low_value_pool_risk_snapshot_service()
            coverage = risk_service.coverage_for_active_pool(source_as_of=as_of)
            if coverage["complete"]:
                stages["RISK_SNAPSHOT_READY"] = "REUSED"
            else:
                result = risk_service.refresh_active_low_value_risk_snapshots(source_as_of=as_of)
                coverage = risk_service.coverage_for_active_pool(source_as_of=as_of)
                if result.get("status") != "COMPLETED" or not coverage["complete"]:
                    stages["RISK_SNAPSHOT_READY"] = "PARTIAL"
                    return self._record_partial(store, pool=pool, as_of=as_of, stages=stages,
                                                message="risk snapshot coverage incomplete")
                stages["RISK_SNAPSHOT_READY"] = "READY"

            # Source preparation is deliberately asynchronous: it may reuse or
            # collect periodic reports and run existing business research, but
            # must never delay the core L3/low-value/risk-snapshot projection.
            from src.risk_research_preparation import schedule_current_low_value_preparation
            stages["LOW_VALUE_RISK_DATA_PREPARATION"] = (
                "QUEUED" if schedule_current_low_value_preparation(source_as_of=as_of) else "RUNNING"
            )

            from src.investment_research_supervisor import (
                get_daily_brief_bitable_publisher,
                get_investment_research_daily_brief_service,
            )
            brief_result = get_investment_research_daily_brief_service().build(research_as_of=as_of)
            if brief_result.status != "READY":
                stages["DAILY_RESEARCH_BRIEF_READY"] = "FAILED"
                return self._record_partial(
                    store, pool=pool, as_of=as_of, stages=stages,
                    message="daily research brief generation failed",
                )
            stages["DAILY_RESEARCH_BRIEF_READY"] = "REUSED" if brief_result.reused else "READY"

            get_daily_brief_bitable_publisher().publish(research_as_of=as_of)

            # The scheduled job refreshes research data and publishes the
            # current Bitable snapshot, but never pushes a daily-report chat
            # message.  Low-value pool event alerts remain independent and
            # keep their existing delivery / retry behavior.
            notification = notification_service.notify(research_as_of=as_of)
            if notification.get("status") == "FAILED":
                stages["LOW_VALUE_NOTIFICATION_READY"] = "FAILED"
                return self._record_partial(
                    store, pool=pool, as_of=as_of, stages=stages,
                    message="low-value Feishu notification failed",
                )
            stages["LOW_VALUE_NOTIFICATION_READY"] = str(notification.get("status") or "READY")

            self._retry_counts.pop(as_of, None)
            store.update_automation(
                last_run_id=pool["id"], last_status="completed", last_error=self._stage_summary(stages),
                next_run_at=next_value_run(local + timedelta(minutes=1)).isoformat(),
            )
            return {"status": "COMPLETED", "as_of": as_of, "pool_id": pool["id"], "stages": stages}
        finally:
            store.release_automation_lock(self.owner)

    def _record_partial(self, store: Level3LeaderStore, *, pool: dict, as_of: str,
                        stages: dict[str, str], message: str) -> dict:
        store.update_automation(
            last_run_id=str(pool.get("id") or ""), last_status="partial",
            last_error=f"{message}; {self._stage_summary(stages)}",
        )
        return {"status": "PARTIAL", "as_of": as_of, "pool_id": pool.get("id"), "stages": stages, "error": message}

    @staticmethod
    def _prepare_incremental(pool: dict) -> dict[str, int]:
        states = [
            item for item in pool.get("research_states", [])
            if item.get("lifecycle_status") != "OUT_OF_TOP2" and item.get("research_status") != "READY"
        ]
        completed = failed = 0

        def prepare(item: dict) -> tuple[dict, dict]:
            snapshot = get_financial_analysis_service().prepare(
                item["stock_code"], as_of=str(pool["as_of"]),
            )
            return item, snapshot

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="value-l3-research") as executor:
            futures = [executor.submit(prepare, item) for item in states]
            for future in as_completed(futures):
                try:
                    item, snapshot = future.result()
                    status = "READY" if snapshot.get("feature_status") in {"READY", "PARTIAL"} else "PARTIAL"
                    update_store = Level3LeaderStore()
                    try:
                        update_store.update_research_state(
                            pool["id"], item["stock_code"], status=status,
                            snapshot_id=snapshot.get("id"), researched_at=snapshot.get("updated_at"),
                        )
                    finally:
                        update_store.close()
                    completed += 1
                except Exception:
                    failed += 1
        return {"total": len(states), "completed": completed, "failed": failed}


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

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.level3_leaders.store import Level3LeaderStore
from src.value_workspace import automation

SHANGHAI = ZoneInfo("Asia/Shanghai")


def leader(as_of: str, industry: str, symbol: str, rank: int, score: float) -> dict:
    return {
        "as_of": as_of,
        "level1_code": "L1", "level1_name": "一级",
        "level2_code": "L2", "level2_name": "二级",
        "level3_code": industry, "level3_name": "三级" + industry,
        "stock_code": symbol, "stock_name": symbol,
        "leader_rank": rank, "leader_score": score,
        "leader_formula_version": "leader-test-v1",
        "component_scores": {"quality": score},
        "coverage": 1.0, "eligibility_status": "eligible",
        "eligibility_reasons": [], "metric_applicability_notes": [],
        "raw_features": {}, "provenance_key": industry + ":" + symbol,
    }


def build(store: Level3LeaderStore, key: str, as_of: str, rows: list[dict]) -> dict:
    run = store.start_run(
        idempotency_key=key, as_of=as_of, catalog_as_of=as_of,
        formula_version="leader-test-v1",
    )
    store.finish_run(run["id"], rows=rows, statistics={})
    pool, _ = store.materialize_pool(run["id"])
    return pool


def test_next_value_run_skips_weekend() -> None:
    friday_after_close = datetime(2026, 8, 21, 17, 0, tzinfo=SHANGHAI)
    assert automation.next_value_run(friday_after_close).isoformat() == "2026-08-24T16:45:00+08:00"


def _mark_pool_financial_ready(store: Level3LeaderStore, pool: dict) -> None:
    for item in pool["research_states"]:
        if item["lifecycle_status"] != "OUT_OF_TOP2":
            store.update_research_state(pool["id"], item["stock_code"], status="READY")


def _install_completed_stage_fakes(monkeypatch: pytest.MonkeyPatch, *, pool_ready: bool, risk_ready: bool):
    import src.low_value_leader_pool as low_value_module
    import src.low_value_risk_snapshot as risk_module
    import src.investment_research_supervisor as supervisor_module

    class FocusRepository:
        def __init__(self):
            self.ready = pool_ready

        def refresh_status(self, *, source_as_of: str, source_pool_id: str):
            return {"status": "COMPLETED"} if self.ready else None

        def active(self, _market: str):
            return []

    class Focus:
        def __init__(self):
            self.repository = FocusRepository()
            self.calls: list[str] = []

        def refresh_low_value_leader_pool(self, *, as_of: str):
            self.calls.append(as_of)
            self.repository.ready = True
            return {"status": "COMPLETED"}

    class Risk:
        def __init__(self):
            self.ready = risk_ready
            self.calls: list[str] = []

        def coverage_for_active_pool(self, *, source_as_of: str):
            return {"complete": self.ready}

        def refresh_active_low_value_risk_snapshots(self, *, source_as_of: str):
            self.calls.append(source_as_of)
            self.ready = True
            return {"status": "COMPLETED"}

    focus, risk = Focus(), Risk()

    class BarService:
        def __init__(self):
            self.refreshed: list[str] = []

        def status(self, _market: str, _stock_code: str):
            return {"coverage_status": "READY", "last_date": "2026-08-18"}

        def refresh_company(self, _market: str, stock_code: str, *, as_of: str | None = None):
            self.refreshed.append(stock_code)
            return {"coverage_status": "READY"}

    monkeypatch.setattr(automation, "get_adjusted_daily_bar_service", lambda: BarService())

    class Briefs:
        def build(self, *, research_as_of: str):
            return type("Result", (), {"status": "READY", "reused": False})()

    class BitablePublisher:
        def publish(self, *, research_as_of: str):
            return {"status": "READY", "research_as_of": research_as_of}

    class BriefCards:
        def notify(self, *, research_as_of: str):
            return {"status": "READY", "covers_low_value": True}

    monkeypatch.setattr(low_value_module, "get_low_value_leader_pool_service", lambda: focus)
    monkeypatch.setattr(risk_module, "get_low_value_pool_risk_snapshot_service", lambda: risk)
    monkeypatch.setattr(supervisor_module, "get_investment_research_daily_brief_service", lambda: Briefs())
    monkeypatch.setattr(supervisor_module, "get_daily_brief_bitable_publisher", lambda: BitablePublisher())
    monkeypatch.setattr(supervisor_module, "get_daily_brief_notification_service", lambda: BriefCards())
    return focus, risk


def test_automation_backfills_missing_member_daily_bars_before_pool_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 90),
        ])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self):
            return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=False, risk_ready=False)

    class MemberRepository:
        def refresh_status(self, **_kwargs):
            return None

        def active(self, _market: str):
            return [{"stock_code": "000001.SZ"}, {"stock_code": "000009.SZ"}]

    focus.repository = MemberRepository()

    events: list[str] = []
    original_refresh = focus.refresh_low_value_leader_pool

    def refresh_pool(*, as_of: str):
        events.append("pool")
        return original_refresh(as_of=as_of)

    focus.refresh_low_value_leader_pool = refresh_pool

    class BarService:
        def status(self, _market: str, stock_code: str):
            if stock_code == "000009.SZ":
                return {"coverage_status": "INSUFFICIENT", "last_date": None}
            return {"coverage_status": "READY", "last_date": "2026-08-18"}

        def refresh_company(self, _market: str, stock_code: str, *, as_of: str | None = None):
            events.append(f"bars:{stock_code}:{as_of}")
            return {"coverage_status": "READY"}

    monkeypatch.setattr(automation, "get_adjusted_daily_bar_service", lambda: BarService())

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_status"] == "completed"
        assert "LOW_VALUE_DAILY_BAR_READY=READY" in state["last_error"]
        assert events == ["bars:000009.SZ:2026-08-19", "pool"]
    finally:
        verify.close()


def test_automation_bar_backfill_failure_never_blocks_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 90),
        ])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self):
            return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=False, risk_ready=False)
    focus.repository = type("Repo", (), {
        "refresh_status": staticmethod(lambda **_k: None),
        "active": staticmethod(lambda _m: [{"stock_code": "000009.SZ"}]),
    })()

    class BrokenBarService:
        def status(self, _market: str, _stock_code: str):
            return {"coverage_status": "INSUFFICIENT", "last_date": None}

        def refresh_company(self, _market: str, _stock_code: str, *, as_of: str | None = None):
            raise RuntimeError("tdx unavailable")

    monkeypatch.setattr(automation, "get_adjusted_daily_bar_service", lambda: BrokenBarService())

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_status"] == "completed"
        assert "LOW_VALUE_DAILY_BAR_READY=PARTIAL" in state["last_error"]
        assert focus.calls == ["2026-08-19"]
    finally:
        verify.close()


def test_automation_card_still_sends_when_bitable_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.low_value_risk_snapshot as risk_module
    import src.investment_research_supervisor as supervisor_module

    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 90),
        ])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self):
            return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=False, risk_ready=False)

    card_calls: list[str] = []

    class FailingPublisher:
        def publish(self, *, research_as_of: str):
            return {"status": "FAILED", "error": "feishu down"}

    class BriefCards:
        def notify(self, *, research_as_of: str):
            card_calls.append(research_as_of)
            return {"status": "READY"}

    monkeypatch.setattr(supervisor_module, "get_daily_brief_bitable_publisher", lambda: FailingPublisher())
    monkeypatch.setattr(supervisor_module, "get_daily_brief_notification_service", lambda: BriefCards())

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_status"] == "completed"
        assert "FEISHU_BITABLE_READY=FAILED" in state["last_error"]
        assert "DAILY_BRIEF_CARD_READY=READY" in state["last_error"]
        assert card_calls == ["2026-08-19"]
    finally:
        verify.close()


def test_automation_card_failure_marks_partial_but_publish_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.low_value_risk_snapshot as risk_module
    import src.investment_research_supervisor as supervisor_module

    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 90),
        ])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self):
            return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=False, risk_ready=False)

    class FailingCards:
        def notify(self, *, research_as_of: str):
            return {"status": "FAILED", "error": "card rejected"}

    monkeypatch.setattr(supervisor_module, "get_daily_brief_notification_service", lambda: FailingCards())

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_status"] == "partial"
        assert "FEISHU_BITABLE_READY=READY" in state["last_error"]
        assert "DAILY_BRIEF_CARD_READY=FAILED" in state["last_error"]
    finally:
        verify.close()


def test_same_day_l3_reuses_pool_but_resumes_missing_downstream_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [
            leader("2026-08-19", "I1", "000001.SZ", 1, 90),
        ])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self):
            return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=False, risk_ready=False)

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_run_id"] == pool["id"]
        assert state["last_status"] == "completed"
        assert focus.calls == ["2026-08-19"]
        assert risk.calls == ["2026-08-19"]
    finally:
        verify.close()


def test_same_day_l3_and_pool_only_resume_missing_risk_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [leader("2026-08-19", "I1", "000001.SZ", 1, 90)])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self): return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=True, risk_ready=False)
    scheduler = automation.ValueResearchScheduler()
    scheduler.tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))
    scheduler.tick(datetime(2026, 8, 19, 17, 1, tzinfo=SHANGHAI))
    assert focus.calls == []
    assert risk.calls == ["2026-08-19"]  # second tick is fully idempotent


def test_scheduler_uses_latest_completed_close_not_the_natural_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-close-19", "2026-08-19", [leader("2026-08-19", "I1", "000001.SZ", 1, 90)])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self): return True, "", {"market_date": "2026-08-19"}

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    _install_completed_stage_fakes(monkeypatch, pool_ready=True, risk_ready=True)
    automation.ValueResearchScheduler().tick(datetime(2026, 8, 20, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        assert verify.get_automation()["last_run_id"] == pool["id"]
        assert verify.current_pool()["as_of"] == "2026-08-19"
    finally:
        verify.close()


def test_weekend_tick_does_not_create_a_natural_day_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
    finally:
        store.close()
    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: pytest.fail("weekend must not resolve an EOD target"))
    automation.ValueResearchScheduler().tick(datetime(2026, 8, 22, 17, 0, tzinfo=SHANGHAI))


def test_partial_low_value_pool_never_runs_risk_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.low_value_leader_pool as low_value_module
    import src.low_value_risk_snapshot as risk_module

    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [leader("2026-08-19", "I1", "000001.SZ", 1, 90)])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self): return True, "", {"market_date": "2026-08-19"}

    class Repository:
        def refresh_status(self, **_kwargs): return None
        def active(self, _market): return []

    class Focus:
        repository = Repository()
        def refresh_low_value_leader_pool(self, *, as_of): return {"status": "PARTIAL"}

    class Risk:
        def coverage_for_active_pool(self, **_kwargs): pytest.fail("risk coverage must not run")
        def refresh_active_low_value_risk_snapshots(self, **_kwargs): pytest.fail("risk refresh must not run")

    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    monkeypatch.setattr(low_value_module, "get_low_value_leader_pool_service", lambda: Focus())
    monkeypatch.setattr(risk_module, "get_low_value_pool_risk_snapshot_service", lambda: Risk())
    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        assert verify.get_automation()["last_status"] == "partial"
    finally:
        verify.close()


def test_missing_close_snapshot_starts_refresh_then_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True, max_retries=3, retry_minutes=20)
    finally:
        store.close()

    class FakeTdx:
        updates: list[str] = []

        def close_snapshot_ready(self, as_of: str) -> tuple[bool, str, None]:
            return False, f"close snapshot {as_of} is missing", None

        def start_update(self, reason: str) -> None:
            self.updates.append(reason)

    fake = FakeTdx()
    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: fake)

    automation.ValueResearchScheduler().tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert fake.updates == ["market_close"]
        assert state["last_status"] == "waiting_data"
        assert "2026-08-19" in state["last_error"]
        assert state["lock_owner"] is None
    finally:
        verify.close()


def test_waiting_for_close_honors_the_persisted_retry_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True, max_retries=3, retry_minutes=20)
    finally:
        store.close()

    class FakeTdx:
        updates: list[str] = []

        def close_snapshot_ready(self, _as_of: str) -> tuple[bool, str, None]:
            return False, "close snapshot is missing", None

        def start_update(self, profile: str) -> None:
            self.updates.append(profile)

    fake = FakeTdx()
    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: fake)
    scheduler = automation.ValueResearchScheduler()
    scheduler.tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))
    scheduler.tick(datetime(2026, 8, 19, 17, 1, tzinfo=SHANGHAI))
    assert fake.updates == ["market_close"]

    scheduler.tick(datetime(2026, 8, 19, 17, 20, tzinfo=SHANGHAI))
    assert fake.updates == ["market_close", "market_close"]


def test_same_day_notification_failure_resumes_only_notification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.low_value_leader_notifications as notification_module

    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [leader("2026-08-19", "I1", "000001.SZ", 1, 90)])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self): return True, "", {"market_date": "2026-08-19"}

    class Notification:
        def __init__(self):
            self.prepares = 0
            self.calls = 0

        def prepare_activation(self):
            self.prepares += 1

        def notify(self, *, research_as_of: str):
            self.calls += 1
            return {"status": "FAILED" if self.calls == 1 else "READY", "research_as_of": research_as_of}

    notification = Notification()
    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=True, risk_ready=True)
    monkeypatch.setattr(notification_module, "get_low_value_leader_notification_service", lambda: notification)

    scheduler = automation.ValueResearchScheduler()
    scheduler.tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))
    failed = Level3LeaderStore(db_path)
    try:
        state = failed.get_automation()
        assert state["last_status"] == "partial"
        assert "LOW_VALUE_POOL_READY=REUSED" in state["last_error"]
        assert "RISK_SNAPSHOT_READY=REUSED" in state["last_error"]
        assert "LOW_VALUE_NOTIFICATION_READY=FAILED" in state["last_error"]
    finally:
        failed.close()

    scheduler.tick(datetime(2026, 8, 19, 17, 1, tzinfo=SHANGHAI))
    completed = Level3LeaderStore(db_path)
    try:
        state = completed.get_automation()
        assert state["last_status"] == "completed"
        assert "LOW_VALUE_NOTIFICATION_READY=READY" in state["last_error"]
    finally:
        completed.close()
    assert focus.calls == [] and risk.calls == []
    assert notification.prepares == 2 and notification.calls == 2


def test_daily_brief_failure_retries_without_rerunning_completed_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.investment_research_supervisor as supervisor_module
    import src.low_value_leader_notifications as notification_module

    db_path = tmp_path / "research.db"
    store = Level3LeaderStore(db_path)
    try:
        store.update_automation(enabled=True)
        pool = build(store, "run-current", "2026-08-19", [leader("2026-08-19", "I1", "000001.SZ", 1, 90)])
        _mark_pool_financial_ready(store, pool)
    finally:
        store.close()

    class FakeTdx:
        def latest_qualified_close_snapshot(self): return True, "", {"market_date": "2026-08-19"}

    class Briefs:
        def __init__(self): self.calls = 0
        def build(self, *, research_as_of: str):
            self.calls += 1
            return type("Result", (), {"status": "FAILED" if self.calls == 1 else "READY", "reused": False})()

    class BriefNotification:
        def __init__(self): self.calls: list[str] = []
        def notify(self, *, research_as_of: str):
            # 调度链路在表格发布成功后发送简报卡片（2026-09 产品决策）。
            self.calls.append(research_as_of)
            return {"status": "READY"}

    class Notification:
        def prepare_activation(self): pass
        def notify(self, *, research_as_of: str): return {"status": "READY"}

    briefs = Briefs()
    brief_cards = BriefNotification()
    monkeypatch.setattr(automation, "Level3LeaderStore", lambda: Level3LeaderStore(db_path))
    monkeypatch.setattr(automation, "get_tdx_service", lambda: FakeTdx())
    focus, risk = _install_completed_stage_fakes(monkeypatch, pool_ready=True, risk_ready=True)
    monkeypatch.setattr(supervisor_module, "get_investment_research_daily_brief_service", lambda: briefs)
    monkeypatch.setattr(supervisor_module, "get_daily_brief_notification_service", lambda: brief_cards)
    monkeypatch.setattr(notification_module, "get_low_value_leader_notification_service", lambda: Notification())

    scheduler = automation.ValueResearchScheduler()
    scheduler.tick(datetime(2026, 8, 19, 17, 0, tzinfo=SHANGHAI))
    scheduler.tick(datetime(2026, 8, 19, 17, 1, tzinfo=SHANGHAI))

    verify = Level3LeaderStore(db_path)
    try:
        state = verify.get_automation()
        assert state["last_status"] == "completed"
        assert "L3_READY=REUSED" in state["last_error"]
        assert "STRATEGY_STATE_EVENTS_READY=REUSED" in state["last_error"]
        assert "DAILY_RESEARCH_BRIEF_READY=READY" in state["last_error"]
    finally:
        verify.close()
    assert briefs.calls == 2
    # 第一次 build FAILED 时不得发卡；第二次 READY 后正常发送。
    assert brief_cards.calls == ["2026-08-19"]
    assert focus.calls == [] and risk.calls == []

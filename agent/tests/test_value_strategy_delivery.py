from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.value_strategy.delivery import (
    ValueStrategyEventDeliveryPolicy,
    ValueStrategyEventDeliveryStore,
    ValueStrategyEventNotificationService,
)


def _event(
    event_type: str,
    before: str,
    after: str,
    *,
    event_id: str = "event-1",
    batch: str = "batch-1",
    severity: str = "INFO",
    stock_code: str = "000544.SZ",
    stock_name: str = "中原环保",
    priority: str = "A",
    occurred_at: str = "2026-09-01T10:00:00+00:00",
) -> dict:
    return {
        "id": event_id,
        "transition_batch_id": batch,
        "event_type": event_type,
        "before_value": before,
        "after_value": after,
        "severity": severity,
        "stock_code": stock_code,
        "research_as_of": "2026-09-01",
        "occurred_at": occurred_at,
        "status": "OPEN",
        "after_state": {
            "stock_name": stock_name,
            "priority": {"tier": priority},
            "freshness": {"market_price_as_of": "2026-09-01"},
        },
    }


class _Events:
    def __init__(self, db_path, events):
        self.db_path = db_path
        self.events = events

    def list_events(self, **_kwargs):
        return list(self.events)


class _Sender:
    def __init__(self, error: Exception | None = None):
        self.error, self.calls = error, []

    def send_interactive_card(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def _enable(monkeypatch):
    monkeypatch.setenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED_AT", "2026-09-01T00:00:00+00:00")
    from src.investment_research_supervisor.daily_brief_notification_service import DailyBriefNotificationSettings

    monkeypatch.setattr(
        DailyBriefNotificationSettings,
        "from_channels_config",
        staticmethod(lambda: SimpleNamespace(target_id="oc_test")),
    )


def test_policy_covers_immediate_daily_and_history_only():
    policy = ValueStrategyEventDeliveryPolicy()
    assert policy.batch_mode([_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")]) == "IMMEDIATE"
    assert policy.batch_mode([_event("VALUE_SCOPE_ENTERED", "OUTSIDE_VALUE_SCOPE", "IN_VALUE_SCOPE")]) == "DAILY_DIGEST"
    assert policy.batch_mode([_event("PRICE_ATTENTION_CHANGED", "WATCH", "ATTENTION")]) == "DAILY_DIGEST"
    assert policy.batch_mode([_event("LEADER_SCOPE_CHANGED", "TOP1", "TOP2")]) == "HISTORY_ONLY"


def test_batch_aggregation_uses_highest_priority_chinese_summary():
    policy = ValueStrategyEventDeliveryPolicy()
    batch = policy.aggregate([
        _event("PRIMARY_ACTION_CHANGED", "PRIORITY_RESEARCH", "RISK_REVIEW", event_id="action", severity="INFO"),
        _event("PRIORITY_CHANGED", "A", "C", event_id="priority", severity="HIGH"),
        _event("RISK_CHANGED", "MEDIUM", "HIGH", event_id="risk", severity="HIGH"),
    ])[0]
    assert batch["delivery_mode"] == "IMMEDIATE"
    assert batch["title"] == "中原环保需要风险复核"
    assert "总体风险由中等风险变为高风险" in batch["summary"]
    assert "研究优先级由重点研究调整为暂缓研究" in batch["summary"]
    assert len(batch["event_ids"]) == 3


def test_thesis_falsified_is_critical_immediate():
    batch = ValueStrategyEventDeliveryPolicy().aggregate([
        _event("THESIS_STATUS_CHANGED", "FORMING", "FALSIFIED", severity="CRITICAL"),
    ])[0]
    assert batch["delivery_mode"] == "IMMEDIATE"
    assert batch["severity"] == "CRITICAL"
    assert batch["title"] == "中原环保核心逻辑已被证伪，需要立即复核"


def test_event_digest_translates_internal_enums_to_boss_facing_chinese():
    policy = ValueStrategyEventDeliveryPolicy()
    forming = policy.event_sentence(_event("THESIS_STATUS_CHANGED", "MISSING", "FORMING"))
    provisional = policy.event_sentence(_event("THESIS_AUTHORITY_CHANGED", "MISSING", "AI_PROVISIONAL"))
    trap = policy.event_sentence(_event("VALUE_TRAP_CHANGED", "UNKNOWN", "LOW_TRAP_RISK"))
    attention = policy.event_sentence(_event("PRICE_ATTENTION_CHANGED", "WAIT", "ATTENTION"))

    assert forming == "核心逻辑状态变为正在形成"
    assert provisional == "核心逻辑确认状态变为AI 初步核心逻辑（待人工复核）"
    assert trap == "低估陷阱状态由资料不足变为低估陷阱风险较低"
    assert attention == "价格关注条件由暂未形成关注条件变为需关注"


def test_delivery_store_is_idempotent(tmp_path):
    store = ValueStrategyEventDeliveryStore(tmp_path / "research.db")
    batch = ValueStrategyEventDeliveryPolicy().aggregate([_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])[0]
    first = store.save(batch, channel="feishu")
    second = store.save(batch, channel="feishu")
    assert first["idempotency_key"] == second["idempotency_key"]
    assert first["id"] == second["id"]
    store.close()


def test_disabled_notifications_do_not_send_or_create_delivery(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED", raising=False)
    events = _Events(tmp_path / "research.db", [_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])
    sender = _Sender()
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    result = service.deliver_immediate(research_as_of="2026-09-01")
    assert result["status"] == "DISABLED"
    assert sender.calls == []
    assert service.store.list_sent() == []
    service.store.close()


def test_send_is_one_per_batch_and_records_sent(tmp_path, monkeypatch):
    _enable(monkeypatch)
    events = _Events(tmp_path / "research.db", [_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])
    sender = _Sender()
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    assert service.deliver_immediate(research_as_of="2026-09-01")["sent"] == 1
    assert service.deliver_immediate(research_as_of="2026-09-01")["sent"] == 0
    assert len(sender.calls) == 1
    assert service.store.list_sent()[0]["delivery_status"] == "SENT"
    service.store.close()


def test_old_event_is_never_retroactively_sent(tmp_path, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED_AT", "2026-09-02T00:00:00+00:00")
    events = _Events(tmp_path / "research.db", [_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])
    sender = _Sender()
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    result = service.deliver_immediate(research_as_of="2026-09-01")
    assert result["sent"] == 0 and result["skipped"] == 1
    assert sender.calls == []
    service.store.close()


def test_permanent_failure_stops_retry_and_preserves_event(tmp_path, monkeypatch):
    _enable(monkeypatch)
    events = _Events(tmp_path / "research.db", [_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])
    sender = _Sender(RuntimeError("auth token invalid"))
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    service.deliver_immediate(research_as_of="2026-09-01")
    service.deliver_immediate(research_as_of="2026-09-01")
    row = service.store.list_for_batches(["batch-1"])["batch-1"]
    assert row["delivery_status"] == "SKIPPED"
    assert row["attempt_count"] == 1
    assert len(sender.calls) == 1
    service.store.close()


def test_max_attempts_and_retry_window_prevent_unbounded_retries(tmp_path, monkeypatch):
    _enable(monkeypatch)
    events = _Events(tmp_path / "research.db", [_event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH")])
    sender = _Sender(RuntimeError("temporary network issue"))
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    service.deliver_immediate(research_as_of="2026-09-01")
    # The deterministic five-minute retry window blocks a hot retry loop.
    service.deliver_immediate(research_as_of="2026-09-01")
    assert len(sender.calls) == 1
    row = service.store.list_for_batches(["batch-1"])["batch-1"]
    for _ in range(4):
        service.store.mark(row["idempotency_key"], "FAILED", "temporary")
    service.deliver_immediate(research_as_of="2026-09-01")
    assert len(sender.calls) == 1
    service.store.close()


def test_noncritical_company_throttle_is_two_per_day(tmp_path, monkeypatch):
    _enable(monkeypatch)
    events = _Events(tmp_path / "research.db", [
        _event("RISK_CHANGED", "MEDIUM", "HIGH", event_id="one", batch="one", severity="HIGH"),
        _event("PRIORITY_CHANGED", "A", "C", event_id="two", batch="two", severity="HIGH"),
        _event("VALUATION_RELIABILITY_CHANGED", "RELIABLE", "INSUFFICIENT", event_id="three", batch="three", severity="HIGH"),
    ])
    sender = _Sender()
    service = ValueStrategyEventNotificationService(event_repository=events, sender=sender)
    result = service.deliver_immediate(research_as_of="2026-09-01")
    assert result["sent"] == 2 and result["skipped"] == 1
    assert len(sender.calls) == 2
    service.store.close()


def test_daily_brief_keeps_immediate_batch_as_one_line_index():
    from src.investment_research_supervisor.daily_brief_service import InvestmentResearchDailyBriefService

    immediate = ValueStrategyEventDeliveryPolicy().aggregate([
        _event("RISK_CHANGED", "MEDIUM", "HIGH", severity="HIGH"),
        _event("PRIORITY_CHANGED", "A", "C", event_id="priority", severity="HIGH"),
    ])[0]
    rendered = InvestmentResearchDailyBriefService._render_executive(
        research_as_of="2026-09-01",
        situations=[], watchlist=[],
        strategy_changes={
            "event_first": True,
            "groups": {"今日已即时提醒": [immediate]},
        },
    )
    assert "今日已即时提醒" in rendered
    assert "中原环保需要风险复核（已即时提醒）" in rendered
    assert "总体风险由中等风险变为高风险" not in rendered


def test_deep_prepare_only_explicitly_evaluates_strategy_events(monkeypatch):
    from src.api.deep_research_routes import register_deep_research_routes
    import src.deep_research as deep_research
    import src.value_strategy as value_strategy

    class Preparer:
        def prepare(self, *_args, **_kwargs):
            return {"status": "READY"}

    class EventService:
        def evaluate_company(self, market, stock_code, research_as_of=None):
            assert (market, stock_code, research_as_of) == ("CN", "605108", "2026-09-01")
            return {"status": "UNCHANGED", "created_event_ids": []}

    monkeypatch.setattr(deep_research, "get_deep_research_preparation_service", lambda: Preparer())
    monkeypatch.setattr(value_strategy, "get_value_strategy_event_service", lambda: EventService())
    app = FastAPI()
    register_deep_research_routes(app, lambda: True)
    result = TestClient(app).post("/api/research/deep-prepare/605108", json={"as_of": "2026-09-01"})
    assert result.status_code == 200
    assert result.json()["strategy_event_evaluation"]["status"] == "UNCHANGED"

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import src.low_value_leader_notifications.service as notification_service_module

from src.low_value_leader_notifications.service import (
    LowValueLeaderNotificationService,
    LowValueNotificationSettings,
)
from src.low_value_leader_notifications.store import LowValueLeaderNotificationRepository
from src.low_value_leader_pool.store import LowValueLeaderPoolRepository
from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository


AS_OF = "2026-08-25"


class FakeSender:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, dict]] = []

    def send_interactive_card(self, *, target_id: str, card: dict) -> str:
        self.calls.append((target_id, card))
        if self.failure:
            raise self.failure
        return f"om_{len(self.calls)}"


def _item(stock_code: str, *, status: str = "UNDERVALUED") -> dict:
    return {
        "market": "CN",
        "stock_code": stock_code,
        "company_name": "示例公司" + stock_code,
        "industry_code": "L3",
        "industry_name": "示例L3行业",
        "current_price": 12.34,
        "fair_value_mid": 18.9,
        "valuation_status": status,
        "source_pool_id": "pool-1",
        "metadata": {},
    }


def _insert_event(
    repository: LowValueLeaderPoolRepository,
    *,
    event_type: str,
    stock_code: str,
    after_status: str,
    reason: str,
    before_status: str | None = None,
    source_as_of: str = AS_OF,
) -> None:
    with repository._conn:
        assert repository._insert_event(
            event_type=event_type,
            before_status=before_status,
            after_status=after_status,
            item=_item(stock_code, status=after_status),
            source_as_of=source_as_of,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def _service(tmp_path: Path, *, sender: FakeSender | None = None, enabled: bool = True, dry_run: bool = False):
    db_path = tmp_path / "research.db"
    notifications = LowValueLeaderNotificationRepository(db_path)
    risks = LowValueRiskSnapshotRepository(db_path)
    service = LowValueLeaderNotificationService(
        repository=notifications,
        risk_repository=risks,
        settings=LowValueNotificationSettings(
            enabled=enabled,
            target_id="oc_test_target",
            web_base_url="https://research.example.test",
            dry_run=dry_run,
        ),
        sender=sender or FakeSender(),
    )
    return service, notifications, risks, LowValueLeaderPoolRepository(db_path)


def _save_risk(repository: LowValueRiskSnapshotRepository, stock_code: str, *, high: int = 0, medium: int = 0) -> None:
    repository.save({
        "market": "CN", "stock_code": stock_code, "source_as_of": AS_OF,
        "overall_risk": "LOW", "value_trap_risk": "LOW", "material_risk_count": high + medium,
        "high_risk_count": high, "medium_risk_count": medium, "top_risk_types": ["INTERNAL_ONLY"],
        "risk_summary": "internal summary", "financial_status": "READY", "business_status": "READY",
        "thesis_status": "READY", "formula_version": "test",
    })


def test_enter_event_sends_chinese_card_with_same_date_risk(tmp_path: Path) -> None:
    sender = FakeSender()
    service, _notifications, risks, pool = _service(tmp_path, sender=sender)
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")
    _save_risk(risks, "000001.SZ", medium=1)

    result = service.notify(research_as_of=AS_OF)

    assert result["status"] == "READY" and result["sent"] is True
    assert len(sender.calls) == 1
    payload = json.dumps(sender.calls[0][1], ensure_ascii=False)
    assert "今日低估龙头变化" in payload
    assert "进入低估区域" in payload and "有风险需要复核" in payload
    assert "查看公司研究" in payload and "/company/CN/000001.SZ" in payload
    assert "INTERNAL_ONLY" not in payload


def test_exit_event_sends_reason_and_status_transition(tmp_path: Path) -> None:
    sender = FakeSender()
    service, _notifications, _risks, pool = _service(tmp_path, sender=sender)
    service.prepare_activation()
    _insert_event(
        pool, event_type="EXIT_LOW_VALUE", stock_code="000002.SZ", before_status="DEEPLY_UNDERVALUED",
        after_status="FAIR", reason="VALUATION_RECOVERED",
    )

    service.notify(research_as_of=AS_OF)

    payload = json.dumps(sender.calls[0][1], ensure_ascii=False)
    assert "退出低估" in payload
    assert "估值恢复至非低估区间" in payload
    assert "进入深度低估区域 → 恢复合理估值区间" in payload


def test_enter_and_exit_are_aggregated_into_one_message(tmp_path: Path) -> None:
    sender = FakeSender()
    service, _notifications, risks, pool = _service(tmp_path, sender=sender)
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="DEEPLY_UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")
    _insert_event(pool, event_type="EXIT_LOW_VALUE", stock_code="000002.SZ", before_status="UNDERVALUED", after_status="OVERVALUED", reason="VALUATION_RECOVERED")
    _save_risk(risks, "000001.SZ", high=1)

    service.notify(research_as_of=AS_OF)

    assert len(sender.calls) == 1
    payload = json.dumps(sender.calls[0][1], ensure_ascii=False)
    assert "新增低估：1 家" in payload and "退出低估：1 家" in payload
    assert "有明显风险需要重点核验" in payload


def test_zero_events_marks_ready_without_sending(tmp_path: Path) -> None:
    sender = FakeSender()
    service, notifications, _risks, _pool = _service(tmp_path, sender=sender)
    service.prepare_activation()

    result = service.notify(research_as_of=AS_OF)

    assert result["status"] == "READY" and sender.calls == []
    assert notifications.delivery(event_date=AS_OF, channel="feishu")["status"] == "READY"


def test_repeat_eod_and_restart_do_not_resend_sent_events(tmp_path: Path) -> None:
    first_sender = FakeSender()
    service, notifications, risks, pool = _service(tmp_path, sender=first_sender)
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")
    _save_risk(risks, "000001.SZ")
    assert service.notify(research_as_of=AS_OF)["status"] == "READY"

    restarted_sender = FakeSender()
    restarted = LowValueLeaderNotificationService(
        repository=LowValueLeaderNotificationRepository(notifications.db_path),
        risk_repository=LowValueRiskSnapshotRepository(notifications.db_path),
        settings=service.settings,
        sender=restarted_sender,
    )
    assert restarted.notify(research_as_of=AS_OF)["status"] == "REUSED"
    assert len(first_sender.calls) == 1 and restarted_sender.calls == []


def test_first_activation_never_backfills_existing_events(tmp_path: Path) -> None:
    sender = FakeSender()
    service, notifications, _risks, pool = _service(tmp_path, sender=sender)
    _insert_event(pool, event_type="EXIT_LOW_VALUE", stock_code="000002.SZ", before_status="UNDERVALUED", after_status="FAIR", reason="VALUATION_RECOVERED")

    service.prepare_activation()
    result = service.notify(research_as_of=AS_OF)

    assert result["status"] == "READY" and sender.calls == []
    assert notifications.delivery(event_date=AS_OF, channel="feishu")["event_ids"] == []


def test_failed_delivery_retries_without_mutating_research_data(tmp_path: Path) -> None:
    sender = FakeSender(RuntimeError("network unavailable"))
    service, notifications, risks, pool = _service(tmp_path, sender=sender)
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")
    _save_risk(risks, "000001.SZ")
    before_events = pool.events(event_date=AS_OF)
    before_risk = risks.get("CN", "000001.SZ", AS_OF)

    failed = service.notify(research_as_of=AS_OF)
    assert failed["status"] == "FAILED"
    assert notifications.delivery(event_date=AS_OF, channel="feishu")["status"] == "FAILED"
    assert pool.events(event_date=AS_OF) == before_events
    assert risks.get("CN", "000001.SZ", AS_OF) == before_risk

    retry_sender = FakeSender()
    retry = LowValueLeaderNotificationService(
        repository=LowValueLeaderNotificationRepository(notifications.db_path),
        risk_repository=LowValueRiskSnapshotRepository(notifications.db_path), settings=service.settings, sender=retry_sender,
    )
    assert retry.notify(research_as_of=AS_OF)["status"] == "READY"
    assert notifications.delivery(event_date=AS_OF, channel="feishu")["status"] == "SENT"
    assert len(retry_sender.calls) == 1


def test_sent_events_never_retry_and_card_has_no_trading_semantics(tmp_path: Path) -> None:
    sender = FakeSender()
    service, _notifications, _risks, pool = _service(tmp_path, sender=sender)
    service.prepare_activation()
    _insert_event(pool, event_type="EXIT_LOW_VALUE", stock_code="000002.SZ", before_status="UNDERVALUED", after_status="NO_LONGER_LEADER", reason="NO_LONGER_LEADER")

    assert service.notify(research_as_of=AS_OF)["status"] == "READY"
    assert service.notify(research_as_of=AS_OF)["status"] == "REUSED"
    assert len(sender.calls) == 1
    payload = json.dumps(sender.calls[0][1], ensure_ascii=False)
    assert "已移出低估龙头研究范围" in payload
    assert all(word not in payload for word in ("买入", "卖出", "推荐", "止盈", "止损", "仓位"))


def test_only_matching_research_as_of_events_are_consumed_and_dry_run_does_not_send(tmp_path: Path) -> None:
    sender = FakeSender()
    service, notifications, risks, pool = _service(tmp_path, sender=sender, dry_run=True)
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")
    _insert_event(
        pool, event_type="ENTER_LOW_VALUE", stock_code="000003.SZ", after_status="UNDERVALUED",
        reason="PRICE_ENTERED_LOW_VALUE", source_as_of="2026-08-24",
    )
    _save_risk(risks, "000001.SZ")

    result = service.notify(research_as_of=AS_OF)

    assert result["status"] == "READY" and sender.calls == []
    delivery = notifications.delivery(event_date=AS_OF, channel="feishu")
    assert delivery["status"] == "DRY_RUN" and len(delivery["event_ids"]) == 1
    assert result["card"]["header"]["title"]["content"] == "今日低估龙头变化"


def test_notification_service_uses_supervisor_payload_without_bypassing_delivery(tmp_path: Path) -> None:
    class Payload:
        def card(self) -> dict:
            return {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": "主管简报"}},
                "elements": [{"tag": "markdown", "content": "仅由投研主管组织内容"}],
            }

    class Supervisor:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def build_low_value_notification_payload(self, **kwargs):
            self.calls.append(kwargs)
            return Payload()

    db_path = tmp_path / "research.db"
    notifications = LowValueLeaderNotificationRepository(db_path)
    pool = LowValueLeaderPoolRepository(db_path)
    supervisor = Supervisor()
    service = LowValueLeaderNotificationService(
        repository=notifications,
        risk_repository=LowValueRiskSnapshotRepository(db_path),
        settings=LowValueNotificationSettings(enabled=True, target_id="oc_test_target", dry_run=True),
        sender=FakeSender(),
        supervisor=supervisor,
    )
    service.prepare_activation()
    _insert_event(pool, event_type="ENTER_LOW_VALUE", stock_code="000001.SZ", after_status="UNDERVALUED", reason="PRICE_ENTERED_LOW_VALUE")

    result = service.notify(research_as_of=AS_OF)

    assert len(supervisor.calls) == 1
    assert result["delivery"]["status"] == "DRY_RUN"
    assert result["card"]["header"]["title"]["content"] == "主管简报"


def test_notification_service_has_no_llm_dependency() -> None:
    source = inspect.getsource(notification_service_module)
    assert "src.providers" not in source
    assert "get_llm" not in source

"""Aggregate existing low-value leader events into one Feishu notification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urljoin, urlparse

from src.channels.config import load_channels_config
from src.low_value_risk_snapshot.store import LowValueRiskSnapshotRepository

from .store import LowValueLeaderNotificationRepository


VALUATION_LABELS = {
    "UNDERVALUED": "进入低估区域",
    "DEEPLY_UNDERVALUED": "进入深度低估区域",
    "FAIR": "恢复合理估值区间",
    "OVERVALUED": "离开低估区域，估值偏高",
    "DEEPLY_OVERVALUED": "离开低估区域，估值偏高",
    "NO_LONGER_LEADER": "不再属于当前L3龙头",
}

EXIT_REASON_LABELS = {
    "VALUATION_RECOVERED": "估值恢复至非低估区间",
    "NO_LONGER_LEADER": "不再属于当前L3龙头",
}


@dataclass(frozen=True)
class LowValueNotificationSettings:
    enabled: bool = False
    target_id: str = ""
    web_base_url: str = ""
    dry_run: bool = True

    @classmethod
    def from_channels_config(cls) -> "LowValueNotificationSettings":
        channels = load_channels_config()
        feishu = channels.get("feishu") if isinstance(channels.get("feishu"), dict) else {}
        raw = feishu.get("low_value_leader_notification") if isinstance(feishu, dict) else {}
        values = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=bool(values.get("enabled", False)),
            target_id=str(values.get("target_id") or "").strip(),
            web_base_url=str(values.get("web_base_url") or "").strip(),
            dry_run=bool(values.get("dry_run", True)),
        )


class FeishuNotificationSender(Protocol):
    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        """Deliver one existing-channel interactive card and return its message ID."""


class ExistingFeishuNotificationSender:
    """Resolve the already-running Feishu channel without creating another client."""

    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        manager = getattr(host, "_channel_manager", None) if host is not None else None
        if manager is None:
            raise RuntimeError("Feishu channel runtime is not available")
        channel = manager.get_channel("feishu")
        if channel is None or not getattr(channel, "is_running", False):
            raise RuntimeError("Feishu channel is not running")
        sender = getattr(channel, "send_low_value_notification_card", None)
        if not callable(sender):
            raise RuntimeError("Feishu channel does not support low-value notifications")
        return str(sender(target_id=target_id, card=card))


def _format_number(value: Any) -> str:
    if value is None:
        return "暂无数据"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "暂无数据"


def _valuation_label(value: Any) -> str:
    return VALUATION_LABELS.get(str(value or ""), "资料不足")


def _risk_label(snapshot: dict[str, Any] | None) -> str:
    if not snapshot or snapshot.get("error") or str(snapshot.get("overall_risk") or "") == "UNKNOWN":
        return "资料不足"
    if int(snapshot.get("high_risk_count") or 0) > 0:
        return "有明显风险需要重点核验"
    if int(snapshot.get("medium_risk_count") or 0) > 0 or int(snapshot.get("material_risk_count") or 0) > 0:
        return "有风险需要复核"
    return "暂无明显风险"


def _company_research_url(stock_code: str, web_base_url: str) -> str | None:
    if not web_base_url:
        return None
    parsed = urlparse(web_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path = (
        f"/company/CN/{quote(stock_code, safe='')}?from=%2Fvalue%2Ffocus"
        "&from_label=%E4%BD%8E%E4%BC%B0%E9%BE%99%E5%A4%B4%E6%B1%A0&tab=overview"
    )
    return urljoin(web_base_url.rstrip("/") + "/", path.lstrip("/"))


def _company_elements(event: dict[str, Any], *, risk: dict[str, Any] | None, web_base_url: str) -> list[dict[str, Any]]:
    is_enter = event.get("event_type") == "ENTER_LOW_VALUE"
    code = str(event.get("stock_code") or "")
    name = str(event.get("company_name") or code)
    industry = str(event.get("industry_name") or "暂无数据")
    lines = [
        f"**{name} / {code}**",
        f"L3行业：{industry}",
    ]
    if is_enter:
        lines.extend([
            f"状态：{_valuation_label(event.get('after_status') or event.get('valuation_status'))}",
            f"当前价格：{_format_number(event.get('current_price'))}",
            f"合理价值中枢：{_format_number(event.get('fair_value_mid'))}",
            f"风险复核：{_risk_label(risk)}",
        ])
    else:
        reason = str((event.get("metadata") or {}).get("reason") or "")
        lines.extend([
            f"退出原因：{EXIT_REASON_LABELS.get(reason, _valuation_label(event.get('after_status')))}",
            f"状态变化：{_valuation_label(event.get('before_status'))} → {_valuation_label(event.get('after_status'))}",
            f"当前价格：{_format_number(event.get('current_price'))}",
            f"合理价值中枢：{_format_number(event.get('fair_value_mid'))}",
        ])
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": "\n".join(lines)}]
    url = _company_research_url(code, web_base_url)
    if url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看公司研究"},
                "type": "default",
                "url": url,
            }],
        })
    return elements


def build_feishu_card(
    *,
    research_as_of: str,
    events: list[dict[str, Any]],
    risks: dict[str, dict[str, Any]],
    web_base_url: str,
    supervisor: Any | None = None,
) -> dict[str, Any]:
    if supervisor is not None:
        return supervisor.build_low_value_notification_payload(
            research_as_of=research_as_of,
            events=events,
            risks=risks,
            web_base_url=web_base_url,
        ).card()
    entered = [item for item in events if item.get("event_type") == "ENTER_LOW_VALUE"]
    exited = [item for item in events if item.get("event_type") == "EXIT_LOW_VALUE"]
    elements: list[dict[str, Any]] = [{
        "tag": "markdown",
        "content": f"研究日期：{research_as_of}\n\n新增低估：{len(entered)} 家\n退出低估：{len(exited)} 家",
    }]
    if entered:
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": "**新增低估**"})
        for event in entered:
            elements.extend(_company_elements(event, risk=risks.get(str(event.get("stock_code") or "")), web_base_url=web_base_url))
    if exited:
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": "**退出低估**"})
        for event in exited:
            elements.extend(_company_elements(event, risk=None, web_base_url=web_base_url))
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "今日低估龙头变化"}},
        "elements": elements,
    }


class LowValueLeaderNotificationService:
    def __init__(
        self,
        *,
        repository: LowValueLeaderNotificationRepository | None = None,
        risk_repository: LowValueRiskSnapshotRepository | None = None,
        settings: LowValueNotificationSettings | None = None,
        sender: FeishuNotificationSender | None = None,
        supervisor: Any | None = None,
    ) -> None:
        if supervisor is None:
            from src.investment_research_supervisor import InvestmentResearchSupervisorService
            supervisor = InvestmentResearchSupervisorService(event_only=True)
        self.repository = repository or LowValueLeaderNotificationRepository()
        self.risk_repository = risk_repository or LowValueRiskSnapshotRepository(self.repository.db_path)
        self.settings = settings or LowValueNotificationSettings.from_channels_config()
        self.sender = sender or ExistingFeishuNotificationSender()
        self.supervisor = supervisor

    def prepare_activation(self) -> dict[str, Any]:
        return self.repository.ensure_activation(channel="feishu")

    def notify(self, *, research_as_of: str) -> dict[str, Any]:
        channel = "feishu"
        self.prepare_activation()
        delivery = self.repository.delivery(event_date=research_as_of, channel=channel)
        if delivery and delivery.get("status") in {"SENT", "READY", "SKIPPED_DISABLED", "DRY_RUN"}:
            return {"status": "REUSED", "research_as_of": research_as_of, "delivery": delivery}

        events = self.repository.events_for_notification(channel=channel, research_as_of=research_as_of)
        event_ids = [str(item["id"]) for item in events]
        if not events:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="READY", event_ids=[]
            )
            return {"status": "READY", "research_as_of": research_as_of, "delivery": delivery, "sent": False}

        if not self.settings.enabled:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="SKIPPED_DISABLED", event_ids=event_ids
            )
            return {"status": "READY", "research_as_of": research_as_of, "delivery": delivery, "sent": False}

        try:
            risks = {
                str(event["stock_code"]): self.risk_repository.get("CN", str(event["stock_code"]), research_as_of)
                for event in events if event.get("event_type") == "ENTER_LOW_VALUE"
            }
            card = build_feishu_card(
                research_as_of=research_as_of,
                events=events,
                risks=risks,
                web_base_url=self.settings.web_base_url,
                supervisor=self.supervisor,
            )
        except Exception as exc:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="FAILED", event_ids=event_ids,
                error=f"{type(exc).__name__}: {exc}", increment_attempt=True,
            )
            return {"status": "FAILED", "research_as_of": research_as_of, "delivery": delivery}
        if self.settings.dry_run:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="DRY_RUN", event_ids=event_ids
            )
            return {"status": "READY", "research_as_of": research_as_of, "delivery": delivery, "sent": False, "card": card}
        if not self.settings.target_id:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="FAILED", event_ids=event_ids,
                error="Feishu low-value notification target is not configured", increment_attempt=True,
            )
            return {"status": "FAILED", "research_as_of": research_as_of, "delivery": delivery}
        try:
            message_id = self.sender.send_interactive_card(target_id=self.settings.target_id, card=card)
        except Exception as exc:
            delivery = self.repository.record_delivery(
                event_date=research_as_of, channel=channel, status="FAILED", event_ids=event_ids,
                error=f"{type(exc).__name__}: {exc}", increment_attempt=True,
            )
            return {"status": "FAILED", "research_as_of": research_as_of, "delivery": delivery}
        delivery = self.repository.record_delivery(
            event_date=research_as_of, channel=channel, status="SENT", event_ids=event_ids,
            message_id=message_id, increment_attempt=True,
        )
        return {"status": "READY", "research_as_of": research_as_of, "delivery": delivery, "sent": True, "card": card}


_service: LowValueLeaderNotificationService | None = None


def get_low_value_leader_notification_service() -> LowValueLeaderNotificationService:
    global _service
    if _service is None:
        _service = LowValueLeaderNotificationService()
    return _service

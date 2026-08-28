"""Delivery owner for persisted Investment Research Supervisor daily briefs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from src.investment_research_supervisor.daily_brief_excel import export_daily_brief_workbook
from src.investment_research_supervisor.daily_brief_table_image import render_value_observation_table

from src.channels.config import load_channels_config
from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository


@dataclass(frozen=True)
class DailyBriefNotificationSettings:
    enabled: bool = False
    target_id: str = ""
    web_base_url: str = ""
    dry_run: bool = True

    @classmethod
    def from_channels_config(cls) -> "DailyBriefNotificationSettings":
        channels = load_channels_config()
        supervisor = channels.get("feishu_supervisor") if isinstance(channels.get("feishu_supervisor"), dict) else {}
        raw = supervisor.get("daily_research_brief_notification") if isinstance(supervisor, dict) else {}
        values = raw if isinstance(raw, dict) else {}
        return cls(
            enabled=bool(values.get("enabled", False)),
            target_id=str(values.get("target_id") or "").strip(),
            web_base_url=str(values.get("web_base_url") or "").strip(),
            dry_run=bool(values.get("dry_run", True)),
        )


class DailyBriefNotificationSender(Protocol):
    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        """Deliver one interactive card through the configured supervisor channel."""

    def send_file(self, *, target_id: str, file_path: str) -> str:
        """Deliver one Daily Brief attachment through the configured supervisor channel."""

    def upload_image(self, *, file_path: str) -> str:
        """Upload an image for use in an interactive card."""


class ExistingFeishuSupervisorSender:
    """Resolve the existing supervisor channel; never create a Feishu SDK client."""

    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        channel = self._channel()
        sender = getattr(channel, "send_low_value_notification_card", None)
        if not callable(sender):
            raise RuntimeError("Feishu supervisor channel does not support interactive cards")
        return str(sender(target_id=target_id, card=card))

    def send_file(self, *, target_id: str, file_path: str) -> str:
        channel = self._channel()
        sender = getattr(channel, "send_low_value_notification_file", None)
        if not callable(sender):
            raise RuntimeError("Feishu supervisor channel does not support Daily Brief attachments")
        return str(sender(target_id=target_id, file_path=file_path))

    def upload_image(self, *, file_path: str) -> str:
        channel = self._channel()
        uploader = getattr(channel, "upload_low_value_notification_image", None)
        if not callable(uploader):
            raise RuntimeError("Feishu supervisor channel does not support Daily Brief table images")
        return str(uploader(file_path=file_path))

    @staticmethod
    def _channel() -> Any:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        manager = getattr(host, "_channel_manager", None) if host is not None else None
        if manager is None:
            raise RuntimeError("Feishu supervisor channel runtime is not available")
        channel = manager.get_channel("feishu_supervisor")
        if channel is None or not getattr(channel, "is_running", False):
            raise RuntimeError("Feishu supervisor channel is not running")
        return channel


def _card_value(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def _value_observation_table(brief: dict[str, Any]) -> list[dict[str, Any]]:
    payload = dict(brief.get("brief_payload") or {})
    watchlist = list(payload.get("executive_watchlist") or [])
    if not watchlist:
        return [{"tag": "markdown", "content": "暂无可展示的完整估值观察。"}]

    def column(content: str, *, weight: int) -> dict[str, Any]:
        return {
            "tag": "column",
            "width": "weighted",
            "weight": weight,
            "vertical_align": "top",
            "elements": [{"tag": "markdown", "content": content}],
        }

    elements = [{
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [
            column("**公司 / 代码**", weight=2),
            column("**现价**", weight=1),
            column("**历史支撑**", weight=2),
            column("**合理价值范围**", weight=3),
            column("**中位值差距**", weight=1),
        ],
    }]
    for item in watchlist:
        support = dict(item.get("historical_support") or {})
        support_text = (
            f"{_card_value(support.get('low'))}–{_card_value(support.get('high'))}"
            if support.get("low") is not None and support.get("high") is not None
            else "—"
        )
        gap = item.get("valuation_gap_percent")
        gap_text = f"{gap:.2f}%" if isinstance(gap, (int, float)) else "—"
        fair_value_range = f"{_card_value(item.get('fair_value_low'))}–{_card_value(item.get('fair_value_high'))}"
        elements.append({
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                column(
                    f"{_card_value(item.get('company_name'))}\n"
                    f"{_card_value(item.get('stock_code'))}",
                    weight=2,
                ),
                column(_card_value(item.get("current_price")), weight=1),
                column(support_text, weight=2),
                column(fair_value_range, weight=3),
                column(gap_text, weight=1),
            ],
        })
    return elements


def build_daily_brief_card(
    brief: dict[str, Any], *, value_table_image_key: str | None = None,
) -> dict[str, Any]:
    payload = dict(brief.get("brief_payload") or {})
    text = str(payload.get("text") or "资料不足")
    investment_changes = text.split("\n\n二、重点研究观察", 1)[0]
    bitable_url = str(payload.get("low_value_leader_bitable_url") or "").strip()
    value_table = (
        [{
            "tag": "img",
            "img_key": value_table_image_key,
            "alt": {"tag": "plain_text", "content": "重点研究观察表格"},
            "scale_type": "fit_horizontal",
        }]
        if value_table_image_key
        else _value_observation_table(brief)
    )
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": investment_changes},
        {"tag": "hr"},
        {"tag": "markdown", "content": "**二、重点研究观察**"},
        *value_table,
        {"tag": "hr"},
        {"tag": "markdown", "content": "**三、低估龙头表格**"},
    ]
    if bitable_url:
        elements.append({"tag": "markdown", "content": f"[打开当前低估龙头池（不保留历史）]({bitable_url})"})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "今日投研简报"}},
        "elements": elements,
    }


class DailyBriefNotificationService:
    """Owns only daily-brief delivery state and retries."""

    def __init__(
        self,
        *,
        repository: InvestmentResearchDailyBriefRepository | None = None,
        settings: DailyBriefNotificationSettings | None = None,
        sender: DailyBriefNotificationSender | None = None,
    ) -> None:
        self.repository = repository or InvestmentResearchDailyBriefRepository()
        self.settings = settings or DailyBriefNotificationSettings.from_channels_config()
        self.sender = sender or ExistingFeishuSupervisorSender()

    def notify(self, *, research_as_of: str) -> dict[str, Any]:
        brief = self.repository.get_completed(research_as_of)
        if not brief:
            return {"status": "FAILED", "research_as_of": research_as_of, "error": "daily brief is not ready"}
        channel = "feishu_supervisor"
        target_id = self.settings.target_id
        delivery = self.repository.delivery(
            research_as_of=research_as_of, channel=channel, target_id=target_id,
        )
        if delivery and delivery.get("status") in {"DRY_RUN", "SKIPPED_DISABLED"}:
            return {
                "status": "REUSED", "research_as_of": research_as_of, "delivery": delivery,
                "covers_low_value": delivery.get("status") == "DRY_RUN",
            }
        bitable_delivery = self.repository.delivery(
            research_as_of=research_as_of,
            channel="feishu_bitable",
            target_id="tblJb3Pc7w9fKsjI",
        )
        bitable_published = bool(bitable_delivery and bitable_delivery.get("status") == "SENT")
        if delivery and delivery.get("status") == "SENT" and (
            bitable_published or delivery.get("attachment_message_id")
        ):
            return {
                "status": "REUSED", "research_as_of": research_as_of, "delivery": delivery,
                "covers_low_value": True,
            }
        if not self.settings.enabled:
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of, channel=channel, target_id=target_id,
                status="SKIPPED_DISABLED",
            )
            return {"status": "DISABLED", "research_as_of": research_as_of, "delivery": delivery, "covers_low_value": False}
        card = build_daily_brief_card(brief)
        if self.settings.dry_run:
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of, channel=channel, target_id=target_id, status="DRY_RUN",
            )
            return {
                "status": "READY", "research_as_of": research_as_of, "delivery": delivery,
                "card": card, "covers_low_value": True,
            }
        if not target_id:
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of, channel=channel, target_id=target_id, status="FAILED",
                error="Feishu daily brief target is not configured", increment_attempt=True,
            )
            return {"status": "FAILED", "research_as_of": research_as_of, "delivery": delivery, "covers_low_value": False}
        attachment_message_id = str((delivery or {}).get("attachment_message_id") or "") or None
        message_id = str((delivery or {}).get("message_id") or "") or None
        try:
            if not bitable_published and not attachment_message_id:
                filename = f"低估龙头表格_{research_as_of}.xlsx"
                with TemporaryDirectory(prefix="daily_brief_") as directory:
                    workbook = export_daily_brief_workbook(brief, Path(directory) / filename)
                    attachment_message_id = self.sender.send_file(target_id=target_id, file_path=str(workbook))
                self.repository.record_delivery(
                    research_as_of=research_as_of, channel=channel, target_id=target_id, status="FAILED",
                    attachment_message_id=attachment_message_id,
                )
            if not message_id:
                value_table_image_key: str | None = None
                upload_image = getattr(self.sender, "upload_image", None)
                if callable(upload_image):
                    with TemporaryDirectory(prefix="daily_brief_table_") as directory:
                        image_path = render_value_observation_table(
                            brief, Path(directory) / f"重点研究观察_{research_as_of}.png",
                        )
                        value_table_image_key = str(upload_image(file_path=str(image_path)))
                card = build_daily_brief_card(brief, value_table_image_key=value_table_image_key)
                message_id = self.sender.send_interactive_card(target_id=target_id, card=card)
        except Exception as exc:
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of, channel=channel, target_id=target_id, status="FAILED",
                message_id=message_id, attachment_message_id=attachment_message_id,
                error=f"{type(exc).__name__}: {exc}", increment_attempt=True,
            )
            return {"status": "FAILED", "research_as_of": research_as_of, "delivery": delivery, "covers_low_value": False}
        delivery = self.repository.record_delivery(
            research_as_of=research_as_of, channel=channel, target_id=target_id, status="SENT",
            message_id=message_id, attachment_message_id=attachment_message_id, increment_attempt=True,
        )
        return {
            "status": "READY", "research_as_of": research_as_of, "delivery": delivery,
            "card": card, "covers_low_value": True,
        }


_service: DailyBriefNotificationService | None = None


def get_daily_brief_notification_service() -> DailyBriefNotificationService:
    global _service
    if _service is None:
        _service = DailyBriefNotificationService()
    return _service

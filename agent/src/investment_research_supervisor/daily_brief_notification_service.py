"""Delivery owner for persisted Investment Research Supervisor daily briefs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from src.investment_research_supervisor.daily_brief_excel import export_daily_brief_workbook

from src.channels.config import load_channels_config
from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository


@dataclass(frozen=True)
class DailyBriefNotificationSettings:
    enabled: bool = False
    target_id: str = ""
    web_base_url: str = ""
    dry_run: bool = True
    delivery_channel: str = "feishu_supervisor"

    @classmethod
    def from_channels_config(cls) -> "DailyBriefNotificationSettings":
        channels = load_channels_config()
        supervisor = channels.get("feishu_supervisor") if isinstance(channels.get("feishu_supervisor"), dict) else {}
        raw = supervisor.get("daily_research_brief_notification") if isinstance(supervisor, dict) else {}
        values = raw if isinstance(raw, dict) else {}
        enabled = bool(values.get("enabled", False))
        target_id = str(values.get("target_id") or "").strip()
        # A disabled backend channel must never be revived merely because an
        # old nested daily-brief setting remains in its config. Hermes owns
        # the bot lifecycle; use its outbound credentials below instead.
        if bool(supervisor.get("enabled", False)) and enabled and target_id:
            return cls(
                enabled=True,
                target_id=target_id,
                web_base_url=str(values.get("web_base_url") or "").strip(),
                dry_run=bool(values.get("dry_run", True)),
            )

        # Inbound legacy channels remain disabled: Hermes owns all bot
        # conversations.  Daily reports are a separate outbound capability
        # and use the Hermes supervisor application's existing credentials.
        try:
            from .hermes_feishu import HermesSupervisorFeishuCredentials

            credentials = HermesSupervisorFeishuCredentials.load()
            return cls(
                enabled=True,
                target_id=credentials.target_id,
                dry_run=False,
                delivery_channel="hermes_feishu_supervisor",
            )
        except RuntimeError:
            return cls()


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


class ShortLivedFeishuBriefSender:
    """Short-lived SDK client sender for when no long-connection channel runs.

    The Value Line runtime deliberately keeps backend Feishu channels
    disabled while Hermes gateways own the bots, so durable notifications
    use a request-scoped SDK client built from the same supervisor
    credentials (same approach as the Bitable publisher).
    """

    _client: Any = None

    @classmethod
    def _sdk_client(cls) -> Any:
        if cls._client is None:
            from .hermes_feishu import HermesSupervisorFeishuCredentials

            cls._client = HermesSupervisorFeishuCredentials.load().create_lark_client()
        return cls._client

    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        import json

        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = self._sdk_client()
        receive_id_type = "chat_id" if target_id.startswith("oc_") else "open_id"
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(target_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu card delivery failed: code={response.code}, msg={response.msg}")
        return str(response.data.message_id)

    def upload_image(self, *, file_path: str) -> str:
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        client = self._sdk_client()
        with open(file_path, "rb") as handle:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder().image_type("message").image(handle).build()
                )
                .build()
            )
            response = client.im.v1.image.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return str(response.data.image_key)

    def send_file(self, *, target_id: str, file_path: str) -> str:
        import json

        from lark_oapi.api.im.v1 import (
            CreateFileRequest,
            CreateFileRequestBody,
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = self._sdk_client()
        with open(file_path, "rb") as handle:
            request = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(Path(file_path).name)
                    .file(handle)
                    .build()
                )
                .build()
            )
            response = client.im.v1.file.create(request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        file_key = str(response.data.file_key)
        receive_id_type = "chat_id" if target_id.startswith("oc_") else "open_id"
        message_request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(target_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        message_response = client.im.v1.message.create(message_request)
        if not message_response.success():
            raise RuntimeError(f"Feishu file delivery failed: code={message_response.code}, msg={message_response.msg}")
        return str(message_response.data.message_id)


class AutoFeishuBriefSender:
    """Prefer a running supervisor channel; fall back to short-lived SDK."""

    def __init__(self) -> None:
        self._short_lived: ShortLivedFeishuBriefSender | None = None

    def _short(self) -> ShortLivedFeishuBriefSender:
        if self._short_lived is None:
            self._short_lived = ShortLivedFeishuBriefSender()
        return self._short_lived

    def send_interactive_card(self, *, target_id: str, card: dict[str, Any]) -> str:
        try:
            return ExistingFeishuSupervisorSender().send_interactive_card(target_id=target_id, card=card)
        except Exception:  # noqa: BLE001 - channel not running is the common case
            return self._short().send_interactive_card(target_id=target_id, card=card)

    def send_file(self, *, target_id: str, file_path: str) -> str:
        try:
            return ExistingFeishuSupervisorSender().send_file(target_id=target_id, file_path=file_path)
        except Exception:  # noqa: BLE001
            return self._short().send_file(target_id=target_id, file_path=file_path)

    def upload_image(self, *, file_path: str) -> str:
        try:
            return ExistingFeishuSupervisorSender().upload_image(file_path=file_path)
        except Exception:  # noqa: BLE001
            return self._short().upload_image(file_path=file_path)


def _card_value(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _fmt_number(value: Any) -> str:
    """Compact money-like number: 12.74 / 163.2 / 312（去尾零，克制小数位）."""
    number = _finite_number(value)
    if number is None:
        return "—"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _fmt_price(value: Any) -> str:
    number = _finite_number(value)
    return "—" if number is None else f"{number:.2f}"


def _fmt_gap(value: Any) -> str:
    number = _finite_number(value)
    return "—" if number is None else f"{number:+.0f}%"


def _range_text(low: Any, high: Any) -> str:
    if _finite_number(low) is None and _finite_number(high) is None:
        return "—"
    return f"{_fmt_number(low)}–{_fmt_number(high)}"


def _short_text(value: Any, *, limit: int = 76) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _summary_metrics(payload: dict[str, Any]) -> str:
    changes = dict(payload.get("strategy_changes") or {})
    groups = changes.get("groups") if isinstance(changes.get("groups"), dict) else {}
    visible_changes = sum(len(items or []) for items in groups.values())
    overflow = int(changes.get("overflow") or 0)
    watchlist_count = len(list(payload.get("executive_watchlist") or []))
    as_of = _card_value(payload.get("research_as_of"))
    change_text = str(visible_changes + overflow) if visible_changes + overflow else "无"
    return f"**{as_of}**　·　重点研究 {watchlist_count} 家　·　今日变化 {change_text} 项"


def _compact_strategy_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the card concise; the persisted brief remains the audit record."""
    changes = dict(payload.get("strategy_changes") or {})
    groups = changes.get("groups") if isinstance(changes.get("groups"), dict) else {}
    order = ("重点处理", "风险和逻辑变化", "研究优先级变化", "其他研究状态变化", "今日已即时提醒")
    rows: list[dict[str, Any]] = []
    shown = 0
    for heading in order:
        items = list(groups.get(heading) or [])
        if not items or shown >= 5:
            continue
        if not rows:
            rows.append({"tag": "markdown", "content": "**今日变化**"})
        for item in items:
            if shown >= 5:
                break
            summary = _short_text(item.get("summary") or item.get("primary_reason") or "研究状态已更新")
            rows.append({"tag": "markdown", "content": f"• **{_card_value(item.get('stock_name'))}**　{summary}"})
            shown += 1
    if not rows:
        rows.append({"tag": "markdown", "content": "**今日变化**\n当日没有需要主动展示的研究状态变化。"})
    overflow = int(changes.get("overflow") or 0)
    if overflow or shown < sum(len(items or []) for items in groups.values()):
        rows.append({"tag": "note", "elements": [{
            "tag": "plain_text",
            "content": "其余研究状态变化已记录在系统中，避免日报重复堆叠。",
        }]})
    return rows


def _compact_investment_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    situations = list(payload.get("executive_situations") or [])[:3]
    if not situations:
        return []
    rows: list[dict[str, Any]] = [{"tag": "markdown", "content": "**判断变化**"}]
    for item in situations:
        company, code = _card_value(item.get("company_name")), _card_value(item.get("stock_code"))
        basis = _short_text(item.get("basis") or "研究判断已更新")
        rows.append({"tag": "markdown", "content": f"• **{company} {code}**　{basis}"})
    return rows


def _value_observation_table(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """每家两行的紧凑摘要：一行身份与现价，一行估值与支撑。"""
    payload = dict(brief.get("brief_payload") or {})
    watchlist = list(payload.get("executive_watchlist") or [])
    if not watchlist:
        return [{"tag": "markdown", "content": "暂无通过风险、资料与估值质量条件筛选的重点研究。"}]

    elements: list[dict[str, Any]] = []
    for index, item in enumerate(watchlist, start=1):
        support = dict(item.get("historical_support") or {})
        support_text = _range_text(support.get("low"), support.get("high"))
        industry = _card_value(item.get("industry_name"))
        code = _card_value(item.get("stock_code"))
        head = (
            f"**{index}. {_card_value(item.get('company_name'))}**　{code}"
            + (f"　·　{industry}" if industry and industry != "—" else "")
            + f"　·　现价 **{_fmt_price(item.get('current_price'))}**"
        )
        detail = (
            f"　合理 {_range_text(item.get('fair_value_low'), item.get('fair_value_high'))}"
            f"　·　支撑 {support_text}"
            f"　·　差距 **{_fmt_gap(item.get('valuation_gap_percent'))}**"
        )
        elements.append({"tag": "markdown", "content": head + "\n" + detail})
    return elements


def _macro_environment_block(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """当前研究环境一行摘要（fail-soft，宏观不可用时整块省略）。"""
    env = dict(brief.get("macro_environment") or (brief.get("brief_payload") or {}).get("macro_environment") or {})
    if not env.get("available"):
        return []
    text = _short_text(env.get("text") or "", limit=280)
    return [
        {"tag": "markdown", "content": f"**当前研究环境**\n{text}"},
    ]


def _price_condition_digest_of(brief: dict[str, Any]) -> dict[str, Any]:
    """Read the digest from the persisted brief (payload copy is durable)."""
    digest = dict(brief.get("price_condition_digest") or {})
    if not digest:
        digest = dict((brief.get("brief_payload") or {}).get("price_condition_digest") or {})
    return digest


def _price_condition_digest_block(brief: dict[str, Any]) -> list[dict[str, Any]]:
    """老板第一眼：今天盯谁 / 谁要复核 / 谁掉出名单（研究结论，不是交易指令）。"""
    digest = _price_condition_digest_of(brief)
    rows: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "**今日价格条件**"},
    ]
    lines = list(digest.get("lines") or [])
    if not lines:
        rows.append({"tag": "markdown", "content": "今日无价格条件变化。"})
        return rows
    for item in lines:
        company = _card_value(item.get("company_name"))
        code = _card_value(item.get("stock_code"))
        scope = "在范围内" if item.get("eligibility_status") == "IN_VALUE_SCOPE" else "不在范围内"
        action = _short_text(item.get("primary_action_label") or "资料不足", limit=26)
        price = _fmt_price(item.get("current_price"))
        position = str(item.get("position_sentence") or "资料不足，无法判断落点")
        if position.startswith("现价"):
            position = position[len("现价"):].lstrip("，, ")
            detail = f"现价 {price}，{position}"
        else:
            detail = f"现价 {price}；{position}"
        rows.append({"tag": "markdown", "content": (
            f"• **{company}** {code} · {scope} · **{action}**\n"
            f"　{detail}"
        )})
    omitted = int(digest.get("omitted_count") or 0)
    if omitted > 0:
        rows.append({"tag": "note", "elements": [{
            "tag": "plain_text",
            "content": f"另有 {omitted} 家见公司研究页，未列入日报。",
        }]})
    return rows


def build_daily_brief_card(
    brief: dict[str, Any], *, value_table_image_key: str | None = None,
    include_bitable_link: bool = True,
) -> dict[str, Any]:
    payload = dict(brief.get("brief_payload") or {})
    bitable_url = str(payload.get("low_value_leader_bitable_url") or "").strip()
    as_of = _card_value(payload.get("research_as_of"))
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": _summary_metrics(payload)},
        *_macro_environment_block(brief),
        *_price_condition_digest_block(brief),
    ]
    changes = [
        *_compact_strategy_changes(payload),
        *_compact_investment_changes(payload),
    ]
    if changes:
        elements.extend(changes)
    elements.extend([
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**重点研究 · {len(list(payload.get('executive_watchlist') or []))} 家**　*研究结论，不构成买卖建议*"},
        *_value_observation_table(brief),
    ])
    if bitable_url and include_bitable_link:
        elements.extend([
            {"tag": "hr"},
            {"tag": "action", "actions": [{
                "tag": "button", "type": "primary",
                "text": {"tag": "plain_text", "content": "打开低估龙头池"},
                "url": bitable_url,
            }]},
        ])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "indigo",
            "title": {"tag": "plain_text", "content": f"投研日报 · {as_of}"},
        },
        "elements": elements,
    }


def _brief_updated_after_delivery(brief: dict[str, Any], delivery: dict[str, Any] | None) -> bool:
    """Resend the card when the persisted brief is newer than the last SENT card."""
    if not delivery:
        return False
    brief_ts = str(brief.get("updated_at") or "")
    sent_ts = str(delivery.get("sent_at") or delivery.get("updated_at") or "")
    return bool(brief_ts and sent_ts and brief_ts > sent_ts)


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
        channel = self.settings.delivery_channel
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
        brief_stale_vs_card = _brief_updated_after_delivery(brief, delivery)
        if delivery and delivery.get("status") == "SENT" and (
            bitable_published or delivery.get("attachment_message_id")
        ) and not brief_stale_vs_card:
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
        card = build_daily_brief_card(brief, include_bitable_link=bitable_published)
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
        message_id = None if brief_stale_vs_card else (str((delivery or {}).get("message_id") or "") or None)
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
                card = build_daily_brief_card(
                    brief,
                    include_bitable_link=bitable_published,
                )
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
        _service = DailyBriefNotificationService(sender=AutoFeishuBriefSender())
    return _service

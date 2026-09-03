"""Explicit Feishu Bitable publication for the current low-value-leader pool."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from src.investment_research_supervisor.daily_brief_store import InvestmentResearchDailyBriefRepository


LOW_VALUE_LEADER_BITABLE_APP_TOKEN = "WOxgbNUrVagmjCsfNNZcXCySndh"
LOW_VALUE_LEADER_BITABLE_TABLE_ID = "tblJb3Pc7w9fKsjI"
LOW_VALUE_LEADER_BITABLE_URL = (
    "https://acnhfzsa8929.feishu.cn/base/WOxgbNUrVagmjCsfNNZcXCySndh"
    "?table=tblJb3Pc7w9fKsjI&view=vewDbrHG2e"
)
MANAGED_SOURCE = "投资研究日报低估龙头池"

_FIELD_NAMES = (
    "研究日期", "股票代码", "公司", "行业", "估值状态", "现价", "合理价值范围",
    "相对中位值差距", "历史支撑范围", "研究重点", "关键反证",
    "日报版本", "同步来源",
)
_RETIRED_FIELD_NAMES = ("合理价值低", "合理价值中", "合理价值高", "历史支撑低", "历史支撑高")
_LEGACY_SYSTEM_VERSIONS = {"daily-brief-v10", "daily-brief-v11"}


class DailyBriefBitableGateway(Protocol):
    def list_fields(self) -> list[dict[str, Any]]:
        """Return Bitable fields as mappings containing field_name."""

    def create_text_field(self, name: str) -> None:
        """Create one text field in the configured Bitable."""

    def delete_field(self, field_id: str) -> None:
        """Delete a retired system-owned field by its Bitable field ID."""

    def list_records(self) -> list[dict[str, Any]]:
        """Return Bitable records containing record_id and fields."""

    def batch_create(self, records: list[dict[str, Any]]) -> None:
        """Create records whose values are supplied as field mappings."""

    def batch_update(self, records: list[dict[str, Any]]) -> None:
        """Update records containing record_id and fields."""

    def batch_delete(self, record_ids: list[str]) -> None:
        """Delete records by record identifier."""


@dataclass(frozen=True)
class DailyBriefBitableSettings:
    app_token: str = LOW_VALUE_LEADER_BITABLE_APP_TOKEN
    table_id: str = LOW_VALUE_LEADER_BITABLE_TABLE_ID
    url: str = LOW_VALUE_LEADER_BITABLE_URL


class ExistingFeishuBitableGateway:
    """Use the supervisor app credentials for Bitable publication.

    When the dedicated supervisor bot is running, reuse its Lark client.  A
    Bitable refresh must not, however, depend on opening a second WebSocket
    session just to update a table, so a configured-but-disabled bot falls
    back to a short-lived SDK client with the same existing credentials.
    """

    def __init__(self, settings: DailyBriefBitableSettings | None = None) -> None:
        self.settings = settings or DailyBriefBitableSettings()

    @staticmethod
    def _client() -> Any:
        from src.investment_research_supervisor.daily_brief_notification_service import (
            ExistingFeishuSupervisorSender,
        )

        try:
            client = getattr(ExistingFeishuSupervisorSender._channel(), "_client", None)
            if client is not None:
                return client
        except RuntimeError:
            # The table is a publication target, not an inbound chatbot.  It
            # must remain synchronizable when the supervisor's long connection
            # is intentionally disabled.
            pass

        from src.investment_research_supervisor.hermes_feishu import HermesSupervisorFeishuCredentials

        return HermesSupervisorFeishuCredentials.load().create_lark_client()

    @staticmethod
    def _require_success(response: Any) -> Any:
        if not response.success():
            raise RuntimeError(
                f"Feishu Bitable request failed: code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )
        return response.data

    def list_fields(self) -> list[dict[str, Any]]:
        import lark_oapi.api.bitable.v1 as bitable

        page_token: str | None = None
        fields: list[dict[str, Any]] = []
        while True:
            builder = (
                bitable.ListAppTableFieldRequest.builder()
                .app_token(self.settings.app_token)
                .table_id(self.settings.table_id)
                .page_size(100)
            )
            if page_token:
                builder.page_token(page_token)
            data = self._require_success(self._client().bitable.v1.app_table_field.list(builder.build()))
            for item in list(getattr(data, "items", None) or []):
                fields.append({
                    "field_id": item.field_id,
                    "field_name": item.field_name,
                    "type": getattr(item, "type", None),
                })
            if not getattr(data, "has_more", False):
                return fields
            page_token = str(getattr(data, "page_token", "") or "")
            if not page_token:
                return fields

    def create_text_field(self, name: str) -> None:
        import lark_oapi.api.bitable.v1 as bitable

        field = bitable.AppTableField.builder().field_name(name).type(1).build()
        request = (
            bitable.CreateAppTableFieldRequest.builder()
            .app_token(self.settings.app_token)
            .table_id(self.settings.table_id)
            .request_body(field)
            .build()
        )
        self._require_success(self._client().bitable.v1.app_table_field.create(request))

    def delete_field(self, field_id: str) -> None:
        import lark_oapi.api.bitable.v1 as bitable

        request = (
            bitable.DeleteAppTableFieldRequest.builder()
            .app_token(self.settings.app_token)
            .table_id(self.settings.table_id)
            .field_id(field_id)
            .build()
        )
        self._require_success(self._client().bitable.v1.app_table_field.delete(request))

    def list_records(self) -> list[dict[str, Any]]:
        import lark_oapi.api.bitable.v1 as bitable

        page_token: str | None = None
        records: list[dict[str, Any]] = []
        while True:
            builder = (
                bitable.ListAppTableRecordRequest.builder()
                .app_token(self.settings.app_token)
                .table_id(self.settings.table_id)
                .page_size(500)
            )
            if page_token:
                builder.page_token(page_token)
            data = self._require_success(self._client().bitable.v1.app_table_record.list(builder.build()))
            for item in list(getattr(data, "items", None) or []):
                records.append({"record_id": item.record_id, "fields": dict(item.fields or {})})
            if not getattr(data, "has_more", False):
                return records
            page_token = str(getattr(data, "page_token", "") or "")
            if not page_token:
                return records

    def batch_create(self, records: list[dict[str, Any]]) -> None:
        self._write_records("create", records)

    def batch_update(self, records: list[dict[str, Any]]) -> None:
        self._write_records("update", records)

    def _write_records(self, operation: str, records: list[dict[str, Any]]) -> None:
        import lark_oapi.api.bitable.v1 as bitable

        for start in range(0, len(records), 500):
            rows = records[start:start + 500]
            payload = [
                bitable.AppTableRecord.builder()
                .record_id(str(row.get("record_id") or ""))
                .fields(dict(row.get("fields") or {}))
                .build()
                for row in rows
            ]
            if operation == "create":
                request = (
                    bitable.BatchCreateAppTableRecordRequest.builder()
                    .app_token(self.settings.app_token)
                    .table_id(self.settings.table_id)
                    .request_body(bitable.BatchCreateAppTableRecordRequestBody.builder().records(payload).build())
                    .build()
                )
                response = self._client().bitable.v1.app_table_record.batch_create(request)
            else:
                request = (
                    bitable.BatchUpdateAppTableRecordRequest.builder()
                    .app_token(self.settings.app_token)
                    .table_id(self.settings.table_id)
                    .request_body(bitable.BatchUpdateAppTableRecordRequestBody.builder().records(payload).build())
                    .build()
                )
                response = self._client().bitable.v1.app_table_record.batch_update(request)
            self._require_success(response)

    def batch_delete(self, record_ids: list[str]) -> None:
        import lark_oapi.api.bitable.v1 as bitable

        for start in range(0, len(record_ids), 500):
            request = (
                bitable.BatchDeleteAppTableRecordRequest.builder()
                .app_token(self.settings.app_token)
                .table_id(self.settings.table_id)
                .request_body(
                    bitable.BatchDeleteAppTableRecordRequestBody.builder()
                    .records(record_ids[start:start + 500])
                    .build()
                )
                .build()
            )
            self._require_success(self._client().bitable.v1.app_table_record.batch_delete(request))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _field_text(fields: dict[str, Any], name: str) -> str:
    value = fields.get(name)
    if isinstance(value, list):
        return "".join(str(item.get("text") or item) if isinstance(item, dict) else str(item) for item in value).strip()
    return _text(value)


def _number(value: Any) -> float | None:
    """Keep Bitable number fields numeric rather than formatting them as text."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coerce_for_bitable_fields(
    fields: dict[str, Any], field_types: dict[str, int | None],
) -> dict[str, Any]:
    """Preserve the table owner's existing storage types when publishing.

    The low-value table predates this publisher and users may have configured
    a display column as text rather than number.  Sending a Python float to
    such a column makes Feishu reject the *entire* batch with
    ``TextFieldConvFail``.  The publisher therefore adapts its payload to the
    actual, read-only schema instead of assuming a type from the field name.
    """
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if field_types.get(name) == 1:  # Feishu Bitable text field
            normalized[name] = _text(value)
        else:
            normalized[name] = value
    return normalized


def _price_range(low: Any, high: Any) -> str:
    """Format an existing low/high result for the boss-facing Bitable view."""
    lower = _number(low)
    upper = _number(high)
    if lower is None and upper is None:
        return "数据不足"
    if lower is None:
        return f"不低于{_text(upper)}元"
    if upper is None:
        return f"不高于{_text(lower)}元"
    return f"{_text(lower)}–{_text(upper)}元"


def _is_specific_counterpoint(value: Any) -> bool:
    text = _text(value)
    return bool(text) and not text.startswith("发现 ") and "需要继续观察" not in text


class DailyBriefBitablePublisher:
    """Synchronize only the persisted Daily Brief's current low-value pool."""

    def __init__(
        self,
        *,
        repository: InvestmentResearchDailyBriefRepository | None = None,
        gateway: DailyBriefBitableGateway | None = None,
        settings: DailyBriefBitableSettings | None = None,
    ) -> None:
        self.repository = repository or InvestmentResearchDailyBriefRepository()
        self.settings = settings or DailyBriefBitableSettings()
        self._gateway_was_injected = gateway is not None
        self.gateway = gateway or ExistingFeishuBitableGateway(self.settings)

    # Bitable is an optional archival channel.  When credentials are absent
    # (e.g. the backend Feishu channels were deliberately disabled in favour
    # of Hermes gateways), skip gracefully instead of triggering a retry storm.
    MAX_PUBLISH_ATTEMPTS = 20

    def _credentials_available(self) -> bool:
        try:
            from src.investment_research_supervisor.hermes_feishu import HermesSupervisorFeishuCredentials

            HermesSupervisorFeishuCredentials.load()
            return True
        except Exception:  # noqa: BLE001
            return False

    def publish(self, *, research_as_of: str) -> dict[str, Any]:
        brief = self.repository.get_completed(research_as_of)
        if not brief:
            return {"status": "FAILED", "research_as_of": research_as_of, "error": "daily brief is not ready"}
        # Bitable is optional archival: when credentials are absent (backend
        # Feishu channels deliberately disabled for Hermes), skip gracefully
        # instead of triggering a retry storm.  Feishu card is the primary.
        if not self._gateway_was_injected and not self._credentials_available():
            return {
                "status": "SKIPPED", "research_as_of": research_as_of,
                "error": "Feishu supervisor credentials not configured; Bitable archival skipped",
            }
        # Stop retrying after the cap; the Feishu card is the primary channel.
        existing = self.repository.delivery(
            research_as_of=research_as_of, channel="feishu_bitable", target_id=self.settings.table_id)
        if existing and existing.get("status") == "FAILED" and int(existing.get("attempts") or 0) >= self.MAX_PUBLISH_ATTEMPTS:
            return {"status": "SKIPPED", "research_as_of": research_as_of, "error": "max publish attempts reached"}
        source_rows = self._source_rows(brief)
        try:
            field_items = self.gateway.list_fields()
            existing_fields = {str(item.get("field_name") or "") for item in field_items}
            for name in _FIELD_NAMES:
                if name not in existing_fields:
                    self.gateway.create_text_field(name)
            field_types = {
                str(item.get("field_name") or ""): item.get("type")
                for item in field_items
            }
            source_rows = {
                code: _coerce_for_bitable_fields(fields, field_types)
                for code, fields in source_rows.items()
            }
            existing_records = self.gateway.list_records()
            current_codes = set(source_rows)
            updates: list[dict[str, Any]] = []
            creates: list[dict[str, Any]] = []
            deletes: list[str] = []
            matched_codes: set[str] = set()
            for record in existing_records:
                fields = dict(record.get("fields") or {})
                code = _field_text(fields, "股票代码").upper()
                if self._is_managed(fields):
                    if code in current_codes and code not in matched_codes:
                        updates.append({"record_id": record["record_id"], "fields": source_rows[code]})
                        matched_codes.add(code)
                    else:
                        deletes.append(str(record["record_id"]))
                elif self._is_known_legacy_system_record(fields, code):
                    # Earlier versions of the daily brief did not populate
                    # ``同步来源``.  Treat those rows as system-owned too: the
                    # Bitable is a current-snapshot view, not a growing daily
                    # archive.  Only the same-date, still-current company is
                    # updated; every other legacy snapshot row is removed.
                    if (
                        _field_text(fields, "研究日期") == str(brief.get("research_as_of") or "")
                        and code in current_codes
                        and code not in matched_codes
                    ):
                        updates.append({"record_id": record["record_id"], "fields": source_rows[code]})
                        matched_codes.add(code)
                    else:
                        deletes.append(str(record["record_id"]))
            for code, fields in source_rows.items():
                if code not in matched_codes:
                    creates.append({"fields": fields})
            if updates:
                self.gateway.batch_update(updates)
            if creates:
                self.gateway.batch_create(creates)
            if deletes:
                self.gateway.batch_delete(deletes)
            retired_field_ids = [
                str(item.get("field_id") or "")
                for item in field_items
                if str(item.get("field_name") or "") in _RETIRED_FIELD_NAMES
            ]
            for field_id in retired_field_ids:
                if field_id:
                    self.gateway.delete_field(field_id)
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of,
                channel="feishu_bitable",
                target_id=self.settings.table_id,
                status="SENT",
                message_id=self.settings.url,
            )
            return {
                "status": "READY", "research_as_of": research_as_of, "url": self.settings.url,
                "created": len(creates), "updated": len(updates), "deleted": len(deletes),
                "retired_fields": len(retired_field_ids),
                "row_count": len(source_rows), "delivery": delivery,
            }
        except Exception as exc:
            delivery = self.repository.record_delivery(
                research_as_of=research_as_of,
                channel="feishu_bitable",
                target_id=self.settings.table_id,
                status="FAILED",
                error=f"{type(exc).__name__}: {exc}",
                increment_attempt=True,
            )
            return {
                "status": "FAILED", "research_as_of": research_as_of,
                "error": delivery.get("error"), "delivery": delivery,
            }

    @staticmethod
    def _is_managed(fields: dict[str, Any]) -> bool:
        return _field_text(fields, "同步来源") == MANAGED_SOURCE

    @staticmethod
    def _is_known_legacy_system_record(fields: dict[str, Any], code: str) -> bool:
        version = _field_text(fields, "日报版本")
        research_date = _field_text(fields, "研究日期")
        primary_key = _field_text(fields, "文本")
        return (
            _field_text(fields, "同步来源") == ""
            and bool(code)
            and bool(research_date)
            and (
                version in _LEGACY_SYSTEM_VERSIONS
                or version.startswith("daily-brief-v")
                or primary_key == f"{research_date}|{code}"
            )
        )

    @staticmethod
    def _source_rows(brief: dict[str, Any]) -> dict[str, dict[str, Any]]:
        payload = dict(brief.get("brief_payload") or {})
        deep_by_code = {
            str(item.get("stock_code") or "").upper(): item
            for item in list(payload.get("deeply_undervalued_companies") or [])
        }
        watch_by_code = {
            str(item.get("stock_code") or "").upper(): item
            for item in list(payload.get("executive_watchlist") or [])
        }
        rows: dict[str, dict[str, Any]] = {}
        for item in list(payload.get("low_value_leader_table") or []):
            code = str(item.get("stock_code") or "").upper()
            if not code:
                continue
            deep = dict(deep_by_code.get(code) or {})
            watch = dict(watch_by_code.get(code) or {})
            # 支撑区间优先用简报现算（深度低估/A级），否则回退池快照自带的
            # 支撑区间，避免“未在简报中现算”被误显示为数据不足。
            support = dict(
                deep.get("historical_support")
                or watch.get("historical_support")
                or item.get("historical_support")
                or {}
            )
            risk = watch.get("risk_summary")
            rows[code] = {
                "研究日期": _text(brief.get("research_as_of")),
                "股票代码": code,
                "公司": _text(item.get("company_name")),
                "行业": _text(item.get("industry_name")),
                "估值状态": _text(item.get("valuation_label")),
                "现价": _number(item.get("current_price")),
                "合理价值范围": _price_range(item.get("fair_value_low"), item.get("fair_value_high")),
                "相对中位值差距": _number(item.get("valuation_gap_percent")),
                "历史支撑范围": _price_range(support.get("low"), support.get("high")),
                "研究重点": _text(watch.get("research_change") or watch.get("research_priority_reason") or f"当前处于{_text(item.get('valuation_label'))}状态"),
                "关键反证": _text(risk) if _is_specific_counterpoint(risk) else "",
                "日报版本": _text(brief.get("formula_version")),
                "同步来源": MANAGED_SOURCE,
            }
        return rows


_service: DailyBriefBitablePublisher | None = None


def get_daily_brief_bitable_publisher() -> DailyBriefBitablePublisher:
    global _service
    if _service is None:
        _service = DailyBriefBitablePublisher()
    return _service

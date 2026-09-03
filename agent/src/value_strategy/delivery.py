"""Deterministic boss-facing projection and optional delivery of strategy events."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root
from src.research_workspace.store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


_ORDER = {"THESIS_STATUS_CHANGED": 1, "THESIS_AUTHORITY_CHANGED": 2, "RISK_CHANGED": 3, "PRIORITY_CHANGED": 4, "VALUE_SCOPE_EXITED": 5, "VALUATION_RELIABILITY_CHANGED": 6, "PRIMARY_ACTION_CHANGED": 7, "VALUE_SCOPE_ENTERED": 8, "LEADER_SCOPE_CHANGED": 9}
_SEVERITY = {"INFO": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_CN = {
    "A": "重点研究", "B": "正常跟踪", "C": "暂缓研究",
    "HIGH": "高风险", "MEDIUM": "中等风险", "LOW": "低风险", "UNKNOWN": "资料不足",
    # These labels are only for the boss-facing event digest.  Event records
    # retain their stable enum values for audit and idempotency purposes.
    "FORMING": "正在形成", "STRENGTHENING": "逻辑正在增强", "UNCHANGED": "逻辑基本稳定",
    "WEAKENING": "逻辑正在减弱", "FALSIFIED": "核心逻辑已失效", "MISSING": "尚未建立",
    "AI_PROVISIONAL": "AI 初步核心逻辑（待人工复核）",
    "HUMAN_CONFIRMED": "人工已确认", "LEGACY_UNVERIFIED": "历史逻辑待核验",
    "HUMAN_REJECTED": "未获人工认可",
    "LOW_TRAP_RISK": "低估陷阱风险较低", "MEDIUM_TRAP_RISK": "低估陷阱风险中等",
    "HIGH_TRAP_RISK": "低估陷阱风险较高", "NOT_APPLICABLE": "不适用",
    "TOP1": "第1", "TOP2": "第2", "NORMAL": "正常", "WATCH": "需关注",
    "WAIT": "暂未形成关注条件", "ATTENTION": "需关注", "HIGH_ATTENTION": "重点关注",
    "BLOCKED": "研究条件受阻", "VALUATION_REVIEW_REQUIRED": "估值依据待核验",
    "DATA_REVIEW_REQUIRED": "估值资料待核验", "INSUFFICIENT": "资料不足", "RELIABLE": "可靠",
    "LIMITED": "有限", "WEAK": "偏弱", "OPEN": "待处理",
}


def _label(value: Any) -> str:
    return _CN.get(str(value), str(value or "资料不足"))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class ValueStrategyEventDeliveryPolicy:
    def mode(self, event: dict[str, Any], batch_events: list[dict[str, Any]]) -> str:
        event_type = str(event.get("event_type"))
        after, before = str(event.get("after_value")), str(event.get("before_value"))
        priority = str(((event.get("after_state") or {}).get("priority") or {}).get("tier") or "")
        if ((event_type == "THESIS_STATUS_CHANGED" and after == "FALSIFIED") or (event_type == "THESIS_AUTHORITY_CHANGED" and after == "HUMAN_REJECTED") or (event_type == "RISK_CHANGED" and after == "HIGH" and before != "HIGH") or (event_type == "PRIORITY_CHANGED" and before == "A" and after == "C") or (event_type == "VALUATION_RELIABILITY_CHANGED" and after == "INSUFFICIENT" and priority == "A")):
            return "IMMEDIATE"
        if event_type == "LEADER_SCOPE_CHANGED" and before == "TOP1" and after == "TOP2" and len(batch_events) == 1:
            return "HISTORY_ONLY"
        if event_type == "REVIEW_PRESSURE_CHANGED" and before == "NORMAL" and after == "WATCH" and len(batch_events) == 1:
            return "HISTORY_ONLY"
        return "DAILY_DIGEST"

    def batch_mode(self, events: list[dict[str, Any]]) -> str:
        modes = [self.mode(event, events) for event in events]
        return "IMMEDIATE" if "IMMEDIATE" in modes else "DAILY_DIGEST" if "DAILY_DIGEST" in modes else "HISTORY_ONLY"

    @staticmethod
    def event_sentence(event: dict[str, Any]) -> str:
        before, after = _label(event.get("before_value")), _label(event.get("after_value"))
        labels = {"RISK_CHANGED": f"总体风险由{before}变为{after}", "PRIORITY_CHANGED": f"研究优先级由{before}调整为{after}", "PRIMARY_ACTION_CHANGED": f"当前研究动作调整为{after}", "VALUE_SCOPE_ENTERED": "进入低估龙头研究范围", "VALUE_SCOPE_EXITED": "移出当前低估龙头研究范围", "THESIS_STATUS_CHANGED": f"核心逻辑状态变为{after}", "THESIS_AUTHORITY_CHANGED": f"核心逻辑确认状态变为{after}", "LEADER_SCOPE_CHANGED": f"L3 龙头位置由{before}变为{after}", "VALUATION_RELIABILITY_CHANGED": f"估值资料状态由{before}变为{after}", "PRICE_ATTENTION_CHANGED": f"价格关注条件由{before}变为{after}", "REVIEW_PRESSURE_CHANGED": f"研究复核压力由{before}变为{after}", "VALUE_TRAP_CHANGED": f"低估陷阱状态由{before}变为{after}"}
        return labels.get(str(event.get("event_type")), f"研究状态由{before}变为{after}")

    def aggregate(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            groups[str(event.get("transition_batch_id") or event.get("id"))].append(event)
        result: list[dict[str, Any]] = []
        for batch_id, items in groups.items():
            items.sort(key=lambda event: (_ORDER.get(str(event.get("event_type")), 99), str(event.get("id"))))
            primary = items[0]
            state = primary.get("after_state") or {}
            name = str(state.get("stock_name") or primary.get("stock_code") or "该公司")
            event_type, after = str(primary.get("event_type")), str(primary.get("after_value"))
            if event_type == "THESIS_STATUS_CHANGED" and after == "FALSIFIED":
                title = f"{name}核心逻辑已被证伪，需要立即复核"
            elif event_type == "THESIS_AUTHORITY_CHANGED" and after == "HUMAN_REJECTED":
                title = f"{name}核心逻辑未获人工认可，需要立即复核"
            elif event_type == "RISK_CHANGED" and after == "HIGH":
                title = f"{name}需要风险复核"
            elif event_type == "VALUE_SCOPE_EXITED":
                title = f"{name}已移出当前研究范围"
            else:
                title = f"{name}研究状态发生变化"
            sentences = [self.event_sentence(event) for event in items]
            severity = max((str(event.get("severity") or "INFO") for event in items), key=lambda value: _SEVERITY.get(value, 0))
            result.append({"transition_batch_id": batch_id, "stock_code": primary.get("stock_code"), "stock_name": name, "severity": severity, "delivery_mode": self.batch_mode(items), "title": title, "summary": "；".join(sentences), "primary_reason": sentences[0], "simultaneous_changes": sentences[1:], "research_as_of": primary.get("research_as_of"), "source_dates": (state.get("freshness") or {}), "status": primary.get("status"), "event_ids": [str(event.get("id")) for event in items], "events": items})
        return sorted(result, key=lambda item: (_SEVERITY.get(item["severity"], 0), item["transition_batch_id"]), reverse=True)


class ValueStrategyEventDeliveryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or (get_runtime_root() / "research.db"))
        schema = ResearchWorkspaceStore(self.db_path)
        schema.close()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()

    def save(self, batch: dict[str, Any], *, channel: str, status: str = "PENDING") -> dict[str, Any]:
        now = _now()
        key = _hash({"batch": batch["transition_batch_id"], "channel": channel, "mode": batch["delivery_mode"]})
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO value_strategy_event_deliveries(id,transition_batch_id,channel,delivery_mode,delivery_status,event_ids_json,idempotency_key,attempt_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO UPDATE SET updated_at=excluded.updated_at""", (f"vsd_{key[:28]}", batch["transition_batch_id"], channel, batch["delivery_mode"], status, _json(batch["event_ids"]), key, 0, now, now))
        return self.get_by_key(key) or {}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM value_strategy_event_deliveries WHERE idempotency_key=?", (key,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["event_ids"] = _load(result.pop("event_ids_json"), [])
        return result

    def list_for_batches(self, batch_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not batch_ids:
            return {}
        markers = ",".join("?" for _ in batch_ids)
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM value_strategy_event_deliveries WHERE transition_batch_id IN ({markers})", tuple(batch_ids)).fetchall()
        return {str(row["transition_batch_id"]): self.get_by_key(str(row["idempotency_key"])) or {} for row in rows}

    def list_sent(self, *, channel: str = "feishu") -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM value_strategy_event_deliveries WHERE channel=? AND delivery_status='SENT'",
                (channel,),
            ).fetchall()
        return [self.get_by_key(str(row["idempotency_key"])) or {} for row in rows]

    def mark(self, key: str, status: str, error: str | None = None) -> None:
        now = _now()
        next_retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat() if status == "FAILED" else None
        with self._lock, self._conn:
            self._conn.execute("UPDATE value_strategy_event_deliveries SET delivery_status=?,attempt_count=attempt_count+1,last_error=?,next_retry_at=?,sent_at=CASE WHEN ?='SENT' THEN ? ELSE sent_at END,updated_at=? WHERE idempotency_key=?", (status, error, next_retry, status, now, now, key))


class ValueStrategyEventNotificationService:
    def __init__(self, *, event_repository: Any, store: ValueStrategyEventDeliveryStore | None = None, policy: ValueStrategyEventDeliveryPolicy | None = None, sender: Any = None) -> None:
        self.events = event_repository
        self.store = store or ValueStrategyEventDeliveryStore(event_repository.db_path)
        self.policy = policy or ValueStrategyEventDeliveryPolicy()
        self.sender = sender

    @staticmethod
    def enabled() -> bool:
        return str(os.getenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}

    def dry_run(self, *, research_as_of: str | None = None) -> dict[str, Any]:
        batches = self.policy.aggregate(self.events.list_events(market="CN", research_as_of=research_as_of, limit=500))
        return {"batches_total": len(batches), "would_immediate": sum(item["delivery_mode"] == "IMMEDIATE" for item in batches), "would_daily_digest": sum(item["delivery_mode"] == "DAILY_DIGEST" for item in batches), "history_only": sum(item["delivery_mode"] == "HISTORY_ONLY" for item in batches), "items": [{"stock": item["stock_code"], "batch": item["transition_batch_id"], "title": item["title"], "delivery_mode": item["delivery_mode"]} for item in batches]}

    @staticmethod
    def _feature_enabled_at() -> datetime | None:
        """A deliberate enable timestamp is required before any real delivery.

        This makes a later ``true`` flip unable to replay the historical event
        ledger.  The timestamp belongs to deployment configuration rather than
        research state, so it cannot alter event generation.
        """
        return _parse_time(os.getenv("VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED_AT"))

    @staticmethod
    def _is_permanent_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(token in text for token in ("auth", "credential", "token", "config", "schema", "target_id", "permission"))

    def _sent_count_today(self, stock_code: str, all_batches: list[dict[str, Any]], research_as_of: str) -> int:
        event_to_stock = {event_id: batch["stock_code"] for batch in all_batches for event_id in batch["event_ids"]}
        count = 0
        for row in self.store.list_sent():
            if any(event_to_stock.get(event_id) == stock_code for event_id in row.get("event_ids", [])):
                if any(batch.get("research_as_of") == research_as_of and batch.get("stock_code") == stock_code for batch in all_batches):
                    count += 1
        return count

    def deliver_immediate(self, *, research_as_of: str) -> dict[str, Any]:
        projection = self.dry_run(research_as_of=research_as_of)
        if not self.enabled():
            return {**projection, "status": "DISABLED", "sent": 0}
        enabled_at = self._feature_enabled_at()
        if enabled_at is None:
            return {**projection, "status": "SKIPPED", "sent": 0, "error": "VALUE_STRATEGY_EVENT_NOTIFICATIONS_ENABLED_AT missing"}
        from src.investment_research_supervisor.daily_brief_notification_service import DailyBriefNotificationSettings, ShortLivedFeishuBriefSender
        settings = DailyBriefNotificationSettings.from_channels_config()
        if not settings.target_id:
            return {**projection, "status": "SKIPPED", "sent": 0, "error": "target_id missing"}
        sender, sent, skipped = self.sender or ShortLivedFeishuBriefSender(), 0, 0
        batches = self.policy.aggregate(self.events.list_events(market="CN", research_as_of=research_as_of, limit=500))
        for item in batches:
            if item["delivery_mode"] != "IMMEDIATE":
                continue
            row = self.store.save(item, channel="feishu")
            occurred_times = [_parse_time(event.get("occurred_at")) for event in item["events"]]
            occurred = min((value for value in occurred_times if value is not None), default=None)
            if occurred is None or occurred < enabled_at:
                if row.get("delivery_status") != "SKIPPED":
                    self.store.mark(str(row["idempotency_key"]), "SKIPPED", "event predates notification enablement")
                    skipped += 1
                continue
            if row.get("delivery_status") in {"SENT", "SKIPPED"} or int(row.get("attempt_count") or 0) >= 5:
                continue
            retry_at = _parse_time(row.get("next_retry_at"))
            if row.get("delivery_status") == "FAILED" and retry_at is not None and retry_at > datetime.now(timezone.utc):
                continue
            if item["severity"] != "CRITICAL" and self._sent_count_today(str(item["stock_code"]), batches, research_as_of) >= 2:
                self.store.mark(str(row["idempotency_key"]), "SKIPPED", "same-company daily immediate throttle")
                skipped += 1
                continue
            try:
                card = {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "投研主管｜研究状态变化"}}, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{item['title']}**\n{item['summary']}\n研究日期：{item.get('research_as_of') or '资料不足'}"}}]}}
                sender.send_interactive_card(target_id=settings.target_id, card=card)
                self.store.mark(str(row["idempotency_key"]), "SENT")
                sent += 1
            except Exception as exc:
                self.store.mark(str(row["idempotency_key"]), "SKIPPED" if self._is_permanent_error(exc) else "FAILED", str(exc))
        return {**projection, "status": "COMPLETED", "sent": sent, "skipped": skipped}

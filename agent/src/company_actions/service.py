"""Explicit preparation and read-only querying of company-action events."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.company_actions.store import CompanyActionEventStore
from src.config.paths import get_runtime_root
from src.disclosure_materials.store import DisclosureMaterialStore
from src.research_workspace.store import normalize_market, normalize_symbol
from src.tdx_data.store import TdxDataStore


EXTRACTOR_VERSION = "company-action-events-v1.0.0"
TDX_DIVIDEND_SOURCE = "TongDaXin:get_divid_factors:ex_date_proxy"
TDX_CAPITAL_SOURCE = "TongDaXin:get_gb_info_by_date"
KNOWN_EVENT_TYPES = (
    "CASH_DIVIDEND", "BONUS_SHARE", "RIGHTS_ISSUE", "SHARE_REPURCHASE", "SHARE_CANCELLATION",
    "PRIVATE_PLACEMENT", "CONVERTIBLE_BOND", "EQUITY_INCENTIVE", "SHARE_CAPITAL_CHANGE",
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_text(value: Any) -> str | None:
    raw = str(value or "").replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class CompanyActionEventService:
    """Only explicit ``prepare`` writes; ``get_events`` is strictly read-only."""

    def __init__(
        self, *, tdx_store: TdxDataStore | None = None, db_path: Path | None = None,
        disclosure_store: DisclosureMaterialStore | None = None,
    ) -> None:
        self.tdx_store = tdx_store or TdxDataStore()
        self.db_path = db_path
        self.disclosure_store = disclosure_store

    def _store(self, *, initialize: bool) -> CompanyActionEventStore:
        return CompanyActionEventStore(self.db_path, initialize=initialize)

    @staticmethod
    def _detail(store: TdxDataStore, symbol: str) -> tuple[dict[str, Any], str | None]:
        record = store.get_record("security_details", symbol)
        if not record:
            return {}, None
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        return payload, str(payload.get("updated_at") or record.get("updated_at") or "") or None

    @staticmethod
    def _source_ref(*, source_type: str, source_id: str, raw: dict[str, Any], event_date: str | None, detail_updated_at: str | None) -> dict[str, Any]:
        return {
            "source_type": source_type, "source_id": source_id, "source_url": "", "source_hash": _hash(raw),
            "announcement_date": None, "event_date": event_date, "pit_status": "PIT_LIMITED",
            "source_payload": {"cache_updated_at": detail_updated_at, "raw": raw},
        }

    @staticmethod
    def _canonical(market: str, symbol: str, event_type: str, event_date: str | None, stage: str, values: dict[str, Any]) -> str:
        normalized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return f"{market}:{symbol}:{event_type}:{event_date or ''}:{stage}:{_hash(normalized)[:20]}"

    def _event(
        self, *, market: str, symbol: str, event_type: str, event_date: str, title: str, summary: str,
        raw: dict[str, Any], source_id: str, detail_updated_at: str | None, values: dict[str, Any],
        event_stage: str = "REPORTED_EFFECTIVE_DATE", reason: str | None = None, reason_source_event_id: str | None = None,
    ) -> dict[str, Any]:
        source = self._source_ref(source_type=TDX_DIVIDEND_SOURCE if event_type != "SHARE_CAPITAL_CHANGE" else TDX_CAPITAL_SOURCE,
                                  source_id=source_id, raw=raw, event_date=event_date, detail_updated_at=detail_updated_at)
        canonical = self._canonical(market, symbol, event_type, event_date, event_stage, values)
        return {
            "canonical_key": canonical,
            "fingerprint": _hash({"canonical": canonical, "source": source["source_id"], "hash": source["source_hash"]}),
            "market": market, "stock_code": symbol, "event_type": event_type, "event_status": "DERIVED_FROM_TDX",
            "event_stage": event_stage, "parent_event_id": None, "announcement_date": None, "event_date": event_date,
            "effective_date": event_date, "research_visible_from": event_date, "source_type": source["source_type"],
            "source_id": source["source_id"], "source_url": "", "source_hash": source["source_hash"], "title": title,
            "summary": summary, "cash_amount": values.get("cash_amount"), "share_count": values.get("share_count"),
            "share_ratio": values.get("share_ratio"), "price": values.get("price"), "currency": "CNY",
            "shares_before": values.get("shares_before"), "shares_after": values.get("shares_after"),
            "purpose": None, "reason": reason, "reason_source_event_id": reason_source_event_id,
            "pit_status": "PIT_LIMITED", "confidence": "MEDIUM", "data_quality": "READY",
            "extractor_version": EXTRACTOR_VERSION, "payload": values, "source_ref": source,
        }

    @staticmethod
    def _capital_points(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        unknown: list[dict[str, Any]] = []
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                unknown.append({"source_index": index, "status": "UNKNOWN_RAW_FIELD", "raw": item})
                continue
            stamp, shares = _date_text(item.get("Date")), _number(item.get("Zgb"))
            if not stamp or shares is None or shares <= 0:
                unknown.append({"source_index": index, "status": "UNKNOWN_RAW_FIELD", "raw": item})
                continue
            grouped.setdefault(stamp, []).append({"source_index": index, "shares": shares, "raw": item})
        output: list[dict[str, Any]] = []
        for stamp, values in sorted(grouped.items()):
            if len({row["shares"] for row in values}) != 1:
                unknown.extend({"source_index": row["source_index"], "status": "UNKNOWN_RAW_FIELD", "raw": row["raw"]} for row in values)
                continue
            output.append(values[-1])
        return output, unknown

    @staticmethod
    def _match_reason(
        event_date: str, shares_before: float, shares_after: float, known_events: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return an evidenced share-change reason, or leave it unknown.

        A nearby ex-date alone is not enough.  In particular, an equity
        incentive or another issuance may sit next to a bonus-share ex-date.
        Bonus shares must reproduce the observed share-count ratio; rights
        issues may be partly subscribed, but cannot exceed their announced
        maximum ratio.  This is deliberately conservative.
        """
        candidates = [item for item in known_events if item.get("event_type") in {"RIGHTS_ISSUE", "BONUS_SHARE"} and item.get("event_date")]
        candidates.sort(key=lambda item: (abs((datetime.fromisoformat(str(item["event_date"])) - datetime.fromisoformat(event_date)).days), item["event_type"]))
        if shares_before <= 0 or shares_after <= shares_before:
            return None
        observed_ratio = shares_after / shares_before - 1
        for candidate in candidates:
            days = abs((datetime.fromisoformat(str(candidate["event_date"])) - datetime.fromisoformat(event_date)).days)
            if days > 45:
                continue
            announced_ratio = _number(candidate.get("share_ratio"))
            if announced_ratio is None or announced_ratio <= 0:
                continue
            if candidate.get("event_type") == "BONUS_SHARE":
                # Share bonus ratios are exact; allow only small rounding.
                if abs(observed_ratio - announced_ratio) <= max(0.001, announced_ratio * 0.005):
                    return candidate
            elif observed_ratio <= announced_ratio + 0.001:
                # Rights issues can be partially subscribed, so only accept a
                # positive ratio no larger than the disclosed allocation.
                return candidate
        return None

    def _capabilities(self, symbol: str, *, event_types: set[str]) -> dict[str, dict[str, str]]:
        if self.disclosure_store is not None:
            document_count = len(self.disclosure_store.list_documents(symbol))
        else:
            # This deliberately bypasses DisclosureMaterialStore construction:
            # its schema initializer is appropriate for a sync worker but not
            # for this endpoint's read-only path.
            path = Path(self.db_path) if self.db_path else get_runtime_root() / "research.db"
            document_count = 0
            if path.exists():
                conn = sqlite3.connect(str(path))
                try:
                    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_disclosure_documents'").fetchone()
                    if exists:
                        document_count = int(conn.execute("SELECT COUNT(*) FROM company_disclosure_documents WHERE stock_code=?", (symbol.split(".")[0],)).fetchone()[0])
                finally:
                    conn.close()
        return {
            "CASH_DIVIDEND": {"status": "READY" if "CASH_DIVIDEND" in event_types else "PARTIAL", "source": "TDX get_divid_factors"},
            "BONUS_SHARE": {"status": "PARTIAL", "source": "TDX get_divid_factors"},
            "RIGHTS_ISSUE": {"status": "PARTIAL", "source": "TDX get_divid_factors"},
            "SHARE_CAPITAL_CHANGE": {"status": "PARTIAL", "source": "TDX get_gb_info_by_date"},
            "SHARE_REPURCHASE": {"status": "MISSING", "source": "MISSING_SOURCE"},
            "SHARE_CANCELLATION": {"status": "MISSING", "source": "MISSING_SOURCE"},
            "PRIVATE_PLACEMENT": {"status": "MISSING", "source": "MISSING_SOURCE"},
            "CONVERTIBLE_BOND": {"status": "MISSING", "source": "MISSING_SOURCE"},
            "EQUITY_INCENTIVE": {"status": "RAW_NOT_STRUCTURED" if document_count else "MISSING", "source": "CNINFO_PERIODIC_REPORT_RAW" if document_count else "NOT_COLLECTED"},
        }

    def prepare_from_cached_details(self, market: str, stock_code: str) -> dict[str, Any]:
        """Explicitly project current local TDX cache into durable events; no network calls."""
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        detail, detail_updated_at = self._detail(self.tdx_store, symbol)
        if not detail:
            return {"company": {"market": normalized_market, "stock_code": symbol}, "status": "NOT_COLLECTED", "created": 0, "events": [], "unknown_raw_fields": [], "message": "尚未缓存该公司的 security_details；准备操作不会自动刷新通达信。"}
        store = self._store(initialize=True)
        created, events, known_events, unknown = 0, [], [], []
        try:
            for index, raw in enumerate(detail.get("dividends") if isinstance(detail.get("dividends"), list) else []):
                if not isinstance(raw, dict):
                    unknown.append({"source_index": index, "status": "UNKNOWN_RAW_FIELD", "raw": raw})
                    continue
                event_date = _date_text(raw.get("Date"))
                if not event_date:
                    unknown.append({"source_index": index, "status": "UNKNOWN_RAW_FIELD", "raw": raw})
                    continue
                type_value, bonus = str(raw.get("Type") or ""), _number(raw.get("Bonus"))
                share_bonus, allotment, allot_price = _number(raw.get("ShareBonus")), _number(raw.get("Allotment")), _number(raw.get("AllotPrice"))
                # TDX bundled `tqcenter.py` confirms all ratios are per ten
                # shares; only positive confirmed values produce actions.
                candidates: list[dict[str, Any]] = []
                if type_value == "1" and bonus is not None and bonus > 0:
                    candidates.append(self._event(market=normalized_market, symbol=symbol, event_type="CASH_DIVIDEND", event_date=event_date,
                        title="现金分红（通达信除权因子）", summary=f"每10股现金分红 {bonus:g} 元。", raw=raw, source_id=f"tdx-dividend:{event_date}:cash:{bonus:g}", detail_updated_at=detail_updated_at,
                        values={"cash_per_10_shares": bonus, "cash_per_share": bonus / 10, "cash_amount": bonus / 10, "raw_type": type_value}))
                if share_bonus is not None and share_bonus > 0:
                    candidates.append(self._event(market=normalized_market, symbol=symbol, event_type="BONUS_SHARE", event_date=event_date,
                        title="送股（通达信除权因子）", summary=f"每10股送 {share_bonus:g} 股。", raw=raw, source_id=f"tdx-dividend:{event_date}:bonus-share:{share_bonus:g}", detail_updated_at=detail_updated_at,
                        values={"share_bonus_per_10_shares": share_bonus, "share_ratio": share_bonus / 10, "raw_type": type_value}))
                if allotment is not None and allotment > 0:
                    candidates.append(self._event(market=normalized_market, symbol=symbol, event_type="RIGHTS_ISSUE", event_date=event_date,
                        title="配股（通达信除权因子）", summary=f"每10股配 {allotment:g} 股。", raw=raw, source_id=f"tdx-dividend:{event_date}:rights:{allotment:g}:{allot_price or 0:g}", detail_updated_at=detail_updated_at,
                        values={"rights_issue_per_10_shares": allotment, "share_ratio": allotment / 10, "price": allot_price if allot_price and allot_price > 0 else None, "raw_type": type_value}))
                if not candidates:
                    unknown.append({"source_index": index, "event_date": event_date, "status": "UNKNOWN_RAW_FIELD", "raw": raw})
                for candidate in candidates:
                    saved, is_new = store.save_event(candidate)
                    created += int(is_new)
                    events.append(saved)
                    known_events.append(saved)
            points, capital_unknown = self._capital_points(detail.get("capital"))
            unknown.extend(capital_unknown)
            prior = None
            for point in points:
                if prior is not None and point["shares"] != prior["shares"]:
                    event_date = _date_text(point["raw"].get("Date")) or ""
                    reason_event = self._match_reason(event_date, prior["shares"], point["shares"], known_events)
                    reason = str(reason_event.get("event_type")) if reason_event else "UNKNOWN"
                    candidate = self._event(market=normalized_market, symbol=symbol, event_type="SHARE_CAPITAL_CHANGE", event_date=event_date,
                        title="总股本变化（通达信股本资料）", summary=f"总股本由 {prior['shares']:g} 变为 {point['shares']:g}；原因：{reason}。", raw=point["raw"], source_id=f"tdx-capital:{event_date}:{prior['shares']:g}:{point['shares']:g}", detail_updated_at=detail_updated_at,
                        values={"shares_before": prior["shares"], "shares_after": point["shares"], "change_pct": round((point["shares"] / prior["shares"] - 1) * 100, 4)},
                        reason=reason, reason_source_event_id=str(reason_event.get("id")) if reason_event else None)
                    saved, is_new = store.save_event(candidate)
                    created += int(is_new)
                    events.append(saved)
                prior = point
            return {
                "company": {"market": normalized_market, "stock_code": symbol}, "status": "READY", "created": created,
                "events": events, "unknown_raw_fields": unknown,
                "capabilities": self._capabilities(symbol, event_types={str(item.get("event_type")) for item in events}),
                "message": "仅由已缓存通达信详情投影；没有调用网络或通达信客户端。",
            }
        finally:
            store.close()

    def get_events(
        self, market: str, stock_code: str, *, as_of: str | None = None, event_type: str | None = None,
        start_date: str | None = None, end_date: str | None = None,
    ) -> dict[str, Any]:
        normalized_market = normalize_market(market)
        symbol = normalize_symbol(normalized_market, stock_code)
        store = self._store(initialize=False)
        try:
            events = store.list_events(normalized_market, symbol, as_of=as_of, event_type=event_type, start_date=start_date, end_date=end_date)
            all_events = store.list_events(normalized_market, symbol)
        finally:
            store.close()
        return {
            "company": {"market": normalized_market, "stock_code": symbol}, "as_of": str(as_of)[:10] if as_of else None,
            "event_type": event_type.upper() if event_type else None, "events": events,
            "event_count": len(events), "read_only": True, "extractor_version": EXTRACTOR_VERSION,
            "capabilities": self._capabilities(symbol, event_types={str(item.get("event_type")) for item in all_events}),
        }


_service: CompanyActionEventService | None = None


def get_company_action_event_service() -> CompanyActionEventService:
    global _service
    if _service is None:
        _service = CompanyActionEventService()
    return _service
